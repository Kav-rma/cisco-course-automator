"""
Interactive content (INTERACTIVE / SUMMARY): expand every expandable element, then verify completion.

Pattern-based: enumerates accordion items from the component itself (button.accordion__item-btn[data-index]),
so GPS/Wi-Fi/Bluetooth/NFC or any other set of items works the same way. Component types other than
accordion are reported as unhandled (not silently skipped) so they can be added once seen.
"""
from __future__ import annotations

import time

from core import content_frame as cf
from core.browser import wait_until
from core.page_detector import INTERACTIVE_TAGS, PageType

from .base import HandlerContext, HandlerResult, register, scroll_through_unit, unit_complete, wait_unit_complete


def process_accordion(ctx: HandlerContext, modelid: str) -> dict:
    t = ctx.cfg["timeouts"]["element"]
    items = cf.accordion_items(ctx.sb, modelid)
    ctx.log.info("  Found %d expandable elements", len(items))
    opened = 0
    for i, it in enumerate(items, 1):
        ctx.log.info("  Processing expandable element %d/%d: %s", i, len(items), it["title"][:60])
        if cf.accordion_open(ctx.sb, modelid, it["index"], t):
            opened += 1
            time.sleep(0.4)   # brief human-like pause so the open animation / inview tracking registers
        else:
            ctx.log.warning("  Could not expand item %s", it["title"][:60])
    done = cf.wait_complete(ctx.sb, modelid, ctx.cfg["timeouts"]["completion"])
    return {"type": "accordion", "items": len(items), "opened": opened, "complete": done}


def process_animation(ctx: HandlerContext, modelid: str) -> dict:
    """'Click Play in the figure' (adobe-animate-view). Verified flow: start it through the view's own
    togglePlayback(), confirm frames advance, then jump the CreateJS clip to (total - 3) while playing so it reaches
    the end and marks itself complete. If the jump API is missing, let it run to its natural end (bounded)."""
    t = ctx.cfg["timeouts"]
    st = cf.anim_state(ctx.sb, modelid)
    if not st:
        return {"type": "animation", "ok": False, "error": "no animation state"}
    if st["complete"]:
        return {"type": "animation", "ok": True, "note": "already complete"}
    r = cf.anim_cmd(ctx.sb, modelid, "play")
    if not r.startswith("ok"):
        return {"type": "animation", "ok": False, "error": r}
    v0 = st.get("frame") or st.get("value") or 0
    started = wait_until(ctx.sb, lambda s: ((cf.anim_state(s, modelid) or {}).get("frame") or (cf.anim_state(s, modelid) or {}).get("value") or 0) > v0,
                         t["element"], poll=0.3, what="animation playing")
    if not started:
        # one more try through the play-pause element, then give up on shortcuts (natural run below may still work)
        cf.anim_cmd(ctx.sb, modelid, "play")
        started = wait_until(ctx.sb, lambda s: ((cf.anim_state(s, modelid) or {}).get("frame") or 0) > v0, 5, poll=0.3, what="animation playing (retry)")
    total = (cf.anim_state(ctx.sb, modelid) or {}).get("total") or (cf.anim_state(ctx.sb, modelid) or {}).get("max") or 0
    jumped = None
    if started and total and total > 6:
        jumped = cf.anim_cmd(ctx.sb, modelid, "seek", max(0, total - 3))
        ctx.log.info("  Figure: %d frames -> jumped near the end (%s)", total, jumped)
    done = cf.wait_complete(ctx.sb, modelid, t["completion"])
    if not done:
        # natural run (no repeated presses - they interfere with a human): wait for the end / completion, bounded
        wait_until(ctx.sb, lambda s: (lambda a: bool(a) and (a["complete"] or (a.get("total") and (a.get("frame") or 0) >= a["total"] - 1)))(cf.anim_state(s, modelid)),
                   180, poll=1.0, what="animation end")
        done = cf.wait_complete(ctx.sb, modelid, t["completion"])
    final = cf.anim_state(ctx.sb, modelid) or {}
    return {"type": "animation", "ok": bool(done), "started": started, "frames": total, "jumped": jumped, "complete": done,
            "final_frame": final.get("frame"), "paused": final.get("paused")}


def process_tabs(ctx: HandlerContext, modelid: str) -> dict:
    """tabs-view (verified markup 18.2.4): button[role=tab][data-index][aria-selected]; select every tab."""
    t = ctx.cfg["timeouts"]["element"]
    js_tabs = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return '[]';
