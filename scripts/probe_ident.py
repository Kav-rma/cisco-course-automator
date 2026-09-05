"""Read-only: for given items, dump how the assist could identify the open item - outline active title,
the classes on outline item anchors (to find the real 'active' marker), page headings, frame href, and
secure-state. Never presses Start. Run: ... probe_ident.py 23.2.3 5.4.3"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav, outline as ol, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS_ANCHORS = r"""
return JSON.stringify(Array.from(document.querySelectorAll('a[class*="blockContainer--"]')).map(a => ({
  cls: a.className, cur: a.getAttribute('aria-current'), sel: a.getAttribute('aria-selected'),
  title: (a.querySelector('[class*="blockName--"]')||{}).title || a.innerText.trim().slice(0,60)})));
"""
ids = [a for a in sys.argv[1:] if not a.startswith("--")] or ["23.2.3"]
cfg = load_config(course=None); log = get_logger("probe_ident", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
out = {}
with launch(cfg) as sb:
    open_course(sb, cfg)
    for iid in ids:
        node, sec, it = nav.locate(structure, iid)
        nav.goto_item(sb, cfg, node, sec, it)
        cf.leave(sb)
        act = ol.active_item_title(sb)
        anchors = json.loads(sb.execute_script(JS_ANCHORS))
        cf.enter(sb)
        heads = cf.item_headings(cf.read_page_model(sb))
        href = cf.frame_href(sb)
        st = qx.secure_state(sb)
        rec = {"active_item_title": act, "anchors_rendered": len(anchors),
               "anchor_classes_sample": sorted({a["cls"] for a in anchors})[:6],
               "anchors_with_marker": [a for a in anchors if a["cur"] or a["sel"] or __import__("re").search(r"active|selected|current", a["cls"], __import__("re").I)],
               "headings": heads[:6], "frame_href": href,
               "secure": {k: st.get(k) for k in ("start_visible", "counter", "active_q")}}
        out[iid] = rec
        log.info("%s active_item_title=%r", iid, act)
        log.info("   anchors=%d marker-anchors=%s", len(anchors), [(a["title"], a["cls"][:50]) for a in rec["anchors_with_marker"]][:3])
        log.info("   headings=%s", heads[:4])
        log.info("   href=%s", href)
        log.info("   secure=%s", rec["secure"])
    (cfg_path(cfg, "recon") / "ident_probe.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
