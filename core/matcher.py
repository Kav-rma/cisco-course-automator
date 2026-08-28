"""
Question matching against the local bank. Conservative by design: exact normalized match first,
fuzzy (difflib) only as a fallback, and MATCH_NOT_CONFIDENT whenever the evidence is weak.
Never relies on question numbers, option order, or screen position.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

MATCH_NOT_CONFIDENT = "MATCH_NOT_CONFIDENT"
CONFIDENT = 0.93        # fuzzy ratio at/above which a question match is trusted
AMBIGUOUS_GAP = 0.03    # best must beat runner-up by at least this
OPTION_CONFIDENT = 0.90

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = t.lower()
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


@dataclass
class Match:
    matched: bool
    status: str                      # "EXACT" | "FUZZY" | MATCH_NOT_CONFIDENT
    confidence: float
    question_id: str | None = None
    lesson_id: str | None = None
    entry: dict | None = None
    candidates: list = field(default_factory=list)   # [(question_id, score)] top few, for diagnostics


class QuestionBank:
    def __init__(self, entries: list[dict]):
        self.entries = entries
        self._by_norm: dict[str, list[dict]] = {}
        for e in entries:
            self._by_norm.setdefault(normalize(e["question"]), []).append(e)

    @classmethod
    def load(cls, path) -> "QuestionBank":
        import json
        from pathlib import Path
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"questions": []}
        return cls(data.get("questions", []))

    def match(self, question_text: str, option_texts: list[str] | None = None) -> Match:
        qn = normalize(question_text)
        if not qn:
            return Match(False, MATCH_NOT_CONFIDENT, 0.0)
        exact = self._by_norm.get(qn, [])
        if exact:
            e = _disambiguate(exact, option_texts)
            if e is not None:
                return Match(True, "EXACT", 1.0, e["question_id"], e["lesson_id"], e, [(x["question_id"], 1.0) for x in exact])
            return Match(False, MATCH_NOT_CONFIDENT, 1.0, candidates=[(x["question_id"], 1.0) for x in exact])
        scored = sorted(((similarity(question_text, e["question"]), e) for e in self.entries), key=lambda x: -x[0])[:5]
        cands = [(e["question_id"], round(s, 3)) for s, e in scored]
        if not scored:
            return Match(False, MATCH_NOT_CONFIDENT, 0.0)
        best_s, best = scored[0]
        second_s = scored[1][0] if len(scored) > 1 else 0.0
        if best_s >= CONFIDENT and (best_s - second_s) >= AMBIGUOUS_GAP:
            if option_texts and not _options_consistent(best, option_texts):
                return Match(False, MATCH_NOT_CONFIDENT, best_s, candidates=cands)
            return Match(True, "FUZZY", best_s, best["question_id"], best["lesson_id"], best, cands)
        return Match(False, MATCH_NOT_CONFIDENT, best_s, candidates=cands)


def _options_consistent(entry: dict, option_texts: list[str]) -> bool:
    """All bank options should appear among the on-screen options (order-independent, fuzzy per option)."""
    shown = [normalize(t) for t in option_texts]
    for o in entry.get("options", []):
        on = normalize(o["text"])
        if on in shown:
            continue
        if max((difflib.SequenceMatcher(None, on, s).ratio() for s in shown), default=0) < OPTION_CONFIDENT:
            return False
    return True


def _disambiguate(entries: list[dict], option_texts: list[str] | None):
    if len(entries) == 1:
        return entries[0] if (not option_texts or _options_consistent(entries[0], option_texts)) else None
    if not option_texts:
        return None
    ok = [e for e in entries if _options_consistent(e, option_texts)]
    if len(ok) == 1:
        return ok[0]
    # The same question can appear in several lessons; if every consistent entry agrees on the answer, it is safe.
    if ok and len({tuple(sorted(normalize(t) for t in e.get("correct_texts", []))) for e in ok}) == 1:
        return ok[0]
    return None


def pick_correct_on_screen(entry: dict, shown_options: list[dict]) -> list[dict] | None:
    """Map the bank's correct option TEXTS onto the on-screen options (shuffled). Returns the on-screen
    option dicts to select, or None if any correct option cannot be located confidently."""
    chosen = []
    for ct in entry.get("correct_texts", []):
        best, best_s = None, 0.0
        for o in shown_options:
            s = 1.0 if normalize(o["text"]) == normalize(ct) else similarity(o["text"], ct)
            if s > best_s:
                best, best_s = o, s
        if best is None or best_s < OPTION_CONFIDENT:
            return None
        chosen.append(best)
    return chosen or None
