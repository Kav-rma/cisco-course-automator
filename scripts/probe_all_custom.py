"""Dump markup + model + methods for every remaining custom component type (one representative item each),
in ONE login. Output: data/recon/component_<item>_<tag>.json. Read-only; touches nothing.
Run: .venv\\Scripts\\python.exe scripts\\probe_all_custom.py
"""
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course, save_diagnostics
from core.config import load_config, path as cfg_path
from core.logger import get_logger

def _pop_course(argv):
    """Remove '--course KEY' (or '--course=KEY') from argv and return KEY (or None)."""
    key = None
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--course" and i + 1 < len(argv):
            key = argv[i + 1]; i += 2; continue
        if a.startswith("--course="):
            key = a.split("=", 1)[1]; i += 1; continue
        out.append(a); i += 1
    return key, out

TARGETS = [  # (item id, component tag)
    ("17.1.4", "commandwindow-view"),
    ("18.2.4", "tabs-view"),
    ("20.1.4", "binary-to-decimal"),
    ("20.1.7", "decimal-to-binary"),
    ("21.4.6", "switch-it-view"),
    ("23.1.6", "anding-activity-view"),
    ("30.4.4", "cable-pinout-view"),
    ("28.4.4", "packettracer-view"),
    ("9.2.3", "adobe-animate-ia-view"),
]

# same dumper as probe_component.py, inline
JS = cf.JS_BY_ID + r"""
const tag = arguments[0]; const out = [];
for (const el of deepQ(tag)) {
  const model = el.model || null; let m = null;
  try { const get = (k) => model && model.get ? model.get(k) : (model ? model[k] : undefined);
    const attrs = model && model.attributes ? JSON.parse(JSON.stringify(model.attributes)) : null;
    m = {attributes: attrs, items: JSON.parse(JSON.stringify(get('_items') || [])).slice(0, 30)}; } catch (e) { m = {err: String(e)}; }
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
  while (q && d < 2) { protoMethods.push(...Object.getOwnPropertyNames(q).filter(n => { const ds = Object.getOwnPropertyDescriptor(q, n); return ds && typeof ds.value === 'function' && !/^(constructor|_\$)/.test(n); })); q = Object.getPrototypeOf(q); d++; }
  const own = Object.getOwnPropertyNames(el).filter(n => !/^_\$/.test(n));
  out.push({modelid: el.getAttribute('modelid'), model: m, methods: protoMethods.slice(0, 80), own, html: ser(el).slice(0, 60000), complete: completion(stateDiv(el))});
}
return JSON.stringify(out);
"""

_course, _argv = _pop_course(sys.argv[1:])
if _argv:  # e.g. 38.1.1:flipcard-view 38.1.9:narrative-view
    TARGETS = [tuple(a.split(":", 1)) for a in _argv]
cfg = load_config(course=_course); log = get_logger("probe_all", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
with launch(cfg) as sb:
    open_course(sb, cfg)
    for item_id, tag in TARGETS:
        loc = nav.locate(structure, item_id)
        if not loc:
            log.warning("%s not in structure", item_id); continue
        node, sec, it = loc
        try:
            nav.goto_item(sb, cfg, node, sec, it)
            res = json.loads(sb.execute_script(JS, tag))
            p = cfg_path(cfg, "recon") / f"component_{item_id}_{tag}.json"
            p.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
            log.info("%s %s -> %d component(s) dumped (%s)", item_id, tag, len(res), p.name)
        except Exception as e:
            log.exception("probe failed for %s: %s", item_id, e)
            save_diagnostics(sb, cfg, f"probe_{item_id}")
log.info("done")
