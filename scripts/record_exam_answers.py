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

# Read object-matching (drag-connect) questions. Each category button ends with its label letter
# ("Occurs first B"); each option button is prefixed with the category letter YOU connected it to
# ("done B The switch adds..."). We pair by matching letters -> YOUR pairing. Reads only what you connected.
JS_OBJMATCH_READ = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const oms = deepQ('object-matching-view').filter(vis);
return JSON.stringify(oms.map(m => ({
  question: dtext(deepQ('.component__body-inner', m)[0] || m),
  cats: deepQ('button.objectMatching-category-item', m).map(b => dtext(b)),
  opts: deepQ('button.objectMatching-option-item', m).map(b => dtext(b)),
})));
"""


def _parse_objmatch(om):
    """Turn the letter-badged category/option texts into ordered {prompt -> your answer} pairs."""
    cat_order, cat_label = [], {}       # label -> prompt, preserving category order
    for t in om.get("cats", []):
        m = re.match(r"^(.*?)\s+([A-Z])$", (t or "").strip())
        if m:
            cat_order.append(m.group(2))
            cat_label[m.group(2)] = m.group(1).strip()
    opt_by_letter = {}
    for t in om.get("opts", []):
        s = re.sub(r"^\s*done\s+", "", (t or "").strip(), flags=re.I)   # drop the "done" completion icon text
        m = re.match(r"^([A-Z])\s+(.*)$", s)                            # leading letter = the category you linked it to
        if m:
            opt_by_letter[m.group(1)] = m.group(2).strip()
    return [{"prompt": cat_label[L], "answer": opt_by_letter[L]} for L in cat_order if L in opt_by_letter]

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


# Read EVERY question component in DOM order (Q1..Qn) in one pass. Used on a COMPLETED exam, where all
# questions are rendered at once, so per-question strip-walking can't tell them apart (it kept re-reading the
# first object-matching view). DOM order == the exam's question order.
JS_READ_ALL = cf.JS_DEEP + r"""
const bodyText = (m) => { const b = deepQ('.mcq__body-inner, .matching__body-inner, .component__body-inner', m)[0]; return b ? dtext(b) : ''; };
const out = [];
for (const m of deepQ('mcq-view, matching-view, object-matching-view')) {
  const tag = m.tagName.toLowerCase();
  if (tag === 'mcq-view') {
    const opts = deepQ('.mcq__item', m).map(o => {
      const t = deepQ('.mcq__item-text-inner', o)[0];
      let text = t ? dtext(t) : '';
      for (const sr of (t ? deepQ('.screenReader-position-text', t) : [])) text = text.replace(clean(sr.textContent), '').trim();
      return {text, checked: o.getAttribute('aria-checked') === 'true', multiple: o.getAttribute('role') === 'checkbox'};
    });
    out.push({type: 'mcq', question: bodyText(m), options: opts});
  } else if (tag === 'matching-view') {
    const dds = deepQ('matching-dropdown-view', m).map(d => ({
      title: dtext(deepQ('.matching__item-title .matching__item-title_inner', d)[0] || deepQ('.matching__item-title', d)[0]),
      selected: dtext(deepQ('.js-dropdown-inner', d)[0]),
      options: deepQ('li.js-dropdown-list-item', d).map(li => {
        const inner = deepQ('.js-dropdown-list-item-inner', li)[0] || li; let text = dtext(inner);
        for (const sr of deepQ('.sr-only', inner)) text = text.replace(dtext(sr), ''); return {text: text.trim(), selected: li.getAttribute('aria-selected') === 'true'};
      }),
    }));
    out.push({type: 'matching', question: bodyText(m), dropdowns: dds});
  } else {
    out.push({type: 'object-matching', question: bodyText(m),
              cats: deepQ('button.objectMatching-category-item', m).map(b => dtext(b)),
              opts: deepQ('button.objectMatching-option-item', m).map(b => dtext(b))});
  }
}
return JSON.stringify(out);
"""


def _rec_from_component(comp, n):
    """Build a stored record from one component read of JS_READ_ALL (reads only YOUR selections)."""
    typ, q = comp.get("type"), comp.get("question") or ""
    base = {"n": n, "question": q or f"question-{n}", "question_norm": normalize(q or f"question-{n}")}
    if typ == "mcq":
        chosen = [o["text"] for o in comp.get("options", []) if o.get("checked")]
        mult = any(o.get("multiple") for o in comp.get("options", []))
        return {**base, "type": "multiple" if mult else "single",
                "answer_texts": chosen, "status": "recorded" if chosen else "unanswered"}
    if typ == "matching":
        pairs = []
        for d in comp.get("dropdowns", []):
            prompt = (d.get("title") or "").strip()
            choice = _dropdown_choice(d)
            if prompt and choice:
                pairs.append({"prompt": prompt, "answer": choice})
        raw = "; ".join(f"{p['prompt']} → {p['answer']}" for p in pairs)
        return {**base, "type": "matching", "answer_texts": [], "pairs": pairs, "raw_answer": raw,
                "status": "recorded" if pairs else "unanswered"}
    # object-matching
    pairs = _parse_objmatch(comp)
    raw = "; ".join(f"{p['prompt']} → {p['answer']}" for p in pairs)
    return {**base, "type": "object-matching", "answer_texts": [], "pairs": pairs, "raw_answer": raw,
            "status": "recorded" if pairs else "unanswered"}


def record_all_visible(sb, store, item, title, log):
    """One-pass capture of a COMPLETED exam: every question in DOM order (Q1..Qn), each distinct."""
    cf.enter(sb)
    comps = json.loads(sb.execute_script(JS_READ_ALL))
    added = 0
    for i, comp in enumerate(comps, 1):
        if store.record(item, title, _rec_from_component(comp, i)):
            added += 1
    if comps:
        store.save()
    log.info("  one-pass read: %d questions on the completed exam, %d recorded/updated", len(comps), added)
    return len(comps), added


# Read the question CURRENTLY ON SCREEN (largest area in the viewport), with YOUR selections. This is the
# reliable way to read one question at a time: inactive questions are off-screen (area ~0), so an exam with
# several matching questions no longer collapses onto the first one. Full mcq screen-reader text cleanup.
JS_READ_DISPLAYED = cf.JS_DEEP + r"""
const vh=window.innerHeight||9999, vw=window.innerWidth||9999;
const area=(e)=>{const r=e.getBoundingClientRect();const w=Math.max(0,Math.min(r.right,vw)-Math.max(r.left,0));const h=Math.max(0,Math.min(r.bottom,vh)-Math.max(r.top,0));return w*h;};
let best=null,bestA=0,bestTag=null;
for (const tag of ['mcq-view','matching-view','object-matching-view']){
  for (const m of deepQ(tag)){
    const b=deepQ('.mcq__body-inner,.matching__body-inner,.component__body-inner',m)[0]; if(!b) continue;
    const a=area(b); if(a>bestA){bestA=a;best=m;bestTag=tag;}
  }
}
if(!best||bestA<=0) return null;
const m=best,tag=bestTag,body=deepQ('.mcq__body-inner,.matching__body-inner,.component__body-inner',m)[0];
const question=body?dtext(body):'';
if(tag==='mcq-view'){
  const opts=deepQ('.mcq__item',m).map(o=>{const t=deepQ('.mcq__item-text-inner',o)[0];let text=t?dtext(t):'';for(const sr of (t?deepQ('.screenReader-position-text',t):[]))text=text.replace(clean(sr.textContent),'').trim();return {text,checked:o.getAttribute('aria-checked')==='true',multiple:o.getAttribute('role')==='checkbox'};});
  return JSON.stringify({type:'mcq',question,options:opts});
}
if(tag==='matching-view'){
  const dds=deepQ('matching-dropdown-view',m).map(d=>({title:dtext(deepQ('.matching__item-title .matching__item-title_inner',d)[0]||deepQ('.matching__item-title',d)[0]),selected:dtext(deepQ('.js-dropdown-inner',d)[0]),options:deepQ('li.js-dropdown-list-item',d).map(li=>{const inner=deepQ('.js-dropdown-list-item-inner',li)[0]||li;let text=dtext(inner);for(const sr of deepQ('.sr-only',inner))text=text.replace(dtext(sr),'');return{text:text.trim(),selected:li.getAttribute('aria-selected')==='true'};})}));
  return JSON.stringify({type:'matching',question,dropdowns:dds});
}
return JSON.stringify({type:'object-matching',question,cats:deepQ('button.objectMatching-category-item',m).map(b=>dtext(b)),opts:deepQ('button.objectMatching-option-item',m).map(b=>dtext(b))});
"""
JS_STRIP_MAX = cf.JS_DEEP + r"""
let mx=0; for(const b of deepQ('button.block-button')){const m=dtext(b).match(/(\d+)/); if(m)mx=Math.max(mx,Number(m[1]));} return mx;
"""


def _dropdown_choice(d):
    """The option YOU picked in a matching dropdown: prefer the aria-selected <li>, else the button's shown text."""
    for o in d.get("options", []):
        if o.get("selected"):
            return (o.get("text") or "").strip()
    s = (d.get("selected") or "").strip()
    # ignore the unselected placeholder ("Select...", "Choose...", "--", or an empty button)
    if not s or re.search(r"^\s*(select|choose|--|pick)\b", s, re.I) or s.lower() in ("", "select", "choose"):
        return ""
    return s


