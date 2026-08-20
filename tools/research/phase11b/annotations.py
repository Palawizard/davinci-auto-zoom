"""The frozen semantic annotation of every hard cut in Shorts 1, 2 and 3.

THIS FILE IS THE BLIND ARTEFACT. The Short 3 rows below were written from the local transcript
of `Timeline 1 copy`, which carries **no** manual zoom on Short 3, and were committed before
`Timeline 1` was ever asked what the creator actually did there. They are not to be edited
after that commit; a mistake found later is reported as a mistake, not corrected in place.

Frames and categories only. Not one word the creator spoke appears here, or anywhere in
`.agent/` (`AGENTS.md`, "User media").

## THE RUBRIC — frozen on Shorts 1 and 2, applied unchanged to Short 3

Input: at most ten aligned words each side of the cut, scoped to the cut's own content island.
Output: one class, one subtype, one confidence.

    DISCOURSE_BOUNDARY        the words after the cut OPEN a new discourse unit
      ANSWER_TO_HOOK          the Short's opening question/premise ends, the body begins
      NEW_TOPIC               a new subject is introduced
      NEW_STEP                a new step of a described procedure is announced
      CONSEQUENCE             a new sentence stating the result of what precedes
      CONCLUSION              a wrap-up, a summary, or a call to action
      CONTRAST                an explicit adversative opening a new sentence

    SAME_THOUGHT_CONTINUES    the current discourse unit is still running
      EXPLANATION_CONTINUES   the same proposition continues
      SUBORDINATE_CLAUSE      the words after the cut open a subordinate clause of it
      APPOSITION              they name or qualify the thing just introduced
      LIST_CONTINUES          another item of a list already running
      EXAMPLE_CONTINUES       an illustration of the current point
      TAKE_SPLICE             a hesitation or false start removed; the clause resumes

    AMBIGUOUS                 neither reading is clearly better. Never predicts a reset.

Decision rules, so that two runs of the rubric agree:

    R1  a boundary needs a new SENTENCE-LEVEL unit after the cut. A conjunction sitting
        mid-clause (`et`, `mais`, `du coup`) does not create one — position and function, not
        word presence. Phase 11a family D is the evidence for this.
    R2  boundary requires BOTH: the words before end a complete proposition, and the words
        after start a new one.
    R3  a word split across the cut is irrelevant to the class. Phase 11a measured a median
        3-frame overhang at edit boundaries; it is a trim habit, not a discourse fact.
    R4  the last cut of an island is annotated on its text like any other. The loop reset is a
        policy override, not a semantic class.
    R5  when a sentence boundary falls strictly BETWEEN two consecutive hard cuts, it is
        assigned to the nearer cut by word time, and the other cut is a continuation. Ties go
        to the earlier cut. (Short 1 and Short 2 place their boundaries on cuts directly, so
        this rule was never exercised there; it is written down because Short 3's geometry
        needs it and guessing at annotation time would not be reproducible.)
"""

from __future__ import annotations

from tools.research.phase11b.policy import (
    ANSWER_TO_HOOK,
    APPOSITION,
    CONCLUSION,
    CONSEQUENCE,
    CONTRAST,
    DISCOURSE_BOUNDARY,
    EXPLANATION_CONTINUES,
    LIST_CONTINUES,
    NEW_STEP,
    NEW_TOPIC,
    SAME_THOUGHT_CONTINUES,
    SUBORDINATE_CLAUSE,
    SemanticAnnotation,
)

_B = DISCOURSE_BOUNDARY
_S = SAME_THOUGHT_CONTINUES

