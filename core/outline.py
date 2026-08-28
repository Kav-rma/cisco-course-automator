"""
Course outline access (left-hand curriculum tree in the TOP page, not the content iframe).

Verified DOM facts (2026-08-18):
  node      : [class*="nodeContainer--"] > ... button[id="node-button-<uuid>"]  (aria-expanded)
                label    [class*="nodeName--"]
                progress [data-percentage] (optional, async)
              aria-controls="node-<uuid>" points to an id that does NOT exist -> use the container.
  section   : within the node container: [class*="subModuleContainer--"] > ... button[id^="submodule-button-"]
                (ids are NOT unique: every section of a module reuses the module uuid)
                title    [class*="subModuleName--"][title]      e.g. "3.1. Wireless Networks"
                counter  [class*="descendantProgress--"]        e.g. "0 / 4"
                status   img[alt] in [class*="subModuleStatus--"]  start | in progress | completed
  item      : rendered ONLY while its section is expanded:
              [class*="blockMainContainer--"] > a[class*="blockContainer--"][role=button]
                title    [class*="blockName--"][title]          e.g. "3.1.1 Video - Types of Wireless Networks"
                status   img[alt]
CSS-module hashes (nodeName--AZrtx) change per deploy -> match on the prefix with [class*="..--"].
"""
from __future__ import annotations

import json
import logging
import re
import time

from .browser import wait_until

log = logging.getLogger(__name__)

# ---- JS helpers (all return JSON strings; parsed in Python for driver-independence) ----

_JS_COMMON = r"""
const q = (root, s) => Array.from(root.querySelectorAll(s));
const nodeBtn = (uuid) => document.getElementById('node-button-' + uuid);
const nodeContainer = (uuid) => nodeBtn(uuid).closest('[class*="nodeContainer--"]');
const sectionContainers = (uuid) => q(nodeContainer(uuid), '[class*="subModuleContainer--"]');
const sectionBtn = (uuid, i) => sectionContainers(uuid)[i].querySelector('button[id^="submodule-button-"]');
const alt = (root, s) => { const el = root.querySelector(s); return el ? el.alt : null; };
const txt = (root, s) => { const el = root.querySelector(s); return el ? el.innerText.trim() : null; };
const ttl = (root, s) => { const el = root.querySelector(s); return el ? (el.title || el.innerText.trim()) : null; };
"""

JS_READ_NODES = _JS_COMMON + r"""
const title = (document.querySelector('main h1') || {}).innerText || document.title;
const nodes = q(document, 'button[id^="node-button-"]').map((btn, index) => {
  const uuid = btn.id.replace('node-button-', '');
  const prog = btn.querySelector('[data-percentage]');
  const sections = sectionContainers(uuid).map((sc, i) => {
    const b = sc.querySelector('button[id^="submodule-button-"]');
    return {
      index: i,
      title: ttl(sc, '[class*="subModuleName--"]'),
      counter: txt(sc, '[class*="descendantProgress--"]'),
      status: alt(sc, '[class*="subModuleStatus--"] img[alt]'),
      // Expandable sections carry aria-expanded + a hasBlocks-- container; graded leaves (exams) do not.
      expandable: !!(b && b.hasAttribute('aria-expanded')) || /hasBlocks--/.test(sc.className),
      expanded: !!(b && b.getAttribute('aria-expanded') === 'true'),
      graded: !!sc.querySelector('[class*="graded--"]'),
      max_grade: txt(sc, '[class*="maxGrade--"]'),
    };
  });
  return {index, uuid, name: txt(btn, '[class*="nodeName--"]') || btn.innerText.trim(),
          percentage: prog ? Number(prog.dataset.percentage) : null,
          expanded: btn.getAttribute('aria-expanded') === 'true', sections};
});
return JSON.stringify({title, nodes});
"""

JS_READ_ITEMS = _JS_COMMON + r"""
const [uuid, i] = arguments;
const sc = sectionContainers(uuid)[i];
const items = q(sc, '[class*="blockMainContainer--"] a[class*="blockContainer--"]').map((a, j) => ({
  index: j,
  title: ttl(a, '[class*="blockName--"]'),
  status: alt(a, 'img[alt]'),
}));
return JSON.stringify(items);
"""

JS_CLICK_NODE = _JS_COMMON + r"""
const [uuid] = arguments; const b = nodeBtn(uuid); b.scrollIntoView({block: 'center'}); b.click(); return b.getAttribute('aria-expanded');
"""
JS_NODE_EXPANDED = _JS_COMMON + r"""
const [uuid] = arguments; return nodeBtn(uuid).getAttribute('aria-expanded') === 'true';
"""
JS_CLICK_SECTION = _JS_COMMON + r"""
const [uuid, i] = arguments; const b = sectionBtn(uuid, i); b.scrollIntoView({block: 'center'}); b.click(); return b.getAttribute('aria-expanded');
"""
JS_SECTION_EXPANDED = _JS_COMMON + r"""
const [uuid, i] = arguments; return sectionBtn(uuid, i).getAttribute('aria-expanded') === 'true';
"""
JS_ITEM_COUNT = _JS_COMMON + r"""
const [uuid, i] = arguments; return q(sectionContainers(uuid)[i], 'a[class*="blockContainer--"]').length;
"""
JS_CLICK_ITEM = _JS_COMMON + r"""
const [uuid, i, j] = arguments;
const a = q(sectionContainers(uuid)[i], 'a[class*="blockContainer--"]')[j];
a.scrollIntoView({block: 'center'}); a.click(); return ttl(a, '[class*="blockName--"]');
"""
JS_ACTIVE_ITEM = _JS_COMMON + r"""
// The currently open item is the block whose container carries an "active"/"selected"-ish class.
const a = q(document, 'a[class*="blockContainer--"]').find(el => /active|selected|current/i.test(el.className) || el.getAttribute('aria-current'));
return a ? ttl(a, '[class*="blockName--"]') : null;
"""


