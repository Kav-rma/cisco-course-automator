"""
Access to the lesson content iframe (the "sgpulse" Lit web-component player).

Verified facts (2026-08-18, dumps in data/recon/iframe_*.html):
  * iframe[title="Course content"] is same-origin; its src attribute never changes - the player routes
    internally, only contentWindow.location changes. One iframe PAGE == one course SECTION.
  * Everything inside is behind nested OPEN shadow roots (~120 hosts). Plain querySelector/innerText
    from the document root see nothing; every query must walk shadowRoot recursively.
  * Model: page-view > article-view > block-view > <component>-view, all with modelid="..".
    Outline items are the article-view or block-view whose heading-view[headingtitle] starts with the
    item id ("3.1.3 Other Wireless Networks").
  * Completion classes: .article/.block/.component/.js-heading carry is-complete | is-incomplete.
"""
from __future__ import annotations

import json
import logging
import re

from .browser import wait_until, wait_stable

log = logging.getLogger(__name__)

IFRAME_SEL = 'iframe[title="Course content"]'

# ---- JS prelude: composed-tree walking helpers (prepend to every script run inside the frame) ----
JS_DEEP = r"""
function* walk(root) {
  for (const el of root.querySelectorAll('*')) { yield el; if (el.shadowRoot) yield* walk(el.shadowRoot); }
}
const ALL = Array.from(walk(document));
// scoped: include the scope's OWN shadow root (a component host has no light-DOM children worth speaking of)
const within = (scope) => [...Array.from(walk(scope)), ...(scope.shadowRoot ? Array.from(walk(scope.shadowRoot)) : [])];
const deepQ = (sel, scope) => (scope ? within(scope) : ALL).filter(el => { try { return el.matches(sel); } catch (e) { return false; } });
const sr = (el) => el && el.shadowRoot;
const cls = (el) => (el ? String(el.className || '') : '');
// Articles/blocks/components only ever GAIN is-complete (no is-incomplete marker) -> element present & unmarked == false.
const completion = (el) => el ? /\bis-complete\b/.test(cls(el)) : null;
const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
function deepText(node) {
  if (!node) return '';
  if (node.nodeType === Node.TEXT_NODE) return node.textContent;
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const tag = node.tagName.toLowerCase();
  if (tag === 'style' || tag === 'script' || tag === 'template') return '';
  const s = node.shadowRoot ? Array.from(node.shadowRoot.childNodes).map(deepText).join(' ') : '';
  return s + Array.from(node.childNodes).map(deepText).join(' ');
}
const dtext = (el) => clean(deepText(el));
"""

