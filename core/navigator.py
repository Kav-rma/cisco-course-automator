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
    ok = wait_until(sb, lambda s: any(h.startswith(it["id"] + " ") for h in cf.item_headings(cf.read_page_model(s))),
                    t["page_load"], what=f"heading for {it['id']}")
    if not ok:
        # Not fatal by itself (anonymous units such as quiz launchers have no heading); the detector decides.
        log.debug("heading for %s not found on page", it["id"])


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
