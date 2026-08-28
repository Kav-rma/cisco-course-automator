"""
Stage 2 - Content / Question Collector.

Visits every outline item that is (or turns out to be) a knowledge check, extracts each question with its
options and the correct option(s) (from the component's own model), and stores data/question_bank.json.
Nothing is selected or submitted. Graded quizzes/exams are skipped (ASSESSMENT), labs are skipped.

Run:  .venv\Scripts\python.exe scripts\02_content_collector.py [--modules 3,4] [--resume] [--all-items]
  --all-items : visit every item (not just title-inferred knowledge checks) to catch un-titled question blocks.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import content_frame as cf  # noqa: E402
from core import question_extractor as qx  # noqa: E402
from core.browser import launch, open_course, save_diagnostics  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.page_detector import PageType, detect  # noqa: E402
from phase3_detect import goto_item  # noqa: E402


def iter_items(structure, modules: set[int] | None):
    for node in structure["nodes"]:
        if modules and node.get("module_number") not in modules:
            continue
        for sec in node["sections"]:
            for it in sec["items"]:
                yield node, sec, it


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modules", default=None, help="comma list of module numbers (default: all)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--all-items", action="store_true")
    ap.add_argument("--course", default=None, help="course key from config/courses.json (default: ask)")
    args = ap.parse_args()
    modules = {int(x) for x in args.modules.split(",")} if args.modules else None

    cfg = load_config(course=args.course)
    log = get_logger("collector", cfg_path(cfg, "logs"), cfg.get("debug", True))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    out = cfg_path(cfg, "data") / "question_bank.json"
    bank = {"course": structure["course"]["title"], "collected_at": None, "questions": [], "visited_items": []}
    if args.resume and out.exists():
        bank = json.loads(out.read_text(encoding="utf-8"))
    visited = set(bank.get("visited_items", []))

    def save():
        bank["collected_at"] = datetime.now().isoformat(timespec="seconds")
        bank["visited_items"] = sorted(visited)
        tmp = out.with_suffix(".tmp"); tmp.write_text(json.dumps(bank, indent=1, ensure_ascii=False), encoding="utf-8"); tmp.replace(out)

    started = time.time()
    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            for node, sec, it in iter_items(structure, modules):
                if not it.get("id") or it["id"] in visited:
                    continue
                if not args.all_items and it["inferred_type"] != "knowledge_check":
                    continue
                if sec.get("leaf"):
                    continue
                try:
                    goto_item(sb, cfg, node, sec, it)
                    det = detect(cf.read_page_model(sb), it, sec)
                except Exception as e:  # transient nav problems: log, diagnostics, continue
                    log.warning("could not open %s: %s", it["id"], str(e).splitlines()[0][:160])
                    save_diagnostics(sb, cfg, f"collector_nav_{it['id']}")
                    continue
                if det.page_type not in (PageType.KNOWLEDGE_CHECK,):
                    visited.add(it["id"]); save()
                    if det.page_type == PageType.ASSESSMENT:
                        log.info("%s is a graded assessment - skipped", it["id"])
                    continue
                qs = qx.extract(sb, qx.question_ids(det))
                for c in qx.unsupported_question_components(det):
                    log.warning("%s has an unsupported question type %s (not collected)", it["id"], c["tag"])
                # drop any previous entries for this item (re-collection), then append fresh ones
                bank["questions"] = [q for q in bank["questions"] if q["lesson_id"] != it["id"]]
                for n, q in enumerate(qs, 1):
                    entry = {
                        "question_id": f"{it['id']}-q{n}", "module_id": str(node.get("module_number")), "module_title": node["title"],
                        "section_id": sec["id"], "section_title": sec["title"], "lesson_id": it["id"], "lesson_title": it["title"],
                        "question_number": n, "heading": q["heading"], "question": q["question"], "type": q["type"],
                        "options": [{"index": o["index"], "text": o["text"]} for o in sorted(q["options"], key=lambda o: o["index"])],
                        "correct_indices": q["correct_indices"], "correct_texts": q["correct_texts"],
                        "mcq_modelid": q["modelid"], "scope_modelid": det.scope_modelid, "page_location_id": None,
                        "feedback": q["feedback"], "complete_when_collected": q["complete"],
                        "collected_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    if not q["question"] or not q["options"] or not q["correct_indices"]:
                        log.warning("%s q%d incomplete extraction: question=%r options=%d correct=%s", it["id"], n, q["question"][:40], len(q["options"]), q["correct_indices"])
                        save_diagnostics(sb, cfg, f"collector_extract_{it['id']}")
                    bank["questions"].append(entry)
                for n, q in enumerate(qx.extract_matching(sb, qx.matching_ids(det)), len(qs) + 1):
                    bank["questions"].append({
                        "question_id": f"{it['id']}-q{n}", "module_id": str(node.get("module_number")), "module_title": node["title"],
                        "section_id": sec["id"], "section_title": sec["title"], "lesson_id": it["id"], "lesson_title": it["title"],
                        "question_number": n, "heading": q["heading"], "question": q["question"], "type": "matching",
                        "options": [{"index": i["index"], "text": i["text"]} for i in q["items"]],
                        "items": [{"index": i["index"], "text": i["text"], "options": [o["text"] for o in i["options"]],
                                   "correct_text": next((o["text"] for o in i["options"] if o["correct"]), None)} for i in q["items"]],
                        "correct_indices": [], "correct_texts": [], "mcq_modelid": q["modelid"], "scope_modelid": det.scope_modelid,
                        "complete_when_collected": q["complete"], "collected_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    qs.append(q)
                visited.add(it["id"]); save()
                log.info("%s %s -> %d question(s)", it["id"], it["title"][:50], len(qs))
        except Exception as e:
            log.exception("collector failed: %s", e)
            save_diagnostics(sb, cfg, "collector_error")
            save()
            return 1
    save()
    log.info("Question bank: %d questions from %d items in %.0fs -> %s", len(bank["questions"]), len(visited), time.time() - started, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
