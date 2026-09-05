"""
ACTIVITY: bespoke interactive components ("Activity - ..."). Each known component type has its own small
driver registered in ACTIVITY_DRIVERS; unknown ones are handed to the student (needs_user) with the tag named,
so they can be added once seen. All drivers verify completion via the component's is-complete class.
"""
from __future__ import annotations

import json
import re
import time

from core import content_frame as cf
from core.browser import wait_until
from core.ipv6_tools import compress_ipv6, omit_leading_zeros
from core.number_tools import and_octets, bits_to_decimal, decimal_to_bits
from core.page_detector import PageType

from .base import HandlerContext, HandlerResult, register, unit_complete, wait_unit_complete

# ---- generic DOM helpers inside a component (by modelid) ----
JS_FORM_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
return {
  inputs: deepQ('input, textarea', el).map((i, k) => ({k, cls: cls(i), type: i.type, value: i.value, output: i.getAttribute('data-output'),
                                                    maxlength: i.getAttribute('maxlength'), disabled: i.disabled, aria: i.getAttribute('aria-label')})),
  buttons: deepQ('button', el).map(b => ({cls: cls(b), text: dtext(b).slice(0, 30), disabled: b.disabled})),
  toasts: deepQ('.Toastify__toast, .Toastify__toast-body', el).map(dtext).slice(0, 5),
  complete: completion(stateDiv(el)),
};
"""
JS_SET_INPUT = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const inputs = deepQ('input, textarea', el); const i = inputs[arguments[1]]; if (!i) return 'no-input';
const v = String(arguments[2]);
i.focus();
const proto = i.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; setter.call(i, v);
for (const t of ['input', 'change', 'keyup']) i.dispatchEvent(new Event(t, {bubbles: true, composed: true}));
i.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, composed: true, key: 'Tab'}));
i.blur(); i.dispatchEvent(new Event('blur', {bubbles: true, composed: true}));
return i.value === v ? 'ok' : 'mismatch:' + i.value;
"""
JS_CLICK_BTN = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ('button' + arguments[1], el)[0]; if (!b) return 'no-button';
if (b.disabled) return 'disabled';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""


def form_state(sb, mid):
    return sb.execute_script(JS_FORM_STATE, mid)


def set_input(sb, mid, k, value):
    return sb.execute_script(JS_SET_INPUT, mid, k, value)


def click_btn(sb, mid, selector_suffix):
    return sb.execute_script(JS_CLICK_BTN, mid, selector_suffix)


# ---- driver: ipv6addressrepresentation-view (10.2.6 "Activity - ..."): verified markup ----
#   tr "Omit leading zeroes": 8 x input.omit-leading-zero[data-output=<preferred hextet>]
#   tr "Compressed format" : input.compressed-ipv6
#   buttons: .check-answer (Check) .change-question (Next) .show-answer .reset-answer
def drive_ipv6_representation(ctx: HandlerContext, mid: str) -> dict:
    t = ctx.cfg["timeouts"]
    rounds = []
    for rnd in range(12):
        st = form_state(ctx.sb, mid)
        if not st:
            return {"ok": False, "error": "no state"}
        if st["complete"]:
            break
        hex_inputs = [i for i in st["inputs"] if "omit-leading-zero" in i["cls"]]
        comp_inputs = [i for i in st["inputs"] if "compressed-ipv6" in i["cls"]]
        if len(hex_inputs) != 8 or not comp_inputs:
            return {"ok": False, "error": f"unexpected inputs: {len(hex_inputs)} hextets, {len(comp_inputs)} compressed"}
        hextets = [i["output"] or "" for i in hex_inputs]
        for i in hex_inputs:
            set_input(ctx.sb, mid, i["k"], omit_leading_zeros(i["output"] or ""))
        set_input(ctx.sb, mid, comp_inputs[0]["k"], compress_ipv6(hextets))
        r = click_btn(ctx.sb, mid, ".check-answer")
        time.sleep(0.8)
        after = form_state(ctx.sb, mid)
        rounds.append({"address": ":".join(hextets), "compressed": compress_ipv6(hextets), "check": r, "toasts": after and after["toasts"], "complete": after and after["complete"]})
        ctx.log.info("  IPv6 round %d: %s -> %s | check=%s | %s", rnd + 1, ":".join(hextets), compress_ipv6(hextets), r, (after or {}).get("toasts"))
        if cf.wait_complete(ctx.sb, mid, 4):
            break
        nxt = click_btn(ctx.sb, mid, ".change-question")
        if nxt != "ok":
            break
        time.sleep(0.6)
    done = cf.wait_complete(ctx.sb, mid, t["completion"])
    return {"ok": bool(done), "rounds": rounds, "complete": done}


