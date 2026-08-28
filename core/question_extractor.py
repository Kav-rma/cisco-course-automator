"""
Knowledge-check question extraction + interaction (inside the content frame).

Verified markup (Adapt-style mcq-view, 2026-08-19):
  mcq-view[modelid]           component host; .model (Backbone-like) -> get('_items')[i]._shouldBeSelected / _index
  .mcq__body-inner            question text
  .mcq__item[role=radio|checkbox][data-socialgoodpulse-index=N]   option (N == authoring _index; DISPLAY ORDER IS SHUFFLED)
     label.mcq__item-label    click target; .mcq__item-text-inner holds text (+ a screen-reader "1 of 4" span to strip)
  buttons-view button.js-btn-action  Submit;  button.js-btn-feedback  Show feedback
  marking: .mcq__item gets is-correct / is-incorrect after submit; component div gets is-complete
"""
from __future__ import annotations

import json

from . import content_frame as cf
from .browser import wait_until

JS_EXTRACT = cf.JS_BY_ID + r"""
// arguments[0]: null (whole page) | a scope modelid (host) | an array of mcq modelids (a unit may span hosts)
const arg = arguments[0];
let mcqs;
if (Array.isArray(arg)) mcqs = arg.map(byId).filter(Boolean);
else { const scope = arg ? byId(arg) : null; mcqs = deepQ('mcq-view', scope || undefined); }
const out = mcqs.map(m => {
  const model = m.model || null;
  let items = [];
  try { const raw = (model && model.get) ? model.get('_items') : (model && model._items); items = raw || []; } catch (e) {}
  const correct = items.filter(i => i._shouldBeSelected).map(i => i._index);
  const opts = deepQ('.mcq__item', m).map(o => {
    const t = deepQ('.mcq__item-text-inner', o)[0];
    const sr = t ? Array.from(t.querySelectorAll('.screenReader-position-text')).map(s => s.textContent) : [];
    let text = t ? dtext(t) : '';
    for (const s of sr) text = text.replace(clean(s), '').trim();
    return {index: Number(o.getAttribute('data-socialgoodpulse-index')), text, role: o.getAttribute('role'),
            checked: o.getAttribute('aria-checked') === 'true', correct_mark: /is-correct/.test(cls(o)) ? true : (/is-incorrect/.test(cls(o)) ? false : null)};
  });
  const q = deepQ('.mcq__body-inner', m)[0];
  const hv = deepQ('heading-view', m)[0];
  const comp = stateDiv(m);
  const submit = deepQ('button.js-btn-action', m)[0];
  const fbBtn = deepQ('button.js-btn-feedback', m)[0];
  const marked = opts.some(o => o.correct_mark !== null);
  const fb = deepQ('.mcq__feedback, .notify__content, [class*="feedback__"]', m).map(dtext).filter(Boolean);
  let selectable = null; try { selectable = model && model.get ? model.get('_selectable') : null; } catch (e) {}
  return {modelid: m.getAttribute('modelid'), heading: hv ? hv.getAttribute('headingtitle') : null,
          question: q ? dtext(q) : '', type: opts.length && opts[0].role === 'checkbox' ? 'multiple' : 'single',
          selectable, options: opts, correct_indices: correct,
          correct_texts: items.filter(i => i._shouldBeSelected).map(i => clean(i.text)),
          model_option_texts: items.map(i => ({index: i._index, text: clean(i.text)})),
          complete: completion(comp), submitted: marked || (!!submit && submit.disabled && !!fbBtn && !fbBtn.disabled),
          submit_enabled: !!submit && !submit.disabled, feedback: fb.slice(0, 5)};
});
return JSON.stringify(out);
"""

