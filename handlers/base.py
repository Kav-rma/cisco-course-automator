"""
Handler infrastructure.

A handler receives a HandlerContext (browser already switched INTO the content frame, the outline item,
its section, and the Detection for it) and must:
  * perform only the legitimate interaction the content asks for,
  * verify completion via the DOM (is-complete) where the content exposes it,
  * never mark something complete that is not,
  * return a HandlerResult (completed / needs_user / failed) with notes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core import content_frame as cf
from core.page_detector import Detection, PageType


@dataclass
class HandlerContext:
    sb: object
    cfg: dict
    item: dict
    section: dict | None
    detection: Detection
    log: logging.Logger


@dataclass
class HandlerResult:
    status: str                      # "completed" | "already_complete" | "needs_user" | "failed" | "skipped"
    notes: list[str] = field(default_factory=list)
    components: dict = field(default_factory=dict)   # modelid -> per-component outcome

    @property
    def ok(self) -> bool:
        return self.status in ("completed", "already_complete")


def unit_complete(ctx: HandlerContext) -> bool | None:
    """Completion of the item's scope (article/block), re-read live."""
    return cf.is_complete(ctx.sb, ctx.detection.scope_modelid)


def wait_unit_complete(ctx: HandlerContext, timeout: float | None = None) -> bool:
    t = timeout if timeout is not None else ctx.cfg["timeouts"]["completion"]
    return cf.wait_complete(ctx.sb, ctx.detection.scope_modelid, t)


def scroll_through_unit(ctx: HandlerContext) -> None:
    """Read through the unit: every component passes fully through the viewport (Adapt completes static
    content on in-view, which requires both its top and bottom to have been on screen)."""
    for c in ctx.detection.components:
        if c["complete"] is True:
            continue
        cf.scroll_read_through(ctx.sb, c["modelid"])
    cf.scroll_to(ctx.sb, ctx.detection.scope_modelid, "start")


_REGISTRY: dict[PageType, type] = {}


def register(ptype: PageType):
    def deco(cls):
        _REGISTRY[ptype] = cls
        return cls
    return deco


def dispatch(ctx: HandlerContext) -> HandlerResult:
    # Import handlers lazily so registration happens on first use.
    from . import lesson_handler, interactive_handler, video_handler, knowledge_check_handler, activity_handler  # noqa: F401
    cls = _REGISTRY.get(ctx.detection.page_type)
    if cls is None:
        return HandlerResult("skipped", [f"no handler for {ctx.detection.page_type.value}"])
    return cls().handle(ctx)
