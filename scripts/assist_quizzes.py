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


def format_answer(rec):
    """The answer value as HTML: MCQ texts, matching pairs (one per line), or a raw string."""
    if rec.get("answer_texts"):
        return "; ".join(rec["answer_texts"])
    if rec.get("pairs"):
        return "<br>".join(f"{p['prompt']} → {p['answer']}" for p in rec["pairs"])
    if rec.get("raw_answer"):
        return rec["raw_answer"].replace("; ", "<br>")
    return "<i>(answer this one yourself)</i>"


def answers_list(kq):
    rows = [f"<div style='margin:6px 0'><b>Q{rec['n']}.</b> {format_answer(rec)}</div>"
            for rec in sorted(kq.values(), key=lambda r: r.get("n") or 0)]
    return "".join(rows) or "<div>(no saved answers)</div>"


def render_panel(title, info, m):
    """m = {cur_text, rec, ...}. Show the answer for the question ON SCREEN (matched by text), then the full
    list as a reference. This is what makes shuffled checkpoint exams work - the number is meaningless, the
    text is the key."""
    if m.get("rec"):
        cur = ("<div style='background:#12401f;border:1px solid #66c430;border-radius:8px;padding:10px 12px;margin-bottom:10px'>"
               "<div style='opacity:.65;font-size:11px;text-transform:uppercase;letter-spacing:.5px'>Answer for the question on screen</div>"
               f"<div style='margin:5px 0 8px;opacity:.9'>{(m.get('cur_text') or '')[:200]}</div>"
               f"<div style='font-weight:700;color:#8ff06a;font-size:14px'>{format_answer(m['rec'])}</div></div>")
    elif m.get("cur_text"):
        cur = ("<div style='background:#5a1f1f;border-radius:8px;padding:10px 12px;margin-bottom:10px'>"
               "⚠️ This question isn't in the saved answers:<br>"
               f"<span style='opacity:.85'>{m['cur_text'][:160]}</span></div>")
    else:
        cur = "<div style='opacity:.7;margin-bottom:10px'>Waiting for a question on screen…</div>"
    full = ("<details style='margin-top:4px'><summary style='cursor:pointer;opacity:.65'>"
            "All saved answers (order differs from your attempt — go by the box above)</summary>"
            f"<div style='margin-top:8px'>{answers_list(info['answers'])}</div></details>")
    return f"<div style='font-weight:700;font-size:14px;margin-bottom:8px'>{title}</div>{cur}{full}"


# The active question when it's a matching/object-matching (no active mcq). Inactive questions stay in the DOM
# (scrolled off-screen, not display:none), so "first with a box" returns the wrong one when an exam has several
# matching questions. Pick the one with the largest area ON SCREEN (in the viewport) = the active question.
JS_VISIBLE_MATCH = cf.JS_DEEP + r"""
const vh = window.innerHeight || 9999, vw = window.innerWidth || 9999;
const onscreen = (e) => { const r = e.getBoundingClientRect();
  const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
  const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
  return w * h; };
let best = null, bestArea = 0;
for (const tag of ['matching-view', 'object-matching-view']) {
  for (const m of deepQ(tag)) {
    const b = deepQ('.matching__body-inner, .component__body-inner', m)[0];
    if (!b) continue;
    const a = onscreen(b);
    if (a > bestArea) { bestArea = a; best = b; }
  }
}
if (best && bestArea > 0) { const t = dtext(best); if (t) return t; }
return null;
"""


def current_texts(sb):
    """Text of the ONE question currently on screen, plus the total from the counter.
    MCQ -> the active mcq (by active_id). Otherwise the visible matching/object-matching question. No fallback
    to a lingering mcq (that mapped the wrong answer onto matching questions)."""
    cf.enter(sb)
    texts, total = [], None
    try:
        st = qx.secure_state(sb)
        m = re.search(r"(\d+)\s+of\s+(\d+)", st.get("counter") or "")
        total = int(m.group(2)) if m else None
        aid = st.get("active_id")
        if aid:
            q = next((x for x in qx.extract(sb, [aid]) if x.get("question")), None)
            if q:
                texts.append(q["question"])
        else:                                   # active question isn't an mcq -> it's matching/object-matching
            t = sb.execute_script(JS_VISIBLE_MATCH)
            if t:
                texts.append(t)
    except Exception:
        pass
    return texts, total


