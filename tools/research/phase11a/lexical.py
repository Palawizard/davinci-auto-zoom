"""Discourse markers and sentence boundaries, as *measurable features* of a cut.

Two traps this module exists to avoid.

**A word is not a boundary.** "donc" appears inside clauses all the time ("donc si tu fais
ça..."), and counting every occurrence as a reset cue is how a rule reaches 100% recall and
useless precision. So a marker is only recorded together with *where* it sits: at the start of
the segment after the cut, at the end of the segment before it, or somewhere in the middle.
The study reports the three positions separately.

**A seed list is not a vocabulary.** The creator named `et donc` and `du coup`. Those are seeds,
not a lexicon; anything else here comes from a linguistic category (consequence, topic shift,
enumeration, contrast, summary) rather than from reading the labels and adding whatever word
happened to be there. `MARKERS` is small and categorised on purpose — a thirty-entry list fitted
to 28 cuts would be memorisation, not a finding.

ASR punctuation is a *prediction* of the model, never ground truth, and is reported as its own
feature so a result that depends on it can be recognised as depending on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.research.phase11a.words import AlignedWord

CONSEQUENCE = "consequence"
TOPIC_SHIFT = "topic_shift"
ENUMERATION = "enumeration"
CONTRAST = "contrast"
SUMMARY = "summary"

#: French discourse markers by category, longest phrase first within a category so that
#: "du coup" wins over a bare "coup". The two seeds the creator gave are in `CONSEQUENCE`.
MARKERS: dict[str, tuple[tuple[str, ...], ...]] = {
    CONSEQUENCE: (
        ("et", "donc"),
        ("du", "coup"),
        ("alors",),
        ("donc",),
    ),
    TOPIC_SHIFT: (
        ("et", "sinon"),
        ("bref",),
        ("sinon",),
        ("maintenant",),
    ),
    ENUMERATION: (
        ("et", "ensuite"),
        ("et", "après"),
        ("ensuite",),
        ("puis",),
    ),
    CONTRAST: (
        ("par", "contre"),
        ("mais",),
        ("pourtant",),
    ),
    SUMMARY: (
        ("en", "gros"),
        ("voilà",),
        ("finalement",),
    ),
}

#: Where the marker sits relative to the cut. The distinction is the whole point.
AT_SEGMENT_START = "at_segment_start"
AT_SEGMENT_END = "at_segment_end"
MID_SEGMENT = "mid_segment"

#: Punctuation the ASR uses to close a sentence.
SENTENCE_ENDINGS = frozenset(".!?…")
CLAUSE_ENDINGS = frozenset(",;:")


@dataclass(frozen=True, slots=True)
class MarkerHit:
    category: str
    phrase: str
    position: str
    #: Index into the word sequence the hit was found in. Kept so a hit can be located again.
    index: int


def find_markers(words: tuple[AlignedWord, ...], *, side: str) -> tuple[MarkerHit, ...]:
    """Every marker in `words`, tagged by where it sits.

    `side` is `"after"` for the words following a cut and `"before"` for those preceding it;
    it decides which end of the sequence counts as the interesting position.
    """

    if side not in {"before", "after"}:
        raise ValueError("side must be 'before' or 'after'")
    tokens = [word.normalized_text for word in words]
    hits: list[MarkerHit] = []
    for category, phrases in MARKERS.items():
        # Tokens a longer phrase of this category already claimed, so "et donc" is never also
        # counted as a bare "donc". Categories are independent of each other.
        claimed: set[int] = set()
        for phrase in sorted(phrases, key=len, reverse=True):
            for index in range(len(tokens) - len(phrase) + 1):
                span = range(index, index + len(phrase))
                if claimed.intersection(span):
                    continue
                if tuple(tokens[index : index + len(phrase)]) != phrase:
                    continue
                if side == "after" and index == 0:
                    position = AT_SEGMENT_START
                elif side == "before" and index + len(phrase) == len(tokens):
                    position = AT_SEGMENT_END
                else:
                    position = MID_SEGMENT
                hits.append(
                    MarkerHit(
                        category=category,
                        phrase=" ".join(phrase),
                        position=position,
                        index=index,
                    )
                )
                claimed.update(span)
    return tuple(sorted(hits, key=lambda hit: (hit.index, hit.category)))


@dataclass(frozen=True, slots=True)
class PunctuationEvidence:
    """What the ASR's own punctuation says about the boundary at a cut."""

    #: The last word before the cut ends a sentence (`.`, `!`, `?`, `…`).
    sentence_ends_before: bool
    #: ...or a clause (`,`, `;`, `:`).
    clause_ends_before: bool
    #: The first word after the cut is capitalised, which the model uses for a new sentence.
    capitalised_after: bool

    @property
    def boundary(self) -> bool:
        return self.sentence_ends_before or self.capitalised_after


def punctuation_evidence(
    before: tuple[AlignedWord, ...], after: tuple[AlignedWord, ...]
) -> PunctuationEvidence:
    last = before[-1].text.rstrip() if before else ""
    first = after[0].text.lstrip() if after else ""
    return PunctuationEvidence(
        sentence_ends_before=bool(last) and last[-1] in SENTENCE_ENDINGS,
        clause_ends_before=bool(last) and last[-1] in CLAUSE_ENDINGS,
        # A single-letter token like "J'" is not evidence; require a real word.
        capitalised_after=len(first) > 1 and first[0].isupper(),
    )


__all__ = [
    "AT_SEGMENT_END",
    "AT_SEGMENT_START",
    "CLAUSE_ENDINGS",
    "CONSEQUENCE",
    "CONTRAST",
    "ENUMERATION",
    "MARKERS",
    "MID_SEGMENT",
    "SENTENCE_ENDINGS",
    "SUMMARY",
    "TOPIC_SHIFT",
    "MarkerHit",
    "PunctuationEvidence",
    "find_markers",
    "punctuation_evidence",
]
