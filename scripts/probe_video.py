"""Inspect a media/video component: its tag tree, whether it's a YouTube iframe, and what completion hooks the
component model exposes. Read-only. Run: ... probe_video.py --course networking-essentials --item 7.2.2"""
import json, sys, time
from pathlib import Path
SCR=Path(__file__).resolve().parent; sys.path.insert(0,str(SCR.parent)); sys.path.insert(0,str(SCR))
from core import content_frame as cf, navigator as nav
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect

def _pop(a,f):
    if f in a: i=a.index(f);v=a[i+1];del a[i:i+2];return v
    return None

JS = cf.JS_BY_ID + r"""
const el = byId(arguments[0]); if(!el) return JSON.stringify({err:'no component'});
const all=[]; (function walk(n){ if(!n) return; all.push(n.tagName?n.tagName.toLowerCase():'#'+n.nodeName);
  if(n.shadowRoot) Array.from(n.shadowRoot.children).forEach(walk); Array.from(n.children||[]).forEach(walk); })(el);
const iframes = deepQ('iframe', el).map(f=>({src:(f.src||'').slice(0,80)}));
const videos = deepQ('video', el).length;
let model={}; try{ const m=el.model; const a=m.attributes?JSON.parse(JSON.stringify(m.attributes)):{};
  model={keys:Object.keys(a), isComplete:a._isComplete, isInteractionComplete:a._isInteractionComplete,
         completionObject:a._requireCompletionOf, media:a._media}; }catch(e){ model={err:String(e)}; }
let methods=[]; try{ let q=Object.getPrototypeOf(el.model); let d=0;
  while(q&&d<3){ methods.push(...Object.getOwnPropertyNames(q).filter(n=>typeof q[n]==='function')); q=Object.getPrototypeOf(q); d++; } }catch(e){}
const complMethods = methods.filter(n=>/complet|played|ended|finish|setComp/i.test(n));
return JSON.stringify({tagCounts: all.reduce((o,t)=>{o[t]=(o[t]||0)+1;return o;},{}),
  iframes, videos, model, completionMethods: complMethods.slice(0,20), complete: completion(stateDiv(el))});
"""
a=sys.argv[1:]; course=_pop(a,"--course"); item=_pop(a,"--item") or "7.2.2"
cfg=load_config(course=course); log=get_logger("probe_video",cfg_path(cfg,"logs"))
structure=json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node,sec,it=nav.locate(structure,item)
with launch(cfg) as sb:
    open_course(sb,cfg); nav.goto_item(sb,cfg,node,sec,it); time.sleep(2.0)
    det=detect(cf.read_page_model(sb),it,sec)
    media=[c for c in det.components if c["tag"] in ("media-view","mediaplayer-view","youtube-view","vimeo-view")]
    log.info("media components: %s", [(c["tag"],c["modelid"][:8]) for c in media])
    for c in media:
        cf.scroll_to(sb,c["modelid"],"center"); time.sleep(1.5)
        d=json.loads(sb.execute_script(JS,c["modelid"]))
        log.info("== %s %s", c["tag"], c["modelid"][:8])
        log.info("   videos=%s iframes=%s", d.get("videos"), d.get("iframes"))
        log.info("   tagCounts(video/iframe-ish)=%s", {k:v for k,v in d.get("tagCounts",{}).items() if k in ('video','iframe','object','embed','media-view','div') })
        log.info("   model.keys=%s", d.get("model",{}).get("keys"))
        log.info("   model.isComplete=%s media=%s", d.get("model",{}).get("isComplete"), d.get("model",{}).get("media"))
        log.info("   completionMethods=%s", d.get("completionMethods"))
        log.info("   complete=%s", d.get("complete"))