JS_TABLE_ROWS = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return '[]';
const rows = deepQ('table tr', el).map(tr => Array.from(tr.querySelectorAll('th, td')).map(c => dtext(c)));
return JSON.stringify(rows);
"""


def table_rows(sb, mid) -> list[list[str]]:
    return json.loads(sb.execute_script(JS_TABLE_ROWS, mid))


def _row(rows, label):
    for r in rows:
        if r and r[0].strip().lower().startswith(label.lower()):
            return r
    return None


def _check_round_loop(ctx, mid, fill_round, max_rounds=12, next_btn=".change-question"):
    """Generic loop for the check/next converter activities: fill -> Check -> complete? -> Next."""
    rounds = []
    for rnd in range(max_rounds):
        st = form_state(ctx.sb, mid)
        if not st:
            return {"ok": False, "error": "no state"}
        if st["complete"]:
            break
        info = fill_round(st)
        if "error" in info:
            return {"ok": False, **info, "rounds": rounds}
        r = click_btn(ctx.sb, mid, ".check-answer")
        time.sleep(0.8)
        after = form_state(ctx.sb, mid)
        rounds.append({**info, "check": r, "toasts": after and after["toasts"]})
        ctx.log.info("  round %d: %s | check=%s | %s", rnd + 1, info, r, (after or {}).get("toasts"))
        if cf.wait_complete(ctx.sb, mid, 4):
            break
        if click_btn(ctx.sb, mid, next_btn) != "ok":
            break
        time.sleep(0.6)
    done = cf.wait_complete(ctx.sb, mid, ctx.cfg["timeouts"]["completion"])
    return {"ok": bool(done), "rounds": rounds, "complete": done}


# binary-to-decimal (20.1.4): read the Bit row from the table, type the decimal value, Check/New Number.
def drive_binary_to_decimal(ctx: HandlerContext, mid: str) -> dict:
    def fill(st):
        bits_row = _row(table_rows(ctx.sb, mid), "Bit")
        if not bits_row or len(bits_row) < 9:
            return {"error": f"bit row not found"}
        bits = [b.strip() for b in bits_row[1:9]]
        value = bits_to_decimal(bits)
        k = next((i["k"] for i in st["inputs"] if i["type"] == "text"), None)
        if k is None:
            return {"error": "no input"}
        set_input(ctx.sb, mid, k, str(value))
        return {"bits": "".join(bits), "answer": value}
    return _check_round_loop(ctx, mid, fill)


# decimal-to-binary (20.1.7): read the decimal value, type the 8 bits MSB-first.
def drive_decimal_to_binary(ctx: HandlerContext, mid: str) -> dict:
    def fill(st):
        dec_row = _row(table_rows(ctx.sb, mid), "Decimal Value")
        if not dec_row or len(dec_row) < 2 or not dec_row[1].strip().isdigit():
            return {"error": "decimal value not found"}
        bits = decimal_to_bits(int(dec_row[1].strip()))
        ks = [i["k"] for i in st["inputs"] if "mask-inputs" in i["cls"]]
        if len(ks) != 8:
            return {"error": f"{len(ks)} bit inputs"}
        for k, b in zip(ks, bits):
            set_input(ctx.sb, mid, k, b)
        return {"decimal": dec_row[1].strip(), "answer": "".join(bits)}
    return _check_round_loop(ctx, mid, fill)


# anding-activity (23.1.6): AND the host/mask binary rows; fill 4 binary + 4 decimal octets.
def drive_anding(ctx: HandlerContext, mid: str) -> dict:
    def fill(st):
        rows = table_rows(ctx.sb, mid)
        host = _row(rows, "Host Address in binary")
        mask = _row(rows, "Subnet Mask in binary")
        if not host or not mask or len(host) < 5 or len(mask) < 5:
            return {"error": "binary rows not found"}
        net = [and_octets(h.strip(), m.strip()) for h, m in zip(host[1:5], mask[1:5])]
        bin_ks = [i["k"] for i in st["inputs"] if "binary-input" in i["cls"]]
        dec_ks = [i["k"] for i in st["inputs"] if "decimal-input" in i["cls"]]
        if len(bin_ks) != 4 or len(dec_ks) != 4:
            return {"error": f"{len(bin_ks)} binary / {len(dec_ks)} decimal inputs"}
        for k, b in zip(bin_ks, net):
            set_input(ctx.sb, mid, k, b)
        for k, b in zip(dec_ks, net):
            set_input(ctx.sb, mid, k, str(int(b, 2)))
        return {"answer": ".".join(str(int(b, 2)) for b in net)}
    return _check_round_loop(ctx, mid, fill, next_btn=".change-question")


# cable-pinout (30.4.4): drag each wire (.option[data-option=N]) onto its pin (.target[data-target=N]),
# then Check. The drag is simulated with pointer+mouse (and HTML5 dnd as fallback) event sequences.
JS_PINOUT_DRAG = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const n = String(arguments[1]);
const opt = deepQ('.option[data-option="' + n + '"]', el)[0];
const tgt = deepQ('.target[data-target="' + n + '"]', el)[0];
if (!opt || !tgt) return 'no-elements';
opt.scrollIntoView({block: 'center', behavior: 'instant'});
const c = (e) => { const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2}; };
const a = c(opt), b = c(tgt);
const fire = (target, type, x, y, extra) => {
  const Ev = type.startsWith('pointer') ? PointerEvent : MouseEvent;
  target.dispatchEvent(new Ev(type, Object.assign({bubbles: true, composed: true, cancelable: true, view: window,
    clientX: x, clientY: y, button: 0, buttons: type.endsWith('up') ? 0 : 1, pointerId: 1, isPrimary: true}, extra || {})));
};
// pointer/mouse drag
fire(opt, 'pointerdown', a.x, a.y); fire(opt, 'mousedown', a.x, a.y);
const steps = 6;
for (let i = 1; i <= steps; i++) {
  const x = a.x + (b.x - a.x) * i / steps, y = a.y + (b.y - a.y) * i / steps;
  const over = document.elementFromPoint ? (el.shadowRoot ? el : document) : document;
  fire(document, 'pointermove', x, y); fire(document, 'mousemove', x, y);
  fire(tgt, 'pointermove', x, y); fire(tgt, 'mousemove', x, y);
}
fire(tgt, 'pointerup', b.x, b.y); fire(tgt, 'mouseup', b.x, b.y);
fire(document, 'pointerup', b.x, b.y); fire(document, 'mouseup', b.x, b.y);
// HTML5 dnd fallback
try {
  const dt = new DataTransfer();
  opt.dispatchEvent(new DragEvent('dragstart', {bubbles: true, composed: true, cancelable: true, dataTransfer: dt, clientX: a.x, clientY: a.y}));
  tgt.dispatchEvent(new DragEvent('dragenter', {bubbles: true, composed: true, cancelable: true, dataTransfer: dt, clientX: b.x, clientY: b.y}));
  tgt.dispatchEvent(new DragEvent('dragover', {bubbles: true, composed: true, cancelable: true, dataTransfer: dt, clientX: b.x, clientY: b.y}));
  tgt.dispatchEvent(new DragEvent('drop', {bubbles: true, composed: true, cancelable: true, dataTransfer: dt, clientX: b.x, clientY: b.y}));
  opt.dispatchEvent(new DragEvent('dragend', {bubbles: true, composed: true, cancelable: true, dataTransfer: dt, clientX: b.x, clientY: b.y}));
} catch (e) {}
return 'ok';
"""
JS_PINOUT_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
return {options: deepQ('.option', el).map(o => ({n: o.getAttribute('data-option'), cls: cls(o)})),
        targets: deepQ('.target', el).map(t => ({n: t.getAttribute('data-target'), cls: cls(t), kids: t.children.length})),
        check_disabled: (deepQ('#check', el)[0] || {}).disabled, complete: completion(stateDiv(el))};