# ---- Page model: page > articles > blocks > components, with headings + completion ----
JS_PAGE_MODEL = JS_DEEP + r"""
const html = document.documentElement;
const pageView = deepQ('page-view')[0] || null;
const isOwnerTag = (h) => { const t = h.tagName.toLowerCase();
  return t === 'article-view' || t === 'block-view' || t === 'page-view' || h.getAttribute('sgpulse-type') === 'component'; };
const ownerOf = (el) => {                  // nearest article/block/page/component host up the shadow chain
  let r = el.getRootNode();
  while (r && r.host) { if (isOwnerTag(r.host)) return r.host; r = r.host.getRootNode(); }
  return null;
};
const headingOf = (hostEl) => {           // heading-view directly owned by this article/block/page
  if (!hostEl || !hostEl.shadowRoot) return null;
  const hv = Array.from(walk(hostEl.shadowRoot)).find(e => e.tagName.toLowerCase() === 'heading-view' && ownerOf(e) === hostEl);
  if (!hv) return null;
  const jsHeading = hv.shadowRoot && hv.shadowRoot.querySelector('.js-heading');
  const lvl = hv.shadowRoot && hv.shadowRoot.querySelector('[aria-level]');
  return {title: clean(hv.getAttribute('headingtitle')), complete: completion(jsHeading), level: lvl ? lvl.getAttribute('aria-level') : null};
};
const componentInfo = (c) => {
  const root = c.shadowRoot ? (c.shadowRoot.querySelector('.component, .component__inner') || null) : null;
  // some components put is-complete on an inner div rather than the first .component
  const compDiv = c.shadowRoot ? Array.from(c.shadowRoot.querySelectorAll('.component')).find(d => /is-(in)?complete/.test(cls(d))) || root : null;
  const hv = c.shadowRoot ? Array.from(walk(c.shadowRoot)).find(e => e.tagName.toLowerCase() === 'heading-view' && ownerOf(e) === c) : null;
  return {
    tag: c.tagName.toLowerCase(),
    modelid: c.getAttribute('modelid'),
    complete: completion(compDiv),
    heading: hv ? clean(hv.getAttribute('headingtitle')) : null,
    classes: cls(c),
    text_len: dtext(c).length,
  };
};
const blockInfo = (b) => {
  const div = b.shadowRoot ? b.shadowRoot.querySelector('.block') : null;
  // direct components of this block only (nested ones, e.g. dynamic-graphic inside an accordion, are the component's own business)
  const comps = b.shadowRoot ? Array.from(walk(b.shadowRoot)).filter(e => e.getAttribute('sgpulse-type') === 'component' && ownerOf(e) === b) : [];
  return {modelid: b.getAttribute('modelid'), complete: completion(div), heading: headingOf(b), components: comps.map(componentInfo)};
};
const articleInfo = (a) => {
  const div = a.shadowRoot ? a.shadowRoot.querySelector('.article') : null;
  const blocks = a.shadowRoot ? Array.from(walk(a.shadowRoot)).filter(e => e.tagName.toLowerCase() === 'block-view') : [];
  return {modelid: a.getAttribute('modelid'), complete: completion(div), heading: headingOf(a), blocks: blocks.map(blockInfo)};
};
const articles = pageView && pageView.shadowRoot ? Array.from(walk(pageView.shadowRoot)).filter(e => e.tagName.toLowerCase() === 'article-view') : [];
const progressBtn = deepQ('.pagelevelprogress')[0];
return JSON.stringify({
  href: location.href,
  location_id: (html.getAttribute('data-location') || '').replace(/^page-/, ''),
  title: document.title,
  page_modelid: pageView ? pageView.getAttribute('modelid') : null,
  page_heading: pageView ? headingOf(pageView) : null,
  progress_label: progressBtn ? progressBtn.getAttribute('aria-label') : null,
  articles: articles.map(articleInfo),
});
"""


# ---- Element-level helpers (all keyed by modelid, never by position) ----
JS_BY_ID = JS_DEEP + r"""
// Prefer the VISIBLE element when a modelid is rendered twice (e.g. the CCNA secure-quiz re-renders its mcq-view).
const byId = (id) => { const c = ALL.filter(e => e.getAttribute && e.getAttribute('modelid') === id);
  return c.find(e => e.offsetWidth || e.offsetHeight || e.getClientRects().length) || c[0] || null; };
const stateDiv = (el) => {   // the div carrying is-complete for an article/block/component host
  if (!el || !el.shadowRoot) return null;
  const t = el.tagName.toLowerCase();
  if (t === 'article-view') return el.shadowRoot.querySelector('.article');
  if (t === 'block-view') return el.shadowRoot.querySelector('.block');
  return Array.from(el.shadowRoot.querySelectorAll('.component')).find(d => /is-complete/.test(cls(d))) || el.shadowRoot.querySelector('.component');
};
"""

JS_SCROLL_TO = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return false;
el.scrollIntoView({block: arguments[1] || 'center', behavior: 'instant'}); return true;
"""
JS_IS_COMPLETE = JS_BY_ID + r"""
const el = byId(arguments[0]); return el ? completion(stateDiv(el)) : null;
"""
JS_SCROLL_BY = r"""window.scrollBy(0, arguments[0]); return [window.scrollY, document.documentElement.scrollHeight, window.innerHeight];"""
JS_RECT = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const r = el.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, height: r.height, vh: window.innerHeight, y: window.scrollY};
"""


def rect(sb, modelid: str) -> dict | None:
    return sb.execute_script(JS_RECT, modelid)


def scroll_read_through(sb, modelid: str, pause: float = 0.25, step_ratio: float = 0.7, max_steps: int = 60) -> None:
    """Scroll so that the whole element passes through the viewport top-to-bottom (Adapt's in-view completion
    needs both the top and the bottom of a component to have been on screen)."""
    import time as _t
    scroll_to(sb, modelid, "start")
    _t.sleep(pause)
    for _ in range(max_steps):
        r = rect(sb, modelid)
        if not r or r["bottom"] <= r["vh"]:
            break
        sb.execute_script(JS_SCROLL_BY, r["vh"] * step_ratio)
        _t.sleep(pause)
    _t.sleep(pause)

