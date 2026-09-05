"""
Quiz ASSIST - fully MANUAL, no automation.

This script only:
  1. opens the browser (your saved login) and the course
  2. pins a small button in the TOP-LEFT of the page: "Fetch answers"
  3. when YOU click that button, it reads whatever quiz question is on screen right now, figures out which
     module quiz it is, and shows ALL your saved answers for that quiz in a panel.

It never presses Start, never selects an option, never submits, never skips, never changes pages. You drive the
whole quiz yourself; the button just reveals your own saved answers on demand. Leave it running and use the button
on any graded quiz. Press Ctrl+C in the terminal (or close the browser) to stop.

Run:  .venv\\Scripts\\python.exe scripts\\assist_quizzes.py [--course KEY]
      [--profile NAME] [--fresh-login] [--keep-session]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content_frame as cf  # noqa: E402
from core import outline as ol  # noqa: E402
from core import question_extractor as qx  # noqa: E402
from core.browser import launch, open_course  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.matcher import normalize  # noqa: E402

# Pin the button (top-left) and return how many times it has been clicked. Re-runs safely (re-adds if missing).
JS_BALL = r"""
if (!document.getElementById('assist-ball')) {
  var b = document.createElement('div'); b.id = 'assist-ball';
  b.textContent = '📘 Fetch answers';
  b.style.cssText = 'position:fixed;left:14px;top:14px;z-index:2147483647;cursor:pointer;'
    + 'background:#66c430;color:#0d274d;font:700 13px system-ui,Arial;padding:10px 15px;border-radius:22px;'
    + 'box-shadow:0 4px 14px rgba(0,0,0,.35);user-select:none;';
  b.onclick = function(){ window.__assistClick = (window.__assistClick||0)+1; };
  document.body.appendChild(b);
}
return window.__assistClick || 0;
"""

# Which outline node is currently open? Checkpoint exams are graded leaf sections with no item id, so we
# identify them by the uuid of the node whose row is marked active/selected/current.
JS_ACTIVE_NODE = r"""
const q = (s) => Array.from(document.querySelectorAll(s));
const act = (e) => /active|selected|current/i.test(e.className || '') || !!e.getAttribute('aria-current');
let sc = q('[class*="subModuleContainer--"]').find(act);
let host = sc ? sc.closest('[class*="nodeContainer--"]') : null;
if (!host) { const nb = q('button[id^="node-button-"]').find(act); host = nb ? nb.closest('[class*="nodeContainer--"]') : null; }
if (!host) return null;
const b = host.querySelector('button[id^="node-button-"]');
return b ? b.id.replace('node-button-', '') : null;
"""

# Show the answers panel just under the button (has its own close button; no Python needed to dismiss).
JS_PANEL = r"""
var p = document.getElementById('assist-panel');
if (!p) {
  p = document.createElement('div'); p.id = 'assist-panel';
  p.style.cssText = 'position:fixed;left:14px;top:58px;z-index:2147483647;width:360px;max-height:86vh;overflow:auto;'
    + 'background:#0d274d;color:#fff;font:13px/1.5 system-ui,Arial;padding:12px 14px;border-radius:10px;'
    + 'box-shadow:0 6px 24px rgba(0,0,0,.4);border:2px solid #66c430;';
  document.body.appendChild(p);
}
p.style.display = 'block';
p.innerHTML = '<div onclick="this.parentNode.style.display=\'none\'" '
  + 'style="float:right;cursor:pointer;font-weight:700;opacity:.7">✕</div>' + arguments[0];
