"""Probe the adobe-animate-view player: find the first incomplete animated figure in a module, dump its JS API,
then observe plain Play for a few seconds and (optionally) try a CreateJS timeline jump.
Run: .venv\\Scripts\\python.exe scripts\\probe_anim.py 9 [--try-jump]
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

JS_INFO = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return '{}';
const props = (o, n=80) => { try { return Object.getOwnPropertyNames(o).slice(0, n); } catch (e) { return String(e); } };
const pp = deepQ('play-pause', el)[0];
const rng = deepQ('input[type="range"]', el)[0];
const canvas = deepQ('canvas', el)[0];
let stageInfo = null;
try {
  const st = el.stage || el._stage || (el.component && el.component.stage) || null;
  if (st) {
    const root = st.children && st.children[0];
    stageInfo = {hasStage: true, children: st.children ? st.children.length : null, tickEnabled: st.tickEnabled,
                 rootProps: root ? props(root, 40) : null,
                 totalFrames: root && root.totalFrames, currentFrame: root && root.currentFrame, paused: root && root.paused,
                 timelineDuration: root && root.timeline && root.timeline.duration, loop: root && root.loop};
  } else stageInfo = {hasStage: false};
} catch (e) { stageInfo = {err: String(e)}; }
const protoMethods = (o) => { const out = []; let q = Object.getPrototypeOf(o); let d = 0; while (q && d < 4) { out.push(...Object.getOwnPropertyNames(q).filter(n => { const d = Object.getOwnPropertyDescriptor(q, n); return d && typeof d.value === 'function' && !/^(constructor|_\$|__)/.test(n); })); q = Object.getPrototypeOf(q); d++; } return out.slice(0, 120); };
const clip = el.__animClip; const clipInfo = clip ? {totalFrames: clip.totalFrames, currentFrame: clip.currentFrame, paused: clip.paused, loop: clip.loop,
  duration: clip.timeline && clip.timeline.duration, hasGotoAndPlay: typeof clip.gotoAndPlay === 'function', tickEnabled: clip.tickEnabled} : null;
const ppHandlers = pp ? Object.keys(pp).filter(k => /click|handler|toggle|play/i.test(k)) : null;
return JSON.stringify({
  viewMethods: protoMethods(el), ppMethods: pp ? protoMethods(pp) : null, clipInfo, viewPaused: el.__paused, viewFrame: el.__currentFrame,
  ppHandlers,
  viewProps: props(el), ppProps: pp ? props(pp) : null, ppAttrs: pp ? Array.from(pp.attributes).map(a => a.name + '=' + a.value) : null,
  range: rng ? {value: rng.value, max: rng.max, min: rng.min, step: rng.step} : null,
  canvas: canvas ? {w: canvas.width, h: canvas.height} : null,
  createjs: typeof window.createjs !== 'undefined', AdobeAn: typeof window.AdobeAn !== 'undefined',
  exportRootGlobal: typeof window.exportRoot !== 'undefined',
  stageInfo, complete: completion(stateDiv(el)),
});
"""
JS_JUMP = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return 'no-el';
const st = el.stage || el._stage || (el.component && el.component.stage) || null; if (!st) return 'no-stage';
const root = st.children && st.children[0]; if (!root || !root.gotoAndPlay) return 'no-root';
const total = root.totalFrames || (root.timeline && root.timeline.duration) || 0; if (!total) return 'no-total';
root.gotoAndPlay(Math.max(0, total - 3)); return 'jumped-to-' + Math.max(0, total - 3) + '/' + total;
"""


def main():
    module = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 9
    try_jump = "--try-jump" in sys.argv
    cfg = load_config(); log = get_logger("probe_anim", cfg_path(cfg, "logs"))
    structure = json.loads((cfg_path(cfg, "data") / "course_structure.json").read_text(encoding="utf-8"))
    out = {"module": module, "items": []}
    with launch(cfg) as sb:
        open_course(sb, cfg)
        for node in structure["nodes"]:
            if node.get("module_number") != module:
                continue
            for sec in node["sections"]:
                for it in sec["items"]:
                    if not it.get("id") or sec.get("leaf"):
                        continue
                    if nav.live_item_status(sb, node, sec, it) == "completed":
                        continue
                    nav.goto_item(sb, cfg, node, sec, it)
                    det = detect(cf.read_page_model(sb), it, sec)
                    anims = [c for c in det.components if c["tag"] == "adobe-animate-view" and c["complete"] is not True]
                    if not anims:
                        continue
                    mid = anims[0]["modelid"]
                    log.info("=== %s %s : animated figure %s", it["id"], it["title"], mid)
                    info = json.loads(sb.execute_script(JS_INFO, mid))
                    log.info("info: %s", json.dumps(info)[:1500])
                    cf.scroll_to(sb, mid, "center")
                    log.info("press Play, observe 6s (no seeking)")
                    log.info("play -> %s", cf.anim_cmd(sb, mid, "play"))
                    samples = []
                    for i in range(12):
                        time.sleep(0.5)
                        samples.append(cf.anim_state(sb, mid))
                    log.info("samples: %s", samples)
                    st = json.loads(sb.execute_script(JS_INFO, mid)).get("stageInfo")
                    log.info("stage after play: %s", st)
                    rec = {"item": it["id"], "modelid": mid, "info": info, "samples": samples, "stage_after_play": st}
                    # try the view's own API: find a play/toggle method name and call it
                    JS_TRY = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const name = arguments[1]; if (!el) return 'no-el';
try { if (typeof el[name] === 'function') { el[name](); return 'called ' + name; } return 'no-method ' + name; } catch (e) { return 'err ' + e.message; }
"""
                    for name in ("togglePlayback",):
                        r = sb.execute_script(JS_TRY, mid, name)
                        if r.startswith("called"):
                            smp = []
                            for i in range(6):
                                time.sleep(0.5); smp.append(cf.anim_state(sb, mid))
                            log.info("%s -> samples %s", r, smp); rec[f"try_{name}"] = smp
                            break
                    # clip-level: play via createjs MovieClip API
                    JS_CLIP = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const clip = el && el.__animClip; if (!clip) return 'no-clip';
const cmd = arguments[1], val = arguments[2];
try { if (cmd === 'play') { clip.paused = false; if (clip.play) clip.play(); return 'ok'; }
      if (cmd === 'goto') { clip.gotoAndPlay(val); return 'ok'; } } catch (e) { return 'err ' + e.message; }
return 'unknown';
"""
                    if try_jump:
                        total = (json.loads(sb.execute_script(JS_INFO, mid)).get("clipInfo") or {}).get("totalFrames") or 0
                        r = sb.execute_script(JS_CLIP, mid, "goto", max(0, total - 3)) + f" (goto {max(0,total-3)}/{total})"
                        log.info("jump -> %s", r)
                        done = cf.wait_complete(sb, mid, 20)
                        rec["jump"] = r; rec["complete_after_jump"] = done
                        rec["state_after_jump"] = cf.anim_state(sb, mid)
                        log.info("complete after jump: %s  state: %s", done, rec["state_after_jump"])
                    out["items"].append(rec)
                    (cfg_path(cfg, "recon") / "probe_anim.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
                    return 0
    log.info("no incomplete animated figure found in module %s", module)
    return 0


if __name__ == "__main__":
    sys.exit(main())
