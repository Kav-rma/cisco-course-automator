"""
Phase 4 - live test of content handlers on specific items.

For each item id: navigate via the outline, detect, dispatch the handler, then report
unit completion (frame DOM) and the outline status (top page) before/after.

Run:  .venv\\Scripts\\python.exe scripts\\phase4_handlers.py 3.1.3 3.1.1 3.1.2
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content_frame as cf  # noqa: E402
from core import outline as ol  # noqa: E402
from core.browser import launch, open_course, save_diagnostics, wait_until  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.page_detector import detect  # noqa: E402
from handlers import HandlerContext, dispatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase3_detect import goto_item, locate  # noqa: E402

def _pop_course(argv):
    """Remove '--course KEY' (or '--course=KEY') from argv and return KEY (or None)."""
    key = None
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--course" and i + 1 < len(argv):
            key = argv[i + 1]; i += 2; continue
        if a.startswith("--course="):
            key = a.split("=", 1)[1]; i += 1; continue
        out.append(a); i += 1
    return key, out


def outline_status(sb, node, sec, it, expect_completed: bool = False, timeout: float = 15):
    """Outline status from the top page; optionally wait (bounded) for the async progress sync to show 'completed'."""
    cf.leave(sb)
    read = lambda s: ol.read_items(s, node["uuid"], sec["index"])[it["index"]]["status"]  # noqa: E731
    if expect_completed:
        wait_until(sb, lambda s: read(s) == "completed", timeout, poll=1.0, what="outline status completed")
    return read(sb)


def main() -> int:
    _course, _argv = _pop_course(sys.argv[1:])
    ids = _argv or ["3.1.3", "3.1.1"]
    cfg = load_config(course=_course)
    log = get_logger("phase4", cfg_path(cfg, "logs"), cfg.get("debug", True))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    report = []
    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            for item_id in ids:
                loc = locate(structure, item_id)
                if not loc:
                    log.error("Item %s not in structure", item_id); continue
                node, sec, it = loc
                goto_item(sb, cfg, node, sec, it)
                model = cf.read_page_model(sb)
                det = detect(model, it, sec)
                log.info("=== %s %s -> %s (unit complete=%s)", item_id, it["title"], det.page_type.value, det.complete)
                st_before = outline_status(sb, node, sec, it)
                cf.enter(sb)
                t0 = time.time()
                res = dispatch(HandlerContext(sb, cfg, it, sec, det, log))
                dt = time.time() - t0
                unit_after = cf.is_complete(sb, det.scope_modelid) if det.scope_modelid else None
                st_after = outline_status(sb, node, sec, it, expect_completed=bool(unit_after))
                log.info("    handler: %s in %.1fs | unit complete %s -> %s | outline %s -> %s | %s",
                         res.status, dt, det.complete, unit_after, st_before, st_after, "; ".join(res.notes))
                for mid, out in res.components.items():
                    log.info("      component %s: %s", mid[:8], json.dumps(out))
                report.append({"item": item_id, "type": det.page_type.value, "status": res.status, "seconds": round(dt, 1),
                               "unit_complete": unit_after, "outline": [st_before, st_after], "notes": res.notes, "components": res.components})
                if res.status == "failed":
                    save_diagnostics(sb, cfg, f"handler_failed_{item_id}")
        except Exception as e:
            log.exception("phase4 failed: %s", e)
            save_diagnostics(sb, cfg, "phase4_error")
            return 1
    (cfg_path(cfg, "recon") / "phase4_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