# Accordion (verified markup): button.accordion__item-btn[aria-expanded][data-index] + #accordion-item-N region
JS_ACCORDION_ITEMS = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return '[]';
const btns = deepQ('button.accordion__item-btn', el);
return JSON.stringify(btns.map(b => ({index: b.getAttribute('data-index'),
  title: dtext(b.querySelector('.accordion__item-title-inner') || b), expanded: b.getAttribute('aria-expanded') === 'true'})));
"""
JS_ACCORDION_CLICK = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return false;
const b = deepQ('button.accordion__item-btn[data-index="' + arguments[1] + '"]', el)[0]; if (!b) return false;
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return true;
"""
JS_ACCORDION_EXPANDED = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return false;
const b = deepQ('button.accordion__item-btn[data-index="' + arguments[1] + '"]', el)[0];
return !!b && b.getAttribute('aria-expanded') === 'true';
"""

# Video (verified markup): media-view > ... div#my-player.video-js > video#my-player_html5_api
JS_VIDEO_STATE = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const v = deepQ('video', el)[0]; if (!v) return null;
return {duration: v.duration, currentTime: v.currentTime, paused: v.paused, ended: v.ended, readyState: v.readyState,
        rate: v.playbackRate, muted: v.muted, src: (v.currentSrc || '').slice(0, 60), complete: completion(stateDiv(el))};
"""
JS_VIDEO_CMD = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-component';
const v = deepQ('video', el)[0]; if (!v) return 'no-video';
const cmd = arguments[1], val = arguments[2];
try {
  if (cmd === 'play') { v.scrollIntoView({block: 'center', behavior: 'instant'}); const p = v.play(); if (p && p.catch) p.catch(e => console.warn('play rejected', e)); return 'ok'; }
  if (cmd === 'pause') { v.pause(); return 'ok'; }
  if (cmd === 'seek') { v.currentTime = val; return 'ok'; }
  if (cmd === 'rate') { v.playbackRate = val; return 'ok'; }
  if (cmd === 'mute') { v.muted = !!val; return 'ok'; }
} catch (e) { return 'error: ' + e.message; }
return 'unknown-cmd';
"""


def scroll_to(sb, modelid: str, block: str = "center") -> bool:
    return bool(sb.execute_script(JS_SCROLL_TO, modelid, block))


def is_complete(sb, modelid: str):
    return sb.execute_script(JS_IS_COMPLETE, modelid)


def wait_complete(sb, modelid: str, timeout: float) -> bool:
    return wait_until(sb, lambda s: s.execute_script(JS_IS_COMPLETE, modelid) is True, timeout, what=f"{modelid} complete")


def accordion_items(sb, modelid: str) -> list[dict]:
    return json.loads(sb.execute_script(JS_ACCORDION_ITEMS, modelid))


def accordion_open(sb, modelid: str, index: str, timeout: float) -> bool:
    if sb.execute_script(JS_ACCORDION_EXPANDED, modelid, index):
        return True
    sb.execute_script(JS_ACCORDION_CLICK, modelid, index)
    return wait_until(sb, lambda s: s.execute_script(JS_ACCORDION_EXPANDED, modelid, index), timeout, what="accordion item open")


def video_state(sb, modelid: str) -> dict | None:
    return sb.execute_script(JS_VIDEO_STATE, modelid)


def video_cmd(sb, modelid: str, cmd: str, val=None) -> str:
    return sb.execute_script(JS_VIDEO_CMD, modelid, cmd, val)


# ---- Python API ----

def enter(sb) -> None:
    """Switch the WebDriver context into the content iframe (idempotent: always from top)."""
    sb.driver.switch_to.default_content()
    frame = sb.driver.find_element("css selector", IFRAME_SEL)
    sb.driver.switch_to.frame(frame)


def leave(sb) -> None:
    sb.driver.switch_to.default_content()


def frame_href(sb) -> str | None:
    """Current URL of the content frame, read from the TOP context (same-origin)."""
    sb.driver.switch_to.default_content()
    return sb.execute_script(
        f"const f=document.querySelector('{IFRAME_SEL}'); try {{ return f.contentWindow.location.href; }} catch(e) {{ return f ? f.src : null; }}")


def wait_page_ready(sb, timeout: float, settle: float = 6.0) -> None:
    """Inside the frame: wait for a page-view with articles, then for the composed text to stop growing."""
    wait_until(sb, lambda s: s.execute_script(JS_DEEP + "return deepQ('article-view').length > 0"), timeout, what="page-view articles")
    wait_stable(sb, lambda s: s.execute_script(JS_DEEP + "return dtext(document.body).length"), timeout=settle, ticks=3, poll=0.6)


def read_page_model(sb) -> dict:
    """Inside the frame: structured page > articles > blocks > components model."""
    return json.loads(sb.execute_script(JS_PAGE_MODEL))


_ID_PREFIX = re.compile(r"^(\d+(?:\.\d+)+)\b")

# Components that stand for a whole outline item on their own even without a heading (quiz/exam launchers).
STANDALONE_TAGS = {"adaptive-start-screen-view", "assessment-view", "assessmentresults-view", "start-screen-view"}


def _hid(h: dict | None) -> str | None:
    t = (h or {}).get("title") or ""
    m = _ID_PREFIX.match(t)
    return m.group(1) if m else None


def build_units(model: dict) -> list[dict]:
    """Split the page into outline-item scopes ("units") in page order.

    Rules (verified on sections 3.0/3.1/3.3):
      * an article or block with a numbered heading starts a unit (article-level heading owns all its blocks);
      * heading-less blocks/articles attach to the previous unit (e.g. 3.1.4's Question 2 lives in the next article);
      * EXCEPT a block holding a standalone component (quiz/exam start screen) starts an anonymous unit;
      * heading-less content before the first heading is the page preamble (unit id None, kind "preamble").
    """
    units: list[dict] = []

    def new_unit(kind, holder, heading, article_modelid, anonymous=False):
        u = {"kind": kind, "modelid": holder["modelid"], "article_modelid": article_modelid, "heading": (heading or {}).get("title"),
             "item_id": _hid(heading), "complete": holder["complete"], "heading_complete": (heading or {}).get("complete"),
             "components": [], "blocks": [], "anonymous": anonymous}
        units.append(u)
        return u

    cur = None
    for a in model.get("articles", []):
        if a.get("heading") and _hid(a["heading"]):
            cur = new_unit("article", a, a["heading"], a["modelid"])
            for b in a["blocks"]:
                cur["blocks"].append(b); cur["components"].extend(b["components"])
            continue
        for b in a["blocks"]:
            tags = {c["tag"] for c in b["components"]}
            if b.get("heading") and _hid(b["heading"]):
                cur = new_unit("block", b, b["heading"], a["modelid"])
            elif tags & STANDALONE_TAGS:
                cur = new_unit("block", b, None, a["modelid"], anonymous=True)
            elif cur is None:
                cur = {"kind": "preamble", "modelid": a["modelid"], "article_modelid": a["modelid"], "heading": None, "item_id": None,
                       "complete": a["complete"], "heading_complete": None, "components": [], "blocks": [], "anonymous": False}
                units.append(cur)
            cur["blocks"].append(b); cur["components"].extend(b["components"])
    return units


def find_item_scope(model: dict, item_id: str, section_items: list[dict] | None = None) -> dict | None:
    """Locate the unit for `item_id`: by heading id first; otherwise, if `section_items` (the section's outline
    items in order) is given, map anonymous units to the outline items that have no heading, in order."""
    units = build_units(model)
    for u in units:
        if u["item_id"] == item_id:
            return u
    if section_items:
        matched = {u["item_id"] for u in units if u["item_id"]}
        unmatched_items = [it["id"] for it in section_items if it.get("id") and it["id"] not in matched]
        anon = [u for u in units if u["anonymous"]]
        for iid, u in zip(unmatched_items, anon):
            if iid == item_id:
                return u
    return None


def item_headings(model: dict) -> list[str]:
    out = []
    for a in model.get("articles", []):
        if (a.get("heading") or {}).get("title"):
            out.append(a["heading"]["title"])
        for b in a["blocks"]:
            if (b.get("heading") or {}).get("title"):
                out.append(b["heading"]["title"])
    return out


# ---- Adobe-Animate figures ("Click Play in the figure"): adobe-animate-view > play-pause button + aa-scrubber input[type=range]
JS_ANIM_STATE = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const rng = deepQ('input[type="range"]', el)[0];
const clip = el.__animClip || null;
return {value: rng ? Number(rng.value) : (clip ? clip.currentFrame : null), max: rng ? Number(rng.max) : (clip ? clip.totalFrames : null),
        paused: (typeof el.__paused === 'boolean') ? el.__paused : null,
        frame: clip ? clip.currentFrame : null, total: clip ? clip.totalFrames : null,
        api: typeof el.togglePlayback === 'function', complete: completion(stateDiv(el))};
"""
# Verified 2026-08-19 (probe_anim.py on 9.1.2): the view owns the player. el.togglePlayback() starts/stops it
# (el.__paused is the gate); el.__animClip is the CreateJS MovieClip; gotoAndPlay(total-3) WHILE PLAYING runs to
# the end and the component marks itself complete. Synthetic clicks on the inner button and the scrubber do NOT work
# (the scrubber seek even leaves the figure paused/stuck), so neither is used.
JS_ANIM_CMD = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-component';
const cmd = arguments[1], val = arguments[2];
try {
  if (cmd === 'play') {
    el.scrollIntoView({block: 'center', behavior: 'instant'});
    if (typeof el.togglePlayback === 'function') { if (el.__paused !== false) el.togglePlayback(); return 'ok'; }
    const pp = deepQ('play-pause', el)[0];
    if (pp && typeof pp.togglePlayPause === 'function') { pp.togglePlayPause(); return 'ok-pp'; }
    return 'no-api';
  }
  if (cmd === 'pause') { if (typeof el.togglePlayback === 'function' && el.__paused === false) el.togglePlayback(); return 'ok'; }
  if (cmd === 'seek') {
    const clip = el.__animClip; if (!clip || typeof clip.gotoAndPlay !== 'function') return 'no-clip';
    clip.gotoAndPlay(Number(val)); return 'ok';
  }
} catch (e) { return 'error: ' + e.message; }
return 'unknown-cmd';
"""


def anim_state(sb, modelid: str) -> dict | None:
    return sb.execute_script(JS_ANIM_STATE, modelid)


def anim_cmd(sb, modelid: str, cmd: str, val=None) -> str:
    return sb.execute_script(JS_ANIM_CMD, modelid, cmd, val)


# ---- Packet Tracer items: pagetracer-view > button.pageTracer-button / a.download-btn
JS_PT_BUTTONS = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return '[]';
const out = [];
// CCNA packettracer-view: button.open-dialog opens the lab PDF in a new tab and completes the item (verified 1.3.6)
deepQ('button.open-dialog', el).forEach((b, i) => out.push({kind: 'dialog', title: dtext(b), id: String(i)}));
deepQ('button.pageTracer-button', el).forEach(b => out.push({kind: 'button', title: b.title || dtext(b), id: b.getAttribute('data-page-tracer-button-id')}));
deepQ('a.download-btn', el).forEach(a => out.push({kind: 'download', title: a.title || dtext(a), id: a.getAttribute('data-page-tracer-download-button-id'), href: a.getAttribute('href')}));
deepQ('a.download-file', el).forEach(a => out.push({kind: 'file', title: dtext(a), id: a.getAttribute('data-index'), href: a.getAttribute('href')}));
return JSON.stringify(out);
"""
JS_PT_CLICK = JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-component';
let b;
if (arguments[1] === 'dialog') b = deepQ('button.open-dialog', el)[Number(arguments[2])];
else {
  const sel = arguments[1] === 'download' ? 'a.download-btn[data-page-tracer-download-button-id="' + arguments[2] + '"]'
            : arguments[1] === 'file' ? 'a.download-file[data-index="' + arguments[2] + '"]'
            : 'button.pageTracer-button[data-page-tracer-button-id="' + arguments[2] + '"]';
  b = deepQ(sel, el)[0];
}
if (!b) return 'no-target';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""


def pt_buttons(sb, modelid: str) -> list[dict]:
    return json.loads(sb.execute_script(JS_PT_BUTTONS, modelid))


def pt_click(sb, modelid: str, kind: str, bid: str) -> str:
    return sb.execute_script(JS_PT_CLICK, modelid, kind, bid)
