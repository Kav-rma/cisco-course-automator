"""
Record YOUR checkpoint-exam answers as you solve them (manual, boundary-safe).

You solve each checkpoint exam yourself. This script never selects an option and never submits - it only READS
which options you have checked and saves the option TEXT (not A/B/C/D) into exam_answer_key.json, in the same
format the "Fetch answers" button (assist_quizzes.py) already reads. It deliberately ignores the page's built-in
correct-answer key: it records YOUR selections, nothing else.

How to use:
  1. run it, pick the course
  2. a panel pins top-left with [▶ Start recording] / status / [■ Save & finish]
  3. open a checkpoint exam, press Cisco's own Start, then click ▶ Start recording
  4. solve the exam normally - the panel shows "Recording exam-N · Q x/y · z recorded" and autosaves each answer
  5. click ■ Save & finish (it reads through the strip once to catch anything missed), then do the next exam
  6. Ctrl+C in the terminal also saves

Run:  .venv\\Scripts\\python.exe scripts\\record_exam_answers.py [--course KEY]
      [--profile NAME] [--fresh-login] [--keep-session]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content_frame as cf  # noqa: E402
from core import question_extractor as qx  # noqa: E402
from core.browser import launch, open_course  # noqa: E402
from core.config import load_config, path as cfg_path  # noqa: E402
from core.logger import get_logger  # noqa: E402
from core.matcher import normalize  # noqa: E402
from extract_quizzes import JS_GOTO_Q  # noqa: E402  (read-only strip navigation, never answers)

# The control panel: two buttons + a status line, all in the TOP document. Buttons only set window flags;
# Python reads them and does the (read-only) work.
JS_PANEL = r"""
let p = document.getElementById('rec-panel');
if (!p) {
  p = document.createElement('div'); p.id = 'rec-panel';
  p.style.cssText = 'position:fixed;left:12px;top:12px;z-index:2147483647;width:320px;'
    + 'background:#0d274d;color:#fff;font:13px/1.5 system-ui,Arial;padding:12px 14px;border-radius:10px;'
    + 'box-shadow:0 6px 24px rgba(0,0,0,.4);border:2px solid #66c430;';
  p.innerHTML =
      '<div style="font-weight:700;font-size:14px;margin-bottom:8px">📝 Record my checkpoint answers</div>'
    + '<div id="rec-status" style="margin-bottom:10px;min-height:34px">Idle. Open a checkpoint exam, press '
    + '<b>Start</b>, then click <b>▶ Start recording</b>.</div>'
    + '<button id="rec-start" style="cursor:pointer;background:#66c430;color:#0d274d;border:0;font-weight:700;'
    + 'padding:7px 12px;border-radius:7px;margin-right:6px">▶ Start recording</button>'
    + '<button id="rec-stop" style="cursor:pointer;background:#e0664b;color:#fff;border:0;font-weight:700;'
    + 'padding:7px 12px;border-radius:7px">■ Save &amp; finish</button>';
  document.body.appendChild(p);
  window.__recCmd = null; window.__recSeq = 0;
  p.querySelector('#rec-start').onclick = function(){ window.__recCmd = 'start'; window.__recSeq++; };
  p.querySelector('#rec-stop').onclick  = function(){ window.__recCmd = 'stop';  window.__recSeq++; };
}
return JSON.stringify({cmd: window.__recCmd, seq: window.__recSeq || 0});
"""
JS_STATUS = r"""
const s = document.getElementById('rec-status'); if (s) s.innerHTML = arguments[0]; return true;
"""
# checkpoint exams are top-level graded nodes with no item id -> identify by the open node's uuid
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


def top(sb):
    sb.driver.switch_to.default_content()


def panel(sb):
    top(sb)
    try:
        return json.loads(sb.execute_script(JS_PANEL))
    except Exception:
        return {"cmd": None, "seq": 0}


def status(sb, html):
    top(sb)
    try:
        sb.execute_script(JS_STATUS, html)
    except Exception:
        pass


def read_active(sb):
    """Read the active question and the option text(s) YOU have checked. Returns a record or None.
    Only reads `checked` (your selection); the model's correct answer is intentionally not used."""
    cf.enter(sb)
    st = qx.secure_state(sb)
    total = None
    m = re.search(r"(\d+)\s+of\s+(\d+)", st.get("counter") or "")
    if m:
        total = int(m.group(2))
    n = st.get("active_q")
    ids = st.get("mcq_ids") or []
    aid = st.get("active_id") or (ids[0] if ids else None)
    rec = None
    if aid:
        qs = qx.extract(sb, [aid])
        q = next((x for x in qs if x.get("options")), None)
        if q:
            chosen = [o["text"] for o in sorted(q["options"], key=lambda o: o["index"]) if o.get("checked")]
            rec = {"n": n, "question": q["question"], "question_norm": normalize(q["question"]),
                   "type": q.get("type", "single"),
                   "answer_texts": chosen, "status": "recorded" if chosen else "unanswered"}
    return rec, n, total


