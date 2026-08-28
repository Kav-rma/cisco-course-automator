"""
KNOWLEDGE_CHECK: answer ungraded "Check Your Understanding" questions from the local question bank.
ASSESSMENT / LAB: pause for the student.

Flow per question (mcq-view):
  1. extract question + shuffled options from the DOM
  2. match question text against the bank (exact -> fuzzy -> MATCH_NOT_CONFIDENT)
  3. map the bank's correct option TEXTS onto the on-screen options (never by position)
  4. if no confident bank match: fall back to the component's own model (config.knowledge_check.live_model_fallback),
     recording the question into the bank; otherwise leave it for the student (needs_user)
  5. select, submit, verify marking (is-correct) and component completion
Graded assessments are never touched.
"""
from __future__ import annotations

import json
from datetime import datetime

from core import content_frame as cf
from core import question_extractor as qx
from core.config import ROOT
from core.matcher import MATCH_NOT_CONFIDENT, QuestionBank, normalize, pick_correct_on_screen
from core.page_detector import PageType

from .base import HandlerContext, HandlerResult, register, unit_complete, wait_unit_complete

_BANK: QuestionBank | None = None


def bank(cfg) -> QuestionBank:
    global _BANK
    if _BANK is None:
        _BANK = QuestionBank.load(ROOT / cfg["paths"]["data"] / "question_bank.json")
    return _BANK


def _record(cfg, rec: dict) -> None:
    p = ROOT / cfg["paths"]["data"] / "kc_results.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def answer_question(ctx: HandlerContext, q: dict) -> dict:
    t = ctx.cfg["timeouts"]["element"]
    kcfg = ctx.cfg.get("knowledge_check", {})
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "lesson_id": ctx.item.get("id"), "mcq": q["modelid"],
           "question": q["question"][:120]}
    if q["complete"] or q["submitted"]:
        rec.update(status="already_answered"); return rec

    m = bank(ctx.cfg).match(q["question"], [o["text"] for o in q["options"]])
    rec.update(match=m.status, confidence=round(m.confidence, 3), question_id=m.question_id)
    targets = pick_correct_on_screen(m.entry, q["options"]) if m.matched else None
    source = "bank"
    if targets is None:
        if kcfg.get("live_model_fallback", True) and q["correct_indices"]:
            targets = [o for o in q["options"] if o["index"] in q["correct_indices"]]
            source = "live_model"
            ctx.log.warning("  No confident bank match (%s, %.2f) - using the component's own answer key", m.status, m.confidence)
        else:
            ctx.log.warning("  %s for question: %s", MATCH_NOT_CONFIDENT, q["question"][:80])
            rec.update(status="needs_user"); return rec
    rec.update(source=source, chosen=[o["text"] for o in targets])
    ctx.log.info("  Q: %s", q["question"][:90])
    ctx.log.info("     -> %s (%s, %s %.2f)", " | ".join(o["text"] for o in targets), source, m.status, m.confidence)

    for o in targets:
        if not qx.select_option(ctx.sb, q["modelid"], o["index"], t):
            rec.update(status="select_failed", option=o["text"]); return rec
    r = qx.submit(ctx.sb, q["modelid"], t)
    if r != "ok":
        rec.update(status="submit_failed", detail=r); return rec
    after = next((x for x in qx.extract(ctx.sb, [q["modelid"]])), {})
    marks = {o["index"]: o["correct_mark"] for o in after.get("options", [])}
    correct = all(marks.get(o["index"]) is True for o in targets) and not any(
        marks.get(o["index"]) is False for o in targets)
    done = cf.wait_complete(ctx.sb, q["modelid"], ctx.cfg["timeouts"]["completion"])
    rec.update(status="answered", marked_correct=correct, complete=done, feedback=after.get("feedback"))
    if not correct:
        ctx.log.warning("  Marked INCORRECT (bank entry may be stale): %s", q["question"][:80])
    return rec


