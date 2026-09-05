"""Test whether cable-pinout is click-source-then-click-target (not drag). For component 0: click
option 1, read its class + check state; click target 1, read; then try mapping all 8 by data-option==data-target."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS_CLICK_EL = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if(!el) return 'no-el';
const sel = arguments[1];
const t = deepQ(sel, el)[0]; if(!t) return 'no-target:'+sel;
t.scrollIntoView({block:'center', behavior:'instant'}); t.click(); return 'clicked';
"""
JS_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if(!el) return null;
const chk = deepQ('#check', el)[0];
const opts = deepQ('.option', el).map(o=>({n:o.getAttribute('data-option'), cls:cls(o), kids:o.children.length}));
const tgts = deepQ('.target', el).map(t=>({n:t.getAttribute('data-target'), cls:cls(t), kids:t.children.length}));
return {check_disabled: chk?chk.disabled:null, complete: completion(stateDiv(el)),
        sel_opts: opts.filter(o=>/select|active|chosen|current/i.test(o.cls)).map(o=>o.n),
        filled_tgts: tgts.filter(t=>t.kids>0||/filled|placed|has/i.test(t.cls)).map(t=>t.n)};
"""

cfg = load_config(course=None); log = get_logger("probe_pinout3", cfg_path(cfg,"logs"))
structure = json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "30.4.4")
with launch(cfg) as sb:
    open_course(sb, cfg); nav.goto_item(sb, cfg, node, sec, it); time.sleep(1.5)
    model = cf.read_page_model(sb)
    mids = [c["modelid"] for u in cf.build_units(model) for c in u.get("components",[]) if c.get("tag")=="cable-pinout-view"]
    mid = mids[0]
    log.info("component 0 = %s", mid)
    log.info("state0: %s", json.dumps(sb.execute_script(JS_STATE, mid)))
    log.info("click option 1 -> %s", sb.execute_script(JS_CLICK_EL, mid, '.option[data-option="1"]')); time.sleep(0.5)
    log.info("state after opt1: %s", json.dumps(sb.execute_script(JS_STATE, mid)))
    log.info("click target 1 -> %s", sb.execute_script(JS_CLICK_EL, mid, '.target[data-target="1"]')); time.sleep(0.5)
    log.info("state after tgt1: %s", json.dumps(sb.execute_script(JS_STATE, mid)))
    # if that placed a wire, do the rest 2..8
    for n in range(2,9):
        sb.execute_script(JS_CLICK_EL, mid, f'.option[data-option="{n}"]'); time.sleep(0.25)
        sb.execute_script(JS_CLICK_EL, mid, f'.target[data-target="{n}"]'); time.sleep(0.25)
    log.info("state after all click-click: %s", json.dumps(sb.execute_script(JS_STATE, mid)))
