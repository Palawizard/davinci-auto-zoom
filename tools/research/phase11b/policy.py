"""The frozen Phase 11b reset candidates, and the blind prediction record they produce.

Everything here was fixed on the DEVELOPMENT SET (Shorts 1 and 2 of `Timeline 1 copy`) and
committed **before** the blind Short's manual zooms were looked at. Three ingredients:

    SEMANTIC   a hard cut where the discourse itself turns over. Annotated by the coding agent
               from the local transcript against a rubric frozen on the development set, and it
               is `AGENT_SEMANTIC_REASONING` — not something DAZ can compute today.
    RHYTHM     `state == FACE_X3` at a hard cut. Measured, not assumed: in the development set
               every one of the six hard cuts that happens while the picture is at FACE_X3
               carries a reset, and no temporal threshold is needed to say so
               (`LOCALLY_COMPUTABLE_STRUCTURAL_SIGNAL`).
    LOOP       the last hard cut of a content island, which the creator uses to make the Short
               loop at X0. Stated by the creator, 2/2 in Phase 11a (`USER RULE`).

All three are gated by the transition graph: a reset is only legal from a face state, so a cut
where the simulated state is X0 can never receive one. That gate is structure, not a model.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from davinci_auto_zoom.domain.transitions import STATE_FACE_X3, STATE_X0
from tools.research.phase11a.structure import ContentIsland
from tools.research.phase11b.simulate import ReentryPolicy, ResetPolicy, RhythmContext

# --------------------------------------------------------------------------- semantic rubric
#: The two decisions the rubric allows, plus the escape hatch it requires.
SAME_THOUGHT_CONTINUES = "SAME_THOUGHT_CONTINUES"
DISCOURSE_BOUNDARY = "DISCOURSE_BOUNDARY"
AMBIGUOUS = "AMBIGUOUS"

#: Sub-types. `DISCOURSE_BOUNDARY` ones argue for a reset, `SAME_THOUGHT` ones against.
NEW_TOPIC = "NEW_TOPIC"
NEW_STEP = "NEW_STEP"
CONCLUSION = "CONCLUSION"
CONSEQUENCE = "CONSEQUENCE"
CONTRAST = "CONTRAST"
ANSWER_TO_HOOK = "ANSWER_TO_HOOK"
EXPLANATION_CONTINUES = "EXPLANATION_CONTINUES"
EXAMPLE_CONTINUES = "EXAMPLE_CONTINUES"
LIST_CONTINUES = "LIST_CONTINUES"
SUBORDINATE_CLAUSE = "SUBORDINATE_CLAUSE"
APPOSITION = "APPOSITION"
TAKE_SPLICE = "TAKE_SPLICE"

BOUNDARY_SUBTYPES = frozenset(
    {NEW_TOPIC, NEW_STEP, CONCLUSION, CONSEQUENCE, CONTRAST, ANSWER_TO_HOOK}
)
CONTINUATION_SUBTYPES = frozenset(
    {EXPLANATION_CONTINUES, EXAMPLE_CONTINUES, LIST_CONTINUES, SUBORDINATE_CLAUSE, APPOSITION,
     TAKE_SPLICE}
)

REASON_SEMANTIC = "SEMANTIC_RESET"
REASON_RHYTHM = "RHYTHM_REFRESH_RESET"
REASON_LOOP = "LOOP_RESET"
REASON_BASELINE = "EVERY_ELIGIBLE_CUT"


@dataclass(frozen=True, slots=True)
class SemanticAnnotation:
    """One frozen judgement about one hard cut. Carries no transcript text, by construction.

    `confidence` is the annotator's own, on a three-value scale, so that a later failure
    analysis can ask whether the mistakes were the ones already flagged as uncertain.
    """

    frame: int
    island_index: int
    cut_index: int
    semantic_class: str
    subtype: str
    confidence: str

    def __post_init__(self) -> None:
        if self.semantic_class not in (SAME_THOUGHT_CONTINUES, DISCOURSE_BOUNDARY, AMBIGUOUS):
            raise ValueError(f"{self.semantic_class!r} is not a rubric class")
        if self.confidence not in ("high", "medium", "low"):
            raise ValueError(f"{self.confidence!r} is not a confidence level")
        if self.semantic_class == DISCOURSE_BOUNDARY and self.subtype not in BOUNDARY_SUBTYPES:
            raise ValueError(f"{self.subtype!r} is not a boundary subtype")
        if self.semantic_class == SAME_THOUGHT_CONTINUES and self.subtype not in (
            CONTINUATION_SUBTYPES
        ):
            raise ValueError(f"{self.subtype!r} is not a continuation subtype")

    @property
    def predicts_reset(self) -> bool:
        """AMBIGUOUS never predicts a reset: the rubric must commit before it counts."""

        return self.semantic_class == DISCOURSE_BOUNDARY


# --------------------------------------------------------------------------- reset policies
def semantic_policy(annotations: Sequence[SemanticAnnotation]) -> ResetPolicy:
    """P0 — a reset exactly where the discourse turns over, and the state graph allows it."""

    boundary = {a.frame for a in annotations if a.predicts_reset}

    def decide(context: RhythmContext) -> str | None:
        if context.state == STATE_X0:
            return None
        return REASON_SEMANTIC if context.frame in boundary else None

    return decide


def combined_policy(
    annotations: Sequence[SemanticAnnotation], *, rhythm: bool, loop: bool
) -> ResetPolicy:
    """P1/P2/P3 — P0 plus the deterministic overrides, in a fixed precedence.

    Precedence is `loop -> semantic -> rhythm`, so the reason a report shows is the strongest
    claim available for that cut rather than whichever test happened to run first.
    """

    boundary = {a.frame for a in annotations if a.predicts_reset}

    def decide(context: RhythmContext) -> str | None:
        if context.state == STATE_X0:
            return None
        if loop and context.is_last_cut_of_island:
            return REASON_LOOP
        if context.frame in boundary:
            return REASON_SEMANTIC
        if rhythm and context.state == STATE_FACE_X3:
            return REASON_RHYTHM
        return None

    return decide


def baseline_policy() -> ResetPolicy:
    """Every hard cut the graph allows a reset at. The recall ceiling, and the precision floor."""

    def decide(context: RhythmContext) -> str | None:
        return None if context.state == STATE_X0 else REASON_BASELINE

    return decide


#: The frozen re-entry gap, in frames. Measured on the development set: the manual reset ->
#: next FACE_X1 distance has median 36 frames (600 ms), and NO label-free anchor reproduces it
#: better — "first qualifying voice recovery after the animation" misses by a median of 33
#: frames and "first hard cut after the animation" by 55, against 14 for this constant.
REENTRY_GAP_FRAMES = 36


def fixed_gap_reentry(gap: int = REENTRY_GAP_FRAMES) -> ReentryPolicy:
    """Re-entry policy: FACE_X1 restarts `gap` frames after the reset, inside the island.

    Returned as a plain closure matching `simulate.ReentryPolicy`. A re-entry that would fall
    at or past the island end is not made at all — that is the loop reset, and the Short is
    meant to end at X0.
    """

    def choose(reset_frame: int, island: ContentIsland, recoveries: Sequence[int]) -> int | None:
        entry = reset_frame + gap
        return None if entry >= island.end else entry

    return choose


# --------------------------------------------------------------------------- the frozen record
@dataclass(frozen=True, slots=True)
class CutPrediction:
    """One row of the blind prediction table. Frames and categories only — never a word."""

    island_index: int
    cut_index: int
    frame: int
    semantic_class: str
    semantic_subtype: str
    confidence: str
    #: Simulated state at the cut, per candidate. The state is a *consequence* of the policy,
    #: so P0 and P3 can legitimately disagree about it.
    state: dict[str, str]
    #: Candidate -> the reason it gave, or "" when it predicted no reset.
    predicted: dict[str, str]


@dataclass(frozen=True, slots=True)
class BlindPredictions:
    """Everything frozen at the checkpoint, in a form that serialises deterministically."""

    timeline: str
    island_index: int
    island_start: int
    island_end: int
    hard_cuts: tuple[int, ...]
    rows: tuple[CutPrediction, ...]
    #: Candidate -> predicted reset frames, in time order.
    reset_frames: dict[str, tuple[int, ...]]
    #: Candidate -> predicted FACE_X1 re-entry frames, in time order.
    entry_frames: dict[str, tuple[int, ...]]

    def to_json(self) -> str:
        payload: dict[str, Any] = {
            "timeline": self.timeline,
            "island_index": self.island_index,
            "island": [self.island_start, self.island_end],
            "hard_cuts": list(self.hard_cuts),
            "rows": [asdict(row) for row in self.rows],
            "reset_frames": {k: list(v) for k, v in sorted(self.reset_frames.items())},
            "entry_frames": {k: list(v) for k, v in sorted(self.entry_frames.items())},
        }
        return json.dumps(payload, indent=2, sort_keys=True)


__all__ = [
    "AMBIGUOUS",
    "ANSWER_TO_HOOK",
    "APPOSITION",
    "BOUNDARY_SUBTYPES",
    "CONCLUSION",
    "CONSEQUENCE",
    "CONTINUATION_SUBTYPES",
    "CONTRAST",
    "DISCOURSE_BOUNDARY",
    "EXAMPLE_CONTINUES",
    "EXPLANATION_CONTINUES",
    "LIST_CONTINUES",
    "NEW_STEP",
    "NEW_TOPIC",
    "REASON_BASELINE",
    "REASON_LOOP",
    "REASON_RHYTHM",
    "REASON_SEMANTIC",
    "REENTRY_GAP_FRAMES",
    "SAME_THOUGHT_CONTINUES",
    "SUBORDINATE_CLAUSE",
    "TAKE_SPLICE",
    "BlindPredictions",
    "CutPrediction",
    "SemanticAnnotation",
    "baseline_policy",
    "combined_policy",
    "fixed_gap_reentry",
    "semantic_policy",
]
