"""Dump the secure-one-question quiz DOM after Start (start-screen component + the live question container)."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS_SER_FN = r"""
function ser(node) {
  if (node.nodeType === Node.TEXT_NODE) { const t = node.textContent.replace(/\s+/g, ' '); return t.trim() ? t : ''; }
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const t = node.tagName.toLowerCase(); if (t === 'style' || t === 'script' || t === 'svg' || t === 'link') return '';
  const attrs = Array.from(node.attributes).filter(a => !/^(is-touch|fullscreen|device|browser|os|dir|location|theme|orientation|themetype|style|model|src)$/.test(a.name))
    .map(a => ` ${a.name}="${String(a.value).replace(/"/g, '&quot;').slice(0, 120)}"`).join('');
  let s = `<${t}${attrs}>`;
  if (node.shadowRoot) s += '<template shadowroot="open">' + Array.from(node.shadowRoot.childNodes).map(ser).join('') + '</template>';
  return s + Array.from(node.childNodes).map(ser).join('') + `</${t}>`;
}
"""
JS_DUMP = cf.JS_BY_ID + JS_SER_FN + r"""
const ss = byId(arguments[0]);
const mcqs = deepQ('mcq-view');
const chain = (el) => { const out = []; let r = el.getRootNode(); while (r && r.host) { out.push(r.host.tagName.toLowerCase() + (r.host.getAttribute('modelid') ? '[' + r.host.getAttribute('modelid').slice(0,8) + ']' : '')); r = r.host.getRootNode(); } return out; };
return JSON.stringify({
  start_screen_html: ss ? ser(ss) : null,
  mcqs: mcqs.map(m => ({modelid: m.getAttribute('modelid'), chain: chain(m), html: ser(m).slice(0, 30000),
                        visible: !!(m.offsetWidth || m.offsetHeight)})),
});
"""
JS_CLICK_START = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ('.start-button, [class*="start-button"][role=button]', el)[0]; if (!b) return 'no-start';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'clicked ' + dtext(b);
"""
item_id = sys.argv[1] if len(sys.argv) > 1 else "1.5.11"
cfg = load_config(interactive=False); log = get_logger("probe_ss2", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    mid = next(c["modelid"] for c in det.components if c["tag"] == "adaptive-start-screen-view")
    print("START ->", sb.execute_script(JS_CLICK_START, mid)); time.sleep(2.5)
    d = json.loads(sb.execute_script(JS_DUMP, mid))
    (cfg_path(cfg, "recon") / f"startscreen2_{item_id}.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    print("start_screen_html len:", len(d["start_screen_html"] or ""))
    for m in d["mcqs"]: print("MCQ", m["modelid"][:8], "visible", m["visible"], "chain", m["chain"][:8], "html len", len(m["html"]))
    print("saved")
