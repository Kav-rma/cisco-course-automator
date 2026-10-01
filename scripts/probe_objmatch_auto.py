"""Auto: open the course, go to a completed checkpoint, and dump any object-matching question's state
(your submitted connections) + whatever the completed exam actually renders. Read-only.
Run: .venv\Scripts\python.exe scripts\probe_objmatch_auto.py --course ccna-srwe [--exam exam-1]
"""
import json, sys, time
from pathlib import Path
SCR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCR.parent)); sys.path.insert(0, str(SCR))
from core import content_frame as cf, outline as ol
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from extract_exams import goto_exam

def _pop(argv, flag):
    if flag in argv:
        i = argv.index(flag); v = argv[i+1]; del argv[i:i+2]; return v
    return None

JS_DUMP = cf.JS_DEEP + r"""
const oms = deepQ('object-matching-view');
const mvs = deepQ('matching-view');
const anyq = deepQ('mcq-view, object-matching-view, matching-view').map(e=>e.tagName.toLowerCase());
function dumpOM(m){
  let items=[], attrs={};
  try { const md=m.model; items = JSON.parse(JSON.stringify((md.get?md.get('_items'):md._items)||[])); } catch(e){}
  try { const md=m.model; attrs = JSON.parse(JSON.stringify(md.attributes||{})); } catch(e){}
  const cats = deepQ('button.objectMatching-category-item', m).map(b=>({data_id:b.getAttribute('data-id'),
     itemindex:b.getAttribute('data-itemindex'), cls:cls(b), text:dtext(b),
     attrs:Array.from(b.attributes).map(a=>a.name+'='+a.value).join(' ')}));
  const opts = deepQ('button.objectMatching-option-item', m).map(b=>({data_id:b.getAttribute('data-id'),
     itemindex:b.getAttribute('data-itemindex'), cls:cls(b), text:dtext(b),
     attrs:Array.from(b.attributes).map(a=>a.name+'='+a.value).join(' ')}));
  return {question:dtext(deepQ('.component__body-inner',m)[0]||m).slice(0,160), model_items:items,
          model_attr_keys:Object.keys(attrs),
          userAnswer: attrs._userAnswer!==undefined?attrs._userAnswer:(attrs.userAnswer!==undefined?attrs.userAnswer:null),
          selectable: attrs._selectable, isEnabled: attrs._isEnabled, categories:cats, options:opts};
}
return JSON.stringify({tags_present: anyq, object_matching: oms.map(dumpOM),
   matching_count: mvs.length,
   page_text_snip: (deepQ('.adaptive-container, .assessment, [class*="result"], [class*="review"]').map(dtext)[0]||'').slice(0,200)});
"""

argv = sys.argv[1:]
course = _pop(argv, "--course")
exam = _pop(argv, "--exam") or "exam-1"
cfg = load_config(course=course); log = get_logger("probe_om_auto", cfg_path(cfg,"logs"))
structure = json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
exams = [n for n in structure["nodes"] if n.get("kind")=="checkpoint_exam"]
idx = int(exam.split("-")[1]) - 1
node = exams[idx]
log.info("Target %s: %s", exam, node["title"])
with launch(cfg) as sb:
    open_course(sb, cfg)
    try:
        goto_exam(sb, cfg, node)
    except Exception as e:
        log.warning("goto_exam: %s", str(e).splitlines()[0][:160])
    time.sleep(2.0)
    cf.enter(sb)
    dump = json.loads(sb.execute_script(JS_DUMP))
    log.info("tags on page: %s", dump["tags_present"])
    log.info("review/result text: %s", dump["page_text_snip"])
    log.info("object-matching components: %d  matching-view: %d", len(dump["object_matching"]), dump["matching_count"])
    for om in dump["object_matching"]:
        log.info("Q: %s", om["question"])
        log.info("  userAnswer=%s selectable=%s enabled=%s attr_keys=%s",
                 om.get("userAnswer"), om.get("selectable"), om.get("isEnabled"), om["model_attr_keys"])
        log.info("  cats: %s", [(c["data_id"], c["itemindex"], c["text"][:22], c["cls"][:40]) for c in om["categories"]])
        log.info("  opts: %s", [(o["data_id"], o["itemindex"], o["text"][:22], o["cls"][:40]) for o in om["options"]])
    (cfg_path(cfg,"recon")/"objmatch_auto.json").write_text(json.dumps(dump,indent=1,ensure_ascii=False),encoding="utf-8")
    log.info("saved data/recon/objmatch_auto.json")
