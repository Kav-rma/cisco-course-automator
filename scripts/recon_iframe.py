"""
Phase 3 prep - open specific items and dump the INSIDE of the content iframe.

For each requested item id (e.g. 3.1.1 3.1.3 3.1.4): expand its module + section in the outline,
click the item, wait for the content iframe to navigate, switch into the iframe and save
data/recon/iframe_<id>.{summary.json,html,png}. Also records the outline status of the item
before/after the visit and the top-page prev/next labels.

No interaction with the lesson content itself is performed (no scrolling, no clicks inside).

Run:  .venv\\Scripts\\python.exe scripts\\recon_iframe.py 3.0.1 3.1.1 3.1.2 3.1.3 3.1.4 3.3.1
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import outline as ol  # noqa: E402
from core.browser import launch, open_course, save_diagnostics, wait_until, wait_stable  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402

IFRAME_SEL = 'iframe[title="Course content"]'

# Structural summary of whatever is inside the iframe - no selector assumptions.
# Deep (shadow-piercing) helpers, shared by summary + html dump.
JS_DEEP = r"""
function* walk(root) {                       // composed-tree walk through nested open shadow roots
  const els = root.querySelectorAll('*');
  for (const el of els) { yield el; if (el.shadowRoot) yield* walk(el.shadowRoot); }
}
const ALL = Array.from(walk(document));
const deepQ = (sel) => ALL.filter(el => { try { return el.matches(sel); } catch (e) { return false; } });
const hosts = ALL.filter(el => el.shadowRoot);
function deepText(node) {                    // visible-ish text across shadow boundaries
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const tag = node.tagName.toLowerCase();
  if (tag === 'style' || tag === 'script' || tag === 'template') return '';
  let s = node.shadowRoot ? Array.from(node.shadowRoot.childNodes).map(deepText).join(' ') : '';
  return s + Array.from(node.childNodes).map(deepText).join(' ');
}
const DEEP_TEXT = deepText(document.body).replace(/\s+/g, ' ').trim();
"""

JS_DEEP_TEXT_LEN = JS_DEEP + "return DEEP_TEXT.length;"

JS_IFRAME_SUMMARY = JS_DEEP + r"""
const host = hosts[0] || null;
const R = document;
const q = deepQ;
const txt = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ');
const attrs = (el) => Object.fromEntries(Array.from(el.attributes).map(a => [a.name, String(a.value).slice(0, 150)]));
const summarize = (els, n = 80) => els.slice(0, n).map(el => ({tag: el.tagName.toLowerCase(), text: txt(el).slice(0, 160), attrs: attrs(el)}));
const classPrefixes = {};
q('*').forEach(el => (el.className && typeof el.className === 'string' ? el.className.split(/\s+/) : []).forEach(c => {
  const p = c.replace(/--?[A-Za-z0-9_+-]{4,7}$/, ''); classPrefixes[p] = (classPrefixes[p] || 0) + 1; }));
const scrollers = q('*').filter(el => { const cs = getComputedStyle(el); return /(auto|scroll)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 20; })
  .map(el => ({tag: el.tagName.toLowerCase(), id: el.id, cls: String(el.className).slice(0, 100), sh: el.scrollHeight, ch: el.clientHeight}));
