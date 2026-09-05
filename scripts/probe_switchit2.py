"""Probe v2: find buttons via deep shadow walk, read pointer geometry, solve one round and Check."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

JS = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if (!el) return JSON.stringify({err:'no component'});
// deep-walk every shadow root under the component
const all = []; (function walk(n){ if(!n) return; all.push(n);
  if (n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
const txt = (e) => (e ? (e.textContent||'').replace(/\s+/g,' ').trim() : null);
const buttons = all.filter(e => e.tagName==='BUTTON' || e.getAttribute && e.getAttribute('role')==='button')
   .map(b => ({text: txt(b), cls: String(b.className).slice(0,60), disabled: !!b.disabled,
               rect: (r=>({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width)}))(b.getBoundingClientRect())}));
const ptr = all.find(e => /pointer-image/.test(String(e.className)));
const main = all.find(e => /main-image/.test(String(e.className)));
const geo = (e) => e ? (r=>({x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}))(e.getBoundingClientRect()) : null;
const feedback = all.filter(e => /feedback|notify|correct|incorrect|result/i.test(String(e.className)))
   .map(e=>({cls:String(e.className).slice(0,50), t:txt(e).slice(0,140)})).filter(f=>f.t).slice(0,6);
return JSON.stringify({buttons, ptr:{geo:geo(ptr), style: ptr?ptr.getAttribute('style'):null, src: ptr?ptr.getAttribute('src'):null},
                       main:{geo:geo(main)}, feedback, complete: completion(stateDiv(el))});
"""
JS_ACT = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const want=String(arguments[1]).toLowerCase();
const all=[]; (function walk(n){ if(!n) return; all.push(n);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
const b = all.find(x => (x.tagName==='BUTTON') && (x.textContent||'').trim().toLowerCase()===want);
if(!b) return 'not-found'; b.scrollIntoView({block:'center'}); b.click(); return 'clicked';
"""
JS_TICK = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); const labels = arguments[1];
const all=[]; (function walk(n){ if(!n) return; all.push(n);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk);
  Array.from(n.children||[]).forEach(walk); })(el);
let done=[];
for (const want of labels) {
  const opt = all.find(o => /(^| )option( |$)/.test(String(o.className)) && o.getAttribute('aria-label')===want);
  if (!opt) continue;
  const i = opt.querySelector('input');
  if (i && !i.checked) { i.click(); done.push(want); }
}
return JSON.stringify(done);
"""

cfg = load_config(course=None); log = get_logger("probe_switchit2", cfg_path(cfg, "logs"))
structure = json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node, sec, it = nav.locate(structure, "21.4.6")
mid = "7502db7b-4a1e-489e-a882-a2799c463951"
with launch(cfg) as sb:
    open_course(sb, cfg); nav.goto_item(sb, cfg, node, sec, it); time.sleep(1.5)
    st = json.loads(sb.execute_script(JS, mid))
    log.info("BUTTONS: %s", [(b["text"], b["cls"][:24]) for b in st["buttons"]])
    log.info("POINTER: %s", st["ptr"])
    log.info("MAIN IMG: %s", st["main"])
    log.info("complete=%s feedback=%s", st["complete"], st["feedback"])
    # solve: dest 0C is on Fa5 -> tick Fa5 + 'unicast ... specific port only'
    r = sb.execute_script(JS_TICK, mid, ["Fa5", "Frame is a unicast frame and will be sent to specific port only."])
    log.info("ticked: %s", r)
    log.info("click Check -> %s", sb.execute_script(JS_ACT, mid, "Check")); time.sleep(2.0)
    st2 = json.loads(sb.execute_script(JS, mid))
    log.info("AFTER CHECK feedback=%s complete=%s", st2["feedback"], st2["complete"])
    (cfg_path(cfg,"recon")/"switchit_live2.json").write_text(json.dumps({"before":st,"after":st2},indent=1,ensure_ascii=False),encoding="utf-8")