# ---------------------------------------------------------------------------------------------------------
# On-screen HIGHLIGHT (display only). Marks the option(s) that match YOUR saved answer with a green outline, and
# tags matching rows with "-> answer". It only changes styling / adds a non-clickable label: it never clicks,
# selects, or submits anything - you still do that yourself.
# ---------------------------------------------------------------------------------------------------------
JS_MARK_MCQ = cf.JS_BY_ID + r"""
// Mark the option(s) whose index is in arguments[1]: a green "ANSWER" tag in front of the option + a green
// outline around it. Styling/label only (pointer-events:none) - nothing is clicked or selected.
const m = byId(arguments[0]); if (!m) return 0;
const want = (arguments[1] || []).map(Number); let n = 0;
const paint = (e, on) => { if (!e) return;
  e.style.outline = on ? '3px solid #2e9e1f' : ''; e.style.outlineOffset = on ? '-3px' : '';
  e.style.boxShadow = on ? 'inset 10px 0 0 #2e9e1f' : ''; };
for (const o of deepQ('.mcq__item', m)) {
  const on = want.includes(Number(o.getAttribute('data-socialgoodpulse-index')));
  const lab = deepQ('label.mcq__item-label', o)[0] || o;
  let badge = deepQ('[data-assist-badge]', o)[0];
  if (on) {
    paint(o, true); paint(lab, true);
    if (!badge) {
      badge = document.createElement('span'); badge.setAttribute('data-assist-badge', '1');
      badge.textContent = '✔ ANSWER';
      badge.style.cssText = 'display:inline-block;flex:0 0 auto;align-self:center;margin:0 10px 0 14px;padding:3px 9px;'
        + 'background:#2e9e1f;color:#fff;font:700 12px/1.3 system-ui,Arial;border-radius:12px;white-space:nowrap;'
        + 'pointer-events:none;letter-spacing:.3px';
      lab.insertBefore(badge, lab.firstChild);     // in FRONT of the option, outside its text element
    }
    n++;
  } else {
    if (badge) badge.remove();
    if (o.style.outline || lab.style.outline) { paint(o, false); paint(lab, false); }
  }
}
return n;
"""
_TAG_CSS = ("display:block;margin-top:4px;font:700 12px/1.35 system-ui,Arial;color:#0b5d1e;background:#d9f7c9;"
            "border-left:4px solid #66c430;padding:2px 6px;border-radius:3px;pointer-events:none;text-align:left")
JS_MARK_ROWS = cf.JS_BY_ID + r"""
// arguments: modelid, [[key, answerText], ...], rowSelectorPrefix, titleSelector, css
const m = byId(arguments[0]); if (!m) return 0;
const css = arguments[4]; let n = 0;
for (const [key, ans] of (arguments[1] || [])) {
  if (!ans) continue;
  const row = deepQ(arguments[2] + '"' + key + '"]', m)[0]; if (!row) continue;
  const host = (arguments[3] ? deepQ(arguments[3], row)[0] : null) || row;
  let tag = deepQ('[data-assist-mark]', row)[0];
  if (!tag) { tag = document.createElement('span'); tag.setAttribute('data-assist-mark', '1'); tag.style.cssText = css; host.appendChild(tag); }
  const txt = '→ ' + ans;
  if (tag.textContent !== txt) tag.textContent = txt;
  n++;
}
return n;
"""
JS_ONSCREEN_MATCH_ID = cf.JS_DEEP + r"""
const vh = window.innerHeight || 9999, vw = window.innerWidth || 9999;
const onscreen = (e) => { const r = e.getBoundingClientRect();
  return Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0)) * Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0)); };
let best = null, bestArea = 0;
for (const tag of ['matching-view', 'object-matching-view']) {
  for (const m of deepQ(tag)) {
    const b = deepQ('.matching__body-inner, .component__body-inner', m)[0]; if (!b) continue;
    const a = onscreen(b); if (a > bestArea) { bestArea = a; best = m; }
  }
}
return best ? {tag: best.tagName.toLowerCase(), modelid: best.getAttribute('modelid')} : null;
"""