const rootText = DEEP_TEXT;
const customTags = {};
ALL.forEach(e => { const t = e.tagName.toLowerCase(); if (t.includes('-')) customTags[t] = (customTags[t] || 0) + 1; });
return JSON.stringify({
  url: location.href, title: document.title, readyState: document.readyState,
  shadowHosts: hosts.map(h => h.tagName.toLowerCase() + (h.id ? '#' + h.id : '') + (h.getAttribute('modelid') ? '[modelid=' + h.getAttribute('modelid') + ']' : '')).slice(0, 80),
  customTags,
  bodyTextLen: rootText.length,
  idsSample: q('[id]').slice(0, 150).map(e => e.tagName.toLowerCase() + '#' + e.id),
  h1: q('h1').map(txt), h2: q('h2').map(txt).slice(0, 30), h3: q('h3').map(txt).slice(0, 30),
  counts: {
    iframe: q('iframe').length, video: q('video').length, audio: q('audio').length,
    button: q('button').length, a: q('a').length, input: q('input').length,
    radio: q('input[type=radio]').length, checkbox: q('input[type=checkbox]').length,
    roleButton: q('[role=button]').length, roleRadio: q('[role=radio]').length, roleCheckbox: q('[role=checkbox]').length,
    ariaExpanded: q('[aria-expanded]').length, details: q('details').length, tabs: q('[role=tab]').length,
    dataAttrs: q('*').filter(e => Array.from(e.attributes).some(a => a.name.startsWith('data-'))).length,
    shadowHosts: q('*').filter(e => e.shadowRoot).length, forms: q('form').length, fieldset: q('fieldset').length,
  },
  iframes: q('iframe').map(f => ({src: f.src.slice(0, 200), title: f.title, id: f.id, cls: String(f.className).slice(0, 80)})),
  videos: summarize(q('video, [class*="video" i], [id*="video" i], [class*="player" i]'), 20),
  ariaExpanded: summarize(q('[aria-expanded]'), 60),
  buttons: summarize(q('button, [role=button]'), 80),
  inputs: summarize(q('input, [role=radio], [role=checkbox], select, textarea'), 80),
  fieldsets: summarize(q('fieldset, legend, [class*="question" i], [class*="quiz" i], [class*="assessment" i]'), 60),
  dataAttrNames: Array.from(new Set(q('*').flatMap(e => Array.from(e.attributes).map(a => a.name).filter(n => n.startsWith('data-'))))).sort(),
  classPrefixesTop: Object.entries(classPrefixes).sort((a, b) => b[1] - a[1]).slice(0, 120),
  scrollers,
  bodyText: rootText.slice(0, 6000),
});
"""

JS_IFRAME_HTML = r"""
// Serialize the composed tree: shadow roots inlined as <template shadowroot="open">, <style>/<script> dropped.
function ser(node, depth) {
  if (node.nodeType === Node.TEXT_NODE) { const t = node.textContent.replace(/\s+/g, ' '); return t.trim() ? t : ''; }
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const tag = node.tagName.toLowerCase();
  if (tag === 'style' || tag === 'script' || tag === 'svg' || tag === 'link') return '';
  const attrs = Array.from(node.attributes).map(a => ` ${a.name}="${String(a.value).replace(/"/g, '&quot;').slice(0, 300)}"`).join('');
  let out = `<${tag}${attrs}>`;
  if (node.shadowRoot) out += '<template shadowroot="open">' + Array.from(node.shadowRoot.childNodes).map(c => ser(c, depth + 1)).join('') + '</template>';
  out += Array.from(node.childNodes).map(c => ser(c, depth + 1)).join('');
  return out + `</${tag}>`;
}
return ser(document.body, 0);
"""


def locate(structure: dict, item_id: str):
    for node in structure["nodes"]:
        for sec in node["sections"]:
            for it in sec["items"]:
                if it["id"] == item_id:
                    return node, sec, it
    return None


def iframe_src(sb):
    # The player routes internally: the src ATTRIBUTE never changes, the frame's own location does (same-origin).
    return sb.execute_script(f"const f=document.querySelector('{IFRAME_SEL}'); try {{ return f.contentWindow.location.href; }} catch(e) {{ return f ? f.src : null; }}")


def main() -> int:
    ids = sys.argv[1:] or ["3.0.1", "3.1.1", "3.1.2", "3.1.3", "3.1.4", "3.3.1"]
    cfg = load_config()
    log = get_logger("recon_iframe", cfg_path(cfg, "logs"), cfg.get("debug", True))
    t = cfg["timeouts"]
    recon = cfg_path(cfg, "recon")

    struct_path = cfg_path(cfg, "data") / "course_structure.json"
    if not struct_path.exists():
        cand = sorted(recon.glob("structure_*.json"))
        if not cand:
            log.error("No course structure found. Run 01_course_explorer.py first.")
            return 1
        struct_path = cand[-1]
    structure = json.loads(struct_path.read_text(encoding="utf-8"))
    log.info("Using structure: %s", struct_path)

    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            for item_id in ids:
                loc = locate(structure, item_id)
                if not loc:
                    log.error("Item %s not in structure", item_id)
                    continue
                node, sec, it = loc
                log.info("=== Item %s: %s (inferred %s)", item_id, it["title"], it["inferred_type"])
                ol.ensure_node_expanded(sb, node["uuid"], t["element"])
                ol.ensure_section_expanded(sb, node["uuid"], sec["index"], t["element"])
                ol.wait_items_rendered(sb, node["uuid"], sec["index"], t["element"])
                before = ol.read_items(sb, node["uuid"], sec["index"])[it["index"]]
                src_before = iframe_src(sb)
                clicked = ol.click_item(sb, node["uuid"], sec["index"], it["index"])
                log.info("clicked outline item: %s", clicked)
                # Items within the same section share one iframe page, so the URL may legitimately not change.
                changed = wait_until(sb, lambda s: iframe_src(s) != src_before, 5, what="iframe location change")
                log.info("iframe location changed: %s -> ...%s", changed, (iframe_src(sb) or "")[-60:])

                # Switch into the iframe and wait for its content to settle (text length stable).
                sb.driver.switch_to.default_content()
                frame = sb.driver.find_element("css selector", IFRAME_SEL)
                sb.driver.switch_to.frame(frame)
                text_len_js = JS_DEEP_TEXT_LEN
                wait_until(sb, lambda s: s.execute_script("return document.readyState === 'complete'") and s.execute_script(text_len_js) > 50,
                           t["page_load"], what="iframe content")
                wait_stable(sb, lambda s: s.execute_script(text_len_js), timeout=t["element"], ticks=3, poll=0.7)
                summary = json.loads(sb.execute_script(JS_IFRAME_SUMMARY))
                html = sb.execute_script(JS_IFRAME_HTML)
                sb.driver.switch_to.default_content()

                after = ol.read_items(sb, node["uuid"], sec["index"])[it["index"]]
                nav = sb.execute_script("return Array.from(document.querySelectorAll('button[class*=\"moduleNavBtn--\"]')).map(b => b.getAttribute('aria-label'))")
                summary["outline_status_before"] = before["status"]
                summary["outline_status_after"] = after["status"]
                summary["top_prev_next"] = nav
                summary["outline_item"] = it
                base = recon / f"iframe_{item_id}"
                Path(f"{base}.summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
                Path(f"{base}.html").write_text(html, encoding="utf-8")
                sb.save_screenshot(f"{base}.png")
                log.info("saved %s.* | status %s -> %s | h1=%s | counts=%s", base.name, before["status"], after["status"],
                         summary["h1"], json.dumps(summary["counts"]))
                time.sleep(1.0)  # brief human-like pause between item visits
        except Exception as e:
            log.exception("recon_iframe failed: %s", e)
            save_diagnostics(sb, cfg, "recon_iframe_error")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
