"""Walk a completed checkpoint question-by-question (click strip Qn) and read the DISPLAYED question via
viewport-largest detection. Prints type + question + your selected answer, so we can confirm each question
(esp. multiple matching) is read distinctly and mcq selections are visible. Read-only.
Run: .venv\Scripts\python.exe scripts\probe_walk.py --course networking-essentials --exam exam-5
"""
import json, sys, time
from pathlib import Path
SCR=Path(__file__).resolve().parent; sys.path.insert(0,str(SCR.parent)); sys.path.insert(0,str(SCR))
from core import content_frame as cf
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from extract_exams import goto_exam
from extract_quizzes import JS_GOTO_Q

def _pop(a,f):
    if f in a: i=a.index(f); v=a[i+1]; del a[i:i+2]; return v
    return None

JS_READ_DISPLAYED = cf.JS_DEEP + r"""
const vh=window.innerHeight||9999, vw=window.innerWidth||9999;
const area=(e)=>{const r=e.getBoundingClientRect();const w=Math.max(0,Math.min(r.right,vw)-Math.max(r.left,0));const h=Math.max(0,Math.min(r.bottom,vh)-Math.max(r.top,0));return w*h;};
let best=null,bestA=0,bestTag=null;
for (const tag of ['mcq-view','matching-view','object-matching-view']){
  for (const m of deepQ(tag)){
    const b=deepQ('.mcq__body-inner,.matching__body-inner,.component__body-inner',m)[0]; if(!b) continue;
    const a=area(b); if(a>bestA){bestA=a;best=m;bestTag=tag;}
  }
}
if(!best||bestA<=0) return null;
const m=best,tag=bestTag,body=deepQ('.mcq__body-inner,.matching__body-inner,.component__body-inner',m)[0];
const question=body?dtext(body):'';
if(tag==='mcq-view'){
  const opts=deepQ('.mcq__item',m).map(o=>({text:dtext(deepQ('.mcq__item-text-inner',o)[0]||o),checked:o.getAttribute('aria-checked')==='true'}));
  return JSON.stringify({type:'mcq',question,options:opts});
}
if(tag==='matching-view'){
  const dds=deepQ('matching-dropdown-view',m).map(d=>({title:dtext(deepQ('.matching__item-title',d)[0]),selected:dtext(deepQ('.js-dropdown-inner',d)[0])}));
  return JSON.stringify({type:'matching',question,dropdowns:dds});
}
return JSON.stringify({type:'object-matching',question,cats:deepQ('button.objectMatching-category-item',m).map(b=>dtext(b)),opts:deepQ('button.objectMatching-option-item',m).map(b=>dtext(b))});
"""
JS_STRIP_MAX = cf.JS_DEEP + r"""
let mx=0; for(const b of deepQ('button.block-button')){const m=dtext(b).match(/(\d+)/); if(m)mx=Math.max(mx,Number(m[1]));} return mx;
"""

a=sys.argv[1:]; course=_pop(a,"--course"); exam=_pop(a,"--exam") or "exam-5"
cfg=load_config(course=course); log=get_logger("probe_walk",cfg_path(cfg,"logs"))
structure=json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
nodes=[n for n in structure["nodes"] if n.get("kind")=="checkpoint_exam"]
node=nodes[int(exam.split("-")[1])-1]
log.info("Walking %s: %s", exam, node["title"])
with launch(cfg) as sb:
    open_course(sb,cfg)
    goto_exam(sb,cfg,node); time.sleep(2.0); cf.enter(sb)
    total=sb.execute_script(JS_STRIP_MAX) or 40
    log.info("strip max = %s", total)
    for n in range(1,total+1):
        cf.enter(sb)
        if sb.execute_script(JS_GOTO_Q,n)=="no-strip": continue
        time.sleep(0.5)
        raw=sb.execute_script(JS_READ_DISPLAYED)
        if not raw: log.info("Q%d: (nothing displayed)"%n); continue
        d=json.loads(raw)
        if d["type"]=="mcq":
            sel=[o["text"] for o in d["options"] if o["checked"]]
            log.info("Q%d [mcq] %s | selected=%s", n, d["question"][:50], sel)
        elif d["type"]=="matching":
            log.info("Q%d [matching] %s | %s", n, d["question"][:50], [(x["title"],x["selected"]) for x in d["dropdowns"]][:4])
        else:
            log.info("Q%d [objmatch] %s | cats=%s", n, d["question"][:50], [c[:18] for c in d["cats"]])
