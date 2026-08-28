"""
Phase 3 - live test of the content-frame page model + page detector.

For each item id: navigate to it via the outline, enter the content iframe, read the page model,
run detect(), print the result, and save the model as an offline test fixture
(tests/fixtures/page_model_<section>.json) plus data/recon/detect_<item>.json.

No lesson content is interacted with.

Run:  .venv\\Scripts\\python.exe scripts\\phase3_detect.py 3.0.1 3.1.1 3.1.2 3.1.3 3.1.4 3.3.1 3.3.3
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content_frame as cf  # noqa: E402
from core import outline as ol  # noqa: E402
from core.browser import launch, open_course, save_diagnostics, wait_until  # noqa: E402
from core.config import ROOT, load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.page_detector import detect  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def locate(structure: dict, item_id: str):
    for node in structure["nodes"]:
        for sec in node["sections"]:
            for it in sec["items"]:
                if it["id"] == item_id:
                    return node, sec, it
    return None


def goto_item(sb, cfg, node, sec, it) -> None:
    """Navigate to an outline item and wait until the frame shows its heading."""
    t = cfg["timeouts"]
    cf.leave(sb)
    ol.ensure_node_expanded(sb, node["uuid"], t["element"])
    ol.ensure_section_expanded(sb, node["uuid"], sec["index"], t["element"])
    ol.wait_items_rendered(sb, node["uuid"], sec["index"], t["element"])
    ol.click_item(sb, node["uuid"], sec["index"], it["index"])
    cf.enter(sb)
    cf.wait_page_ready(sb, t["page_load"])
    # The item's heading must be on the page (same-section items share the page, so this may already be true).
    wait_until(sb, lambda s: any(h.startswith(it["id"] + " ") for h in cf.item_headings(cf.read_page_model(s))),
               t["page_load"], what=f"heading for {it['id']}")


def main() -> int:
    ids = sys.argv[1:] or ["3.0.1", "3.1.1", "3.1.2", "3.1.3", "3.1.4", "3.3.1", "3.3.3"]
    cfg = load_config()
    log = get_logger("phase3", cfg_path(cfg, "logs"), cfg.get("debug", True))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    FIXTURES.mkdir(parents=True, exist_ok=True)
    recon = cfg_path(cfg, "recon")

    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            for item_id in ids:
                loc = locate(structure, item_id)
                if not loc:
                    log.error("Item %s not in structure", item_id)
                    continue
                node, sec, it = loc
                goto_item(sb, cfg, node, sec, it)
                model = cf.read_page_model(sb)
                det = detect(model, it, sec)
                cf.leave(sb)
                (FIXTURES / f"page_model_{sec['id']}.json").write_text(json.dumps(model, indent=1, ensure_ascii=False), encoding="utf-8")
                (recon / f"detect_{item_id}.json").write_text(json.dumps(det.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
                comps = ", ".join(f"{c['tag']}{'✓' if c['complete'] else ('✗' if c['complete'] is False else '?')}" for c in det.components)
                log.info("%s -> %-16s scope=%s complete=%s | %s | %s", item_id, det.page_type.value, det.scope_kind, det.complete, comps, "; ".join(det.reasons))
                if det.page_type.value == "UNKNOWN":
                    save_diagnostics(sb, cfg, f"unknown_{item_id}")
        except Exception as e:
            log.exception("phase3 failed: %s", e)
            save_diagnostics(sb, cfg, "phase3_error")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
