"""Exercise the hotgraphic driver on an item (even if already complete): pins -> popup -> close, report counts.
Run: .venv\\Scripts\\python.exe scripts\\probe_hotgraphic.py 38.3.1
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect
from handlers.base import HandlerContext
from handlers.interactive_handler import process_hotgraphic

item_id = sys.argv[1] if len(sys.argv) > 1 else "38.3.1"
cfg = load_config(); log = get_logger("probe_hot", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    ctx = HandlerContext(sb, cfg, it, sec, det, log)
    for c in det.components:
        if c["tag"] == "hotgraphic-view":
            out = process_hotgraphic(ctx, c["modelid"])
            log.info("RESULT %s: %s", item_id, out)