def answer_matching(ctx: HandlerContext, q: dict) -> dict:
    """Matching question: choose, for every item, the option marked correct (bank entry by question+items if
    available, else the component's own model), submit, verify marking."""
    t = ctx.cfg["timeouts"]["element"]
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "lesson_id": ctx.item.get("id"), "mcq": q["modelid"],
           "question": q["question"][:120], "type": "matching"}
    if q["complete"] or q["submitted"]:
        rec.update(status="already_answered"); return rec
    # answer key: bank first (matched by question text + item texts), else live model
    m = bank(ctx.cfg).match(q["question"], [i["text"] for i in q["items"]])
    key = None
    if m.matched and m.entry.get("type") == "matching":
        key = {normalize(i["text"]): i["correct_text"] for i in m.entry.get("items", [])}
        source = "bank"
    if key is None:
        key = {normalize(i["text"]): next((o["text"] for o in i["options"] if o["correct"]), None) for i in q["items"]}
        source = "live_model"
    rec.update(source=source, match=m.status, confidence=round(m.confidence, 3))
    ctx.log.info("  Matching: %s (%d items, %s)", q["question"][:70], len(q["items"]), source)
    chosen = []
    for dd in q["dropdowns"]:
        want = key.get(normalize(dd["title"]))
        opt = next((o for o in dd["options"] if normalize(o["text"]) == normalize(want or "")), None)
        if opt is None:
            rec.update(status="needs_user", detail=f"no answer for item '{dd['title']}'"); return rec
        if not qx.select_matching(ctx.sb, q["modelid"], dd["index"], opt["index"], t):
            rec.update(status="select_failed", item=dd["title"]); return rec
        chosen.append(f"{dd['title']} -> {opt['text']}")
    rec["chosen"] = chosen
    r = qx.submit_view(ctx.sb, q["modelid"], t, qx.extract_matching)
    if r != "ok":
        rec.update(status="submit_failed", detail=r); return rec
    after = next((x for x in qx.extract_matching(ctx.sb, [q["modelid"]])), {})
    marks = [d["correct_mark"] for d in after.get("dropdowns", [])]
    done = cf.wait_complete(ctx.sb, q["modelid"], ctx.cfg["timeouts"]["completion"])
    rec.update(status="answered", marked_correct=all(x is not False for x in marks), complete=done)
    return rec


def answer_object_matching(ctx: HandlerContext, q: dict) -> dict:
    """Click-to-match: for every item k, click category k then option k (option data-id encodes the item it
    answers), then Submit and verify completion."""
    t = ctx.cfg["timeouts"]["element"]
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "lesson_id": ctx.item.get("id"), "mcq": q["modelid"],
           "question": (q.get("question") or "")[:120], "type": "object-matching"}
    if q["complete"] or q["submitted"]:
        rec.update(status="already_answered"); return rec
    cat_ids = [c["id"] for c in q["categories"]]
    opt_ids = {o["id"] for o in q["options"]}
    pairs = []
    for cid in cat_ids:
        if cid not in opt_ids:
            rec.update(status="needs_user", detail=f"no option for category {cid}"); return rec
        r1, r2 = qx.object_matching_pair(ctx.sb, q["modelid"], cid)
        pairs.append(f"{cid}:{r1}/{r2}")
    rec["pairs"] = pairs
    ctx.log.info("  Object matching: %d pairs clicked", len(pairs))
    r = qx.submit_view(ctx.sb, q["modelid"], t, qx.extract_object_matching)
    if r != "ok":
        rec.update(status="submit_failed", detail=r); return rec
    done = cf.wait_complete(ctx.sb, q["modelid"], ctx.cfg["timeouts"]["completion"])
    rec.update(status="answered", complete=done)
    return rec


