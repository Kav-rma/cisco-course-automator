"""Verify CDP click mechanics: elementFromPoint at computed coords (top + frame), zoom/DPR, and a known-effect click."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect
from handlers.activity_handler import _frame_offset, _cdp_mouse

JS_LINK = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const a = deepQ('a.download-file', el)[0]; if (!a) return null;
a.scrollIntoView({block: 'center', behavior: 'instant'}); const r = a.getBoundingClientRect();
return {x: r.x + 30, y: r.y + r.height / 2, dpr: window.devicePixelRatio, iw: window.innerWidth, ih: window.innerHeight, vv: window.visualViewport ? [window.visualViewport.scale, window.visualViewport.offsetLeft, window.visualViewport.offsetTop] : null};
"""
JS_EFP_FRAME = cf.JS_DEEP + r"""
const x = arguments[0], y = arguments[1];
let e = document.elementFromPoint(x, y); const path = [];
while (e) { path.push(e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ').slice(0,2).join('.') : '')); if (e.shadowRoot) { const inner = e.shadowRoot.elementFromPoint(x, y); if (inner && inner !== e) { e = inner; continue; } } break; }
return path.slice(0, 12);
"""
class Ctx:
    def __init__(self, sb): self.sb = sb
item_id = sys.argv[1] if len(sys.argv) > 1 else "1.3.6"
cfg = load_config(interactive=False); log = get_logger("probe_cdp", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    mid = next(c["modelid"] for c in det.components if c["tag"] == "packettracer-view")
    r = sb.execute_script(JS_LINK, mid); time.sleep(0.3); r = sb.execute_script(JS_LINK, mid); print("LINK rect/viewport:", r)
    print("frame elementFromPoint:", sb.execute_script(JS_EFP_FRAME, r["x"], r["y"]))
    fx, fy = _frame_offset(Ctx(sb)); print("frame offset:", (fx, fy))
    # top-document element at absolute coords
    sb.driver.switch_to.default_content()
    top = sb.execute_script("const e=document.elementFromPoint(arguments[0], arguments[1]); return e ? e.tagName + '#' + e.id + '.' + (e.className||'').toString().slice(0,60) + ' title=' + (e.title||'') : null;", fx + r["x"], fy + r["y"])
    print("top elementFromPoint:", top, "| top dpr/size:", sb.execute_script("return [window.devicePixelRatio, window.innerWidth, window.innerHeight, window.outerWidth, window.outerHeight]"))
    # CDP layout metrics
    try:
        m = sb.driver.execute_cdp_cmd("Page.getLayoutMetrics", {}); print("layout metrics:", {k: m[k] for k in m if k in ('cssVisualViewport','visualViewport','cssLayoutViewport')})
    except Exception as e: print("layout metrics err", e)
    cf.enter(sb)
    # known-effect test: CDP-click the link and watch for a 'click' listener firing (install a capture listener in the frame)
    sb.execute_script(cf.JS_BY_ID + "const el=byId(arguments[0]); const a=deepQ('a.download-file', el)[0]; window.__clicks=0; a.addEventListener('click', e => { window.__clicks++; window.__trusted = e.isTrusted; }, true); return 'listener ok';", mid)
    _cdp_mouse(Ctx(sb), "mouseMoved", fx + r["x"], fy + r["y"], 0)
    _cdp_mouse(Ctx(sb), "mousePressed", fx + r["x"], fy + r["y"], 1)
    _cdp_mouse(Ctx(sb), "mouseReleased", fx + r["x"], fy + r["y"], 0)
    time.sleep(2)
    print("after CDP: clicks on link =", sb.execute_script("return [window.__clicks, window.__trusted]"), "| handles:", len(sb.driver.window_handles), "| complete:", cf.is_complete(sb, mid))