"""


def _frame_offset(ctx) -> tuple[float, float]:
    """Top-document offset of the content iframe (for translating in-frame coords to viewport coords)."""
    drv = ctx.sb.driver
    drv.switch_to.default_content()
    r = ctx.sb.execute_script(
        "const f=document.querySelector('iframe[title=\"Course content\"]'); const b=f.getBoundingClientRect(); return [b.x, b.y];")
    cf.enter(ctx.sb)
    return float(r[0]), float(r[1])


def _cdp_mouse(ctx, etype, x, y, buttons=1):
    ctx.sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
        "type": etype, "x": float(x), "y": float(y), "button": "left", "buttons": buttons, "clickCount": 1, "pointerType": "mouse"})


def drive_cable_pinout(ctx: HandlerContext, mid: str) -> dict:
    """Cable pinout: verified 2026-09-05 to be click-source-then-click-target (NOT drag). Click .option[N]
    (it becomes selected), then .target[N] (the wire is placed and the selection clears). data-option N maps
    to data-target N (the correct pin for that wire). After all 8, Check enables -> click it -> complete."""
    t = ctx.cfg["timeouts"]
    st = ctx.sb.execute_script(JS_PINOUT_STATE, mid)
    if not st:
        return {"ok": False, "error": "no state"}
    if st["complete"]:
        return {"ok": True, "note": "already complete"}
    import time as _t
    click_btn(ctx.sb, mid, "#reset")   # clear any partial state from a prior attempt (no-op if disabled)
    _t.sleep(0.3)
    js_click = cf.JS_BY_ID + """
