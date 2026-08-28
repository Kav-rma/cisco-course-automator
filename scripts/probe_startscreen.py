"""Probe the CCNA 'secure one question' check (adaptive-start-screen-view + mcq-view):
Start -> dump question/buttons -> answer Q1 from the model -> submit -> dump navigation -> step once more.
Run: NETACAD_COURSE=ccna-srwe .venv\\Scripts\\python.exe scripts\\probe_startscreen.py 1.5.11
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS_STATE = cf.JS_DEEP + r"""
// everything clickable around the secure quiz: start-screen controls, mcq buttons, nav buttons, results
const pick = (sel) => deepQ(sel).map(b => ({tag: b.tagName.toLowerCase(), cls: cls(b).slice(0, 90), text: dtext(b).slice(0, 40),
   role: b.getAttribute('role'), aria: b.getAttribute('aria-label'), disabled: !!b.disabled,
   visible: !!(b.offsetWidth || b.offsetHeight || b.getClientRects().length)}));
const ss = deepQ('adaptive-start-screen-view')[0];
const ssText = ss ? dtext(ss).slice(0, 300) : null;
return JSON.stringify({
  start_controls: pick('.start-button, [class*="start-button"], [class*="retake"], [class*="results"] button, [class*="secure"] [role=button]'),
  buttons: pick('button').filter(b => b.visible).slice(0, 40),
  role_buttons: pick('[role=button]').filter(b => b.visible && !/accordion|tab/.test(b.cls)).slice(0, 40),
  secure_text: ssText,
  mcq_count: deepQ('mcq-view').length,
  question_counter: deepQ('[class*="question-count"], [class*="questionCount"], [class*="counter"], [class*="progress-text"]').map(dtext).filter(Boolean).slice(0, 6),
});
"""
JS_CLICK_START = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ('.start-button, [class*="start-button"][role=button]', el)[0]; if (!b) return 'no-start';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'clicked ' + dtext(b);
"""
JS_CLICK_TEXT = cf.JS_DEEP + r"""
const rx = new RegExp(arguments[0], 'i');
const cands = deepQ('button, [role=button]').filter(b => rx.test(dtext(b)) || rx.test(b.getAttribute('aria-label') || ''));
const b = cands.find(x => !!(x.offsetWidth || x.offsetHeight)); if (!b) return 'no-match';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'clicked ' + dtext(b).slice(0, 30);
"""
item_id = sys.argv[1] if len(sys.argv) > 1 else "1.5.11"
cfg = load_config(interactive=False); log = get_logger("probe_ss", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
out = {"item": item_id, "steps": []}
def snap(label):
    st = json.loads(sb.execute_script(JS_STATE)); qs = qx.extract(sb)
    rec = {"label": label, "state": st, "mcq": [{k: q[k] for k in ("modelid", "heading", "question", "type", "correct_indices", "correct_texts", "complete", "submitted", "submit_enabled")} | {"options": [(o["index"], o["text"][:30], o["checked"], o["correct_mark"]) for o in q["options"]]} for q in qs]}
    out["steps"].append(rec)
    print(f"== {label}: mcq={len(qs)} start_controls={[ (c['text'], c['visible']) for c in st['start_controls']]} counter={st['question_counter']}")
    print("   buttons:", [(b["text"], b["disabled"]) for b in st["buttons"]][:12], "| role_buttons:", [(b["text"]) for b in st["role_buttons"]][:10])
    for q in rec["mcq"]: print("   Q:", q["heading"], "|", q["question"][:70], "| correct", q["correct_indices"], "| submitted", q["submitted"], "| complete", q["complete"])
    return st, qs
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    mid = next(c["modelid"] for c in det.components if c["tag"] == "adaptive-start-screen-view")
    snap("before start")
    print("START ->", sb.execute_script(JS_CLICK_START, mid)); time.sleep(2.0)
    st, qs = snap("after start")
    # answer the first visible question from the model
    q = next((x for x in qs if not x["submitted"] and x["options"]), None)
    if q:
        for idx in q["correct_indices"]:
            print("select", idx, "->", qx.select_option(sb, q["modelid"], idx, 10))
        print("submit ->", qx.submit(sb, q["modelid"], 10)); time.sleep(1.5)
        st, qs = snap("after submit Q1")
        print("NEXT ->", sb.execute_script(JS_CLICK_TEXT, "^(next|continue|next question)")); time.sleep(2.0)
        snap("after next")
    (cfg_path(cfg, "recon") / f"startscreen_{item_id}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("saved")
