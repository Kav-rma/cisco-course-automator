"""Dump a component's composed HTML + model summary for an item. Read-only.
Run: .venv\\Scripts\\python.exe scripts\\probe_component.py 9.2.7 matching-view
"""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS = cf.JS_BY_ID + r"""
const tag = arguments[0];
const out = [];
for (const el of deepQ(tag)) {
  const model = el.model || null; let m = null;
  try {
    const get = (k) => model && model.get ? model.get(k) : (model ? model[k] : undefined);
    const items = get('_items') || [];
    m = {keys: model ? Object.keys(model.attributes || model).slice(0, 50) : null,
         items: JSON.parse(JSON.stringify(items)).slice(0, 12),
         selectable: get('_selectable'), attempts: get('_attempts'), canShowModelAnswer: get('_canShowModelAnswer'),
         component: get('_component'), isComplete: get('_isComplete')};
  } catch (e) { m = {err: String(e)}; }
  function ser(node) {
    if (node.nodeType === Node.TEXT_NODE) { const t = node.textContent.replace(/\s+/g, ' '); return t.trim() ? t : ''; }
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    const t = node.tagName.toLowerCase(); if (t === 'style' || t === 'script' || t === 'svg') return '';
    const attrs = Array.from(node.attributes).filter(a => !/^(is-touch|fullscreen|device|browser|os|dir|location|theme|orientation|themetype|style|model)$/.test(a.name))
      .map(a => ` ${a.name}="${String(a.value).replace(/"/g, '&quot;').slice(0, 200)}"`).join('');
    let s = `<${t}${attrs}>`;
    if (node.shadowRoot) s += '<template shadowroot="open">' + Array.from(node.shadowRoot.childNodes).map(ser).join('') + '</template>';
    return s + Array.from(node.childNodes).map(ser).join('') + `</${t}>`;
  }
  const protoMethods = []; let q = Object.getPrototypeOf(el); let d = 0;
  while (q && d < 2) { protoMethods.push(...Object.getOwnPropertyNames(q).filter(n => { const ds = Object.getOwnPropertyDescriptor(q, n); return ds && typeof ds.value === 'function' && !/^(constructor|_\$|__)/.test(n); })); q = Object.getPrototypeOf(q); d++; }
  out.push({modelid: el.getAttribute('modelid'), model: m, methods: protoMethods.slice(0, 60), html: ser(el).slice(0, 20000)});
}
return JSON.stringify(out);
"""

item_id, tag = sys.argv[1], sys.argv[2]
cfg = load_config(); log = get_logger("probe_component", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    res = json.loads(sb.execute_script(JS, tag))
    p = cfg_path(cfg, "recon") / f"component_{item_id}_{tag}.json"
    p.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    for r in res:
        print("MODEL:", json.dumps(r["model"], ensure_ascii=False)[:3000])
        print("METHODS:", r["methods"])
        print("HTML:", r["html"][:6000])
