"""Probe an adobe-animate-ia-view (canvas activity): view props/methods, model, CreateJS stage tree with names,
text, and interactive handlers. Read-only. Run: .venv\\Scripts\\python.exe scripts\\probe_ia.py 10.2.6
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS = cf.JS_BY_ID + r"""
const out = [];
for (const el of deepQ('adobe-animate-ia-view, adobe-animate-view')) {
  const own = Object.getOwnPropertyNames(el);
  const protoMethods = []; let q = Object.getPrototypeOf(el); let d = 0;
  while (q && d < 2) { protoMethods.push(...Object.getOwnPropertyNames(q).filter(n => { const ds = Object.getOwnPropertyDescriptor(q, n); return ds && typeof ds.value === 'function' && !/^(constructor|_\$)/.test(n); })); q = Object.getPrototypeOf(q); d++; }
  let model = null; try { const m = el.model; model = m ? JSON.parse(JSON.stringify(m.attributes || {})) : null; } catch (e) { model = 'err ' + e; }
  const stage = el.__animStage || el.stage || null;
  const clip = el.__animClip || null;
  function tree(node, depth) {
    if (!node || depth > 4) return null;
    const kids = (node.children || []).slice(0, 40).map(c => tree(c, depth + 1)).filter(Boolean);
    const info = {name: node.name || null, type: node.constructor && node.constructor.name, frames: node.totalFrames, cur: node.currentFrame,
                  text: node.text !== undefined ? String(node.text).slice(0, 60) : undefined, visible: node.visible, mouseEnabled: node.mouseEnabled,
                  cursor: node.cursor, listeners: node._listeners ? Object.keys(node._listeners) : undefined};
    if (kids.length) info.children = kids;
    return info;
  }
  const dyn = deepQ('#dynamic-text div', el).map(x => ({id: x.id, text: dtext(x), cls: x.className}));
  const inputs = deepQ('input, textarea, select, button', el).map(x => ({tag: x.tagName, type: x.type, id: x.id, cls: String(x.className).slice(0, 60), value: x.value, text: dtext(x).slice(0, 40)}));
  out.push({tag: el.tagName.toLowerCase(), modelid: el.getAttribute('modelid'), own, protoMethods: protoMethods.slice(0, 60), model,
            hasStage: !!stage, clipTree: clip ? tree(clip, 0) : null, stageTree: stage ? tree(stage, 0) : null,
            libKeys: (el.__lib ? Object.keys(el.__lib) : null), globalsWithLib: Object.keys(window).filter(k => /^(lib|AdobeAn|createjs|exportRoot|stage)/.test(k)),
            dyn, inputs, complete: completion(stateDiv(el)), text: dtext(el).slice(0, 800)});
}
return JSON.stringify(out);
"""

item_id = sys.argv[1]
cfg = load_config(); log = get_logger("probe_ia", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, item_id)
with launch(cfg) as sb:
    open_course(sb, cfg)
    nav.goto_item(sb, cfg, node, sec, it)
    det = detect(cf.read_page_model(sb), it, sec)
    print("DETECT:", det.page_type.value, [(c["tag"], c["complete"]) for c in det.components])
    res = json.loads(sb.execute_script(JS))
    p = cfg_path(cfg, "recon") / f"ia_{item_id}.json"
    p.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    for r in res:
        print("==", r["tag"], r["modelid"], "complete", r["complete"])
        print("own:", r["own"]); print("methods:", r["protoMethods"]); print("model:", json.dumps(r["model"])[:800])
        print("globals:", r["globalsWithLib"], "hasStage", r["hasStage"])
        print("dyn:", r["dyn"]); print("inputs:", r["inputs"])
        print("text:", r["text"][:400])
        print("clipTree:", json.dumps(r["clipTree"])[:3000])
