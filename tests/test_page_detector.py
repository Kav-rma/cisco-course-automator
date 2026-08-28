"""Offline page-detector tests on page-model fixtures captured from the live course (tests/fixtures)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.content_frame import build_units, find_item_scope  # noqa: E402
from core.page_detector import PageType, detect  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
STRUCT = json.loads((ROOT / "data" / "networking-essentials" / "course_structure.json").read_text(encoding="utf-8"))


def model(sec):
    return json.loads((FIX / f"page_model_{sec}.json").read_text(encoding="utf-8"))


def section(sec_id):
    for n in STRUCT["nodes"]:
        for s in n["sections"]:
            if s["id"] == sec_id:
                return s
    raise KeyError(sec_id)


def item(sec, iid):
    return next(i for i in section(sec)["items"] if i["id"] == iid)


@pytest.mark.parametrize("sec,iid,expected,tags", [
    ("3.0", "3.0.1", PageType.INTRODUCTION, ["text-view", "graphic-view"]),
    ("3.0", "3.0.2", PageType.INTRODUCTION, ["table-view", "quicknav-view"]),
    ("3.1", "3.1.1", PageType.VIDEO, ["media-view"]),
    ("3.1", "3.1.2", PageType.VIDEO, ["media-view"]),
    ("3.1", "3.1.3", PageType.INTERACTIVE, ["accordion-view", "blank-view"]),
    ("3.1", "3.1.4", PageType.KNOWLEDGE_CHECK, ["mcq-view", "mcq-view", "quicknav-view"]),
    ("3.3", "3.3.1", PageType.INTERACTIVE, ["accordion-view"]),
    ("3.3", "3.3.2", PageType.LESSON, ["text-view", "graphic-view", "blank-view"]),
    ("3.3", "3.3.3", PageType.ASSESSMENT, ["adaptive-start-screen-view"]),
])
def test_detect(sec, iid, expected, tags):
    det = detect(model(sec), item(sec, iid), section(sec))
    assert det.page_type == expected, det.reasons
    assert [c["tag"] for c in det.components] == tags


def test_units_3_1():
    units = build_units(model("3.1"))
    assert [u["item_id"] for u in units] == [None, "3.1.1", "3.1.2", "3.1.3", "3.1.4"]
    assert units[-1]["kind"] == "article" and len(units[-1]["blocks"]) == 3  # Q1 block, Q2 block (next article), quicknav block


def test_anonymous_quiz_maps_to_unmatched_item():
    scope = find_item_scope(model("3.3"), "3.3.3", section("3.3")["items"])
    assert scope and scope["anonymous"] and scope["components"][0]["tag"] == "adaptive-start-screen-view"


def test_unknown_when_item_absent():
    det = detect(model("3.1"), {"id": "9.9.9", "title": "x", "inferred_type": "content"}, None)
    assert det.page_type == PageType.UNKNOWN and "headings present" in det.reasons[1]


def test_completion_flags_are_booleans_for_rendered_units():
    for u in build_units(model("3.1")):
        if u["kind"] != "preamble":
            assert u["complete"] in (True, False)
