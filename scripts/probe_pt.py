"""packettracer-view (CCNA): click button.open-dialog -> new tab? dialog? completion? then follow PDF link if any."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS_CTRLS = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
return JSON.stringify({
  ctrls: deepQ('button, a', el).map(b => ({tag: b.tagName.toLowerCase(), cls: cls(b).slice(0, 70), text: dtext(b).slice(0, 50), href: (b.getAttribute('href') || '').slice(-60), target: b.getAttribute('target'), vis: vis(b)})),
  complete: completion(stateDiv(el)),
});
"""
JS_CLICK_DIALOG = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ('button.open-dialog', el)[0]; if (!b) return 'no-dialog-btn';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""
JS_DIALOG = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const dlg = deepQ('dialog, [role=dialog], [class*="dialog"], [class*="modal"], notify-view').filter(vis);
return JSON.stringify(dlg.slice(0, 6).map(d => ({tag: d.tagName.toLowerCase(), cls: cls(d).slice(0, 60), text: dtext(d).slice(0, 200),
   links: deepQ('a, button', d).filter(vis).map(x => ({tag: x.tagName.toLowerCase(), text: dtext(x).slice(0, 40), href: (x.getAttribute('href') || '').slice(-70), cls: cls(x).slice(0, 50)})).slice(0, 10)})));
"""
item_id = sys.argv[1] if len(sys.argv) > 1 else "1.3.6"
cfg = load_config(interactive=False); log = get_logger("probe_pt", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    mid = next(c["modelid"] for c in det.components if c["tag"] == "packettracer-view")
    main = sb.driver.current_window_handle
    print("CTRLS:", sb.execute_script(JS_CTRLS, mid)[:1200])
    print("click dialog btn ->", sb.execute_script(JS_CLICK_DIALOG, mid)); time.sleep(2.5)
    print("handles:", len(sb.driver.window_handles), "| complete:", cf.is_complete(sb, mid))
    print("DIALOG:", sb.execute_script(JS_DIALOG)[:2000])
    for h in sb.driver.window_handles:
        if h != main:
            sb.driver.switch_to.window(h); print("  new tab:", sb.driver.current_url[:140]); sb.driver.close()
    sb.driver.switch_to.window(main); cf.enter(sb)
    print("complete (wait 10s):", cf.wait_complete(sb, mid, 10), "| outline:", nav.live_item_status(sb, node, sec, it))
