"""
Extract module-quiz QUESTIONS + OPTIONS for study (read-only: never selects an answer, never submits).

For each graded module quiz it opens the quiz, presses Start, walks every question recording the question text
and its options, then abandons the attempt (no answer chosen, no "Submit My Assessment"). Output:
  data/<course>/quiz_questions.json   (module_id, quiz item id, question_number, question, options[])
You solve them and give me the answers; I'll load them into an assist mode where YOU submit.

Run:  .venv\\Scripts\\python.exe scripts\\extract_quizzes.py --course networking-essentials [--modules 1,2] [--only 3.3.3]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content_frame as cf  # noqa: E402
from core import navigator as nav  # noqa: E402
from core import question_extractor as qx  # noqa: E402
from core.browser import launch, open_course, save_diagnostics, wait_until  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.page_detector import detect  # noqa: E402

# advance to the next question WITHOUT answering: prefer a question-strip button, else the "Skip Question" control.
JS_GOTO_Q = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const n = String(arguments[0]);
const strip = deepQ('button.block-button').filter(vis).find(b => (dtext(b).match(/Q?\s*(\d+)/) || [])[1] === n
   || new RegExp('Question\\s+' + n + '(\\D|$)').test(b.getAttribute('aria-label') || ''));
if (strip) { strip.scrollIntoView({block: 'center', behavior: 'instant'}); strip.click(); return 'strip'; }
return 'no-strip';
"""
JS_COOKIES = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const b = deepQ('#onetrust-reject-all-handler, #onetrust-accept-btn-handler, button.save-preference-btn-handler, [class*="reject"]').filter(vis)[0];
if (b) { b.click(); return 'dismissed'; } return 'none';
"""
JS_SKIP = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
// Checking "Skip Question" advances to the next question on its own (verified) - no Submit needed, no answer chosen.
const skip = deepQ('input#skip-question, input[aria-label="Skip Question" i]').filter(vis)[0];
if (skip) { skip.scrollIntoView({block: 'center', behavior: 'instant'}); skip.click(); return 'skipped'; }
const next = deepQ('button[aria-label*="next question" i], button.narrative__controls.next').filter(vis)[0];
if (next && !next.disabled) { next.click(); return 'next'; }
return 'no-advance';
"""


def extract_quiz(sb, cfg, log, node, sec, it) -> dict:
    t = cfg["timeouts"]
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    entry = {"item": it["id"], "module": node.get("module_number"), "title": it["title"],
             "type": det.page_type.value, "questions": []}
    if not any(c["tag"] == "adaptive-start-screen-view" for c in det.components):
        # non-secure quiz: questions may all be on the page at once
        qs = qx.extract(sb)
        for n, q in enumerate(qs, 1):
            entry["questions"].append({"n": n, "question": q["question"], "type": q["type"],
                                       "options": [o["text"] for o in sorted(q["options"], key=lambda o: o["index"])]})
        entry["note"] = "non-secure quiz (no start screen)"
        return entry
    # secure one-question quiz: press Start, then read + skip through each question
    if qx.secure_state(sb).get("start_visible"):
        qx.secure_start(sb, "click")
        if not wait_until(sb, lambda s: bool(qx.secure_state(s)["mcq_ids"]), 5, poll=0.4, what="quiz started"):
            from handlers.activity_handler import _frame_offset, _cdp_mouse

            class _C:  # noqa: N801
                def __init__(self, sb): self.sb = sb
            r = qx.secure_start(sb, "rect"); time.sleep(0.3); r = qx.secure_start(sb, "rect")
            if r:
                fx, fy = _frame_offset(_C(sb))
                _cdp_mouse(_C(sb), "mouseMoved", fx + r["x"], fy + r["y"], 0)
                _cdp_mouse(_C(sb), "mousePressed", fx + r["x"], fy + r["y"], 1)
                _cdp_mouse(_C(sb), "mouseReleased", fx + r["x"], fy + r["y"], 0)
            wait_until(sb, lambda s: bool(qx.secure_state(s)["mcq_ids"]), t["element"], poll=0.4, what="quiz started (cdp)")
    st = qx.secure_state(sb)
    import re
    total = None
    if st.get("counter"):
        m = re.search(r"(\d+)\s+of\s+(\d+)", st["counter"]); total = int(m.group(2)) if m else None
    sb.execute_script(JS_COOKIES)
    seen = {}
    stalls = 0
    for step in range(1, (total or 15) + 5):
        st = qx.secure_state(sb)
        n = st.get("active_q") or (len(seen) + 1)
        rec = _read_current_question(sb, st)
        if rec and rec["question"] and rec["question"] not in seen:
            rec["n"] = n
            seen[rec["question"]] = rec
            log.info("    Q%s [%s]: %s", n, rec["type"], rec["question"][:75])
            stalls = 0
        else:
            stalls += 1
        if total and len(seen) >= total:
            break
        if stalls >= 3:
            break
        r = sb.execute_script(JS_SKIP)
        if r == "no-advance":
            break
        time.sleep(0.7)
    entry["questions"] = sorted(seen.values(), key=lambda x: x["n"])
    entry["total_reported"] = total
    return entry


