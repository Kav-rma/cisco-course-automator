import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
JS = cf.JS_DEEP + r"""
const vis=(e)=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
const info=(b)=>({tag:b.tagName.toLowerCase(),type:b.type||null,id:b.id,cls:cls(b).slice(0,60),text:dtext(b).slice(0,30),aria:b.getAttribute('aria-label'),checked:b.checked,disabled:!!b.disabled});
return JSON.stringify({inputs:deepQ('input').filter(vis).map(info),
  submits:deepQ('button.submit-button, button').filter(vis).filter(b=>/submit|skip|next/i.test(dtext(b)+(b.getAttribute('aria-label')||''))).map(info),
  counter:deepQ('.question-label').filter(vis).map(dtext)});
"""
JS_CHECK=cf.JS_DEEP+r"""
const vis=(e)=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
const b=deepQ('input#skip-question, input[aria-label="Skip Question" i]').filter(vis)[0];
if(!b) return 'no-skip'; if(!b.checked) b.click(); return 'checked='+b.checked;
"""
cfg=load_config(interactive=False); log=get_logger("probe_skip",cfg_path(cfg,"logs"))
structure=json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node,sec,it=nav.locate(structure,"3.3.3")
with launch(cfg) as sb:
    open_course(sb,cfg); nav.goto_item(sb,cfg,node,sec,it)
    if qx.secure_state(sb).get("start_visible"): qx.secure_start(sb,"click"); time.sleep(2)
    print("STATE0:", qx.secure_state(sb).get("counter"))
    print("CONTROLS:", sb.execute_script(JS)[:1400])
    print("check skip ->", sb.execute_script(JS_CHECK)); time.sleep(0.6)
    print("CONTROLS after skip:", sb.execute_script(JS)[:800])
    print("secure_submit ->", qx.secure_submit(sb)); time.sleep(2)
    print("STATE1:", qx.secure_state(sb).get("counter"), "active_q", qx.secure_state(sb).get("active_q"))
