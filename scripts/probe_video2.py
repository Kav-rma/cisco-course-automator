"""After clicking the media poster, inspect what loads: YouTube iframe, window.YT, a reachable player we can
seek/play, and whether the component completes naturally. Read-only diagnosis."""
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

JS_POSTER = cf.JS_BY_ID + r"""
const el=byId(arguments[0]); if(!el) return 'no-el';
const sel='.mediaplayer__play, .mejs__overlay-button, [class*="play"], [class*="poster"], button, [role=button]';
const cands=deepQ(sel, el).filter(e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length));
if(cands.length){ cands[0].scrollIntoView({block:'center'}); cands[0].click(); return 'clicked:'+cands[0].className.slice(0,40); }
el.click(); return 'clicked-component';
"""
JS_INSPECT = cf.JS_BY_ID + r"""
const el=byId(arguments[0]); if(!el) return JSON.stringify({err:'no-el'});
const ifr=deepQ('iframe', el).map(f=>({src:(f.src||'').slice(0,70), id:f.id}));
const hasYT = typeof window.YT!=='undefined' && !!(window.YT&&window.YT.Player);
let players=[]; try{ if(window.YT&&YT.get){} }catch(e){}
// look for a player object on the component model
let modelPlayer=null; try{ const m=el.model; modelPlayer = !!(m && (m.player||m._player)); }catch(e){}
return JSON.stringify({iframes:ifr, hasYT, videosInComponent:deepQ('video',el).length,
  modelPlayer, complete: completion(stateDiv(el))});
"""
a=sys.argv[1:]; course=_pop(a,"--course"); item=_pop(a,"--item") or "7.2.2"
cfg=load_config(course=course); log=get_logger("probe_video2",cfg_path(cfg,"logs"))
structure=json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node,sec,it=nav.locate(structure,item)
with launch(cfg) as sb:
    open_course(sb,cfg); nav.goto_item(sb,cfg,node,sec,it); time.sleep(2.0)
    det=detect(cf.read_page_model(sb),it,sec)
    mid=[c["modelid"] for c in det.components if c["tag"]=="media-view"][0]
    cf.scroll_to(sb,mid,"center"); time.sleep(1.0)
    log.info("before click: %s", sb.execute_script(JS_INSPECT, mid))
    log.info("poster click -> %s", sb.execute_script(JS_POSTER, mid)); time.sleep(4.0)
    log.info("after click : %s", sb.execute_script(JS_INSPECT, mid))
    # watch for natural completion for a bit
    for i in range(6):
        time.sleep(3.0)
        d=json.loads(sb.execute_script(JS_INSPECT, mid))
        log.info("  t+%ds complete=%s videos=%s", (i+1)*3, d.get("complete"), d.get("videosInComponent"))
        if d.get("complete") is True: break