#: Shorts 1 and 2 — the DEVELOPMENT SET. Annotated with the manual zooms visible, exactly as
#: Phase 11a's family F was, and therefore an upper bound there and not a blind score.
DEVELOPMENT: tuple[SemanticAnnotation, ...] = (
    SemanticAnnotation(216045, 0, 0, _B, ANSWER_TO_HOOK, "high"),
    SemanticAnnotation(216153, 0, 1, _S, EXPLANATION_CONTINUES, "high"),
    SemanticAnnotation(216224, 0, 2, _B, NEW_TOPIC, "high"),
    SemanticAnnotation(216270, 0, 3, _S, EXPLANATION_CONTINUES, "high"),
    SemanticAnnotation(216322, 0, 4, _S, EXPLANATION_CONTINUES, "high"),
    SemanticAnnotation(216357, 0, 5, _S, LIST_CONTINUES, "high"),
    SemanticAnnotation(216416, 0, 6, _B, NEW_TOPIC, "high"),
    SemanticAnnotation(216580, 0, 7, _S, LIST_CONTINUES, "medium"),
    SemanticAnnotation(216682, 0, 8, _B, NEW_TOPIC, "high"),
    SemanticAnnotation(216812, 0, 9, _B, NEW_TOPIC, "high"),
    SemanticAnnotation(217055, 0, 10, _B, CONTRAST, "high"),
    SemanticAnnotation(217257, 0, 11, _B, NEW_STEP, "high"),
    SemanticAnnotation(217373, 0, 12, _S, SUBORDINATE_CLAUSE, "high"),
    SemanticAnnotation(217506, 0, 13, _B, NEW_STEP, "high"),
    SemanticAnnotation(217654, 0, 14, _B, CONCLUSION, "high"),
    SemanticAnnotation(217835, 0, 15, _S, EXPLANATION_CONTINUES, "medium"),
    SemanticAnnotation(218115, 1, 0, _B, ANSWER_TO_HOOK, "high"),
    SemanticAnnotation(218166, 1, 1, _B, NEW_STEP, "high"),
    SemanticAnnotation(218235, 1, 2, _S, APPOSITION, "high"),
    SemanticAnnotation(218301, 1, 3, _B, NEW_STEP, "high"),
    SemanticAnnotation(218398, 1, 4, _S, LIST_CONTINUES, "high"),
    SemanticAnnotation(218460, 1, 5, _S, LIST_CONTINUES, "high"),
    SemanticAnnotation(218504, 1, 6, _S, LIST_CONTINUES, "high"),
    SemanticAnnotation(218553, 1, 7, _B, CONSEQUENCE, "medium"),
    SemanticAnnotation(218631, 1, 8, _B, CONCLUSION, "high"),
    SemanticAnnotation(218683, 1, 9, _S, EXPLANATION_CONTINUES, "high"),
    SemanticAnnotation(218735, 1, 10, _S, EXPLANATION_CONTINUES, "medium"),
    SemanticAnnotation(218828, 1, 11, _S, EXPLANATION_CONTINUES, "medium"),
)

#: Short 3 — the BLIND TEST SET. Written with no access to any manual zoom on this Short.
BLIND_SHORT_3: tuple[SemanticAnnotation, ...] = (
    SemanticAnnotation(219179, 2, 0, _B, ANSWER_TO_HOOK, "high"),
    SemanticAnnotation(219224, 2, 1, _B, NEW_TOPIC, "medium"),
    SemanticAnnotation(219253, 2, 2, _S, EXPLANATION_CONTINUES, "medium"),
    SemanticAnnotation(219308, 2, 3, _S, EXPLANATION_CONTINUES, "high"),
    SemanticAnnotation(219354, 2, 4, _S, LIST_CONTINUES, "high"),
    SemanticAnnotation(219417, 2, 5, _S, LIST_CONTINUES, "high"),
    SemanticAnnotation(219525, 2, 6, _S, SUBORDINATE_CLAUSE, "high"),
    SemanticAnnotation(219659, 2, 7, _B, NEW_STEP, "high"),
    SemanticAnnotation(219744, 2, 8, _S, APPOSITION, "medium"),
    SemanticAnnotation(219999, 2, 9, _B, NEW_STEP, "high"),
    SemanticAnnotation(220316, 2, 10, _B, CONSEQUENCE, "high"),
    SemanticAnnotation(220579, 2, 11, _S, EXPLANATION_CONTINUES, "high"),
    SemanticAnnotation(220608, 2, 12, _B, CONCLUSION, "medium"),
    SemanticAnnotation(220679, 2, 13, _B, CONCLUSION, "medium"),
)

__all__ = ["BLIND_SHORT_3", "DEVELOPMENT"]
