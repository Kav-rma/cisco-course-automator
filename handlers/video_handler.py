"""
Video content (VIDEO): play each media component until the player reports `ended`, then verify is-complete.

Completion rule (verified manually by the user and observed in the DOM): the media block completes only when
the video has been played and reaches its end. Strategies (config.video.strategy):
  seek_end : play, wait until duration is known, seek to (duration - seek_margin_sec), let it run to `ended`.
  rate     : play at config.video.playback_rate until `ended`.
If the chosen strategy does not produce is-complete, the fallback strategy is tried once; if that also fails
the handler reports failure instead of pretending.
"""
from __future__ import annotations

import time

from core import content_frame as cf
from core.browser import wait_until
from core.page_detector import PageType

from .base import HandlerContext, HandlerResult, register, unit_complete, wait_unit_complete


def _wait_duration(ctx, modelid, timeout) -> float | None:
    ok = wait_until(ctx.sb, lambda s: (cf.video_state(s, modelid) or {}).get("duration", 0) > 0, timeout, what="video duration")
    return cf.video_state(ctx.sb, modelid)["duration"] if ok else None


def _wait_ended(ctx, modelid, timeout) -> bool:
    return wait_until(ctx.sb, lambda s: bool((cf.video_state(s, modelid) or {}).get("ended")), timeout, poll=0.5, what="video ended")


def play_video(ctx: HandlerContext, modelid: str, strategy: str) -> dict:
    vcfg, t = ctx.cfg["video"], ctx.cfg["timeouts"]
    st = cf.video_state(ctx.sb, modelid)
    if st is None:
        return {"ok": False, "error": "no <video> element in media component"}
    if st["complete"] is True:
        return {"ok": True, "note": "already complete"}

    cf.scroll_to(ctx.sb, modelid, "center")
    r = cf.video_cmd(ctx.sb, modelid, "play")
    if r != "ok":
        return {"ok": False, "error": f"play: {r}"}
    duration = _wait_duration(ctx, modelid, t["element"])
    if not duration:
        return {"ok": False, "error": "duration never became known"}
    # make sure playback actually started (autoplay policy / buffering)
    started = wait_until(ctx.sb, lambda s: (cf.video_state(s, modelid) or {}).get("currentTime", 0) > 0.2, t["element"], what="playback start")
    if not started:
        # last resort within policy: muted playback is always allowed
        cf.video_cmd(ctx.sb, modelid, "mute", True)
        cf.video_cmd(ctx.sb, modelid, "play")
        started = wait_until(ctx.sb, lambda s: (cf.video_state(s, modelid) or {}).get("currentTime", 0) > 0.2, t["element"], what="playback start (muted)")
        if not started:
            return {"ok": False, "error": "playback did not start"}

    if strategy == "seek_end":
        target = max(0.0, duration - float(vcfg.get("seek_margin_sec", 1.0)))
        ctx.log.info("  Video %.0fs long -> seeking to %.1fs", duration, target)
        cf.video_cmd(ctx.sb, modelid, "seek", target)
        # seeking may pause on some players; make sure it is playing
        time.sleep(0.3)
        if cf.video_state(ctx.sb, modelid)["paused"]:
            cf.video_cmd(ctx.sb, modelid, "play")
        ended = _wait_ended(ctx, modelid, timeout=float(vcfg.get("seek_margin_sec", 1.0)) + t["element"])
    else:  # "rate"
        rate = float(vcfg.get("playback_rate", 2.0))
        cf.video_cmd(ctx.sb, modelid, "rate", rate)
        remaining = (duration - cf.video_state(ctx.sb, modelid)["currentTime"]) / rate
        ctx.log.info("  Video %.0fs long -> playing at %.2fx (~%.0fs)", duration, rate, remaining)
        ended = _wait_ended(ctx, modelid, timeout=remaining + 60)

    complete = cf.wait_complete(ctx.sb, modelid, t["completion"])
    final = cf.video_state(ctx.sb, modelid)
    return {"ok": bool(complete), "strategy": strategy, "duration": duration, "ended": ended, "complete": complete,
            "currentTime": final and final["currentTime"]}


@register(PageType.VIDEO)
class VideoHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        if unit_complete(ctx) is True:
            return HandlerResult("already_complete")
        vcfg = ctx.cfg["video"]
        res = HandlerResult("completed")
        for c in ctx.detection.components:
            if c["tag"] != "media-view":
                continue
            out = play_video(ctx, c["modelid"], vcfg.get("strategy", "seek_end"))
            if not out["ok"] and vcfg.get("fallback_strategy") and vcfg.get("fallback_strategy") != vcfg.get("strategy"):
                ctx.log.warning("  %s strategy did not complete the video (%s); falling back to %s",
                                vcfg.get("strategy"), out.get("error") or "not complete", vcfg["fallback_strategy"])
                out = {"first_attempt": out, **play_video(ctx, c["modelid"], vcfg["fallback_strategy"])}
            res.components[c["modelid"]] = out
            if not out["ok"]:
                res.status = "failed"
                res.notes.append(f"media {c['modelid']}: {out.get('error') or 'not complete'}")
        if res.status == "completed" and not wait_unit_complete(ctx):
            res.status = "failed"
            res.notes.append("videos ended but unit still incomplete")
        return res
