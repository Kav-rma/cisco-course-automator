"""Probe cable-pinout state machine on 30.4.4: read state, click Show me, read, click Check, read.
Records check_disabled + complete + option/target child counts at each step, for BOTH pinout components."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if(!el) return null;
const opts = deepQ('.option', el).map(o=>({n:o.getAttribute('data-option'), kids:o.children.length, cls:cls(o)}));
const tgts = deepQ('.target', el).map(t=>({n:t.getAttribute('data-target'), kids:t.children.length, cls:cls(t)}));
const btns = deepQ('button', el).map(b=>({id:b.id, disabled:b.disabled, txt:(b.textContent||'').trim()}));
return {opts_with_kids: opts.filter(o=>o.kids>0).length, opts_total: opts.length,
        tgts_with_kids: tgts.filter(t=>t.kids>0).length, tgts_total: tgts.length,
        buttons: btns, complete: completion(stateDiv(el))};
"""
JS_BTN = cf.JS_CLICK_BTN if hasattr(cf,'JS_CLICK_BTN') else (cf.JS_BY_ID + r"""
const el=byId(arguments[0]); const b=deepQ('button'+arguments[1], el)[0]; if(!b) return 'no-button';
if(b.disabled) return 'disabled'; b.scrollIntoView({block:'center'}); b.click(); return 'ok';
""")

cfg = load_config(course=None); log = get_logger("probe_pinout2", cfg_path(cfg,"logs"))
structure = json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "30.4.4")
with launch(cfg) as sb:
    open_course(sb, cfg); nav.goto_item(sb, cfg, node, sec, it); time.sleep(1.5)
    model = cf.read_page_model(sb)
    mids = [c["modelid"] for u in cf.build_units(model) for c in u.get("components",[]) if c.get("tag")=="cable-pinout-view"]
    log.info("found %d cable-pinout components", len(mids))
    for idx, mid in enumerate(mids):
        log.info("=== component %d (%s)", idx, mid)
        log.info("  before:   %s", json.dumps(sb.execute_script(JS_STATE, mid)))
        r = sb.execute_script(JS_BTN, mid, "#showme"); log.info("  showme -> %s", r); time.sleep(1.5)
        log.info("  after SM: %s", json.dumps(sb.execute_script(JS_STATE, mid)))
        r = sb.execute_script(JS_BTN, mid, "#check"); log.info("  check  -> %s", r); time.sleep(1.5)
        log.info("  after CK: %s", json.dumps(sb.execute_script(JS_STATE, mid)))
        log.info("  complete now: %s", cf.is_complete(sb, mid))
