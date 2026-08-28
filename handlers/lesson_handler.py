"""Static content (LESSON / INTRODUCTION / SUMMARY-without-interactives): view it, verify completion."""
from __future__ import annotations

from core.page_detector import PageType

from .base import HandlerContext, HandlerResult, register, scroll_through_unit, unit_complete, wait_unit_complete


class StaticHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        if unit_complete(ctx) is True:
            return HandlerResult("already_complete")
        scroll_through_unit(ctx)
        if wait_unit_complete(ctx):
            return HandlerResult("completed", ["completed after scrolling into view"])
        # First item on a freshly loaded page sometimes misses the in-view tracker (seen on 8.1.2 / 9.0.1):
        # settle briefly and read through once more before declaring failure.
        import time as _t
        _t.sleep(2.0)
        scroll_through_unit(ctx)
        if wait_unit_complete(ctx):
            return HandlerResult("completed", ["completed after a second read-through"])
        # Some static units contain components that do not complete by view (e.g. unknown widgets) -> report, don't lie.
        pending = [c for c in ctx.detection.components if c["complete"] is False]
        return HandlerResult("failed", [f"unit still incomplete after viewing; pending components: {[c['tag'] for c in pending]}"])


@register(PageType.LESSON)
class LessonHandler(StaticHandler):
    pass


@register(PageType.INTRODUCTION)
class IntroductionHandler(StaticHandler):
    pass