def _read_current_question(sb, st):
    """Return the currently visible secure-quiz question of whatever type (mcq / matching / object-matching)."""
    ids = st.get("mcq_ids") or []
    aid = st.get("active_id") or (ids[0] if ids else None)
    if aid:
        q = next((x for x in qx.extract(sb, [aid]) if x.get("options")), None)
        if q:
            return {"question": q["question"], "type": "mcq",
                    "options": [o["text"] for o in sorted(q["options"], key=lambda o: o["index"])]}
    m = next((x for x in qx.extract_matching(sb) if x.get("items")), None)
    if m:
        return {"question": m["question"], "type": "matching",
                "items": [{"prompt": i["text"], "options": [o["text"] for o in i["options"]]} for i in m["items"]]}
    o = next((x for x in qx.extract_object_matching(sb) if x.get("categories")), None)
    if o:
        return {"question": o.get("question") or "Match each item.", "type": "object-matching",
                "prompts": [c["text"] for c in o["categories"]], "options": [op["text"] for op in o["options"]]}
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default=None)
    ap.add_argument("--modules", default=None, help="comma list of module numbers")
    ap.add_argument("--only", default=None, help="a single item id, e.g. 3.3.3")
    args = ap.parse_args()
    modules = {int(x) for x in args.modules.split(",")} if args.modules else None

    cfg = load_config(course=args.course)
    log = get_logger("extract_quizzes", cfg_path(cfg, "logs"), cfg.get("debug", True))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    out = cfg_path(cfg, "data") / "quiz_questions.json"
    bank = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"course": cfg["course"]["name"], "quizzes": []}
    done = {q["item"] for q in bank["quizzes"]}

    # module quizzes = items titled "... Quiz" (inferred assessment) inside a non-graded module section
    targets = []
    for node in structure["nodes"]:
        if node.get("kind") != "module":
            continue
        if modules and node.get("module_number") not in modules:
            continue
        for sec in node["sections"]:
            if sec.get("leaf"):
                continue
            for it in sec["items"]:
                if not it.get("id"):
                    continue
                if args.only:
                    if it["id"] == args.only:
                        targets.append((node, sec, it))
                elif it.get("inferred_type") == "assessment" and it["id"] not in done:
                    targets.append((node, sec, it))

    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            for node, sec, it in targets:
                log.info("Quiz %s (module %s): %s", it["id"], node.get("module_number"), it["title"])
                try:
                    entry = extract_quiz(sb, cfg, log, node, sec, it)
                except Exception as e:  # noqa: BLE001
                    log.warning("  failed on %s: %s", it["id"], str(e).splitlines()[0][:160])
                    save_diagnostics(sb, cfg, f"quiz_extract_{it['id']}")
                    open_course(sb, cfg)
                    continue
                bank["quizzes"] = [q for q in bank["quizzes"] if q["item"] != it["id"]] + [entry]
                bank["extracted_at"] = datetime.now().isoformat(timespec="seconds")
                out.write_text(json.dumps(bank, indent=1, ensure_ascii=False), encoding="utf-8")
                log.info("  -> %d question(s) saved", len(entry["questions"]))
        except Exception as e:
            log.exception("extract_quizzes failed: %s", e)
            save_diagnostics(sb, cfg, "extract_quizzes_error")
            return 1
    log.info("Done. %d quizzes in %s", len(bank["quizzes"]), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
