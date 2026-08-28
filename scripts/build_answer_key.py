"""
Build a study answer key by joining YOUR solved answers (quiz_answers_user.json) with the extracted
questions (quiz_questions.json). Keyed by quiz item + normalized question TEXT (so it survives the quiz's
shuffling / renumbering). Resolves answer letters (A/B/C..) to the actual option text. Output:
  data/<course>/quiz_answer_key.json
This is only organizing your own answers for the assist tool - it does not answer or submit anything.

Run: .venv\\Scripts\\python.exe scripts\\build_answer_key.py --course networking-essentials
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import load_config, path as cfg_path  # noqa: E402
from core.matcher import normalize  # noqa: E402

LETTERS = "ABCDEFGHIJKLMNOP"


def parse_letters(ans: str):
    """'A, D, E' -> ['A','D','E']; 'B (UTP)' -> ['B']; free text -> []."""
    letters = re.findall(r"\b([A-P])\b", ans.strip())
    # only treat as letters if the answer is basically a list of single letters (avoid grabbing letters from prose)
    core = re.sub(r"[^A-Za-z]", "", re.split(r"[(\[]", ans)[0])
    if letters and len(core) <= len(letters) + 1:
        return letters
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default=None)
    ap.add_argument("--exams", action="store_true",
                    help="build the CHECKPOINT EXAM key instead (exam_questions.json + exam_answers_user.json -> exam_answer_key.json)")
    args = ap.parse_args()
    cfg = load_config(course=args.course)
    ddir = cfg_path(cfg, "data")
    if args.exams:
        q_file, a_file, out_name, item_re, list_key = ("exam_questions.json", "exam_answers_user.json",
                                                       "exam_answer_key.json", r"(exam-\d+)", "exams")
    else:
        q_file, a_file, out_name, item_re, list_key = ("quiz_questions.json", "quiz_answers_user.json",
                                                       "quiz_answer_key.json", r"(\d+(?:\.\d+)+)", "quizzes")
    questions = json.loads((ddir / q_file).read_text(encoding="utf-8"))
    answers = json.loads((ddir / a_file).read_text(encoding="utf-8"))

    # index user answers by item id (parse "QUIZ 1.4.3 — ..." -> 1.4.3 / "EXAM exam-2 — ..." -> exam-2)
    ans_by_item = {}
    for title, qa in answers.items():
        m = re.search(item_re, title)
        if m:
            ans_by_item[m.group(1)] = {k: v for k, v in qa.items() if k.lower().startswith("q") and k.lower() != "question"}

    key = {"course": cfg["course"]["name"], "quizzes": []}
    stats = {"quizzes": 0, "questions": 0, "resolved": 0, "free_text": 0, "unresolved": []}
    for quiz in questions[list_key]:
        item = quiz["item"]
        ua = ans_by_item.get(item, {})
        qentry = {"item": item, "title": quiz["title"], "questions": []}
        for q in quiz["questions"]:
            raw = ua.get(f"Q{q['n']}")
            rec = {"n": q["n"], "question": q["question"], "question_norm": normalize(q["question"]),
                   "type": q["type"], "raw_answer": raw}
            stats["questions"] += 1
            if raw is None or raw.strip().upper() in ("NOT FOUND", ""):
                rec["status"] = "missing"
                stats["unresolved"].append(f"{item} Q{q['n']}")
            elif q["type"] in ("single", "multiple", "mcq"):
                opts = q.get("options", [])
                letters = parse_letters(raw)
                if letters:
                    idxs = [LETTERS.index(x) for x in letters if LETTERS.index(x) < len(opts)]
                    rec["answer_indices"] = idxs
                    rec["answer_texts"] = [opts[i] for i in idxs]
                    rec["status"] = "resolved" if idxs else "unresolved"
                    if idxs:
                        stats["resolved"] += 1
                    else:
                        stats["unresolved"].append(f"{item} Q{q['n']}")
                else:
                    rec["status"] = "free_text"; stats["free_text"] += 1  # e.g. "(see DNS exhibit)"
                    stats["unresolved"].append(f"{item} Q{q['n']}")
            else:  # matching / object-matching: keep the raw pairing text for display
                rec["status"] = "match_free_text"; stats["free_text"] += 1
            qentry["questions"].append(rec)
        key["quizzes"].append(qentry)
        stats["quizzes"] += 1

    out = ddir / out_name
    out.write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}")
    print(f"quizzes={stats['quizzes']} questions={stats['questions']} resolved(MCQ)={stats['resolved']} "
          f"free_text/match={stats['free_text']} needs_your_eye={len(stats['unresolved'])}")
    if stats["unresolved"]:
        print("  review these (missing/free-text/exhibit answers):", ", ".join(stats["unresolved"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
