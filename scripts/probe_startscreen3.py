"""Secure check: select -> Submit -> what happens next? (state + visible buttons + screenshot, then try Next)."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS_VIS = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const info = (b) => ({tag: b.tagName.toLowerCase(), cls: cls(b).slice(0, 70), text: dtext(b).slice(0, 40), aria: b.getAttribute('aria-label'), disabled: !!b.disabled});
return JSON.stringify({
  buttons: deepQ('button, input[type=button], [role=button]').filter(vis).filter(b => !/tabs__nav|accordion|blockContainer|mcq__item/.test(cls(b))).map(info).slice(0, 40),
  mcq_visible: deepQ('mcq-view').filter(vis).map(m => m.getAttribute('modelid').slice(0, 8)),
  counter: deepQ('.question-label, .question-label-container').filter(vis).map(dtext).slice(0, 2),
  feedback: deepQ('[class*="feedback"], [class*="correct"], [class*="incorrect"]').filter(vis).map(e => cls(e).slice(0, 50) + ' :: ' + dtext(e).slice(0, 60)).slice(0, 8),
});
"""
JS_CLICK_TEXT = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const rx = new RegExp(arguments[0], 'i');
const b = deepQ('button, [role=button], input[type=button]').filter(vis).find(x => rx.test(dtext(x)) || rx.test(x.getAttribute('aria-label') || '') || rx.test(x.value || ''));
if (!b) return 'no-match'; b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'clicked ' + (dtext(b) || b.value || b.getAttribute('aria-label')).slice(0, 30);
"""
item_id = sys.argv[1] if len(sys.argv) > 1 else "1.5.11"
cfg = load_config(interactive=False); log = get_logger("probe_ss3", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
rec = cfg_path(cfg, "recon")
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    st = qx.secure_state(sb); print("STATE0:", st)
    q = next(x for x in qx.extract(sb, st["mcq_ids"]) if x["options"])
    print("Q1:", q["question"][:70], "correct", q["correct_indices"], "submitted", q["submitted"])
    print("select ->", qx.select_option(sb, q["modelid"], q["correct_indices"][0], 10)); time.sleep(0.8)
    print("submit ->", qx.secure_submit(sb)); time.sleep(2.0)
    sb.save_screenshot(str(rec / f"ss5_{item_id}_after_submit.png"))
    v = json.loads(sb.execute_script(JS_VIS)); print("AFTER SUBMIT:", json.dumps(v)[:1500])
    print("STATE1:", qx.secure_state(sb))
    q2 = [x for x in qx.extract(sb, qx.secure_state(sb)["mcq_ids"])]
    print("mcq now:", [(x["modelid"][:8], x["question"][:40], x["submitted"], x["complete"]) for x in q2])
    print("NEXT ->", sb.execute_script(JS_CLICK_TEXT, "^(next|continue)")); time.sleep(2.0)
    v2 = json.loads(sb.execute_script(JS_VIS)); print("AFTER NEXT:", json.dumps(v2)[:1200])
    sb.save_screenshot(str(rec / f"ss5_{item_id}_after_next.png"))
    print("saved")
