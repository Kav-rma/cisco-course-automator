"""yesno-view probe: click Start, dump widget markup; click the correct answer for the first card; dump again."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS_WIDGET = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return null;
const w = deepQ('.yesno_container', el)[0];
function ser(node) {
  if (node.nodeType === Node.TEXT_NODE) { const t = node.textContent.replace(/\s+/g, ' '); return t.trim() ? t : ''; }
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const t = node.tagName.toLowerCase(); if (t === 'style' || t === 'script' || t === 'svg') return '';
  const attrs = Array.from(node.attributes).filter(a => !/^(is-touch|fullscreen|device|browser|os|dir|location|theme|orientation|themetype|style|model|src)$/.test(a.name))
    .map(a => ` ${a.name}="${String(a.value).replace(/"/g, '&quot;').slice(0, 120)}"`).join('');
  let s = `<${t}${attrs}>`;
  if (node.shadowRoot) s += '<template shadowroot="open">' + Array.from(node.shadowRoot.childNodes).map(ser).join('') + '</template>';
  return s + Array.from(node.childNodes).map(ser).join('') + `</${t}>`;
}
const model = el.model; let items = [];
try { items = JSON.parse(JSON.stringify(model.get('_items'))); } catch (e) {}
return JSON.stringify({html: w ? ser(w) : null, complete: completion(stateDiv(el)),
  current_alt: (deepQ('img.current_question', el)[0] || {}).alt, items: items.map(i => ({alt: i._graphic && i._graphic.alt, yes: i._shouldBeSelected}))});
"""
JS_CLICK = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const b = deepQ(arguments[1], el)[0]; if (!b) return 'no-btn';
b.scrollIntoView({block: 'center', behavior: 'instant'}); b.click(); return 'ok';
"""
item_id = sys.argv[1] if len(sys.argv) > 1 else "39.1.6"
cfg = load_config(); log = get_logger("probe_yesno", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    mid = next(c["modelid"] for c in det.components if c["tag"] == "yesno-view")
    print("START ->", sb.execute_script(JS_CLICK, mid, "button.start_button"))
    time.sleep(1.0)
    st = json.loads(sb.execute_script(JS_WIDGET, mid))
    print("ITEMS:", st["items"]); print("CURRENT:", st["current_alt"]); print("HTML1:", st["html"][:3000])
    # answer first card correctly: find yes/no buttons by text
    want = next((i["yes"] for i in st["items"] if i["alt"] == st["current_alt"]), None)
    print("WANT yes?", want)
    sel = "button.yes_button, button[aria-label*='Yes' i]" if want else "button.no_button, button[aria-label*='No' i]"
    print("ANSWER ->", sb.execute_script(JS_CLICK, mid, sel))
    time.sleep(1.0)
    st2 = json.loads(sb.execute_script(JS_WIDGET, mid))
    print("CURRENT2:", st2["current_alt"], "complete", st2["complete"]); print("HTML2:", st2["html"][:3000])
    (cfg_path(cfg, "recon") / f"yesno_{item_id}.json").write_text(json.dumps([st, st2], indent=1), encoding="utf-8")