const el = byId(arguments[0]); if (!el) return 'no-el';
const e = deepQ(arguments[1], el)[0]; if (!e) return 'no-el:' + arguments[1];
e.scrollIntoView({block: 'center', behavior: 'instant'}); e.click(); return 'ok';
"""
    placed = 0
    for o in st["options"]:
        n = o["n"]
        r1 = ctx.sb.execute_script(js_click, mid, f'.option[data-option="{n}"]')
        _t.sleep(0.2)
        r2 = ctx.sb.execute_script(js_click, mid, f'.target[data-target="{n}"]')
        _t.sleep(0.2)
        if r1 == "ok" and r2 == "ok":
            placed += 1
    st2 = ctx.sb.execute_script(JS_PINOUT_STATE, mid)
    ctx.log.info("  cable-pinout placed %d/%d wires (click-click); check_disabled=%s", placed,
                 len(st["options"]), st2["check_disabled"])
    if st2["check_disabled"] is False:
        click_btn(ctx.sb, mid, "#check")
        _t.sleep(0.9)
    done = cf.wait_complete(ctx.sb, mid, t["completion"])
    return {"ok": bool(done), "placed": placed, "check_disabled_after": st2["check_disabled"], "complete": done}


# yesno-view (39.1.6): Start, then one image card at a time; model _items[i] = {_graphic.alt, _shouldBeSelected}.
# Answer controls are div.user_selects_yes / div.user_selects_no (role=button). Cards come in shuffled order,
# so each card is matched by the current image alt.
JS_YESNO_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
let items = []; try { items = JSON.parse(JSON.stringify(el.model.get('_items'))); } catch (e) {}
const cur = deepQ('img.current_question', el)[0];
return {items: items.map(i => ({alt: i._graphic && i._graphic.alt, text: i._questionText, yes: !!i._shouldBeSelected})),
        current_alt: cur ? cur.alt : null, has_start: !!deepQ('button.start_button', el)[0],
        has_yes: !!deepQ('.user_selects_yes', el)[0], has_reset: !!deepQ('button.reset_button, .reset_button', el)[0],
        complete: completion(stateDiv(el))};
"""
JS_YESNO_CLICK = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ(arguments[1], el)[0]; if (!b) return 'no-btn';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""


def drive_yesno(ctx: HandlerContext, mid: str) -> dict:
    t = ctx.cfg["timeouts"]
    st = ctx.sb.execute_script(JS_YESNO_STATE, mid)
    if not st:
        return {"ok": False, "error": "no state"}
    if st["complete"]:
        return {"ok": True, "note": "already complete"}
    if st["has_start"]:
        ctx.sb.execute_script(JS_YESNO_CLICK, mid, "button.start_button")
        wait_until(ctx.sb, lambda s: (s.execute_script(JS_YESNO_STATE, mid) or {}).get("has_yes"), t["element"], poll=0.2, what="yes/no buttons")
    answers = []
    for _ in range(len(st["items"]) + 3):
        cur = ctx.sb.execute_script(JS_YESNO_STATE, mid)
        if not cur or cur["complete"] or not cur["has_yes"]:
            break
        item = next((i for i in cur["items"] if i["alt"] and i["alt"] == cur["current_alt"]), None)
        if item is None:
            return {"ok": False, "error": f"card '{cur['current_alt']}' not in model", "answers": answers}
        sel = ".user_selects_yes" if item["yes"] else ".user_selects_no"
        r = ctx.sb.execute_script(JS_YESNO_CLICK, mid, sel)
        answers.append(f"{cur['current_alt']} -> {'Yes' if item['yes'] else 'No'} ({r})")
        prev = cur["current_alt"]
        wait_until(ctx.sb, lambda s: (lambda a: bool(a) and (a["complete"] or a["current_alt"] != prev or not a["has_yes"]))(s.execute_script(JS_YESNO_STATE, mid)),
                   t["element"], poll=0.3, what="next card")
        time.sleep(0.3)
    ctx.log.info("  Yes/No cards: %s", "; ".join(answers))
    done = cf.wait_complete(ctx.sb, mid, t["completion"])
    return {"ok": bool(done), "answers": answers, "complete": done}