def answer_secure_quiz(ctx: HandlerContext) -> HandlerResult:
    """CCNA one-question-at-a-time check: for each visible question -> answer from bank/model -> Submit -> next."""
    from core.browser import wait_until
    t = ctx.cfg["timeouts"]
    res = HandlerResult("completed")
    st = qx.secure_state(ctx.sb)
    total = None
    if st.get("counter"):
        import re as _re
        m = _re.search(r"(\d+)\s+of\s+(\d+)", st["counter"])
        total = int(m.group(2)) if m else None
    ctx.log.info("  Secure check: %s", st.get("counter") or "(no counter)")
    # Not started yet? press Start (the control is a div[role=button]; a JS click is sometimes ignored -> trusted CDP click)
    if not st["mcq_ids"] and st.get("start_visible"):
        import time as _t
        qx.secure_start(ctx.sb, "click")
        started = wait_until(ctx.sb, lambda s: bool(qx.secure_state(s)["mcq_ids"]), 5, poll=0.4, what="quiz started (js)")
        if not started:
            from handlers.activity_handler import _frame_offset, _cdp_mouse
            rect = qx.secure_start(ctx.sb, "rect"); _t.sleep(0.3); rect = qx.secure_start(ctx.sb, "rect")
            if rect:
                fx, fy = _frame_offset(ctx)
                _cdp_mouse(ctx, "mouseMoved", fx + rect["x"], fy + rect["y"], 0)
                _cdp_mouse(ctx, "mousePressed", fx + rect["x"], fy + rect["y"], 1)
                _cdp_mouse(ctx, "mouseReleased", fx + rect["x"], fy + rect["y"], 0)
                started = wait_until(ctx.sb, lambda s: bool(qx.secure_state(s)["mcq_ids"]), t["element"], poll=0.4, what="quiz started (cdp)")
        ctx.log.info("  Start pressed -> started=%s", started)
        st = qx.secure_state(ctx.sb)
        if st.get("counter"):
            import re as _re
            m = _re.search(r"(\d+)\s+of\s+(\d+)", st["counter"])
            total = int(m.group(2)) if m else total
    answered = 0
    done_ids: set[str] = set()
    for _ in range((total or 10) + 3):
        st = qx.secure_state(ctx.sb)
        if not st["mcq_ids"] or not st.get("submit") or not st["submit"].get("visible"):
            break
        if st.get("active_q") is None and answered > 0:
            break   # strip no longer points at a question -> Submit page
        # The active question is the one the strip points at (button.block-button.active-block "Qn" <-> title
        # "Question n"); answered questions stay in the DOM un-flagged, so never pick by DOM order alone.
        cand = [st["active_id"]] if st.get("active_id") and st["active_id"] not in done_ids else []
        cand += [m for m in st["mcq_ids"] if m not in done_ids and m not in cand]
        q = next((x for x in qx.extract(ctx.sb, cand) if x["options"] and not x["submitted"]), None) if cand else None
        if q is None:
            break
        done_ids.add(q["modelid"])
        rec = answer_question_select_only(ctx, q)
        _record(ctx.cfg, rec)
        if rec["status"] != "selected":
            res.status = "needs_user" if rec["status"] == "needs_user" else "failed"
            res.notes.append(f"{rec['status']}: {q['question'][:60]}")
            return res
        ok = wait_until(ctx.sb, lambda s: (qx.secure_state(s).get("submit") or {}).get("disabled") is False, t["element"], poll=0.2, what="submit enabled")
        r = qx.secure_submit(ctx.sb)
        if r != "ok":
            res.status = "failed"; res.notes.append(f"submit: {r}"); return res
        answered += 1
        ctx.log.info("  submitted (%s)", st.get("counter") or f"#{answered}")
        before_counter, before_ids = st.get("counter"), tuple(st["mcq_ids"])
        wait_until(ctx.sb, lambda s: (lambda a: a.get("counter") != before_counter or tuple(a["mcq_ids"]) != before_ids or not a["mcq_ids"]
                                      or cf.is_complete(s, ctx.detection.scope_modelid) is True)(qx.secure_state(s)),
                   t["element"], poll=0.3, what="next question")
        import time as _t; _t.sleep(0.4)
    ctx.log.info("  Secure check: %d question(s) answered this run", answered)
    # Final page: "Submit My Assessment" -> tick "Yes, confirm my submission" -> Submit. Without this the attempt
    # is discarded on the next page load (verified), so it is part of the flow.
    fin = None
    ok = wait_until(ctx.sb, lambda s: bool((qx.secure_final_state(s) or {}).get("confirm")), t["element"], poll=0.4, what="submit page")
    if ok:
        r1 = qx.secure_final_act(ctx.sb, "check")
        import time as _t; _t.sleep(0.4)
        r2 = qx.secure_final_act(ctx.sb, "submit")
        gone = wait_until(ctx.sb, lambda s: not (qx.secure_final_state(s) or {}).get("confirm"), 6, poll=0.4, what="submit page gone")
        r3 = None
        if not gone:
            # the page button may need a trusted click (like the Start control): CDP click at its coordinates
            from handlers.activity_handler import _frame_offset, _cdp_mouse
            rect = ctx.sb.execute_script(qx.JS_SECURE_FINAL_ACT, "submit_rect")
            if rect:
                fx, fy = _frame_offset(ctx)
                _cdp_mouse(ctx, "mouseMoved", fx + rect["x"], fy + rect["y"], 0)
                _cdp_mouse(ctx, "mousePressed", fx + rect["x"], fy + rect["y"], 1)
                _cdp_mouse(ctx, "mouseReleased", fx + rect["x"], fy + rect["y"], 0)
                r3 = f"cdp-click {rect.get('tag')}.{rect.get('cls')}"
                gone = wait_until(ctx.sb, lambda s: not (qx.secure_final_state(s) or {}).get("confirm"), 8, poll=0.4, what="submit page gone (cdp)")
        ctx.log.info("  Submit My Assessment: confirm=%s submit=%s cdp=%s page_gone=%s", r1, r2, r3, gone)
        fin = {"confirm": r1, "submit": r2, "cdp": r3, "page_gone": gone}
    else:
        fst = qx.secure_final_state(ctx.sb)
        ctx.log.warning("  no confirm checkbox found at the end (page: %s)", (fst or {}).get("page_text"))
        fin = {"error": "no confirm page", "state": fst}
    res.components["secure"] = {"answered": answered, "total": total, "final": fin}
    if not wait_unit_complete(ctx, timeout=max(30, ctx.cfg["timeouts"]["completion"])):
        res.status = "failed"; res.notes.append("secure check submitted but unit still incomplete")
    return res