def read_active(sb):
    """Read the question currently on screen and YOUR answer, via the viewport reader (reliable with several
    matching questions). n comes from the active strip button. Reads only your selections."""
    cf.enter(sb)
    st = qx.secure_state(sb)
    total = None
    m = re.search(r"(\d+)\s+of\s+(\d+)", st.get("counter") or "")
    if m:
        total = int(m.group(2))
    n = st.get("active_q")
    rec = None
    try:
        raw = sb.execute_script(JS_READ_DISPLAYED)
        if raw and n is not None:
            rec = _rec_from_component(json.loads(raw), n)
    except Exception:
        rec = None
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
        """Store one answer (MCQ answer_texts OR matching raw_answer). Returns True if it added/changed something."""
        if not rec or rec.get("n") is None:
            return False
        if not (rec.get("answer_texts") or rec.get("raw_answer")):
            return False   # unanswered / not-yet-captured (incl. object-matching flagged needs_manual)
        slot = self.by_item.setdefault(item, {"title": title, "qs": {}})
        slot["title"] = title
        prev = slot["qs"].get(rec["n"])
        rec = {**rec, "recorded_at": datetime.now().isoformat(timespec="seconds")}
        if (prev and prev.get("answer_texts") == rec.get("answer_texts")
                and prev.get("raw_answer") == rec.get("raw_answer")
                and prev.get("question") == rec["question"]):
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
    """Save-time capture: click each question in the strip (Q1..Qn) and read the one on screen with the viewport
    reader. Works for a live attempt and a completed exam alike, and reads each matching question distinctly.
    Read-only navigation - never selects or submits. Empty reads are skipped so good answers are never wiped."""
    cf.enter(sb)
    try:
        mx = int(sb.execute_script(JS_STRIP_MAX) or 0)
    except Exception:
        mx = 0
    mx = max(mx, total or 0, 40)
    added = 0
    for n in range(1, mx + 1):
        cf.enter(sb)
        if sb.execute_script(JS_GOTO_Q, n) == "no-strip":
            continue
        time.sleep(0.45)
        raw = sb.execute_script(JS_READ_DISPLAYED)
        if not raw:
            continue
        if store.record(item, title, _rec_from_component(json.loads(raw), n)):
            added += 1
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
                            status(sb, f"💾 Saving {it} — reading through every question once…")
                            try:
                                added = final_sweep(sb, store, it, title_of.get(it, it), total_of.get(it), log)
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
                            shown = "; ".join(rec["answer_texts"]) if rec.get("answer_texts") else (rec.get("raw_answer") or "")
                            log.info("  %s Q%s [%s] = %s", recording, rec["n"], rec.get("type"), shown[:80])
                        cnt = store.count(recording)
                        tt = total_of.get(recording)
                        warn = ""
                        if rec and rec.get("type") in ("matching", "object-matching") and rec.get("status") == "recorded":
                            warn = f"<br><span style='opacity:.7'>matched: {len(rec.get('pairs') or [])} pair(s)</span>"
                        status(sb, f"🔴 Recording <b>{recording}</b> · Q{n or '?'}"
                                   f"{'/' + str(tt) if tt else ''} · <b>{cnt}</b> recorded{warn}<br>"
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