return JSON.stringify(deepQ('button[role=tab]', el).map(b => ({index: b.getAttribute('data-index'),
  title: b.getAttribute('aria-label') || dtext(b), selected: b.getAttribute('aria-selected') === 'true'})));
"""
    js_click = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return false;
const b = deepQ('button[role=tab][data-index="' + arguments[1] + '"]', el)[0]; if (!b) return false;
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return true;
"""
    js_sel = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return false;
const b = deepQ('button[role=tab][data-index="' + arguments[1] + '"]', el)[0];
return !!b && b.getAttribute('aria-selected') === 'true';
"""
    import json as _json
    tabs = _json.loads(ctx.sb.execute_script(js_tabs, modelid))
    ctx.log.info("  Found %d tabs", len(tabs))
    opened = 0
    for tab in tabs:
        ctx.sb.execute_script(js_click, modelid, tab["index"])
        if wait_until(ctx.sb, lambda s: s.execute_script(js_sel, modelid, tab["index"]), t, poll=0.2, what=f"tab {tab['title'][:20]}"):
            opened += 1
            time.sleep(0.5)   # let the panel render / inview register
        else:
            ctx.log.warning("  Could not select tab %s", tab["title"][:40])
    done = cf.wait_complete(ctx.sb, modelid, ctx.cfg["timeouts"]["completion"])
    return {"type": "tabs", "tabs": len(tabs), "opened": opened, "complete": done}


def _click_all(ctx, modelid, selector, label, wait_cls=None, pause=0.5):
    """Click every element matching `selector` inside the component (by data-index), pausing between clicks."""
    import json as _json
    js_list = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return '[]';
return JSON.stringify(deepQ(arguments[1], el).map((b, i) => ({i, index: b.getAttribute('data-index'), cls: cls(b),
  label: b.getAttribute('aria-label') || dtext(b).slice(0, 40), disabled: !!b.disabled})));
"""
    js_click = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return false;
const b = deepQ(arguments[1], el)[arguments[2]]; if (!b) return false;
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return true;
"""
    items = _json.loads(ctx.sb.execute_script(js_list, modelid, selector))
    ctx.log.info("  Found %d %s", len(items), label)
    clicked = 0
    for it in items:
        if it["disabled"]:
            continue
        if ctx.sb.execute_script(js_click, modelid, selector, it["i"]):
            clicked += 1
            time.sleep(pause)
    return items, clicked


def process_flipcard(ctx: HandlerContext, modelid: str) -> dict:
    """flipcard-view (verified 38.1.1): button.flipcard__item[data-index] -> click each card to flip it."""
    items, clicked = _click_all(ctx, modelid, "button.flipcard__item", "flip cards", pause=0.7)
    done = cf.wait_complete(ctx.sb, modelid, ctx.cfg["timeouts"]["completion"])
    if not done and items:   # some variants need the card flipped back too
        _click_all(ctx, modelid, "button.flipcard__item", "flip cards (back)", pause=0.5)
        done = cf.wait_complete(ctx.sb, modelid, ctx.cfg["timeouts"]["completion"])
    return {"type": "flipcard", "cards": len(items), "clicked": clicked, "complete": done}


def process_narrative(ctx: HandlerContext, modelid: str) -> dict:
    """narrative-view (verified 38.1.9): slides with button.narrative__progress[data-index] indicators and
    button.narrative__controls.next; visit every slide (then open any '+' detail toggles if still incomplete)."""
    t = ctx.cfg["timeouts"]["completion"]
    items, clicked = _click_all(ctx, modelid, "button.narrative__progress", "slides", pause=0.6)
    done = cf.wait_complete(ctx.sb, modelid, t)
    if not done:
        # fall back to pressing Next until it disables
        js_next = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ('button.narrative__controls.next', el)[0]; if (!b) return 'no-next';
if (b.disabled || /\bdisabled\b/.test(cls(b))) return 'disabled';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""
        for _ in range(max(3, len(items) + 2)):
            if ctx.sb.execute_script(js_next, modelid) != "ok":
                break
            time.sleep(0.6)
        done = cf.wait_complete(ctx.sb, modelid, t)
    if not done:
        # open "Tap + for more information" toggles, if any
        _click_all(ctx, modelid, "button.narrative__strapline-btn, .narrative__strapline button, button[aria-label*='more' i]", "detail toggles", pause=0.5)
        done = cf.wait_complete(ctx.sb, modelid, t)
    return {"type": "narrative", "slides": len(items), "clicked": clicked, "complete": done}


def process_hotgraphic(ctx: HandlerContext, modelid: str) -> dict:
    """hotgraphic-view (verified 38.3.1): button.hotgraphic__pin[data-index] opens a popup
    (<hotgraphic-popup> inside <notify-view>) with button.hotgraphic-popup__close. For every pin:
    click it, wait for the popup, close it, wait for it to disappear. Pins gain is-visited."""
    import json as _json
    t = ctx.cfg["timeouts"]["element"]
    js_pins = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return '[]';
return JSON.stringify(deepQ('button.hotgraphic__pin', el).map(b => ({index: b.getAttribute('data-index'),
  visited: /is-visited/.test(cls(b)), label: b.getAttribute('aria-label') || ''})));
"""
    js_pin_click = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return false;