def answer_question_select_only(ctx: HandlerContext, q: dict) -> dict:
    """Select the correct option(s) of an mcq (bank first, then the component model) WITHOUT submitting."""
    t = ctx.cfg["timeouts"]["element"]
    kcfg = ctx.cfg.get("knowledge_check", {})
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "lesson_id": ctx.item.get("id"), "mcq": q["modelid"],
           "question": q["question"][:120], "mode": "secure"}
    m = bank(ctx.cfg).match(q["question"], [o["text"] for o in q["options"]])
    rec.update(match=m.status, confidence=round(m.confidence, 3), question_id=m.question_id)
    targets = pick_correct_on_screen(m.entry, q["options"]) if m.matched else None
    source = "bank"
    if targets is None:
        if kcfg.get("live_model_fallback", True) and q["correct_indices"]:
            targets = [o for o in q["options"] if o["index"] in q["correct_indices"]]; source = "live_model"
        else:
            rec.update(status="needs_user"); return rec
    rec.update(source=source, chosen=[o["text"] for o in targets])
    ctx.log.info("  Q: %s", q["question"][:90])
    ctx.log.info("     -> %s (%s)", " | ".join(o["text"] for o in targets), source)
    for o in targets:
        if not qx.select_option(ctx.sb, q["modelid"], o["index"], t):
            rec.update(status="select_failed", option=o["text"]); return rec
    rec.update(status="selected")
    return rec