return true;
"""


def run_js(sb, script, *args):
    sb.driver.switch_to.default_content()
    try:
        return sb.execute_script(script, *args)
    except Exception:
        return None


def answers_html(title_line, kq):
    rows = []
    for rec in sorted(kq.values(), key=lambda r: r["n"]):
        if rec.get("answer_texts"):
            ans = "; ".join(rec["answer_texts"])
        elif rec.get("raw_answer"):
            ans = rec["raw_answer"]
        else:
            ans = "<i>(answer this one yourself)</i>"
        rows.append(f"<div style='margin:6px 0'><b>Q{rec['n']}.</b> {ans}</div>")
    body = "".join(rows) or "<div>(no saved answers for this quiz)</div>"
    return (f"<div style='font-size:15px;font-weight:700;margin-bottom:6px'>{title_line}</div>"
            f"<div style='opacity:.75;margin-bottom:8px'>Match by the <b>Question number</b> on screen; pick the "
            f"option by its <b>text</b> (letters shuffle each attempt).</div>"
            f"<hr style='border-color:#284a7a'>{body}")


def current_texts(sb):
    """Read the question text(s) visible now, plus the quiz's total question count from the counter."""
    cf.enter(sb)
    texts, total = [], None
    try:
        st = qx.secure_state(sb)
        m = re.search(r"(\d+)\s+of\s+(\d+)", st.get("counter") or "")
        total = int(m.group(2)) if m else None
        ids = st.get("mcq_ids") or []
        aid = st.get("active_id") or (ids[0] if ids else None)
        if aid:
            for q in qx.extract(sb, [aid]):
                if q.get("question"):
                    texts.append(q["question"])
        for m in qx.extract_matching(sb):
            if m.get("question"):
                texts.append(m["question"])
        for o in qx.extract_object_matching(sb):
            if o.get("question"):
                texts.append(o["question"])
    except Exception:
        pass
    return texts, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default=None, help="course key from config/courses.json (default: ask)")
    ap.add_argument("--profile", default=None, help="account profile name -> Chrome profile dir profile_<name>")
    ap.add_argument("--fresh-login", action="store_true", help="clear cookies in the profile first (sign in again)")
    ap.add_argument("--keep-session", action="store_true", help="remember the login between runs")
    args = ap.parse_args()
    if args.keep_session:
        os.environ["NETACAD_SESSION_MODE"] = "persistent"
    if args.profile:
        os.environ["NETACAD_PROFILE"] = args.profile
    if args.fresh_login:
        os.environ["NETACAD_FRESH_LOGIN"] = "1"

    cfg = load_config(course=args.course)
    log = get_logger("assist", cfg_path(cfg, "logs"), cfg.get("debug", True))

    # index: normalized question text -> item id ; and item id -> (title, {n: rec})
    # loads the module-quiz key, plus the checkpoint-exam key when it exists
    norm_to_items, quizzes = {}, {}
    for fname in ("quiz_answer_key.json", "exam_answer_key.json"):
        f = cfg_path(cfg, "data") / fname
        if not f.exists():
            continue
        key = json.loads(f.read_text(encoding="utf-8"))
        for q in key["quizzes"]:
            quizzes[q["item"]] = {"title": q["title"], "answers": {qq["n"]: qq for qq in q["questions"]}}
            for qq in q["questions"]:
                norm_to_items.setdefault(normalize(qq["question"]), []).append(q["item"])
    uuid_to_item = {}
    ef = cfg_path(cfg, "data") / "exam_questions.json"
    if ef.exists():
        for e in json.loads(ef.read_text(encoding="utf-8")).get("exams", []):
            if e.get("uuid"):
                uuid_to_item[e["uuid"]] = e["item"]
    log.info("Loaded answers for %d quizzes/exams (%d exams identifiable by node)", len(quizzes), len(uuid_to_item))

    def which_quiz(sb):
        """Identify the open quiz/exam. The outline's active item is authoritative; question text is the
        fallback (16+ question texts are shared between quizzes, so text alone can pick the wrong one)."""
        texts, total = current_texts(sb)
        cf.leave(sb)
        try:
            item_id, _ = ol.split_id_title(ol.active_item_title(sb))
        except Exception:
            item_id = None
        if item_id and item_id in quizzes:
            return [item_id]
        try:                                   # checkpoint exams: identify by the open node's uuid
            node_item = uuid_to_item.get(sb.execute_script(JS_ACTIVE_NODE))
        except Exception:
            node_item = None
        if node_item and node_item in quizzes:
            return [node_item]
        cands = []
        for tx in texts:
            for it in norm_to_items.get(normalize(tx), []):
                if it not in cands:
                    cands.append(it)
        if not cands:
            return []
        if len(cands) > 1:
            if item_id and item_id in cands:   # outline id known: trust it
                return [item_id]
            if total:                          # else prefer the quiz whose length matches the counter
                sized = [it for it in cands if len(quizzes[it]["answers"]) == total]
                if len(sized) == 1:
                    return sized
                if sized:
                    cands = sized
            log.info("  ambiguous question (appears in %s) - showing all of them", ", ".join(cands))
        return cands

    with launch(cfg) as sb:
        open_course(sb, cfg)
        log.info("Ready. A green 'Fetch answers' button is pinned top-left. Open any quiz, then click it.")
        log.info("This tool does NOT press Start / select / submit / skip. Ctrl+C here to stop.")
        seen_clicks = run_js(sb, JS_BALL) or 0
        try:
            while True:
                clicks = run_js(sb, JS_BALL)
                if clicks is None:            # browser/tab gone
                    time.sleep(1.0)
                    continue
                if clicks > seen_clicks:
                    seen_clicks = clicks
                    items = which_quiz(sb)
                    if items:
                        blocks = []
                        for item in items:
                            info = quizzes[item]
                            label = info["title"] if item.startswith("exam-") else f"Quiz {item} — {info['title']}"
                            if len(items) > 1:
                                label = f"[{item}] {label}"
                            n_ans = sum(1 for r in info["answers"].values() if r.get("answer_texts") or r.get("raw_answer"))
                            label = f"{label}  <span style='opacity:.6;font-weight:400'>({n_ans}/{len(info['answers'])} answers)</span>"
                            blocks.append(answers_html(label, info["answers"]))
                        log.info("Fetch: %s", ", ".join(f"{i} ({quizzes[i]['title']})" for i in items))
                        note = ("<div style='background:#7a3b00;padding:8px;border-radius:6px;margin-bottom:10px'>"
                                "This question appears in more than one assessment. Both answer sets are shown — "
                                "use the one whose title matches the assessment you opened.</div>") if len(items) > 1 else ""
                        run_js(sb, JS_PANEL, note + "<hr style='border-color:#284a7a;margin:14px 0'>".join(blocks))
                    else:
                        log.info("Fetch: no known quiz question detected on screen")
                        run_js(sb, JS_PANEL,
                               "<div style='font-size:15px;font-weight:700;margin-bottom:6px'>No quiz detected</div>"
                               "<div>Open a graded quiz and get a <b>question</b> on screen (press Start yourself), "
                               "then click <b>Fetch answers</b> again.</div>")
                time.sleep(0.8)
        except KeyboardInterrupt:
            log.info("Stopping (Ctrl+C).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
