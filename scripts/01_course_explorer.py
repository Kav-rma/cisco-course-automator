"""
Stage 1 - Course Explorer (run once per course).

Walks the live, authenticated course outline: every course-level node -> every section -> every item,
expanding as needed (items only render while their section is expanded), and writes
data/course_structure.json. Does NOT open lessons and does NOT touch assessments.

Run:  .venv\\Scripts\\python.exe scripts\\01_course_explorer.py [--limit-nodes N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import outline as ol  # noqa: E402
from core.browser import launch, open_course, save_diagnostics, wait_until  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402

SELECTORS_DOC = {
    "outline_scope": "top page (not the content iframe)",
    "node_button": 'button[id^="node-button-<uuid>"]  (aria-expanded; label [class*="nodeName--"]; progress [data-percentage])',
    "node_container": '[class*="nodeContainer--"] (ancestor of node_button; holds the sections)',
    "section_container": '[class*="subModuleContainer--"] within node_container',
    "section_button": 'button[id^="submodule-button-"] (NOT unique; use container index)',
    "section_title": '[class*="subModuleName--"][title]',
    "section_counter": '[class*="descendantProgress--"]  e.g. "0 / 4"',
    "item_link": '[class*="blockMainContainer--"] a[class*="blockContainer--"][role=button] (rendered only when section expanded)',
    "item_title": '[class*="blockName--"][title]',
    "status_icon": 'img[alt] -> start | in progress | completed',
    "content_iframe": 'iframe[title="Course content"]  src ...authoring-resources/index.html?...#/courses/<course>/<module>/id/<page>',
    "prev_next": 'button[class*="moduleNavBtn--"][aria-label^="Go To "]',
}


def iframe_src(sb) -> str | None:
    return sb.execute_script("const f=document.querySelector('iframe[title=\"Course content\"]'); return f ? f.src : null;")


def build_structure(cfg, course_title, result_nodes, src_sample):
    for nd in result_nodes:  # stable positional keys for every section/item (also backfills resumed data)
        for s in nd["sections"]:
            s.setdefault("key", f"{nd['index']}/{s['index']}")
            for it in s["items"]:
                it.setdefault("key", f"{nd['index']}/{s['index']}/{it['index']}")
    total_items = sum(len(s["items"]) for nd in result_nodes for s in nd["sections"])
    return {
        "course": {"title": course_title, "url": cfg["course"]["url"], "explored_at": datetime.now().isoformat(timespec="seconds"),
                   "content_iframe_src_sample": src_sample},
        "selectors": SELECTORS_DOC,
        "stats": {"nodes": len(result_nodes), "sections": sum(len(nd["sections"]) for nd in result_nodes), "items": total_items},
        "nodes": sorted(result_nodes, key=lambda x: x["index"]),
    }


def explore(sb, cfg, log, limit_nodes: int | None, only_nodes: set[int] | None = None,
            out: Path | None = None, resume_nodes: list[dict] | None = None) -> dict:
    t = cfg["timeouts"]
    data = ol.read_nodes(sb)
    course_title = data["title"]
    log.info("Course: %s | %d course-level nodes", course_title, len(data["nodes"]))

    src0 = iframe_src(sb)
    result_nodes = list(resume_nodes or [])
    done = {nd["uuid"] for nd in result_nodes}
    if done:
        log.info("Resuming: %d nodes already explored", len(done))
    for n in data["nodes"]:
        if limit_nodes is not None and n["index"] >= limit_nodes:
            break
        if only_nodes is not None and n["index"] not in only_nodes:
            continue
        if n["uuid"] in done:
            continue
        # The outline occasionally re-renders mid-walk (node button momentarily missing -> JS null).
        # Bounded retry: wait for the button to come back (re-open the course if the outline is gone).
        node = None
        for attempt in range(1, 4):
            try:
                node = explore_node(sb, cfg, log, n, len(data["nodes"]))
                break
            except Exception as e:  # noqa: BLE001 - transient DOM/driver errors are expected here
                log.warning("Node %s attempt %d failed: %s", n["name"], attempt, str(e).splitlines()[0][:200])
                save_diagnostics(sb, cfg, f"node_{n['index']}_attempt{attempt}")
                present = wait_until(sb, lambda s: s.execute_script(
                    "return !!document.getElementById(arguments[0])", "node-button-" + n["uuid"]), t["page_load"], what="node button back")
                if not present:
                    log.warning("Outline missing; re-opening the course")
                    open_course(sb, cfg)
        if node is None:
            log.error("Giving up on node %s after 3 attempts", n["name"])
            continue
        result_nodes.append(node)
        if out is not None:  # incremental, crash-safe save (atomic replace)
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(build_structure(cfg, course_title, result_nodes, iframe_src(sb)), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(out)

    src1 = iframe_src(sb)
    if src0 != src1:
        log.info("Content iframe src changed during exploration (outline clicks navigate content): moduleNumber now %s",
                 (src1 or "").split("moduleNumber=")[-1].split("#")[0])
    return build_structure(cfg, course_title, result_nodes, src1)


def explore_node(sb, cfg, log, n: dict, total: int) -> dict:
    """Expand one course-level node and read all of its sections/items. Raises on transient DOM failure."""
    t = cfg["timeouts"]
    if True:  # (kept indentation of the original loop body)
        kind, mod_no = ol.classify_node(n["name"])
        node = {
            "index": n["index"], "uuid": n["uuid"], "kind": kind, "module_number": mod_no,
            "title": n["name"], "percentage": n["percentage"], "sections": [],
        }
        log.info("Node %d/%d [%s]: %s", n["index"] + 1, total, kind, n["name"])

        if not ol.ensure_node_expanded(sb, n["uuid"], t["element"]):
            raise RuntimeError(f"could not expand node {n['name']}")

        # Re-read the sections after expansion (their status/counter may only render once open).
        # Bounded wait: counters like "0 / 4" populate a moment after expansion; exam nodes may have none.
        def sections_ready(s):
            secs = next(x for x in ol.read_nodes(s)["nodes"] if x["uuid"] == n["uuid"])["sections"]
            return bool(secs) and all(sec["counter"] for sec in secs)
        wait_until(sb, sections_ready, t["element"], what="section counters")
        n_live = next(x for x in ol.read_nodes(sb)["nodes"] if x["uuid"] == n["uuid"])
        for s in n_live["sections"]:
            sid, stitle = ol.split_id_title(s["title"])
            section = {"index": s["index"], "id": sid, "key": f"{n['index']}/{s['index']}", "title": stitle, "raw_title": s["title"],
                       "counter": s["counter"], "status": s["status"], "leaf": not s["expandable"],
                       "graded": s["graded"], "max_grade": s["max_grade"], "items": []}
            if not s["expandable"]:
                # Graded leaf (exam / survey): nothing to expand, and we never click into assessments.
                log.info("  Section %s: %s  [leaf%s]", sid, stitle, ", graded " + (s["max_grade"] or "") if s["graded"] else "")
                node["sections"].append(section)
                continue
            if not ol.ensure_section_expanded(sb, n["uuid"], s["index"], t["element"]):
                log.error("Could not expand section %s", s["title"])
                save_diagnostics(sb, cfg, f"section_{sid}_expand")
                node["sections"].append(section)
                continue
            count = ol.wait_items_rendered(sb, n["uuid"], s["index"], t["element"] / 3)
            if count == 0 and (s["counter"] or "").strip() not in ("", "0 / 0"):
                # Expanded but empty while the counter says there are items -> re-toggle once.
                log.debug("  section %s expanded but empty; re-toggling", s["title"])
                sb.execute_script(ol.JS_CLICK_SECTION, n["uuid"], s["index"])
                wait_until(sb, lambda x: not x.execute_script(ol.JS_SECTION_EXPANDED, n["uuid"], s["index"]), 3, what="collapse")
                ol.ensure_section_expanded(sb, n["uuid"], s["index"], t["element"])
                count = ol.wait_items_rendered(sb, n["uuid"], s["index"], t["element"])
            for it in ol.read_items(sb, n["uuid"], s["index"]):
                iid, ititle = ol.split_id_title(it["title"])
                section["items"].append({
                    "index": it["index"], "id": iid, "key": f"{n['index']}/{s['index']}/{it['index']}",
                    "title": ititle, "raw_title": it["title"],
                    "inferred_type": ol.infer_item_type(ititle), "status": it["status"],
                })
            log.info("  Section %s: %s  [%s] -> %d items", sid, stitle, s["counter"], count)
            if count == 0:
                log.warning("  Section %s rendered 0 items (may be an assessment container)", s["title"])
                log.debug("  section container html: %s", sb.execute_script(
                    ol._JS_COMMON + "const [u,i]=arguments; return sectionContainers(u)[i].outerHTML.slice(0,3000);",
                    n["uuid"], s["index"]))
                save_diagnostics(sb, cfg, f"section_{sid}_no_items")
            node["sections"].append(section)
            # Deliberately NOT collapsing: a section click also navigates the content iframe and
            # animates the outline, and the extra click made the next section's click get swallowed.

        return node

    src1 = iframe_src(sb)
    if src0 != src1:
        log.info("Content iframe src changed during exploration (outline clicks navigate content): moduleNumber now %s",
                 (src1 or "").split("moduleNumber=")[-1].split("#")[0])
    return build_structure(cfg, course_title, result_nodes, src1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-nodes", type=int, default=None, help="explore only the first N course-level nodes (testing)")
    ap.add_argument("--only-nodes", default=None, help="comma-separated node indices to explore (testing), e.g. 3,4")
    ap.add_argument("--out", default=None, help="output path (default data/course_structure.json)")
    ap.add_argument("--resume", action="store_true", help="skip nodes already present in the output file")
    ap.add_argument("--course", default=None, help="course key from config/courses.json (default: ask)")
    args = ap.parse_args()
    only_nodes = {int(x) for x in args.only_nodes.split(",")} if args.only_nodes else None

    cfg = load_config(course=args.course)
    log = get_logger("explorer", cfg_path(cfg, "logs"), cfg.get("debug", True))
    out = Path(args.out) if args.out else cfg_path(cfg, "data") / "course_structure.json"
    resume_nodes = None
    if args.resume and out.exists():
        resume_nodes = json.loads(out.read_text(encoding="utf-8")).get("nodes", [])
        if only_nodes:  # --resume --only-nodes X  ==> redo exactly those nodes, keep the rest
            resume_nodes = [nd for nd in resume_nodes if nd["index"] not in only_nodes]

    started = time.time()
    with launch(cfg) as sb:
        try:
            open_course(sb, cfg)
            structure = explore(sb, cfg, log, args.limit_nodes, only_nodes, out=out, resume_nodes=resume_nodes)
        except Exception as e:
            log.exception("Explorer failed: %s", e)
            save_diagnostics(sb, cfg, "explorer_error")
            return 1
    out.write_text(json.dumps(structure, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote %s  (%s) in %.0fs", out, structure["stats"], time.time() - started)
    return 0


if __name__ == "__main__":
    sys.exit(main())
