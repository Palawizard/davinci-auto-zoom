"""Discourse rubric V2 — what the creator's answer for 219525 forces us to add.

The frozen V1 rubric (`tools/research/phase11b/annotations.py`) has no category for a
concessive or adversative PIVOT. It classified the cut at 219525 `SUBORDINATE_CLAUSE`, i.e. a
continuation, on correct grammatical grounds: what follows really is a subordinate clause of
the sentence before it. The creator reset there anyway, and said why — the clause turns the
statement around ("even if..."). Grammatically subordinate, editorially a new breath.

V2 therefore separates two things V1 collapsed:

    the SEMANTIC CATEGORY          what the discourse does at this cut
    the RESET CANDIDATE decision   whether that is worth returning to X0 for

`CONTRAST_OR_CONCESSION` is a category, not a trigger. It is a reset candidate only when the
marker OPENS the clause after the cut and functions as a pivot there. The single measured
counter-example in three Shorts (216701) carries the same word as two of the positives, 19
frames into a running clause, as a plain intensifier, and the creator does not reset for it.

NO WORDLIST RULE IS PROPOSED. `MARKER_OCCURRENCES` is the complete record of every
concessive/adversative/reformulation marker found in the three Shorts, positive and negative,
so the sample size behind the category is visible: it is small.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.research.phase11b.policy import (
    AMBIGUOUS,
    ANSWER_TO_HOOK,
    APPOSITION,
    CONCLUSION,
    CONSEQUENCE,
    CONTRAST,
    DISCOURSE_BOUNDARY,
    EXAMPLE_CONTINUES,
    EXPLANATION_CONTINUES,
    LIST_CONTINUES,
    NEW_STEP,
    NEW_TOPIC,
    SAME_THOUGHT_CONTINUES,
    SUBORDINATE_CLAUSE,
    TAKE_SPLICE,
)

#: New in V2. V1's `CONTRAST` covered only an adversative that already opened a new SENTENCE;
#: this covers the pivot that opens a subordinate clause and still turns the thought around.
CONTRAST_OR_CONCESSION = "CONTRAST_OR_CONCESSION"
#: New in V2, and reported separately because its evidence is a single occurrence.
REFORMULATION = "REFORMULATION"

#: category -> is this a reset candidate. The mapping IS the rubric; nothing else decides.
RESET_CANDIDATE: dict[str, bool] = {
    ANSWER_TO_HOOK: True,
    NEW_TOPIC: True,
    NEW_STEP: True,
    CONSEQUENCE: True,
    CONCLUSION: True,
    CONTRAST: True,
    CONTRAST_OR_CONCESSION: True,
    REFORMULATION: True,
    EXPLANATION_CONTINUES: False,
    EXAMPLE_CONTINUES: False,
    LIST_CONTINUES: False,
    SUBORDINATE_CLAUSE: False,
    APPOSITION: False,
    TAKE_SPLICE: False,
    AMBIGUOUS: False,
}

#: The V1 judgements V2 changes, and only these. Every other cut of the three Shorts keeps the
#: category the frozen rubric gave it — V2 is an addition, not a re-annotation.
V2_DELTA: dict[int, str] = {
    217373: CONTRAST_OR_CONCESSION,  # Short 1: a concessive clause opens right after the cut
    219525: CONTRAST_OR_CONCESSION,  # Short 3: the creator's own example, D071
}

#: Reported as a separate variant because one occurrence is not evidence for a category.
V2_EXTENDED_DELTA: dict[int, str] = dict(V2_DELTA) | {218735: REFORMULATION}


@dataclass(frozen=True, slots=True)
class MarkerOccurrence:
    """One concessive/adversative/reformulation marker, with its position and its outcome.

    `frames_after_cut` is what separates a pivot from a word that merely occurs: a marker that
    opens the clause after the cut sits a few frames past it, and one that is buried inside a
    running clause does not.
    """

    frame: int
    island_index: int
    #: The hard cut it follows, and how far after it the marker starts.
    cut: int
    frames_after_cut: int
    #: Clause-initial after the cut, or inside a running clause.
    clause_initial: bool
    #: The manual reset attributable to it, or None.
    reset: int | None
    note: str


#: Every occurrence in Shorts 1-3, positive AND negative. Seven in total: six clause-initial,
#: all six with a reset, and one mid-clause with none attributable to it.
MARKER_OCCURRENCES: tuple[MarkerOccurrence, ...] = (
    MarkerOccurrence(216701, 0, 216682, 19, False, None, "intensifier inside a running clause"),
    MarkerOccurrence(217055, 0, 217055, 0, True, 216991, "adversative opening a new sentence"),
    MarkerOccurrence(217381, 0, 217373, 8, True, 217373, "concessive clause, V1 called it SUB"),
    MarkerOccurrence(217838, 0, 217835, 3, True, 217835, "on the loop cut; the loop rule owns it"),
    MarkerOccurrence(218738, 1, 218735, 3, True, 218735, "reformulation of the conclusion"),
    MarkerOccurrence(218831, 1, 218828, 3, True, 218828, "on the loop cut; the loop rule owns it"),
    MarkerOccurrence(219529, 2, 219525, 4, True, 219525, "the creator's own example, D071"),
)

#: The window that separated the six clause-initial markers from the one mid-clause one. It is
#: a DESCRIPTION of seven points, not a tuned threshold: the six sit at 0-8 frames and the one
#: at 19, and nothing lies between.
CLAUSE_INITIAL_MAX_FRAMES = 10


def boundary_frames(
    v1_boundary: frozenset[int], *, extended: bool = False
) -> frozenset[int]:
    """V1's boundary cuts plus the ones V2 promotes. Never removes one."""

    delta = V2_EXTENDED_DELTA if extended else V2_DELTA
    return v1_boundary | {
        frame for frame, category in delta.items() if RESET_CANDIDATE[category]
    }


def marker_summary() -> dict[str, int]:
    """Clause-initial occurrences with and without a reset, and the mid-clause ones."""

    initial = [m for m in MARKER_OCCURRENCES if m.clause_initial]
    return {
        "clause_initial": len(initial),
        "clause_initial_with_reset": sum(1 for m in initial if m.reset is not None),
        "mid_clause": len(MARKER_OCCURRENCES) - len(initial),
        "mid_clause_with_reset": sum(
            1 for m in MARKER_OCCURRENCES if not m.clause_initial and m.reset is not None
        ),
    }


__all__ = [
    "CLAUSE_INITIAL_MAX_FRAMES",
    "CONTRAST_OR_CONCESSION",
    "MARKER_OCCURRENCES",
    "REFORMULATION",
    "RESET_CANDIDATE",
    "V2_DELTA",
    "V2_EXTENDED_DELTA",
    "DISCOURSE_BOUNDARY",
    "SAME_THOUGHT_CONTINUES",
    "MarkerOccurrence",
    "boundary_frames",
    "marker_summary",
]
