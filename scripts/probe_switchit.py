"""Probe the live switch-it-view: read the frame, MAC table, options and buttons, then see what
'Show me' does to the checkboxes. Ungraded practice activity. Output: data/recon/switchit_live.json
Run: .venv\Scripts\python.exe scripts\probe_switchit.py --course networking-essentials
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return JSON.stringify({err:'no component'});
const R = el.shadowRoot || el;
const q = (s) => Array.from(R.querySelectorAll(s));
const txt = (e) => (e ? e.textContent.replace(/\s+/g,' ').trim() : null);
// frame table (first table) and MAC table (.problem-details-mac-table)
const tables = q('table');
const frameHead = tables[0] ? Array.from(tables[0].querySelectorAll('th')).map(txt) : [];
const frameRow  = tables[0] ? Array.from(tables[0].querySelectorAll('tbody td')).map(txt) : [];
const macT = R.querySelector('.problem-details-mac-table');
const macHead = macT ? Array.from(macT.querySelectorAll('th')).map(h=>({t:txt(h),span:h.colSpan})) : [];
const macCells = macT ? Array.from(macT.querySelectorAll('tbody td')).map(td=>{
   const s = td.querySelector('span');
   return s ? {v: txt(s), hidden: /hide/.test(s.className), cls: s.className} : {v:null};
}) : [];
const questions = q('.question').map(qq => ({
  heading: txt(qq.querySelector('h5')),
  options: Array.from(qq.querySelectorAll('.option')).map(o => {
    const i = o.querySelector('input');
    return {label: o.getAttribute('aria-label'), id: i?i.id:null, name: i?i.name:null, checked: i?i.checked:null};
  })
}));
const buttons = q('button').map(b => ({text: txt(b), cls: b.className, disabled: b.disabled}));
const feedback = q('[class*="feedback"], [class*="correct"], [class*="notify"]').map(e=>({cls:e.className, t:txt(e).slice(0,120)})).slice(0,8);
return JSON.stringify({frameHead, frameRow, macHead, macCells, questions, buttons, feedback, complete: completion(stateDiv(el))});
"""
JS_CLICK = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const R = el.shadowRoot || el;
const want = String(arguments[1]).toLowerCase();
const b = Array.from(R.querySelectorAll('button')).find(x => (x.textContent||'').trim().toLowerCase() === want);
if (!b) return 'not-found';
b.scrollIntoView({block:'center'}); b.click(); return 'clicked';
"""

cfg = load_config(course=None); log = get_logger("probe_switchit", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "21.4.6")
out = {}
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    model = cf.read_page_model(sb)
    mid = next((c["modelid"] for u in cf.build_units(model) for c in u.get("components", [])
                if c.get("tag") == "switch-it-view"), None)
    if not mid:
        mid = "7502db7b-4a1e-489e-a882-a2799c463951"
    log.info("modelid=%s", mid)
    out["before"] = json.loads(sb.execute_script(JS_STATE, mid))
    log.info("BEFORE: complete=%s buttons=%s", out["before"].get("complete"),
             [b["text"] for b in out["before"].get("buttons", [])])
    log.info("frame: %s / %s", out["before"].get("frameHead"), out["before"].get("frameRow"))
    log.info("mac  : %s", out["before"].get("macCells"))
    r = sb.execute_script(JS_CLICK, mid, "Show me"); log.info("click 'Show me' -> %s", r)
    time.sleep(2.0)
    out["after_showme"] = json.loads(sb.execute_script(JS_STATE, mid))
    for qq in out["after_showme"].get("questions", []):
        log.info("Q: %s", qq["heading"])
        log.info("   checked now: %s", [o["label"] for o in qq["options"] if o["checked"]])
    log.info("complete after Show me: %s", out["after_showme"].get("complete"))
    p = cfg_path(cfg, "recon") / "switchit_live.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("saved %s", p)
