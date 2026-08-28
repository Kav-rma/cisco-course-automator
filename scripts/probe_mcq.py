"""Probe: what does an mcq-view expose (model property, items, correct flags, buttons)? Read-only."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import content_frame as cf
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from phase3_detect import goto_item, locate

JS = cf.JS_DEEP + r"""
const out = [];
for (const m of deepQ('mcq-view')) {
  const model = m.model || m._model || null;
  let items = null, keys = null, mtype = typeof model;
  try {
    keys = model ? Object.keys(model).slice(0, 60) : null;
    const raw = model && (model._items || (model.get && model.get('_items')) || (model.attributes && model.attributes._items));
    items = raw ? raw.map(i => ({text: i.text, _shouldBeSelected: i._shouldBeSelected, _index: i._index, _isActive: i._isActive, feedback: i.feedback})) : null;
  } catch (e) { items = 'err ' + e.message; }
  const q = deepQ('.mcq__body-inner', m)[0];
  const opts = deepQ('.mcq__item', m).map(o => ({idx: o.getAttribute('data-socialgoodpulse-index'), text: dtext(deepQ('.mcq__item-text-inner', o)[0]), checked: o.getAttribute('aria-checked'), cls: cls(o)}));
  const btns = deepQ('button', m).map(b => ({text: dtext(b), cls: cls(b), disabled: b.disabled}));
  const propNames = Object.getOwnPropertyNames(m).slice(0, 40);
  out.push({modelid: m.getAttribute('modelid'), mtype, keys, items, question: q ? dtext(q) : null, opts, btns, propNames,
            selectable: m.getAttribute('selectable') || (model && model._selectable), attempts: model && model._attempts, canShowModelAnswer: model && model._canShowModelAnswer});
}
return JSON.stringify(out);
"""
cfg = load_config(); log = get_logger("probe", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
with launch(cfg) as sb:
    open_course(sb, cfg)
    node, sec, it = locate(structure, sys.argv[1] if len(sys.argv) > 1 else "3.1.4")
    goto_item(sb, cfg, node, sec, it)
    res = json.loads(sb.execute_script(JS))
    Path("data/recon/probe_mcq.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1)[:6000])
