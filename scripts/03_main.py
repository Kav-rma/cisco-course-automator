"""
Stage 3 - Main course automator.

  1. start the browser with the persistent (authenticated) profile and open the course
  2. read data/course_structure.json (static) and the live outline (progress)
  3. resume at the first item that is not completed (or at --start ITEM)
  4. for each item: navigate -> detect content type -> dispatch handler -> verify completion -> continue
  5. graded assessments, labs, unsupported questions and UNKNOWN pages are never forced: they are reported
     (and skipped or stopped at, per config.run.on_needs_user) - the student handles them

Run:  .venv\\Scripts\\python.exe scripts\\03_main.py [--start 3.1.1] [--modules 3,4] [--max-items 20] [--dry-run]
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
from core.browser import launch, open_course, save_diagnostics  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.page_detector import PageType, detect  # noqa: E402
from handlers import HandlerContext, dispatch  # noqa: E402


def plan(structure: dict, modules: set[int] | None, start: str | None):
    """Yield (node, section, item) in course order, honouring --modules and --start."""
    started = start is None
    for node in structure["nodes"]:
        if modules and node.get("module_number") not in modules:
            continue
        for sec in node["sections"]:
            for it in sec["items"]:
                if not started:
                    if it.get("id") == start:
                        started = True
                    else:
                        continue
                yield node, sec, it


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="item id to start from (default: first incomplete item)")
    ap.add_argument("--modules", default=None, help="comma list of module numbers to process")
    ap.add_argument("--max-items", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="detect only, run no handlers")
    ap.add_argument("--profile", default=None, help="account profile name -> uses Chrome profile dir profile_<name> (one per NetAcad/Google identity)")
    ap.add_argument("--fresh-login", action="store_true", help="(persistent mode) clear all cookies in the profile first, so Google/NetAcad ask you to sign in again")
    ap.add_argument("--keep-session", action="store_true", help="remember the login between runs in the project profile (default: fresh login every run, nothing saved)")
    ap.add_argument("--course", default=None, help="course key from config/courses.json (default: ask)")
    args = ap.parse_args()
    import os as _os
    if args.keep_session:
        _os.environ["NETACAD_SESSION_MODE"] = "persistent"
    if args.profile:
        _os.environ["NETACAD_PROFILE"] = args.profile
    if args.fresh_login:
        _os.environ["NETACAD_FRESH_LOGIN"] = "1"
    modules = {int(x) for x in args.modules.split(",")} if args.modules else None

    cfg = load_config(course=args.course)
    run_cfg = cfg.get("run", {})
    log = get_logger("main", cfg_path(cfg, "logs"), cfg.get("debug", True))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    report = {"started_at": datetime.now().isoformat(timespec="seconds"), "items": [], "needs_user": [], "failed": []}
    report_path = cfg_path(cfg, "data") / f"run_report_{datetime.now():%Y-%m-%d_%H%M%S}.json"

    def save_report():
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    processed = consecutive_failures = 0
    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            live = nav.live_node_progress(sb)
            for node, sec, it in plan(structure, modules, args.start):
                if args.max_items is not None and processed >= args.max_items:
                    log.info("Reached --max-items=%d", args.max_items)
                    break
                if node["kind"] in nav.GRADED_KINDS or sec.get("leaf"):
                    log.info("SKIP %s (%s) - graded/leaf, for the student", node["title"], node["kind"])
                    report["needs_user"].append({"item": it.get("id") or node["title"], "why": node["kind"]})
                    continue
                if not it.get("id"):
                    continue
                if live.get(node["uuid"], {}).get("percentage") == 100:
                    continue   # whole module already done (live outline)
                log.info("Module detected: %s | Section: %s | Lesson: %s %s", node.get("module_number"), sec["id"], it["id"], it["title"])
                status = nav.live_item_status(sb, node, sec, it)
                if status == "completed":
                    log.info("  already completed (outline)")
                    continue
                t0 = time.time()
                entry = {"item": it["id"], "title": it["title"], "module": node.get("module_number")}
                stop_run = False
                for attempt in (1, 2):
                    try:
                        nav.goto_item(sb, cfg, node, sec, it)
                        det = detect(cf.read_page_model(sb), it, sec)
                        entry["type"] = det.page_type.value
                        entry["components"] = [c["tag"] for c in det.components]
                        entry["unit_complete"] = det.complete
                        log.info("  Page type: %s (%s)", det.page_type.value, "; ".join(det.reasons[:1]))
                        if det.page_type == PageType.UNKNOWN:
                            save_diagnostics(sb, cfg, f"unknown_{it['id']}")
                            entry["status"] = "unknown"
                            report["failed"].append(entry)
                            if not args.dry_run:
                                consecutive_failures += 1
                        elif args.dry_run:
                            entry["status"] = "dry_run"
                        else:
                            res = dispatch(HandlerContext(sb, cfg, it, sec, det, log))
                            entry.update(status=res.status, notes=res.notes, seconds=round(time.time() - t0, 1))
                            if res.ok:
                                after = nav.wait_item_status(sb, node, sec, it, "completed", cfg["timeouts"]["element"])
                                entry["outline_after"] = after
                                log.info("  %s in %.1fs -> outline %s", res.status, time.time() - t0, after)
                                consecutive_failures = 0
                            elif res.status == "needs_user":
                                log.warning("  NEEDS YOU: %s", "; ".join(res.notes))
                                report["needs_user"].append({"item": it["id"], "why": "; ".join(res.notes)})
                                if run_cfg.get("on_needs_user", "skip") == "stop":
                                    log.warning("Stopping (run.on_needs_user=stop)")
                                    stop_run = True
                            else:
                                if attempt == 1:
                                    # A handler failure right after successes usually means the player went stale
                                    # (even plain text stops registering as viewed). Reload the course app and
                                    # retry this item once before counting it as failed.
                                    log.warning("  handler failed (%s); reloading the course and retrying %s", "; ".join(res.notes)[:120], it["id"])
                                    open_course(sb, cfg)
                                    continue
                                log.error("  FAILED: %s", "; ".join(res.notes))
                                save_diagnostics(sb, cfg, f"failed_{it['id']}")
                                report["failed"].append(entry)
                                consecutive_failures += 1
                        break   # item handled (one way or another)
                    except Exception as e:  # noqa: BLE001
                        # Typical cause: the NetAcad session expired mid-run (app bounced to login, iframe gone).
                        log.exception("  error on %s (attempt %d): %s", it["id"], attempt, str(e).splitlines()[0][:200])
                        save_diagnostics(sb, cfg, f"error_{it['id']}")
                        try:
                            open_course(sb, cfg)   # recover: reload the course app (re-login if needed)
                        except Exception:
                            log.error("could not recover the course page; stopping")
                            entry["status"] = "error"; entry["error"] = str(e)[:300]
                            report["failed"].append(entry)
                            stop_run = True
                            break
                        if attempt == 1:
                            log.warning("  recovered; retrying %s", it["id"])
                            continue
                        entry["status"] = "error"
                        entry["error"] = str(e)[:300]
                        report["failed"].append(entry)
                        consecutive_failures += 1
                if stop_run:
                    report["items"].append(entry)
                    break
                report["items"].append(entry)
                processed += 1
                save_report()
                if consecutive_failures >= int(run_cfg.get("max_consecutive_failures", 3)):
                    log.error("Stopping safely after %d consecutive failures", consecutive_failures)
                    break
        except Exception as e:
            log.exception("main failed: %s", e)
            save_diagnostics(sb, cfg, "main_error")
        finally:
            save_report()
            # Grace period before the browser closes: the player syncs progress (xAPI) asynchronously,
            # and one video completion was observed to be lost when the session ended immediately after it.
            try:
                time.sleep(8)
            except Exception:
                pass
    done = sum(1 for x in report["items"] if x.get("status") in ("completed", "already_complete"))
    log.info("Run finished: %d items processed, %d completed, %d need you, %d failed. Report: %s",
             len(report["items"]), done, len(report["needs_user"]), len(report["failed"]), report_path)
    for n in report["needs_user"]:
        log.info("  needs you: %s - %s", n["item"], n["why"])
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
