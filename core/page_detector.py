"""
Page / content-type detection for the CURRENT outline item, driven by the content-frame page model.

The detector never looks at item ids or fixed titles to decide behaviour - it classifies by the
component types actually present in the item's scope (article or block). Component tag families:
  QUESTION    mcq-view, gmcq-view, matching-view, textinput-view, slider-view, dragdrop-view, ...
  MEDIA       media-view (video.js)
  INTERACTIVE accordion-view, tabs-view, hotgraphic-view, narrative-view, flipcard-view, reveal-view, ...
  STATIC      text-view, graphic-view, dynamic-graphic-view, blank-view, quicknav-view, ...
Anything unrecognised is reported (never silently treated as static).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum


class PageType(str, Enum):
    LESSON = "LESSON"                    # static text/graphics only
    VIDEO = "VIDEO"
    INTERACTIVE = "INTERACTIVE"          # expandables etc. that need clicks to complete
    KNOWLEDGE_CHECK = "KNOWLEDGE_CHECK"  # ungraded questions (Check Your Understanding)
    ASSESSMENT = "ASSESSMENT"            # graded quiz / exam - never auto-answered
    SUMMARY = "SUMMARY"
    INTRODUCTION = "INTRODUCTION"
    LAB = "LAB"                          # Packet Tracer / hands-on lab - requires the student
    ACTIVITY = "ACTIVITY"                # canvas mini-game / interactive activity - requires the student
    UNKNOWN = "UNKNOWN"


QUESTION_TAGS = {"mcq-view", "gmcq-view", "matching-view", "object-matching-view", "textinput-view", "slider-view", "dragdrop-view",
                 "dragndrop-view", "confidenceslider-view", "opentextinput-view", "ppq-view"}
ASSESSMENT_TAGS = {"adaptive-start-screen-view", "assessment-view", "assessmentresults-view", "start-screen-view"}
LAB_TAGS = {"pagetracer-view", "packettracer-view", "lab-view", "pt-view"}
ACTIVITY_TAGS = {"adobe-animate-ia-view", "ia-view", "game-view", "simulation-view",
                 "ipv6addressrepresentation-view", "binary-to-decimal", "decimal-to-binary",
                 "anding-activity-view", "cable-pinout-view", "switch-it-view", "yesno-view"}
MEDIA_TAGS = {"media-view", "mediaplayer-view", "youtube-view", "vimeo-view", "audio-view"}
INTERACTIVE_TAGS = {"accordion-view", "adobe-animate-view", "tabs-view", "hotgraphic-view", "narrative-view", "flipcard-view", "reveal-view",
                    "hotspot-view", "carousel-view", "stepper-view", "timeline-view", "linkedopenlist-view", "branching-view",
                    "sortable-view", "flashcard-view"}
STATIC_TAGS = {"text-view", "commandwindow-view", "graphic-view", "dynamic-graphic-view", "blank-view", "quicknav-view", "notify-view",
               "buttons-view", "list-view", "table-view", "iframe-view", "signed-url-transcript", "component-selector",
               "inline-svg-viewer", "resources-view", "glossary-view", "pdf-view", "code-view", "codeblock-view"}


@dataclass
class Detection:
    page_type: PageType
    item_id: str
    scope_kind: str | None            # "article" | "block" | None
    scope_modelid: str | None
    heading: str | None
    complete: bool | None
    components: list[dict] = field(default_factory=list)   # [{tag, modelid, complete, heading}]
    families: dict = field(default_factory=dict)             # {"question": n, "media": n, "interactive": n, "static": n, "unknown": [tags]}
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["page_type"] = self.page_type.value
        return d


def _families(components: list[dict]) -> dict:
    fam = {"assessment": 0, "lab": 0, "activity": 0, "question": 0, "media": 0, "interactive": 0, "static": 0, "unknown": []}
    for c in components:
        t = (c.get("tag") or "").lower()
        if t in ASSESSMENT_TAGS:
            fam["assessment"] += 1
        elif t in LAB_TAGS:
            fam["lab"] += 1
        elif t in ACTIVITY_TAGS:
            fam["activity"] += 1
        elif t in QUESTION_TAGS:
            fam["question"] += 1
        elif t in MEDIA_TAGS:
            fam["media"] += 1
        elif t in INTERACTIVE_TAGS:
            fam["interactive"] += 1
        elif t in STATIC_TAGS:
            fam["static"] += 1
        else:
            fam["unknown"].append(t)
    return fam


def detect(model: dict, item: dict, section: dict | None = None) -> Detection:
    """Classify the outline `item` (from course_structure.json) using the frame page `model`.

    `section` (optional) is the item's section dict; a graded/leaf section forces ASSESSMENT.
    """
    from .content_frame import find_item_scope  # local import keeps this module import-light for tests

    item_id = item.get("id") or ""
    scope = find_item_scope(model, item_id, (section or {}).get("items")) if item_id else None
    if scope is None and (item.get("inferred_type") or "") == "assessment":
        return Detection(PageType.ASSESSMENT, item_id, None, None, None, None, [], {},
                         ["quiz/exam item (by title) whose unit has no heading on this page - left for the student"])
    if scope is None:
        return Detection(PageType.UNKNOWN, item_id, None, None, None, None, [], {},
                         [f"no article/block heading starting with '{item_id}' on page {model.get('location_id')}",
                          f"headings present: {[h for h in _headings(model)][:20]}"])

    comps = [{"tag": c["tag"], "modelid": c["modelid"], "complete": c["complete"], "heading": c.get("heading")} for c in scope["components"]]
    fam = _families(comps)
    reasons: list[str] = []
    hint = (item.get("inferred_type") or "").lower()
    title = (item.get("title") or "").lower()

    if fam["assessment"]:
        graded_ctx = bool(section and (section.get("graded") or section.get("leaf")))
        if hint == "knowledge_check" and not graded_ctx:
            # CCNA-style "Check Your Understanding": same start-screen component as quizzes, but ungraded,
            # one question at a time (secure-one-question). Handled by the knowledge-check handler.
            ptype = PageType.KNOWLEDGE_CHECK
            reasons.append("ungraded start-screen check (one question at a time)")
        else:
            ptype = PageType.ASSESSMENT
            reasons.append("assessment start-screen / results component present")
    elif fam["lab"]:
        ptype = PageType.LAB
        reasons.append("hands-on lab component present (Packet Tracer)")
    elif fam["activity"]:
        ptype = PageType.ACTIVITY
        reasons.append("interactive canvas activity present (needs the student)")
    elif fam["question"]:
        graded = bool(section and (section.get("graded") or section.get("leaf")))
        import re as _re
        if graded or hint == "assessment" or ({"quiz", "exam"} & set(_re.findall("[a-z]+", title))):
            ptype = PageType.ASSESSMENT
            reasons.append("question components in a graded/quiz context")
        else:
            ptype = PageType.KNOWLEDGE_CHECK
            reasons.append(f"{fam['question']} question component(s)")
    elif fam["media"]:
        ptype = PageType.VIDEO
        reasons.append(f"{fam['media']} media component(s)")
    elif fam["interactive"]:
        ptype = PageType.INTERACTIVE
        reasons.append(f"{fam['interactive']} interactive component(s)")
    elif fam["unknown"] and not fam["static"]:
        ptype = PageType.UNKNOWN
        reasons.append(f"only unrecognised components: {fam['unknown']}")
    elif comps or scope["kind"] == "article":
        if hint == "summary" or "what did i learn" in title:
            ptype = PageType.SUMMARY
        elif hint == "introduction":
            ptype = PageType.INTRODUCTION
        else:
            ptype = PageType.LESSON
        reasons.append(f"static components only ({fam['static']})")
    else:
        ptype = PageType.UNKNOWN
        reasons.append("scope found but it has no components")

    if fam["unknown"]:
        reasons.append(f"NOTE unrecognised component tags present: {fam['unknown']}")
    if hint == "assessment" and ptype not in (PageType.ASSESSMENT, PageType.UNKNOWN, PageType.KNOWLEDGE_CHECK):
        # Title says quiz/exam but no question components rendered yet (launcher page) -> stay safe.
        ptype = PageType.ASSESSMENT
        reasons.append("outline title indicates a quiz/exam; treated as ASSESSMENT regardless of components")

    return Detection(ptype, item_id, scope["kind"], scope["modelid"], scope["heading"], scope["complete"], comps, fam, reasons)


def _headings(model: dict):
    for a in model.get("articles", []):
        if (a.get("heading") or {}).get("title"):
            yield a["heading"]["title"]
        for b in a.get("blocks", []):
            if (b.get("heading") or {}).get("title"):
                yield b["heading"]["title"]