JS_SELECT = cf.JS_BY_ID + r"""
const m = byId(arguments[0]); if (!m) return 'no-mcq';
const o = deepQ('.mcq__item[data-socialgoodpulse-index="' + arguments[1] + '"]', m)[0]; if (!o) return 'no-option';
o.scrollIntoView({block: 'center', behavior: 'instant'});
const lab = deepQ('label.mcq__item-label', o)[0] || o;
lab.click(); return 'ok';
"""
JS_IS_CHECKED = cf.JS_BY_ID + r"""
const m = byId(arguments[0]); if (!m) return false;
const o = deepQ('.mcq__item[data-socialgoodpulse-index="' + arguments[1] + '"]', m)[0];
return !!o && o.getAttribute('aria-checked') === 'true';
"""
JS_SUBMIT = cf.JS_BY_ID + r"""
const m = byId(arguments[0]); if (!m) return 'no-mcq';
const b = deepQ('button.js-btn-action', m)[0]; if (!b) return 'no-submit';
if (b.disabled) return 'disabled';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""


def extract(sb, scope=None) -> list[dict]:
    """scope: None (whole page), a host modelid, or a list of mcq-view modelids."""
    return json.loads(sb.execute_script(JS_EXTRACT, scope))


def question_ids(detection) -> list[str]:
    """MCQ components only (the extractor/handler understand mcq-view); other question types are reported separately."""
    return [c["modelid"] for c in detection.components if c["tag"] == "mcq-view"]


def unsupported_question_components(detection) -> list[dict]:
    from .page_detector import QUESTION_TAGS
    return [c for c in detection.components if c["tag"] in QUESTION_TAGS and c["tag"] not in ("mcq-view", "matching-view", "object-matching-view")]


def select_option(sb, mcq_id: str, index: int, timeout: float) -> bool:
    if sb.execute_script(JS_IS_CHECKED, mcq_id, index):
        return True
    r = sb.execute_script(JS_SELECT, mcq_id, index)
    if r != "ok":
        return False
    return wait_until(sb, lambda s: s.execute_script(JS_IS_CHECKED, mcq_id, index), timeout, what=f"option {index} checked")


def submit(sb, mcq_id: str, timeout: float) -> str:
    r = sb.execute_script(JS_SUBMIT, mcq_id)
    if r != "ok":
        return r
    ok = wait_until(sb, lambda s: next((q for q in extract(s) if q["modelid"] == mcq_id), {}).get("submitted"), timeout, what="mcq submitted")
    return "ok" if ok else "no-marking"


# ---- Matching questions (matching-view): verified 2026-08-20 on 9.2.7 ----
#   model.get('_items')[i] = {text, _options: [{text, _isCorrect, _index}], _index}
#   matching-dropdown-view[index=i] > button.js-dropdown-btn (opens) > ul.js-dropdown-list > li.js-dropdown-list-item[data-index=j]
#   selected text shows in .js-dropdown-inner; Submit = buttons-view button.js-btn-action (same as mcq)
JS_EXTRACT_MATCHING = cf.JS_BY_ID + r"""
const arg = arguments[0];
let views;
if (Array.isArray(arg)) views = arg.map(byId).filter(Boolean);
else { const scope = arg ? byId(arg) : null; views = deepQ('matching-view', scope || undefined); }
const out = views.map(m => {
  const model = m.model || null; let items = [];
  try { items = (model && model.get ? model.get('_items') : (model && model._items)) || []; } catch (e) {}
  const q = deepQ('.matching__body-inner, .component__body-inner', m)[0];
  const hv = deepQ('heading-view', m)[0];
  const comp = stateDiv(m);
  const dds = deepQ('matching-dropdown-view', m).map(d => ({
    index: Number(d.getAttribute('index')),
    title: dtext(deepQ('.matching__item-title .matching__item-title_inner', d)[0] || deepQ('.matching__item-title', d)[0]),
    selected: dtext(deepQ('.js-dropdown-inner', d)[0]),
    options: deepQ('li.js-dropdown-list-item', d).map(li => { const inner = deepQ('.js-dropdown-list-item-inner', li)[0] || li;
      let text = dtext(inner); for (const sr of deepQ('.sr-only', inner)) text = text.replace(dtext(sr), ''); text = text.replace(/[\s,]+$/, '').trim();
      return {index: Number(li.getAttribute('data-index')), text, selected: li.getAttribute('aria-selected') === 'true'}; }),
    correct_mark: /is-correct/.test(cls(d)) ? true : (/is-incorrect/.test(cls(d)) ? false : null),
  }));
  const submit = deepQ('button.js-btn-action', m)[0];
  const fbBtn = deepQ('button.js-btn-feedback', m)[0];
  return {modelid: m.getAttribute('modelid'), heading: hv ? hv.getAttribute('headingtitle') : null,
          question: q ? dtext(q) : '', type: 'matching',
          items: items.map(i => ({index: i._index, text: clean(i.text), options: (i._options || []).map(o => ({index: o._index, text: clean(o.text), correct: !!o._isCorrect}))})),
          dropdowns: dds, complete: completion(comp),
          submitted: dds.some(d => d.correct_mark !== null) || (!!submit && submit.disabled && !!fbBtn && !fbBtn.disabled),
          submit_enabled: !!submit && !submit.disabled};
});
return JSON.stringify(out);
"""

JS_MATCH_SELECT = cf.JS_BY_ID + r"""
const m = byId(arguments[0]); if (!m) return 'no-view';
const d = deepQ('matching-dropdown-view[index="' + arguments[1] + '"]', m)[0]; if (!d) return 'no-dropdown';
const step = arguments[3];
if (step === 'open') { const b = deepQ('button.js-dropdown-btn', d)[0]; if (!b) return 'no-btn'; b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok'; }
if (step === 'pick') {
  const li = deepQ('li.js-dropdown-list-item[data-index="' + arguments[2] + '"]', d)[0]; if (!li) return 'no-option';
  const inner = deepQ('.js-dropdown-list-item-inner', li)[0] || li; inner.click(); return 'ok';
}
if (step === 'check') { const li = deepQ('li.js-dropdown-list-item[data-index="' + arguments[2] + '"]', d)[0]; return !!li && li.getAttribute('aria-selected') === 'true'; }
if (step === 'isopen') { const b = deepQ('button.js-dropdown-btn', d)[0]; return !!b && b.getAttribute('aria-expanded') === 'true'; }
return 'unknown';
"""


def extract_matching(sb, scope=None) -> list[dict]:
    return json.loads(sb.execute_script(JS_EXTRACT_MATCHING, scope))


def matching_ids(detection) -> list[str]:
    return [c["modelid"] for c in detection.components if c["tag"] == "matching-view"]


def select_matching(sb, view_id: str, item_index: int, option_index: int, timeout: float) -> bool:
    """Open the item's dropdown, pick the option, verify it is selected."""
    if sb.execute_script(JS_MATCH_SELECT, view_id, item_index, option_index, "check"):
        return True
    sb.execute_script(JS_MATCH_SELECT, view_id, item_index, option_index, "open")
    wait_until(sb, lambda s: s.execute_script(JS_MATCH_SELECT, view_id, item_index, option_index, "isopen"), 3, poll=0.2, what="dropdown open")
    sb.execute_script(JS_MATCH_SELECT, view_id, item_index, option_index, "pick")
    return wait_until(sb, lambda s: s.execute_script(JS_MATCH_SELECT, view_id, item_index, option_index, "check"), timeout, poll=0.2, what="option selected")


def submit_view(sb, view_id: str, timeout: float, extractor) -> str:
    """Submit any question view (mcq or matching) and wait for marking."""
    r = sb.execute_script(JS_SUBMIT, view_id)
    if r != "ok":
        return r
    ok = wait_until(sb, lambda s: next((q for q in extractor(s, [view_id])), {}).get("submitted"), timeout, what="submitted")
    return "ok" if ok else "no-marking"


# ---- Object matching (object-matching-view): verified 2026-08-20 on 38.1.11 ----
#   model._items[i] = {question, answer}; categories button.objectMatching-category-item[data-id=i] (A, B, C...)
#   options   button.objectMatching-option-item[data-id=j]  where data-id j == index of the item it answers
#   interaction: click a category, then click an option -> pair; Submit = buttons-view button.js-btn-action
JS_EXTRACT_OBJMATCH = cf.JS_BY_ID + r"""
const arg = arguments[0];
let views;
if (Array.isArray(arg)) views = arg.map(byId).filter(Boolean);
else { const scope = arg ? byId(arg) : null; views = deepQ('object-matching-view', scope || undefined); }
return JSON.stringify(views.map(m => {
  const model = m.model || null; let items = [];
  try { items = (model && model.get ? model.get('_items') : (model && model._items)) || []; } catch (e) {}
  const comp = stateDiv(m);
  const submit = deepQ('button.js-btn-action', m)[0];
  const fbBtn = deepQ('button.js-btn-feedback', m)[0];
  return {modelid: m.getAttribute('modelid'), type: 'object-matching',
          question: dtext(deepQ('.component__body-inner', m)[0]),
          items: items.map(i => ({index: i._index, question: clean(i.question), answer: clean(i.answer)})),
          categories: deepQ('button.objectMatching-category-item', m).map(b => ({id: b.getAttribute('data-id'), text: dtext(deepQ('.category-item-text', b)[0]), cls: cls(b)})),
          options: deepQ('button.objectMatching-option-item', m).map(b => ({id: b.getAttribute('data-id'), text: dtext(deepQ('.category-item-text', b)[0]), cls: cls(b)})),
          complete: completion(comp), submit_enabled: !!submit && !submit.disabled,
          submitted: (!!submit && submit.disabled && !!fbBtn && !fbBtn.disabled)};
}));
"""
JS_OBJMATCH_CLICK = cf.JS_BY_ID + r"""
const m = byId(arguments[0]); if (!m) return 'no-view';
const sel = arguments[1] === 'category' ? 'button.objectMatching-category-item[data-id="' + arguments[2] + '"]'
                                        : 'button.objectMatching-option-item[data-id="' + arguments[2] + '"]';
const b = deepQ(sel, m)[0]; if (!b) return 'no-target';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""


def extract_object_matching(sb, scope=None) -> list[dict]:
    return json.loads(sb.execute_script(JS_EXTRACT_OBJMATCH, scope))


def object_matching_ids(detection) -> list[str]:
    return [c["modelid"] for c in detection.components if c["tag"] == "object-matching-view"]


def object_matching_pair(sb, view_id: str, item_id: str) -> tuple[str, str]:
    r1 = sb.execute_script(JS_OBJMATCH_CLICK, view_id, "category", item_id)
    import time as _t
    _t.sleep(0.25)
    r2 = sb.execute_script(JS_OBJMATCH_CLICK, view_id, "option", item_id)
    _t.sleep(0.25)
    return r1, r2


# ---- CCNA "secure one question" check (adaptive-start-screen-view + one visible mcq-view at a time) ----
# Verified 2026-08-22 on 1.5.11: question strip (button.block-button "Q1"...), counter text "1 of 6 Questions",
# bottom bar: inputs "Skip Question"/"Skip All", button.submit-button (disabled until an option is selected).
JS_SECURE_STATE = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const mcqs = deepQ('mcq-view').filter(vis);
const sub = deepQ('button.submit-button').filter(vis)[0] || deepQ('button.submit-button')[0] || null;
const counter = (deepQ('.question-label, .question-label-container').map(dtext).find(t => /\d+\s+of\s+\d+/i.test(t)) || null);
const results = ALL.filter(e => vis(e) && /result|score|feedback-summary|retake|review/i.test(cls(e))).map(e => cls(e).slice(0, 50) + ' :: ' + dtext(e).slice(0, 80)).slice(0, 8);
const activeBtn = deepQ('button.block-button.active-block').filter(vis)[0] || deepQ('button.block-button.active-block')[0] || null;
const am = activeBtn ? (dtext(activeBtn).match(/Q\s*(\d+)/i) || (activeBtn.getAttribute('aria-label') || '').match(/Question\s+(\d+)/i)) : null;
const active_q = am ? Number(am[1]) : null;
const titles = {}; mcqs.forEach(m => { titles[m.getAttribute('modelid')] = dtext(deepQ('.mcq__title-inner', m)[0] || deepQ('heading-view', m)[0]); });
const active_id = active_q ? (mcqs.map(m => m.getAttribute('modelid')).find(id => new RegExp('Question\\s+' + active_q + '(\\D|$)').test(titles[id] || '')) || null) : null;
return JSON.stringify({mcq_ids: mcqs.map(m => m.getAttribute('modelid')), submit: sub ? {disabled: !!sub.disabled || /is-disabled/.test(cls(sub)), visible: vis(sub)} : null,
                       counter, results, start_visible: deepQ('.start-button').some(vis), active_q, active_id, titles});
"""
JS_SECURE_SUBMIT = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const sub = deepQ('button.submit-button').filter(vis)[0] || deepQ('button.submit-button')[0]; if (!sub) return 'no-submit';
if (sub.disabled || /is-disabled/.test(cls(sub))) return 'disabled';
sub.scrollIntoView({block: 'center', behavior: 'instant'}); sub.click(); return 'ok';
"""


def secure_state(sb) -> dict:
    return json.loads(sb.execute_script(JS_SECURE_STATE))


def secure_submit(sb) -> str:
    return sb.execute_script(JS_SECURE_SUBMIT)


# ---- end of the secure check: "Submit My Assessment" page = confirm checkbox + Submit button ----
JS_SECURE_FINAL_STATE = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const boxes = deepQ('input[type="checkbox"]').filter(vis).map(b => {
  const lab = (b.id ? deepQ('label[for="' + b.id + '"]')[0] : null) || b.closest('label') || b.parentElement;
  return {id: b.id, aria: b.getAttribute('aria-label'), label: lab ? dtext(lab).slice(0, 60) : '', checked: b.checked};
});
const confirm = boxes.find(b => /confirm|yes/i.test(b.label + ' ' + (b.aria || '')) && !/skip/i.test(b.label + ' ' + (b.aria || ''))) || null;
const subs = deepQ('button').filter(vis).filter(b => /^submit$/i.test(dtext(b)) || /submit/i.test(b.getAttribute('aria-label') || ''))
  .map(b => ({cls: cls(b).slice(0, 60), text: dtext(b).slice(0, 30), disabled: !!b.disabled || /is-disabled/.test(cls(b))}));
const page_text = (deepQ('[class*="submit"], [class*="assessment"]').filter(vis).map(dtext).join(' | ')).slice(0, 200);
return JSON.stringify({confirm, boxes: boxes.slice(0, 6), submits: subs, page_text, results: deepQ('[class*="result"], [class*="score"]').filter(vis).map(dtext).filter(Boolean).slice(0, 5)});
"""
JS_SECURE_FINAL_ACT = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const step = arguments[0];
if (step === 'check') {
  const b = deepQ('input[type="checkbox"]').filter(vis).find(x => { const lab = (x.id ? deepQ('label[for="' + x.id + '"]')[0] : null) || x.closest('label') || x.parentElement;
     const t = (lab ? dtext(lab) : '') + ' ' + (x.getAttribute('aria-label') || ''); return /confirm|yes/i.test(t) && !/skip/i.test(t); });
  if (!b) return 'no-checkbox'; b.scrollIntoView({block: 'center', behavior: 'instant'}); if (!b.checked) b.click(); return b.checked ? 'checked' : 'not-checked';
}
const findFinalSubmit = () => {
  // the green page button next to the confirm checkbox - NOT the bottom-bar button.submit-button
  const cands = deepQ('button, [role=button]').filter(vis).filter(x => /^submit$/i.test(dtext(x)) && !/submit-button|abs__btn-arrow/.test(cls(x)) && !x.disabled && !/is-disabled/.test(cls(x)));
  const box = deepQ('input[type="checkbox"]').filter(vis).find(x => { const lab = (x.id ? deepQ('label[for="' + x.id + '"]')[0] : null) || x.closest('label') || x.parentElement;
     return /confirm|yes/i.test((lab ? dtext(lab) : '') + ' ' + (x.getAttribute('aria-label') || '')); });
  if (box) { let r = box.getRootNode(); const near = cands.find(c => c.getRootNode() === r); if (near) return near; }
  return cands[0] || null;
};
if (step === 'submit') {
  const b = findFinalSubmit(); if (!b) return 'no-submit';
  b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
}
if (step === 'submit_rect') {
  const b = findFinalSubmit(); if (!b) return null;
  b.scrollIntoView({block: 'center', behavior: 'instant'}); const r = b.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + r.height / 2, tag: b.tagName.toLowerCase(), cls: cls(b).slice(0, 60)};
}
return 'unknown';
"""


def secure_final_state(sb) -> dict:
    return json.loads(sb.execute_script(JS_SECURE_FINAL_STATE))


def secure_final_act(sb, step: str) -> str:
    return sb.execute_script(JS_SECURE_FINAL_ACT, step)


# ---- secure check: Start control (div.start-button[role=button]); JS click may be ignored -> caller falls back to CDP ----
JS_SECURE_START = cf.JS_DEEP + r"""
const vis = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
const b = deepQ('.start-button').filter(vis)[0]; if (!b) return null;
b.scrollIntoView({block: 'center', behavior: 'instant'});
if (arguments[0] === 'click') { b.focus(); b.click(); return {clicked: true}; }
const r = b.getBoundingClientRect(); return {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height};
"""


def secure_start(sb, mode: str = "click"):
    return sb.execute_script(JS_SECURE_START, mode)
