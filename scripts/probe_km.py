"""Probe the object-matching KC on 37.2.3: dump categories/options, click category->option for each pair,
dump state after each and after Submit, to see why the unit stays 'in progress'. Read-only-ish (it interacts
with an UNGRADED practice KC to diagnose completion; it does not touch graded assessments)."""
import json, sys, time
from pathlib import Path
SCR=Path(__file__).resolve().parent; sys.path.insert(0,str(SCR.parent)); sys.path.insert(0,str(SCR))
from core import content_frame as cf, navigator as nav, question_extractor as qx
from core.browser import launch, open_course
from core.config import load_config, path as cfg_path
from core.logger import get_logger

def _pop(a,f):
    if f in a: i=a.index(f);v=a[i+1];del a[i:i+2];return v
    return None

cfg=load_config(course=_pop(sys.argv[1:],"--course")); log=get_logger("probe_km",cfg_path(cfg,"logs"))
structure=json.loads((cfg_path(cfg,"data")/"course_structure.json").read_text(encoding="utf-8"))
node,sec,it=nav.locate(structure,"37.2.3")
with launch(cfg) as sb:
    open_course(sb,cfg); nav.goto_item(sb,cfg,node,sec,it); time.sleep(1.5)
    oms=qx.extract_object_matching(sb, qx.object_matching_ids(nav_det:=__import__("core.page_detector",fromlist=["detect"]).detect(cf.read_page_model(sb),it,sec)))
    if not oms:
        oms=qx.extract_object_matching(sb)
    log.info("object-matching count: %d", len(oms))
    if not oms: sys.exit()
    q=oms[0]; mid=q["modelid"]
    log.info("Q: %s", (q.get("question") or "")[:80])
    log.info("cats: %s", [(c["id"],c["text"][:20],c["cls"][:30]) for c in q["categories"]])
    log.info("opts: %s", [(o["id"],o["text"][:20],o["cls"][:30]) for o in q["options"]])
    log.info("submit_enabled=%s submitted=%s complete=%s", q.get("submit_enabled"), q.get("submitted"), q.get("complete"))
    # click category id then option id for each category
    for c in q["categories"]:
        cid=c["id"]
        r1,r2=qx.object_matching_pair(sb, mid, cid)
        time.sleep(0.4)
        st=qx.extract_object_matching(sb,[mid])[0]
        cat=next((x for x in st["categories"] if x["id"]==cid),{})
        opt=next((x for x in st["options"] if x["id"]==cid),{})
        log.info("pair cid=%s -> cat_click=%s opt_click=%s | cat_cls=%s opt_cls=%s submit_enabled=%s",
                 cid, r1, r2, cat.get("cls","")[:35], opt.get("cls","")[:35], st.get("submit_enabled"))
    st=qx.extract_object_matching(sb,[mid])[0]
    log.info("before submit: submit_enabled=%s submitted=%s complete=%s", st.get("submit_enabled"),st.get("submitted"),st.get("complete"))
    r=qx.submit_view(sb, mid, cfg["timeouts"]["element"], qx.extract_object_matching)
    log.info("submit -> %s", r); time.sleep(1.5)
    st=qx.extract_object_matching(sb,[mid])[0]
    log.info("after submit: submitted=%s complete=%s", st.get("submitted"), st.get("complete"))
    log.info("unit complete: %s", cf.is_complete(sb, cf.find_item_scope(cf.read_page_model(sb), it["id"]) or mid))
