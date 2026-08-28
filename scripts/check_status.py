"""Read-only: print the live outline status of given item ids (plus frame completion of the current page).
Run: .venv\\Scripts\\python.exe scripts\\check_status.py 21.4.5 21.4.6 20.1.6 30.4.4
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

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

_course, _argv = _pop_course(sys.argv[1:])
ids = _argv or ["21.4.5"]
cfg = load_config(course=_course); log = get_logger("check", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
with launch(cfg) as sb:
    open_course(sb, cfg)
    for iid in ids:
        loc = nav.locate(structure, iid)
        if not loc:
            log.info("%s: not in structure", iid); continue
        node, sec, it = loc
        from core import outline as ol
        ol.ensure_node_expanded(sb, node["uuid"], cfg["timeouts"]["element"])
        ol.ensure_section_expanded(sb, node["uuid"], sec["index"], cfg["timeouts"]["element"])
        ol.wait_items_rendered(sb, node["uuid"], sec["index"], cfg["timeouts"]["element"])
        st = nav.live_item_status(sb, node, sec, it)
        log.info("%s %-50s -> %s", iid, it["title"][:50], st)
