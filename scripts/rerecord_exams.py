"""
Auto-fix checkpoint answer recordings: for each exam whose matching questions were duplicated (the old reader
kept re-reading the first matching view), navigate to each question on the COMPLETED exam and read YOUR answer
for THAT question (viewport-largest component). Reads only your selections; never selects or submits.
Overwrites into exam_answer_key.json (empty reads are skipped, so good answers are never wiped).

Run: .venv\Scripts\python.exe scripts\rerecord_exams.py --course networking-essentials [--exam exam-3,exam-5] [--all]
"""
import json, sys, time, collections
from pathlib import Path
SCR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCR.parent)); sys.path.insert(0, str(SCR))
from core import content_frame as cf
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from extract_exams import goto_exam
from extract_quizzes import JS_GOTO_Q
from record_exam_answers import Store, _rec_from_component

# Read the question currently displayed (largest area in the viewport) with YOUR selections; full mcq text cleanup.
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
  const opts=deepQ('.mcq__item',m).map(o=>{const t=deepQ('.mcq__item-text-inner',o)[0];let text=t?dtext(t):'';for(const sr of (t?deepQ('.screenReader-position-text',t):[]))text=text.replace(clean(sr.textContent),'').trim();return {text,checked:o.getAttribute('aria-checked')==='true',multiple:o.getAttribute('role')==='checkbox'};});
  return JSON.stringify({type:'mcq',question,options:opts});
}
if(tag==='matching-view'){
  const dds=deepQ('matching-dropdown-view',m).map(d=>({title:dtext(deepQ('.matching__item-title .matching__item-title_inner',d)[0]||deepQ('.matching__item-title',d)[0]),selected:dtext(deepQ('.js-dropdown-inner',d)[0]),options:deepQ('li.js-dropdown-list-item',d).map(li=>{const inner=deepQ('.js-dropdown-list-item-inner',li)[0]||li;let text=dtext(inner);for(const sr of deepQ('.sr-only',inner))text=text.replace(dtext(sr),'');return{text:text.trim(),selected:li.getAttribute('aria-selected')==='true'};})}));
  return JSON.stringify({type:'matching',question,dropdowns:dds});
}
return JSON.stringify({type:'object-matching',question,cats:deepQ('button.objectMatching-category-item',m).map(b=>dtext(b)),opts:deepQ('button.objectMatching-option-item',m).map(b=>dtext(b))});
"""
JS_STRIP_MAX = cf.JS_DEEP + r"""
let mx=0; for(const b of deepQ('button.block-button')){const m=dtext(b).match(/(\d+)/); if(m)mx=Math.max(mx,Number(m[1]));} return mx;
"""


def _pop(a, f):
    if f in a:
        i = a.index(f); v = a[i + 1]; del a[i:i + 2]; return v
    return None


def exams_with_dupe_matching(key):
    out = []
    for q in key["quizzes"]:
        matchers = [qq for qq in q["questions"] if qq.get("type") in ("matching", "object-matching") or qq.get("pairs")]
        texts = collections.Counter(qq.get("question", "") for qq in matchers)
        if any(c > 1 for c in texts.values()):
            out.append(q["item"])
    return out


def rerecord(sb, cfg, log, store, node, item):
    goto_exam(sb, cfg, node)
    time.sleep(1.5)
    cf.enter(sb)
    total = sb.execute_script(JS_STRIP_MAX) or 40
    got = matched = 0
    for n in range(1, int(total) + 1):
        cf.enter(sb)
        if sb.execute_script(JS_GOTO_Q, n) == "no-strip":
            continue
        time.sleep(0.45)
        raw = sb.execute_script(JS_READ_DISPLAYED)
        if not raw:
            continue
        comp = json.loads(raw)
        rec = _rec_from_component(comp, n)
        got += 1
        if store.record(item, node["title"], rec):
            matched += 1
        if comp.get("type") in ("matching", "object-matching"):
            log.info("  Q%d [%s] %s", n, comp["type"], (comp.get("question") or "")[:60])
    store.save()
    log.info("%s: read %d questions, %d recorded/updated", item, got, matched)


def main():
    a = sys.argv[1:]
    course = _pop(a, "--course")
    exam_arg = _pop(a, "--exam")
    do_all = "--all" in a
    cfg = load_config(course=course)
    log = get_logger("rerecord", cfg_path(cfg, "logs"), cfg.get("debug", True))
    ddir = cfg_path(cfg, "data")
    structure = json.loads((ddir / "course_structure.json").read_text(encoding="utf-8"))
    key = json.loads((ddir / "exam_answer_key.json").read_text(encoding="utf-8"))
    store = Store(ddir / "exam_answer_key.json", cfg["course"]["name"])
    nodes = [n for n in structure["nodes"] if n.get("kind") == "checkpoint_exam"]
    node_of = {f"exam-{i}": n for i, n in enumerate(nodes, 1)}

    if exam_arg:
        targets = [e.strip() for e in exam_arg.split(",")]
    elif do_all:
        targets = list(node_of)
    else:
        targets = exams_with_dupe_matching(key)
    log.info("Targets (%d): %s", len(targets), ", ".join(targets) or "(none)")
    if not targets:
        log.info("No exams with duplicated matching - nothing to do.")
        return 0
    with launch(cfg) as sb:
        open_course(sb, cfg)
        for item in targets:
            node = node_of.get(item)
            if not node:
                log.warning("%s not found in structure", item); continue
            try:
                rerecord(sb, cfg, log, store, node, item)
            except Exception as e:  # noqa: BLE001
                log.warning("error on %s: %s", item, str(e).splitlines()[0][:160])
                try:
                    open_course(sb, cfg)
                except Exception:
                    break
    log.info("Done. Saved to %s", ddir / "exam_answer_key.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
