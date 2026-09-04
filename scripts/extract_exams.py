"""
Extract CHECKPOINT EXAM questions + options for study (read-only: never selects an answer, never submits).

Checkpoint exams are top-level graded nodes in the outline (no numeric item id), so they are keyed here as
exam-1, exam-2, ... in course order. For each exam it opens the node, presses Start, walks every question with
"Skip Question" (recording text + options, answering nothing), then abandons the attempt. Output:
  data/<course>/exam_questions.json
  data/<course>/exam_answersheet.txt   (fill in the Answer lines and send it back)

NOTE: opening an exam starts an attempt on your account; the attempt is abandoned unanswered (NetAcad lets you
retake checkpoint exams). Already-completed exams are skipped unless --force.

Run:  .venv\\Scripts\\python.exe scripts\\extract_exams.py [--course KEY] [--only exam-3] [--force]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent))
sys.path.insert(0, str(SCRIPTS))

from core import content_frame as cf  # noqa: E402
from core import outline as ol  # noqa: E402
from core import question_extractor as qx  # noqa: E402
from core.browser import launch, open_course, save_diagnostics, wait_until  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.matcher import normalize  # noqa: E402
from extract_quizzes import JS_COOKIES, JS_GOTO_Q, JS_SKIP, _read_current_question  # noqa: E402

LETTERS = "ABCDEFGHIJKLMNOP"


def merge_entry(old: dict, new: dict) -> dict:
    """Union of questions by normalized text (exams shuffle numbering between attempts, so text is the key).
    Existing questions keep their numbers; unseen ones are appended after them."""
    seen = {normalize(q["question"]): q for q in old.get("questions", [])}
    nxt = max((q["n"] for q in old.get("questions", [])), default=0) + 1
    added = 0
    for q in new["questions"]:
        if normalize(q["question"]) not in seen:
            q = dict(q, n=nxt)
            seen[normalize(q["question"])] = q
            nxt += 1
            added += 1
    out = dict(old)
    out["questions"] = sorted(seen.values(), key=lambda x: x["n"])
    out["total_reported"] = max(old.get("total_reported") or 0, new.get("total_reported") or 0) or None
    out["merged_added"] = added
    return out


def goto_exam(sb, cfg, node) -> None:
    """Open a checkpoint-exam node: expand the node, click its (single, graded) section row."""
    t = cfg["timeouts"]
    cf.leave(sb)
    if not ol.ensure_node_expanded(sb, node["uuid"], t["element"]):
        raise RuntimeError(f"could not expand exam node {node['title']}")
    # graded leaf sections have no aria-expanded; clicking the row navigates the content frame to the exam
    sb.execute_script(ol.JS_CLICK_SECTION, node["uuid"], 0)
    cf.enter(sb)
    cf.wait_page_ready(sb, t["page_load"])


def press_start(sb, cfg):
    if not qx.secure_state(sb).get("start_visible"):
        return
    qx.secure_start(sb, "click")
    if wait_until(sb, lambda s: bool(qx.secure_state(s)["mcq_ids"]), 6, poll=0.4, what="exam started"):
        return
    from handlers.activity_handler import _frame_offset, _cdp_mouse

    class _C:  # noqa: N801
        def __init__(self, sb): self.sb = sb
    qx.secure_start(sb, "rect"); time.sleep(0.3); r = qx.secure_start(sb, "rect")
    if r:
        fx, fy = _frame_offset(_C(sb))
        _cdp_mouse(_C(sb), "mouseMoved", fx + r["x"], fy + r["y"], 0)
        _cdp_mouse(_C(sb), "mousePressed", fx + r["x"], fy + r["y"], 1)
        _cdp_mouse(_C(sb), "mouseReleased", fx + r["x"], fy + r["y"], 0)
    wait_until(sb, lambda s: bool(qx.secure_state(s)["mcq_ids"]), cfg["timeouts"]["element"],
               poll=0.4, what="exam started (cdp)")


def read_here(sb):
    """Read the current question, retrying once if the view is still swapping in."""
    rec = _read_current_question(sb, qx.secure_state(sb))
    if not rec or not rec.get("question"):
        time.sleep(0.6)
        rec = _read_current_question(sb, qx.secure_state(sb))
    return rec


def walk_by_strip(sb, log, total):
    """Click each question in the numbered strip (Q1..Qtotal) and record it. Robust to the exam
    reopening mid-way on a re-attempt (skip-walking would miss everything before the resume point).
    Returns (seen, total) — or None if there is no question strip (caller falls back to skip-walking)."""
    # The question strip (Q1..QN buttons) can render a beat after the first question, especially on the
    # first exam of a run. Wait for it before deciding there is no strip.
    if not wait_until(sb, lambda s: s.execute_script(JS_GOTO_Q, 1) == "strip", 10, poll=0.5, what="question strip"):
        return None
    if not total:  # counter often isn't rendered until a question is on screen; re-read it now
        wait_until(sb, lambda s: bool(qx.secure_state(s).get("counter")), 4, poll=0.3, what="counter")
        m = re.search(r"(\d+)\s+of\s+(\d+)", qx.secure_state(sb).get("counter") or "")
        total = int(m.group(2)) if m else total
    seen = {}
    for n in range(1, (total or 40) + 1):
        if sb.execute_script(JS_GOTO_Q, n) == "no-strip":
            continue  # that number isn't in the strip; keep going
        wait_until(sb, lambda s: qx.secure_state(s).get("active_q") == n, 4, poll=0.3, what=f"Q{n} active")
        time.sleep(0.4)
        rec = read_here(sb)
        if rec and rec["question"] and rec["question"] not in seen:
            rec["n"] = n
            seen[rec["question"]] = rec
            log.info("    Q%s [%s]: %s", n, rec["type"], rec["question"][:75])
    return seen, total


def walk_by_skip(sb, log, total):
    """Fallback: page forward with Skip Question (only reaches questions from the current point on)."""
    seen, stalls = {}, 0
    for _ in range(1, (total or 40) + 5):
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
        if (total and len(seen) >= total) or stalls >= 3:
            break
        if sb.execute_script(JS_SKIP) == "no-advance":
            break
        time.sleep(0.7)
    return seen


def extract_exam(sb, cfg, log, node, key) -> dict:
    goto_exam(sb, cfg, node)
    entry = {"item": key, "uuid": node["uuid"], "title": node["title"], "questions": []}
    press_start(sb, cfg)
    sb.execute_script(JS_COOKIES)
    st = qx.secure_state(sb)
    total = None
    if st.get("counter"):
        m = re.search(r"(\d+)\s+of\s+(\d+)", st["counter"])
        total = int(m.group(2)) if m else None
    result = walk_by_strip(sb, log, total)
    if result is None:
        log.info("    (no question strip; walking with Skip from the current question)")
        seen = walk_by_skip(sb, log, total)
    else:
        seen, total = result
    entry["questions"] = sorted(seen.values(), key=lambda x: x["n"])
    entry["total_reported"] = total
    return entry


def write_answersheet(cfg, bank):
    exams = sorted(bank["exams"], key=lambda e: int(e["item"].split("-")[1]))
    out_lines = [f"CHECKPOINT EXAM ANSWER SHEET — {bank.get('course', '')}",
                 f"{len(exams)} exams, {sum(len(e['questions']) for e in exams)} questions",
                 "Fill in each 'Answer:' line (letter(s) for MCQ; the pairing for match questions), then send it back.",
                 "=" * 90, ""]
    for e in exams:
        out_lines.append(f"### EXAM {e['item']} — {e['title']}  ({len(e['questions'])} questions)")
        out_lines.append("")
        for qq in e["questions"]:
            out_lines.append(f"Q{qq.get('n', '?')}. {qq['question']}")
            typ = qq.get("type")
            if typ in ("single", "multiple", "mcq"):
                for i, opt in enumerate(qq.get("options", [])):
                    out_lines.append(f"    {LETTERS[i]}) {opt}")
                hint = "  (choose all that apply)" if typ == "multiple" else ""
                out_lines.append(f"    Answer: ____{hint}")
            elif typ == "matching":
                out_lines.append("    (match each prompt to one option)")
                for it in qq.get("items", []):
                    opts = " / ".join(it.get("options", []))
                    out_lines.append(f"    - {it['prompt']}   ->  ____    [options: {opts}]")
            elif typ == "object-matching":
                out_lines.append("    (match each prompt to one option)")
                out_lines.append(f"    options: {', '.join(qq.get('options', []))}")
                for pr in qq.get("prompts", []):
                    out_lines.append(f"    - {pr}   ->  ____")
            else:
                out_lines.append(f"    [unrecognised question type: {typ}]  Answer: ____")
            out_lines.append("")
        out_lines.append("-" * 90)
        out_lines.append("")
    out = cfg_path(cfg, "data") / "exam_answersheet.txt"
    out.write_text("\n".join(out_lines), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default=None)
    ap.add_argument("--only", default=None, help="one exam key, e.g. exam-3")
    ap.add_argument("--force", action="store_true", help="re-extract even completed / already-extracted exams")
    ap.add_argument("--fill", action="store_true",
                    help="re-run only exams with missing questions and MERGE new ones in (numbering shuffles per attempt)")
    args = ap.parse_args()

    cfg = load_config(course=args.course)
    log = get_logger("extract_exams", cfg_path(cfg, "logs"), cfg.get("debug", True))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    out = cfg_path(cfg, "data") / "exam_questions.json"
    bank = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"course": cfg["course"]["name"], "exams": []}
    done = {e["item"] for e in bank["exams"] if e.get("questions")}

    exams = [(f"exam-{i}", node) for i, node in
             enumerate((n for n in structure["nodes"] if n.get("kind") == "checkpoint_exam"), 1)]
    by_item = {e["item"]: e for e in bank["exams"]}
    if args.fill:
        def gappy(k):
            e = by_item.get(k)
            return bool(e) and len(e["questions"]) < (e.get("total_reported") or 0)
        targets = [(k, n) for k, n in exams if (args.only is None or k == args.only) and gappy(k)]
    else:
        targets = [(k, n) for k, n in exams
                   if (args.only is None or k == args.only) and (args.force or k not in done)]
    log.info("%d checkpoint exam(s) to extract (of %d in the course)", len(targets), len(exams))
    if not targets:
        write_answersheet(cfg, bank)
        return 0

    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            live = {n["uuid"]: n for n in ol.read_nodes(sb)["nodes"]}
            for key, node in targets:
                status = (live.get(node["uuid"], {}).get("sections") or [{}])[0].get("status")
                if status == "completed" and not (args.force or args.fill):
                    log.info("%s: %s -> already completed on this account, skipping", key, node["title"])
                    continue
                log.info("%s: %s (status: %s)", key, node["title"], status)
                try:
                    entry = extract_exam(sb, cfg, log, node, key)
                except Exception as e:  # noqa: BLE001
                    log.warning("  failed on %s: %s", key, str(e).splitlines()[0][:160])
                    save_diagnostics(sb, cfg, f"exam_extract_{key}")
                    open_course(sb, cfg)
                    continue
                if args.fill and key in by_item:
                    entry = merge_entry(by_item[key], entry)
                    log.info("  -> merged: +%d new, %d total (reported %s)",
                             entry.pop("merged_added", 0), len(entry["questions"]), entry.get("total_reported"))
                else:
                    log.info("  -> %d question(s) saved", len(entry["questions"]))
                by_item[key] = entry
                bank["exams"] = [e for e in bank["exams"] if e["item"] != key] + [entry]
                bank["extracted_at"] = datetime.now().isoformat(timespec="seconds")
                out.write_text(json.dumps(bank, indent=1, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log.exception("extract_exams failed: %s", e)
            save_diagnostics(sb, cfg, "extract_exams_error")
            return 1
    sheet = write_answersheet(cfg, bank)
    log.info("Done. %d exams in %s ; answer sheet: %s", len(bank["exams"]), out, sheet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