@register(PageType.KNOWLEDGE_CHECK)
class KnowledgeCheckHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        if unit_complete(ctx) is True:
            return HandlerResult("already_complete")
        if any(c["tag"] == "adaptive-start-screen-view" for c in ctx.detection.components):
            return answer_secure_quiz(ctx)
        res = HandlerResult("completed")
        cf.scroll_to(ctx.sb, ctx.detection.scope_modelid, "start")
        qs = qx.extract(ctx.sb, qx.question_ids(ctx.detection))
        ctx.log.info("  %d question(s) on this knowledge check", len(qs))
        for c in qx.unsupported_question_components(ctx.detection):
            if c["complete"] is not True:
                ctx.log.warning("  Unsupported question type %s - please answer it yourself", c["tag"])
                res.status = "needs_user"; res.notes.append(f"unsupported question type {c['tag']}")
        mqs = qx.extract_matching(ctx.sb, qx.matching_ids(ctx.detection))
        if mqs:
            ctx.log.info("  %d matching question(s)", len(mqs))
        oqs = qx.extract_object_matching(ctx.sb, qx.object_matching_ids(ctx.detection))
        if oqs:
            ctx.log.info("  %d click-to-match question(s)", len(oqs))
        for q, fn in [(q, answer_question) for q in qs] + [(q, answer_matching) for q in mqs] + [(q, answer_object_matching) for q in oqs]:
            rec = fn(ctx, q)
            _record(ctx.cfg, rec)
            res.components[q["modelid"]] = rec
            if rec["status"] in ("needs_user", "select_failed", "submit_failed"):
                res.status = "needs_user" if rec["status"] == "needs_user" else "failed"
                res.notes.append(f"{rec['status']}: {q['question'][:60]}")
        if res.status == "completed" and not wait_unit_complete(ctx, timeout=5):
            # The unit can hold more than the questions (e.g. 4.1.5 has scenario tabs + text): open interactives
            # and read through the statics, then re-check.
            from handlers.interactive_handler import process_interactives
            from handlers.base import scroll_through_unit
            extra = process_interactives(ctx, ctx.detection.components)
            if extra:
                res.components["interactives"] = extra
                ctx.log.info("  Processed %d interactive component(s) inside the check", len(extra))
            scroll_through_unit(ctx)
            if not wait_unit_complete(ctx):
                res.status = "failed"
                res.notes.append("all questions answered but unit still incomplete")
        return res


@register(PageType.LAB)
class LabHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        if unit_complete(ctx) is True:
            return HandlerResult("already_complete")
        # The course marks a Packet Tracer item complete when its activity button/link is clicked (verified by the
        # user). Click the item's own button (instructions), then the download link if still incomplete, verify
        # is-complete. The .pka itself is never opened or solved by the automation.
        res = HandlerResult("completed")
        main_handle = ctx.sb.driver.current_window_handle
        for c in ctx.detection.components:
            if c["tag"] not in ("pagetracer-view", "packettracer-view") or c["complete"] is True:
                continue
            btns = cf.pt_buttons(ctx.sb, c["modelid"])
            ctx.log.info("  Packet Tracer item: %s", [b["title"] for b in btns])
            done = False
            for kind in ("dialog", "button", "download", "file"):
                for b in [x for x in btns if x["kind"] == kind]:
                    r = cf.pt_click(ctx.sb, c["modelid"], kind, b["id"])
                    ctx.log.info("  clicked %s '%s': %s", kind, b["title"], r)
                    import time as _t; _t.sleep(1.5)   # give the new tab (PDF / file) a moment to open
                    for h in list(ctx.sb.driver.window_handles):   # a click may open a new tab -> close, come back
                        if h != main_handle:
                            try:
                                ctx.sb.driver.switch_to.window(h)
                                ctx.sb.driver.close()
                            except Exception:
                                pass
                    ctx.sb.driver.switch_to.window(main_handle)
                    cf.enter(ctx.sb)
                    done = cf.wait_complete(ctx.sb, c["modelid"], ctx.cfg["timeouts"]["completion"])
                    if done:
                        break
                if done:
                    break
            res.components[c["modelid"]] = {"ok": done, "buttons": btns}
            if not done:
                res.status = "needs_user"
                res.notes.append("lab item did not complete after clicking its buttons")
        if res.status == "completed" and not wait_unit_complete(ctx):
            res.status = "needs_user"
            res.notes.append("lab unit still incomplete")
        return res


@register(PageType.ASSESSMENT)
class AssessmentHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        ctx.log.warning("GRADED ASSESSMENT detected (%s) - automation paused; start and take it yourself.", ctx.item.get("title"))
        return HandlerResult("needs_user", ["graded assessment - never automated"])
