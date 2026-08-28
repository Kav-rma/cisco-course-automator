import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
JS = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const info = (b) => ({tag: b.tagName.toLowerCase(), type: b.type || null, cls: cls(b).slice(0, 80), text: dtext(b).slice(0, 50), aria: b.getAttribute('aria-label'), id: b.id, checked: b.checked, disabled: !!b.disabled});
return JSON.stringify({
  inputs: deepQ('input').filter(vis).map(info).slice(0, 20),
  buttons: deepQ('button, [role=button]').filter(vis).filter(b => !/tabs__nav|accordion|blockContainer|mcq__item/.test(cls(b))).map(info).slice(0, 30),
  labels: deepQ('label').filter(vis).map(info).slice(0, 10),
  texts: deepQ('[class*="submit"], [class*="confirm"], [class*="assessment"]').filter(vis).map(e => e.tagName.toLowerCase() + '.' + cls(e).slice(0, 60) + ' :: ' + dtext(e).slice(0, 60)).slice(0, 15),
});
"""
cfg = load_config(interactive=False); log = get_logger("probe_submit", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "1.5.11")
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    print("STATE:", qx.secure_state(sb))
    print("DUMP:", sb.execute_script(JS))
