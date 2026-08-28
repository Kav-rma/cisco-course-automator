import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const info = (x) => { const r = x.getBoundingClientRect(); const cs = getComputedStyle(x);
  return {n: x.getAttribute('data-option') || x.getAttribute('data-target'), x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height), pe: cs.pointerEvents, pos: cs.position, draggable: x.draggable,
          bg: (cs.backgroundImage || '').slice(0, 40), touchAction: cs.touchAction}; };
const host = deepQ('.cable-pinout-container', el)[0];
return {options: deepQ('.option', el).map(info), targets: deepQ('.target', el).map(info),
        containerHtml: host ? host.outerHTML.slice(0, 400) : null,
        viewOwn: Object.getOwnPropertyNames(el).filter(n => !/^_\$/.test(n))};
"""
cfg = load_config(); log = get_logger("probe_pinout", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "30.4.4")
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    from core.page_detector import detect
    det = detect(cf.read_page_model(sb), it, sec)
    for c in det.components:
        if c["tag"] != "cable-pinout-view": continue
        cf.scroll_to(sb, c["modelid"], "center")
        r = sb.execute_script(JS, c["modelid"])
        print("==", c["modelid"][:8]); print(json.dumps(r, indent=1)[:2600])
        break