def _prompt_key(text):
    """Normalized prompt, ignoring a tag we appended earlier ("... -> answer") and a trailing category letter."""
    t = (text or "").split("→")[0].strip()
    return normalize(t), normalize(re.sub(r"\s+[A-Z]$", "", t))


def build_highlight(sb, rec):
    """Work out what to mark for the question on screen. Returns (script, args) to run, or None.
    Reads the page only; the returned script only styles/labels elements."""
    cf.enter(sb)
    aid = qx.secure_state(sb).get("active_id")
    if aid and rec.get("answer_texts"):
        q = next((x for x in qx.extract(sb, [aid]) if x.get("options")), None)
        if not q:
            return None
        want = {normalize(t) for t in rec["answer_texts"]}
        idx = [o["index"] for o in q["options"] if normalize(o["text"]) in want]
        return (JS_MARK_MCQ, [aid, idx]) if idx else None
    if not aid and rec.get("pairs"):
        info = sb.execute_script(JS_ONSCREEN_MATCH_ID)
        if not info or not info.get("modelid"):
            return None
        pairs = {normalize(p["prompt"]): p["answer"] for p in rec["pairs"]}

        def answer_for(text):
            k1, k2 = _prompt_key(text)
            return pairs.get(k1) or pairs.get(k2)

        mid = info["modelid"]
        if info["tag"] == "matching-view":
            mq = next(iter(qx.extract_matching(sb, [mid])), None)
            rows = [[str(d["index"]), answer_for(d.get("title"))] for d in (mq or {}).get("dropdowns", [])]
            return (JS_MARK_ROWS, [mid, rows, 'matching-dropdown-view[index=', '.matching__item-title', _TAG_CSS])
        oq = next(iter(qx.extract_object_matching(sb, [mid])), None)
        rows = [[str(c["id"]), answer_for(c.get("text"))] for c in (oq or {}).get("categories", [])]
        return (JS_MARK_ROWS, [mid, rows, 'button.objectMatching-category-item[data-id=', '', _TAG_CSS])
    return None


