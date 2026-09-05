"""Navigation between outline items + live progress reading (top page <-> content frame)."""
from __future__ import annotations

import logging

from . import content_frame as cf
from . import outline as ol
from .browser import wait_until

log = logging.getLogger(__name__)

GRADED_KINDS = {"checkpoint_exam", "final_exam", "survey", "assessment"}


def locate(structure: dict, item_id: str):
    for node in structure["nodes"]:
        for sec in node["sections"]:
            for it in sec["items"]:
                if it["id"] == item_id:
                    return node, sec, it
    return None


def goto_item(sb, cfg: dict, node: dict, sec: dict, it: dict) -> None:
    """Open an outline item and wait until the content frame shows its heading."""
    t = cfg["timeouts"]
    cf.leave(sb)
    if not ol.ensure_node_expanded(sb, node["uuid"], t["element"]):
        raise RuntimeError(f"could not expand module node {node['title']}")
    if not ol.ensure_section_expanded(sb, node["uuid"], sec["index"], t["element"]):
        raise RuntimeError(f"could not expand section {sec['raw_title']}")
    ol.wait_items_rendered(sb, node["uuid"], sec["index"], t["element"])
    ol.click_item(sb, node["uuid"], sec["index"], it["index"])
    cf.enter(sb)
    cf.wait_page_ready(sb, t["page_load"])
    if it.get("inferred_type") == "assessment":
        return   # graded quiz launchers have no item heading: waiting for one just burns page_load seconds
    heading_ok = lambda s: any(h.startswith(it["id"] + " ") for h in cf.item_headings(cf.read_page_model(s)))  # noqa: E731
    if wait_until(sb, heading_ok, t["page_load"], what=f"heading for {it['id']}"):
        return
    # The outline click can be swallowed while the tree animates, leaving the PREVIOUS page in the frame; a handler
    # would then "complete" the wrong page. Re-click once and re-check before giving up.
    log.warning("heading for %s not found after click - re-clicking the outline item", it["id"])
    cf.leave(sb)
    ol.click_item(sb, node["uuid"], sec["index"], it["index"])
    cf.enter(sb)
    cf.wait_page_ready(sb, t["page_load"])
    if not wait_until(sb, heading_ok, t["page_load"] / 2, what=f"heading for {it['id']} (retry)"):
        # Still not fatal (some anonymous units have no heading); the detector decides, but this is now visible.
        log.warning("heading for %s still not found on page - proceeding on the current frame content", it["id"])


def live_item_status_expanded(sb, cfg: dict, node: dict, sec: dict, it: dict) -> str | None:
    """Like live_item_status, but first expands the node/section so the item row is actually rendered
    (used after a fresh course load, when nothing is expanded yet)."""
    t = cfg["timeouts"]
    cf.leave(sb)
    try:
        ol.ensure_node_expanded(sb, node["uuid"], t["element"])
        ol.ensure_section_expanded(sb, node["uuid"], sec["index"], t["element"])
        ol.wait_items_rendered(sb, node["uuid"], sec["index"], t["element"])
        return ol.read_items(sb, node["uuid"], sec["index"])[it["index"]]["status"]
    except Exception:
        return None


def live_item_status(sb, node: dict, sec: dict, it: dict) -> str | None:
    cf.leave(sb)
    try:
        return ol.read_items(sb, node["uuid"], sec["index"])[it["index"]]["status"]
    except Exception:
        return None


def wait_item_status(sb, node, sec, it, wanted: str, timeout: float) -> str | None:
    wait_until(sb, lambda s: live_item_status(s, node, sec, it) == wanted, timeout, poll=1.0, what=f"{it['id']} -> {wanted}")
    return live_item_status(sb, node, sec, it)


def live_node_progress(sb) -> dict:
    """uuid -> live node dict (percentage, sections[counter,status]) straight from the outline."""
    data = ol.read_nodes(sb)
    return {n["uuid"]: n for n in data["nodes"]}
