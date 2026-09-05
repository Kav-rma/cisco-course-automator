"""Probe v3: for N rounds, record (frame, MAC table, pointer-left) then click 'Show me' and record the
revealed correct answers. Ground truth to derive the forwarding rule. Ungraded practice activity."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

WALK = r"""
const all=[]; (function walk(n){ if(!n) return; all.push(n);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
const txt=(e)=> e ? (e.textContent||'').replace(/\s+/g,' ').trim() : null;
"""
JS_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if(!el) return JSON.stringify({err:'none'});
""" + WALK + r"""
const tbl = all.filter(e=>e.tagName==='TABLE');
const frameRow = tbl[0] ? Array.from(tbl[0].querySelectorAll('tbody td')).map(txt) : [];
const macT = all.find(e=>/problem-details-mac-table/.test(String(e.className)));
const heads = macT ? Array.from(macT.querySelectorAll('th')).map(h=>({t:txt(h),span:h.colSpan})) : [];
const cells = macT ? Array.from(macT.querySelectorAll('tbody td')).map(td=>{
   const s=td.querySelector('span'); return s?{v:txt(s),hidden:/hide/.test(s.className)}:{v:null};}) : [];
const ptr = all.find(e=>/pointer-image/.test(String(e.className)));
const qs = all.filter(e=>/(^| )question(-\d+)?( |$)/.test(String(e.className)) && e.querySelector('.option'));
const questions = qs.map(q=>({h: txt(q.querySelector('h5')),
   opts: Array.from(q.querySelectorAll('.option')).map(o=>{const i=o.querySelector('input');
      return {label:o.getAttribute('aria-label'), checked: i?i.checked:null};})}));
return JSON.stringify({frameRow, heads, cells, ptrStyle: ptr?ptr.getAttribute('style'):null, questions,
                       complete: completion(stateDiv(el))});
"""
JS_BTN = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const want=String(arguments[1]).toLowerCase();
""" + WALK + r"""
const b = all.find(x=>x.tagName==='BUTTON' && (x.textContent||'').trim().toLowerCase()===want);
if(!b) return 'not-found'; b.click(); return 'clicked';
"""

cfg = load_config(course=None); log = get_logger("probe_si3", cfg_path(cfg,"logs"))
structure = json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "21.4.6")
mid = "7502db7b-4a1e-489e-a882-a2799c463951"
rounds=[]
with launch(cfg) as sb:
    open_course(sb, cfg); nav.goto_item(sb, cfg, node, sec, it); time.sleep(1.5)
    for r in range(8):
        before = json.loads(sb.execute_script(JS_STATE, mid))
        sb.execute_script(JS_BTN, mid, "Show me"); time.sleep(1.6)
        after = json.loads(sb.execute_script(JS_STATE, mid))
        ans = [[o["label"] for o in q["opts"] if o["checked"]] for q in after.get("questions",[])]
        rec = {"frame": before.get("frameRow"), "cells": before.get("cells"),
               "heads": before.get("heads"), "ptr": before.get("ptrStyle"),
               "answer": ans, "complete": after.get("complete")}
        rounds.append(rec)
        log.info("R%d frame=%s ptr=%s", r+1, rec["frame"], rec["ptr"])
        log.info("    mac=%s", [(i, c.get("v"), c.get("hidden")) for i,c in enumerate(rec["cells"] or []) if c.get("v")])
        log.info("    ANSWER=%s complete=%s", ans, rec["complete"])
        sb.execute_script(JS_BTN, mid, "New problem"); time.sleep(1.6)
    (cfg_path(cfg,"recon")/"switchit_rounds.json").write_text(json.dumps(rounds,indent=1,ensure_ascii=False),encoding="utf-8")
    log.info("saved %d rounds", len(rounds))
