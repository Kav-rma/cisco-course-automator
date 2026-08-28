"""Turn quiz_questions.json into a human answer sheet (data/<course>/quiz_answersheet.txt) for you to fill in.
Run: .venv\\Scripts\\python.exe scripts\\format_quizzes.py --course networking-essentials
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import load_config, path as cfg_path  # noqa: E402

LETTERS = "ABCDEFGHIJKLMNOP"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default=None)
    args = ap.parse_args()
    cfg = load_config(course=args.course)
    data = json.loads((cfg_path(cfg, "data") / "quiz_questions.json").read_text(encoding="utf-8"))
    quizzes = sorted(data["quizzes"], key=lambda q: [int(p) for p in q["item"].split(".")])
    out_lines = [f"ANSWER SHEET — {data.get('course', '')}",
                 f"{len(quizzes)} module quizzes, {sum(len(q['questions']) for q in quizzes)} questions",
                 "Fill in each 'Answer:' line (letter(s) for MCQ; the pairing for match questions), then send it back.",
                 "=" * 90, ""]
    for q in quizzes:
        out_lines.append(f"### QUIZ {q['item']} — {q['title']}  ({len(q['questions'])} questions)")
        out_lines.append("")
        for qq in q["questions"]:
            n = qq.get("n", "?")
            typ = qq.get("type")
            out_lines.append(f"Q{n}. {qq['question']}")
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
    out = cfg_path(cfg, "data") / "quiz_answersheet.txt"
    out.write_text("\n".join(out_lines), encoding="utf-8")
    print("wrote", out, "(", len(out_lines), "lines )")
    return 0


if __name__ == "__main__":
    sys.exit(main())