class Store:
    """Merges into exam_answer_key.json (what the Fetch-answers button reads), keyed by item + question text."""

    def __init__(self, path: Path, course: str):
        self.path = path
        self.data = {"course": course, "quizzes": []}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        self.by_item = {}
        for q in self.data.get("quizzes", []):
            self.by_item[q["item"]] = {"title": q.get("title", q["item"]),
                                       "qs": {qq["n"]: qq for qq in q.get("questions", [])}}

    def count(self, item):
        return sum(1 for r in self.by_item.get(item, {}).get("qs", {}).values()
                   if r.get("answer_texts") or r.get("raw_answer"))

    def record(self, item, title, rec) -> bool:
        """Store one answer. Returns True if it added/changed something."""
        if not rec or not rec.get("answer_texts") or rec.get("n") is None:
            return False
        slot = self.by_item.setdefault(item, {"title": title, "qs": {}})
        slot["title"] = title
        prev = slot["qs"].get(rec["n"])
        rec = {**rec, "recorded_at": datetime.now().isoformat(timespec="seconds")}
        if prev and prev.get("answer_texts") == rec["answer_texts"] and prev.get("question") == rec["question"]:
            return False
        slot["qs"][rec["n"]] = rec
        return True

    def save(self):
        self.data["quizzes"] = [
            {"item": item, "title": s["title"],
             "questions": sorted(s["qs"].values(), key=lambda r: (r.get("n") or 0))}
            for item, s in sorted(self.by_item.items(),
                                  key=lambda kv: int(kv[0].split("-")[1]) if kv[0].startswith("exam-") else 0)]
        self.data["saved_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def final_sweep(sb, store, item, title, total, log):
    """At Save time (you're done selecting): read through the strip once and capture any answer not caught live.
    Read-only - clicks the question-number strip to view each question, never selects or submits."""
    added = 0
    for n in range(1, (total or 40) + 1):
        cf.enter(sb)
        if sb.execute_script(JS_GOTO_Q, n) == "no-strip":
            continue
        time.sleep(0.35)
        rec, _, _ = read_active(sb)
        if rec and store.record(item, title, rec):
            added += 1
    if added:
        store.save()
    return added


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
    log = get_logger("record_exam", cfg_path(cfg, "logs"), cfg.get("debug", True))
    ddir = cfg_path(cfg, "data")

    # exam metadata: uuid -> exam-N, exam-N -> title, exam-N -> total questions
    uuid_to_item, title_of, total_of = {}, {}, {}
    ef = ddir / "exam_questions.json"
    if ef.exists():
        for e in json.loads(ef.read_text(encoding="utf-8")).get("exams", []):
            if e.get("uuid"):
                uuid_to_item[e["uuid"]] = e["item"]
            title_of[e["item"]] = e.get("title", e["item"])
            total_of[e["item"]] = e.get("total_reported") or len(e.get("questions", [])) or None
    if not uuid_to_item:
        log.warning("No exam_questions.json with uuids found - run extract_exams.py first so checkpoints can be "
                    "identified. You can still record, but the exam id may be unknown.")

    store = Store(ddir / "exam_answer_key.json", cfg["course"]["name"])
    log.info("Loaded %d exam(s) already in the key. Answers save to %s",
             len(store.by_item), (ddir / "exam_answer_key.json"))

    recording = None   # the exam-N currently being recorded, or None
    last_seq = 0
    with launch(cfg) as sb:
        open_course(sb, cfg)
        panel(sb)
        status(sb, "Idle. Open a checkpoint exam, press <b>Start</b>, then click <b>▶ Start recording</b>.")
        log.info("Panel ready. Open a checkpoint, press Start, then ▶ Start recording. Ctrl+C to stop (saves).")
        try:
            while True:
                p = panel(sb)
                if p.get("seq", 0) != last_seq:
                    last_seq = p["seq"]
                    cmd = p.get("cmd")
                    if cmd == "start":
                        top(sb)
                        item = uuid_to_item.get(sb.execute_script(JS_ACTIVE_NODE))
                        cf.enter(sb)
                        started = bool(qx.secure_state(sb).get("mcq_ids"))
                        if not item:
                            status(sb, "⚠️ Couldn't tell which checkpoint this is. Open a <b>checkpoint exam</b> "
                                       "node and press its <b>Start</b>, then click ▶ again.")
                        elif not started:
                            status(sb, f"⚠️ {title_of.get(item, item)} detected, but no question is on screen. "
                                       f"Press the exam's <b>Start</b> first, then click ▶ again.")
                        else:
                            recording = item
                            log.info("Recording %s (%s)", item, title_of.get(item, item))
                            status(sb, f"🔴 Recording <b>{item}</b> — {title_of.get(item,'')}<br>Solve normally; "
                                       f"I save each answer as you pick it.")
                    elif cmd == "stop":
                        if recording:
                            it = recording
                            recording = None
                            status(sb, f"💾 Saving {it} — reading through the questions once…")
                            total = total_of.get(it)
                            try:
                                added = final_sweep(sb, store, it, title_of.get(it, it), total, log)
                            except Exception as e:  # noqa: BLE001
                                added = 0
                                log.warning("final sweep error: %s", str(e).splitlines()[0][:160])
                            store.save()
                            log.info("Saved %s: %d answers (%d from final sweep)", it, store.count(it), added)
                            status(sb, f"✅ Saved <b>{it}</b>: {store.count(it)} answers. "
                                       f"Open the next checkpoint and click ▶, or Ctrl+C to finish.")
                        else:
                            store.save()
                            status(sb, "✅ Saved. Open a checkpoint and click ▶ Start recording.")

                if recording:
                    try:
                        rec, n, total = read_active(sb)
                        if total:
                            total_of[recording] = total
                        if rec and store.record(recording, title_of.get(recording, recording), rec):
                            store.save()
                            log.info("  %s Q%s = %s", recording, rec["n"], "; ".join(rec["answer_texts"])[:70])
                        cnt = store.count(recording)
                        tt = total_of.get(recording)
                        status(sb, f"🔴 Recording <b>{recording}</b> · Q{n or '?'}"
                                   f"{'/' + str(tt) if tt else ''} · <b>{cnt}</b> recorded<br>"
                                   f"<span style='opacity:.7'>Solve normally, then ■ Save &amp; finish.</span>")
                    except Exception as e:  # noqa: BLE001
                        log.debug("poll error: %s", str(e).splitlines()[0][:120])
                time.sleep(0.7)
        except KeyboardInterrupt:
            if recording:
                try:
                    final_sweep(sb, store, recording, title_of.get(recording, recording),
                                total_of.get(recording), log)
                except Exception:
                    pass
            store.save()
            log.info("Stopped (Ctrl+C). Saved to %s", ddir / "exam_answer_key.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