def apply_highlight(sb, call):
    """Run a cached highlight call (idempotent - safe to repeat every poll so marks survive a re-render)."""
    if not call:
        return 0
    try:
        cf.enter(sb)
        return sb.execute_script(call[0], *call[1]) or 0
    except Exception:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course", default=None, help="course key from config/courses.json (default: ask)")
    ap.add_argument("--profile", default=None, help="account profile name -> Chrome profile dir profile_<name>")
    ap.add_argument("--fresh-login", action="store_true", help="clear cookies in the profile first (sign in again)")
    ap.add_argument("--keep-session", action="store_true", help="remember the login between runs")
    ap.add_argument("--no-highlight", action="store_true", help="panel only; do not mark the answer on the page")
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
    norm_to_items, quizzes, by_item_norm = {}, {}, {}
    for fname in ("quiz_answer_key.json", "exam_answer_key.json"):
        f = cfg_path(cfg, "data") / fname
        if not f.exists():
            continue
        key = json.loads(f.read_text(encoding="utf-8"))
        for q in key["quizzes"]:
            quizzes[q["item"]] = {"title": q["title"], "answers": {qq["n"]: qq for qq in q["questions"]}}
            by_item_norm[q["item"]] = {qq["question_norm"]: qq for qq in q["questions"] if qq.get("question_norm")}
            for qq in q["questions"]:
                norm_to_items.setdefault(normalize(qq["question"]), []).append(q["item"])
    uuid_to_item = {}
    ef = cfg_path(cfg, "data") / "exam_questions.json"
    if ef.exists():
        for e in json.loads(ef.read_text(encoding="utf-8")).get("exams", []):
            if e.get("uuid"):
                uuid_to_item[e["uuid"]] = e["item"]
    log.info("Loaded answers for %d quizzes/exams (%d exams identifiable by node)", len(quizzes), len(uuid_to_item))

    def identify(sb, texts, total):
        """Which quiz/exam is open? Outline item id -> exam node uuid -> question-text fallback."""
        cf.leave(sb)
        try:
            item_id, _ = ol.split_id_title(ol.active_item_title(sb))
        except Exception:
            item_id = None
        if item_id and item_id in quizzes:
            return item_id
        try:
            node_item = uuid_to_item.get(sb.execute_script(JS_ACTIVE_NODE))
        except Exception:
            node_item = None
        if node_item and node_item in quizzes:
            return node_item
        cands = []
        for tx in texts:
            for it in norm_to_items.get(normalize(tx), []):
                if it not in cands:
                    cands.append(it)
        if len(cands) == 1:
            return cands[0]
        if cands and total:
            sized = [it for it in cands if len(quizzes[it]["answers"]) == total]
            if len(sized) == 1:
                return sized[0]
        return None   # can't safely disambiguate

    def read_and_match(sb):
        """Read the question on screen, identify the quiz/exam, and match THIS question to its saved answer
        (by text, so shuffled exams work). Returns {item, cur_text, cur_norm, rec, total}."""
        texts, total = current_texts(sb)          # enters the content frame
        item = identify(sb, texts, total)         # leaves the frame
        cur_text = cur_norm = rec = None
        if item:
            for tx in texts:
                r = by_item_norm.get(item, {}).get(normalize(tx))
                if r:
                    cur_text, cur_norm, rec = tx, normalize(tx), r
                    break
            if rec is None and texts:             # question detected but not matched
                cur_text = texts[0]
        return {"item": item, "cur_text": cur_text, "cur_norm": cur_norm, "rec": rec, "total": total}

    with launch(cfg) as sb:
        open_course(sb, cfg)
        log.info("Ready. A green 'Fetch answers' button is pinned top-left. Open a quiz/exam, get a question on")
        log.info("screen, then click it once — the panel then FOLLOWS you, showing each question's answer as you go.")
        log.info("It also outlines your saved answer on the page (display only; --no-highlight turns it off).")
        log.info("This tool does NOT press Start / select / submit / skip. Ctrl+C here to stop.")
        seen_clicks = run_js(sb, JS_BALL) or 0
        following = False
        last_key = None
        hl_call = None          # cached (script, args) for the question on screen
        try:
            while True:
                clicks = run_js(sb, JS_BALL)
                if clicks is None:            # browser/tab gone
                    time.sleep(1.0)
                    continue
                if clicks > seen_clicks:      # a click arms follow-mode (and forces a refresh)
                    seen_clicks = clicks
                    following = True
                    last_key = None
                if following:
                    try:
                        m = read_and_match(sb)
                    except Exception as e:  # noqa: BLE001
                        log.debug("match error: %s", str(e).splitlines()[0][:120])
                        m = None
                    if m and m["item"]:
                        info = quizzes[m["item"]]
                        key = (m["item"], m["cur_norm"], bool(m["rec"]))
                        if key != last_key:
                            last_key = key
                            hl_call = None
                            title = (info["title"] if m["item"].startswith("exam-")
                                     else f"Quiz {m['item']} — {info['title']}")
                            run_js(sb, JS_PANEL, render_panel(title, info, m))
                            if m["rec"]:
                                log.info("%s: matched question -> %s", m["item"],
                                         (format_answer(m["rec"]).replace("<br>", " / "))[:80])
                            elif m["cur_text"]:
                                log.info("%s: question on screen not in saved answers", m["item"])
                        if m["rec"] and not args.no_highlight:
                            if hl_call is None:           # options can render a beat after the question text
                                try:
                                    hl_call = build_highlight(sb, m["rec"])
                                except Exception as e:  # noqa: BLE001
                                    log.debug("highlight build error: %s", str(e).splitlines()[0][:120])
                            apply_highlight(sb, hl_call)
                    elif last_key is None:    # armed but nothing identified yet
                        run_js(sb, JS_PANEL,
                               "<div style='font-weight:700;margin-bottom:6px'>Following…</div>"
                               "<div>Open a quiz/exam and get a <b>question</b> on screen; I'll show its answer here.</div>")
                        last_key = ("waiting",)
                time.sleep(0.7)
        except KeyboardInterrupt:
            log.info("Stopping (Ctrl+C).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