const b = deepQ('button.hotgraphic__pin[data-index="' + arguments[1] + '"]', el)[0]; if (!b) return false;
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return true;
"""
    js_popup_open = cf.JS_DEEP + """
const c = deepQ('hotgraphic-popup button.hotgraphic-popup__close, button.hotgraphic-popup__close')[0];
return !!c && !!(c.offsetWidth || c.offsetHeight || c.getClientRects().length);
"""
    js_popup_close = cf.JS_DEEP + """
const c = deepQ('hotgraphic-popup button.hotgraphic-popup__close, button.hotgraphic-popup__close, notify-view button.notify__close-btn')[0];
if (!c) return 'no-close'; c.click(); return 'ok';
"""
    pins = _json.loads(ctx.sb.execute_script(js_pins, modelid))
    ctx.log.info("  Found %d hotspot pins", len(pins))
    opened = 0
    for pin in pins:
        if not ctx.sb.execute_script(js_pin_click, modelid, pin["index"]):
            continue
        if wait_until(ctx.sb, lambda s: s.execute_script(js_popup_open), t, poll=0.2, what="hotgraphic popup"):
            opened += 1
            time.sleep(0.4)
            ctx.sb.execute_script(js_popup_close)
            wait_until(ctx.sb, lambda s: not s.execute_script(js_popup_open), t, poll=0.2, what="popup closed")
            time.sleep(0.3)
        else:
            ctx.log.warning("  pin %s did not open a popup", pin["label"][:30])
    done = cf.wait_complete(ctx.sb, modelid, ctx.cfg["timeouts"]["completion"])
    return {"type": "hotgraphic", "pins": len(pins), "opened": opened, "complete": done}


INTERACTIVE_PROCESSORS = {
    "accordion-view": process_accordion,
    "tabs-view": process_tabs,
    "flipcard-view": process_flipcard,
    "narrative-view": process_narrative,
    "hotgraphic-view": process_hotgraphic,
    "adobe-animate-view": process_animation,
}


def process_interactives(ctx, components) -> dict:
    """Run the matching processor for every incomplete interactive component in `components`."""
    out = {}
    for c in components:
        fn = INTERACTIVE_PROCESSORS.get(c["tag"])
        if fn and c.get("complete") is not True:
            out[c["modelid"]] = fn(ctx, c["modelid"])
    return out


@register(PageType.INTERACTIVE)
@register(PageType.SUMMARY)
class InteractiveHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        if unit_complete(ctx) is True:
            return HandlerResult("already_complete")
        res = HandlerResult("completed")
        scroll_through_unit(ctx)
        unhandled = []
        for c in ctx.detection.components:
            if c["tag"] == "accordion-view":
                res.components[c["modelid"]] = process_accordion(ctx, c["modelid"])
            elif c["tag"] == "tabs-view":
                res.components[c["modelid"]] = process_tabs(ctx, c["modelid"])
            elif c["tag"] == "hotgraphic-view":
                res.components[c["modelid"]] = process_hotgraphic(ctx, c["modelid"])
            elif c["tag"] == "flipcard-view":
                res.components[c["modelid"]] = process_flipcard(ctx, c["modelid"])
            elif c["tag"] == "narrative-view":
                res.components[c["modelid"]] = process_narrative(ctx, c["modelid"])
            elif c["tag"] == "adobe-animate-view":
                ctx.log.info("  Playing animated figure")
                res.components[c["modelid"]] = process_animation(ctx, c["modelid"])
            elif c["tag"] in INTERACTIVE_TAGS:
                unhandled.append(c["tag"])
        if unhandled:
            res.notes.append(f"unhandled interactive component types: {unhandled}")
        if wait_unit_complete(ctx):
            res.notes.append("unit complete")
            return res
        res.status = "failed"
        res.notes.append("unit still incomplete after processing interactives")
        return res