# ---- switch-it (21.4.6): "Switch It!" frame-forwarding activity -------------------------------------------
# Verified 2026-09-05 over 8 sampled rounds. The MAC table's columns are fixed ports (Fa1..Fa12, Fa9 spans two
# cells); a MAC whose span carries class "hide" is NOT currently in the table. Standard switch behaviour:
#   dest == FF (broadcast)      -> every device port except the ingress port
#   dest in the MAC table       -> just that port ("sent to specific port only")
#   dest not in the MAC table   -> flood: every device port except ingress ("flooded to all ports")
#   source not in the table     -> the switch also learns it ("adds the source MAC address ...")
# The ingress port is the port that owns the source MAC in the table layout.
JS_SWITCHIT_STATE = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const all=[]; (function walk(n){ if(!n) return; all.push(n);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
const txt=(e)=> e ? (e.textContent||'').replace(/\s+/g,' ').trim() : null;
const tables = all.filter(e=>e.tagName==='TABLE');
const frame = tables[0] ? Array.from(tables[0].querySelectorAll('tbody td')).map(txt) : [];
const macT = all.find(e=>/problem-details-mac-table/.test(String(e.className)));
if (!macT) return null;
// expand the header row by colSpan so column index -> port name
const ports=[]; Array.from(macT.querySelectorAll('th')).forEach(h=>{
  for (let i=0;i<(h.colSpan||1);i++) ports.push(txt(h)); });
const cells = Array.from(macT.querySelectorAll('tbody td')).map((td,i)=>{
  const sp=td.querySelector('span');
  return {port: ports[i]||null, mac: sp?txt(sp):null, in_table: sp?!/hide/.test(sp.className):false};});
const qs = all.filter(e=>/(^| )question(-\d+)?( |$)/.test(String(e.className)) && e.querySelector('.option'));
const questions = qs.map(q=>({heading: txt(q.querySelector('h5')),
  options: Array.from(q.querySelectorAll('.option')).map(o=>{const i=o.querySelector('input');
     return {label:o.getAttribute('aria-label'), checked: i?i.checked:false};})}));
return JSON.stringify({frame, cells, questions, complete: completion(stateDiv(el))});
"""
JS_SWITCHIT_SET = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const want = arguments[1];
const all=[]; (function walk(n){ if(!n) return; all.push(n);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
const opts = all.filter(o=>/(^| )option( |$)/.test(String(o.className)) && o.querySelector('input'));
let changed=[];
for (const o of opts) {
  const lab = o.getAttribute('aria-label'); const i = o.querySelector('input');
  const should = want.indexOf(lab) !== -1;
  if (!!i.checked !== should) { i.click(); changed.push((should?'+':'-')+lab); }
}
return JSON.stringify(changed);
"""
JS_SWITCHIT_BTN = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const want=String(arguments[1]).toLowerCase();
const all=[]; (function walk(n){ if(!n) return; all.push(n);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
const b = all.find(x=>x.tagName==='BUTTON' && (x.textContent||'').trim().toLowerCase()===want);
if(!b || b.disabled) return 'not-found';
b.scrollIntoView({block:'center', behavior:'instant'}); b.click(); return 'ok';
"""

_SI_BROADCAST = "Frame is a broadcast frame and will be forwarded to all ports."
_SI_SPECIFIC = "Frame is a unicast frame and will be sent to specific port only."
_SI_FLOOD = "Frame is a unicast frame and will be flooded to all ports."
_SI_LEARN = "Switch adds the source MAC address which is currently not in the MAC address table."


def _switchit_solve(st: dict) -> dict:
    """Work out the ports and statements for the current frame. Returns {} if the state is unreadable."""
    frame = [c for c in (st.get("frame") or [])]
    cells = [c for c in (st.get("cells") or []) if c.get("mac")]
    if len(frame) < 3 or not cells:
        return {}
    dest, src = (frame[1] or "").strip().upper(), (frame[2] or "").strip().upper()
    port_of = {c["mac"].strip().upper(): c["port"] for c in cells if c.get("port")}
    in_table = {c["mac"].strip().upper(): c["in_table"] for c in cells}
    device_ports = sorted({c["port"] for c in cells if c.get("port")},
                          key=lambda p: int(re.sub(r"\D", "", p) or 0))
    ingress = port_of.get(src)
    if dest.startswith("FF"):
        ports, stmt = [p for p in device_ports if p != ingress], _SI_BROADCAST
    elif in_table.get(dest):
        ports, stmt = [port_of[dest]], _SI_SPECIFIC
    else:
        ports, stmt = [p for p in device_ports if p != ingress], _SI_FLOOD
    statements = [stmt]
    if not in_table.get(src, False):
        statements.append(_SI_LEARN)
    return {"dest": dest, "src": src, "ingress": ingress, "ports": ports, "statements": statements}


def drive_switch_it(ctx: HandlerContext, mid: str) -> dict:
    """Solve the Switch It! rounds: read the frame + MAC table, tick the ports/statements, Check, New problem."""
    rounds = []
    for rnd in range(20):
        raw = ctx.sb.execute_script(JS_SWITCHIT_STATE, mid)
        if not raw:
            return {"ok": False, "error": "switch-it state unreadable", "rounds": rounds}
        st = json.loads(raw)
        if st.get("complete"):
            break
        sol = _switchit_solve(st)
        if not sol:
            return {"ok": False, "error": "could not read the frame / MAC table", "rounds": rounds}
        changed = ctx.sb.execute_script(JS_SWITCHIT_SET, mid, sol["ports"] + sol["statements"])
        chk = ctx.sb.execute_script(JS_SWITCHIT_BTN, mid, "Check")
        time.sleep(0.9)
        ctx.log.info("  round %d: %s->%s via %s | %s | check=%s", rnd + 1, sol["src"], sol["dest"],
                     ",".join(sol["ports"]), sol["statements"][0][:38], chk)
        rounds.append({"dest": sol["dest"], "src": sol["src"], "ports": sol["ports"], "check": chk,
                       "changed": json.loads(changed) if changed else []})
        if cf.wait_complete(ctx.sb, mid, 3):
            break
        if ctx.sb.execute_script(JS_SWITCHIT_BTN, mid, "New problem") != "ok":
            break
        time.sleep(1.0)
    done = cf.wait_complete(ctx.sb, mid, ctx.cfg["timeouts"]["completion"])
    return {"ok": bool(done), "rounds": len(rounds), "complete": done}


ACTIVITY_DRIVERS = {
    "yesno-view": drive_yesno,
    "ipv6addressrepresentation-view": drive_ipv6_representation,
    "binary-to-decimal": drive_binary_to_decimal,
    "decimal-to-binary": drive_decimal_to_binary,
    "anding-activity-view": drive_anding,
    "cable-pinout-view": drive_cable_pinout,
    "switch-it-view": drive_switch_it,
}


@register(PageType.ACTIVITY)
class ActivityHandler:
    def handle(self, ctx: HandlerContext) -> HandlerResult:
        if unit_complete(ctx) is True:
            return HandlerResult("already_complete")
        res = HandlerResult("completed")
        cf.scroll_to(ctx.sb, ctx.detection.scope_modelid, "start")
        for c in ctx.detection.components:
            drv = ACTIVITY_DRIVERS.get(c["tag"])
            if drv is None:
                continue
            if c["complete"] is True:
                continue
            ctx.log.info("  Activity %s", c["tag"])
            out = drv(ctx, c["modelid"])
            res.components[c["modelid"]] = out
            if not out.get("ok"):
                res.status = "needs_user"
                res.notes.append(f"{c['tag']}: {out.get('error') or 'did not complete'}")
        pending = [c["tag"] for c in ctx.detection.components if c["complete"] is not True and c["tag"] not in ACTIVITY_DRIVERS
                   and c["tag"] in ACTIVITY_LIKE_TAGS]
        if pending:
            res.status = "needs_user"
            res.notes.append(f"no driver yet for: {pending} - please do this one yourself")
        if res.status == "completed" and not wait_unit_complete(ctx):
            res.status = "needs_user"
            res.notes.append("activity unit still incomplete")
        return res


# tags that make a unit an ACTIVITY (kept in sync with page_detector.ACTIVITY_TAGS)
from core.page_detector import ACTIVITY_TAGS as ACTIVITY_LIKE_TAGS  # noqa: E402
