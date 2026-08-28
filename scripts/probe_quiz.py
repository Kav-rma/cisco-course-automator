import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import content_frame as cf, navigator as nav, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger
from core.page_detector import detect
iid = sys.argv[1] if len(sys.argv)>1 else "11.3.3"
cfg=load_config(interactive=False); log=get_logger("probe_quiz",cfg_path(cfg,"logs"))
structure=json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node,sec,it=nav.locate(structure,iid)
with launch(cfg) as sb:
    open_course(sb,cfg); nav.goto_item(sb,cfg,node,sec,it)
    det=detect(cf.read_page_model(sb),it,sec)
    print("DETECT:",det.page_type.value,"| components:",[(c['tag'],c['complete']) for c in det.components])
    print("secure_state0:",qx.secure_state(sb))
    ss=qx.secure_state(sb)
    if ss.get("start_visible"):
        print("start->",qx.secure_start(sb,"click")); time.sleep(2.5)
        ss=qx.secure_state(sb); print("secure_state1:",ss)
        if not ss["mcq_ids"]:
            from handlers.activity_handler import _frame_offset,_cdp_mouse
            class C:
                def __init__(s,sb): s.sb=sb
            r=qx.secure_start(sb,"rect"); time.sleep(0.3); r=qx.secure_start(sb,"rect")
            print("start rect:",r)
            if r:
                fx,fy=_frame_offset(C(sb)); _cdp_mouse(C(sb),"mouseMoved",fx+r["x"],fy+r["y"],0); _cdp_mouse(C(sb),"mousePressed",fx+r["x"],fy+r["y"],1); _cdp_mouse(C(sb),"mouseReleased",fx+r["x"],fy+r["y"],0)
                time.sleep(2.5); print("secure_state2:",qx.secure_state(sb))
    sb.save_screenshot(str(cfg_path(cfg,"recon")/f"quiz_{iid}.png"))
    print("mcqs on page:", len(qx.extract(sb)))
