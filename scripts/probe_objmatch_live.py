"""Run this WHILE you are on the object-matching question with your pairs connected.
It opens the course, waits for you to press Enter, then dumps the answered object-matching DOM + model so we can
learn how YOUR connections are stored. Read-only. Output: data/recon/objmatch_answered.json
Run: .venv\Scripts\python.exe scripts\probe_objmatch_live.py --course ccna-srwe
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS = cf.JS_DEEP + r"""
const oms = deepQ('object-matching-view');
return JSON.stringify(oms.map(m => {
  const model = m.model || null; let items=[], attrs={};
  try { items = JSON.parse(JSON.stringify((model.get?model.get('_items'):model._items)||[])); } catch(e){}
  try { attrs = JSON.parse(JSON.stringify(model.attributes||{})); } catch(e){}
  const cats = deepQ('button.objectMatching-category-item', m).map(b => ({
     data_id:b.getAttribute('data-id'), itemindex:b.getAttribute('data-itemindex'),
     cur:b.getAttribute('data-currentquestionindex'), cls:cls(b),
     text:dtext(deepQ('.category-item-text',b)[0]||b),
     attrs:Array.from(b.attributes).map(a=>a.name+'='+a.value).join(' ')}));
  const opts = deepQ('button.objectMatching-option-item', m).map(b => ({
     data_id:b.getAttribute('data-id'), itemindex:b.getAttribute('data-itemindex'),
     cur:b.getAttribute('data-currentoptionindex'), cls:cls(b),
     text:dtext(deepQ('.category-item-text',b)[0]||b),
     attrs:Array.from(b.attributes).map(a=>a.name+'='+a.value).join(' ')}));
  const lines = deepQ('[class*="line"], line, path', m).map(l => ({tag:l.tagName, cls:cls(l),
     attrs:Array.from(l.attributes).map(a=>a.name+'='+a.value).join(' ').slice(0,160)})).slice(0,20);
  return {question: dtext(deepQ('.component__body-inner',m)[0]).slice(0,120),
          model_items: items, model_attr_keys: Object.keys(attrs),
          model_userAnswer: attrs._userAnswer!==undefined?attrs._userAnswer:(attrs.userAnswer!==undefined?attrs.userAnswer:null),
          categories: cats, options: opts, lines};
}));
"""
cfg = load_config(course=None); log = get_logger("probe_om", cfg_path(cfg,"logs"))
with launch(cfg) as sb:
    open_course(sb, cfg)
    input(">>> Navigate to the object-matching question, connect ALL your pairs, then press Enter here...")
    cf.enter(sb)
    out = json.loads(sb.execute_script(JS))
    p = cfg_path(cfg,"recon")/"objmatch_answered.json"
    p.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("dumped %d object-matching component(s) -> %s", len(out), p)
    for om in out:
        log.info("Q: %s", om["question"])
        log.info("  cats: %s", [(c["data_id"], c["itemindex"], c["text"][:24], c["cls"]) for c in om["categories"]])
        log.info("  opts: %s", [(o["data_id"], o["itemindex"], o["text"][:24], o["cls"]) for o in om["options"]])
        log.info("  model_attr_keys: %s", om["model_attr_keys"])
        log.info("  userAnswer: %s", om["model_userAnswer"])