# ---- Python API ----

def read_nodes(sb) -> dict:
    return json.loads(sb.execute_script(JS_READ_NODES))


def read_items(sb, uuid: str, section_index: int) -> list[dict]:
    return json.loads(sb.execute_script(JS_READ_ITEMS, uuid, section_index))


def _ensure_expanded(sb, is_expanded, do_click, timeout: float, attempts: int, what: str) -> bool:
    """Click-until-expanded with bounded retries. The outline animates (height transitions) and a
    section click also navigates the content iframe, so a single click can be swallowed while the
    UI is busy; a short wait + retry is more reliable than one click + one long wait."""
    if is_expanded():
        return True
    per_try = max(2.0, timeout / attempts)
    for attempt in range(1, attempts + 1):
        do_click()
        if wait_until(sb, lambda s: is_expanded(), per_try, what=f"{what} (attempt {attempt})"):
            return True
        log.debug("%s: click attempt %d did not expand, retrying", what, attempt)
    return is_expanded()


def ensure_node_expanded(sb, uuid: str, timeout: float, attempts: int = 3) -> bool:
    return _ensure_expanded(
        sb, lambda: sb.execute_script(JS_NODE_EXPANDED, uuid), lambda: sb.execute_script(JS_CLICK_NODE, uuid),
        timeout, attempts, f"node {uuid[:8]} expand")


def ensure_section_expanded(sb, uuid: str, i: int, timeout: float, attempts: int = 3) -> bool:
    """NOTE: clicking a section button ALSO navigates the content iframe to that section."""
    return _ensure_expanded(
        sb, lambda: sb.execute_script(JS_SECTION_EXPANDED, uuid, i), lambda: sb.execute_script(JS_CLICK_SECTION, uuid, i),
        timeout, attempts, f"section {uuid[:8]}/{i} expand")


def wait_items_rendered(sb, uuid: str, i: int, timeout: float) -> int:
    wait_until(sb, lambda s: s.execute_script(JS_ITEM_COUNT, uuid, i) > 0, timeout, what="items render")
    return sb.execute_script(JS_ITEM_COUNT, uuid, i)


def click_item(sb, uuid: str, i: int, j: int) -> str:
    return sb.execute_script(JS_CLICK_ITEM, uuid, i, j)


def active_item_title(sb):
    return sb.execute_script(JS_ACTIVE_ITEM)


# ---- Parsing helpers ----

_ID_TITLE = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(.*)$")


def split_id_title(text: str | None) -> tuple[str | None, str]:
    """'3.1. Wireless Networks' -> ('3.1', 'Wireless Networks'); '3.1.1 Video - X' -> ('3.1.1', 'Video - X')."""
    if not text:
        return None, ""
    m = _ID_TITLE.match(text)
    return (m.group(1), m.group(2).strip()) if m else (None, text.strip())


def classify_node(name: str) -> tuple[str, int | None]:
    m = re.match(r"^Module\s+(\d+):", name)
    if m:
        return "module", int(m.group(1))
    low = name.lower()
    words = set(re.findall("[a-z0-9]+", low))
    if "module" in words and "checkpoint" not in words and not ("final" in words and "exam" in words):
        # e.g. "CCNA 200-301 Exam v1.1 Supplemental Module" is a content module, not an exam
        return "module", None
    if "final exam" in low:
        return "final_exam", None
    if "checkpoint exam" in low or "exam" in low:
        return "checkpoint_exam", None
    if "assessment" in low:
        return "assessment", None
    if "survey" in low:
        return "survey", None
    if "introduction" in low:
        return "course_introduction", None
    return "other", None


def infer_item_type(title: str) -> str:
    """Cheap hint from the outline title only. Authoritative type comes from the page detector (Phase 3)."""
    low = title.lower()
    if low.startswith("video") or " video " in f" {low} ":
        return "video"
    if "check your understanding" in low:
        return "knowledge_check"
    if {"quiz", "exam", "exams"} & set(re.findall("[a-z]+", low)):
        return "assessment"
    if "packet tracer" in low or low.startswith("lab"):
        return "lab"
    if "what did i learn" in low or "summary" in low:
        return "summary"
    if "introduction" in low or "why should i take this module" in low or "what will i learn" in low:
        return "introduction"
    return "content"
