"""Small pop-up messages shown inside the course page itself (bottom-right corner of the browser).

Used by 03_main.py to say when a module is finished and when the whole run is finished. The pop-up fades out on
its own after a few seconds, never blocks clicks (pointer-events: none), and a failure to show one never
interrupts the run.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# arguments: title, message, kind ("ok" | "warn" | "error"), milliseconds to stay on screen
JS_TOAST = r"""
const [title, msg, kind, ms] = arguments;
let box = document.getElementById('na-toasts');
if (!box) {
  box = document.createElement('div'); box.id = 'na-toasts';
  box.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;display:flex;flex-direction:column;'
    + 'gap:8px;align-items:flex-end;pointer-events:none';
  document.body.appendChild(box);
}
const colors = {ok: '#2e9e1f', warn: '#d98b00', error: '#c0392b'};
const t = document.createElement('div');
t.style.cssText = 'min-width:240px;max-width:360px;background:#0d274d;color:#fff;font:13px/1.45 system-ui,Arial;'
  + 'padding:10px 14px;border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.35);'
  + 'border-left:6px solid ' + (colors[kind] || colors.ok) + ';opacity:0;transform:translateY(8px);'
  + 'transition:opacity .25s,transform .25s';
const h = document.createElement('div'); h.style.cssText = 'font-weight:700;margin-bottom:2px'; h.textContent = title;
const b = document.createElement('div'); b.textContent = msg;
t.appendChild(h); t.appendChild(b); box.appendChild(t);
requestAnimationFrame(() => { t.style.opacity = '1'; t.style.transform = 'none'; });
setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, ms || 8000);
return true;
"""


def browser_toast(sb, title: str, message: str, kind: str = "ok", seconds: float = 8) -> None:
    """Show a pop-up in the course page. Leaves the driver in the top document (callers re-enter the frame)."""
    try:
        sb.driver.switch_to.default_content()
        sb.execute_script(JS_TOAST, title, message, kind, int(seconds * 1000))
    except Exception as e:  # noqa: BLE001
        log.debug("browser pop-up failed: %s", e)
