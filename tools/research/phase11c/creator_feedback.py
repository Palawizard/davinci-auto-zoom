"""D071 — the creator's own reading of the four Short 3 resets Phase 11b could not explain.

Phase 11b recorded 219354, 219525, 219784 and 220442 as `RHYTHM_REFRESH_RESET`, which was a
statement about the TEXT (the sentence was still running) and explicitly not a claim about
cause. The creator was then asked and answered. This module records the answers and layers
them **on top of** `tools.research.phase11b.measured`, which stays byte-identical:

    the historical taxonomy is evidence about what the blind study concluded at the time;
    the creator-grounded taxonomy is evidence about what the edit actually is.

Both are kept. `SUPERSEDED_INTERPRETATION` names exactly which Phase 11b sentences the answers
retire, so nobody re-reads the 36% figure as a current fact — and nothing else about the blind
experiment is touched: the protocol, the P0-P3 metrics, the frozen predictions and the real
prediction errors all stand unchanged.
"""

from __future__ import annotations

from tools.research.phase11b.evaluate import (
    AMBIGUOUS_RESET,
    LOOP_RESET,
    RHYTHM_REFRESH_RESET,
    SEMANTIC_RESET,
    VISUAL_PRESENTATION_RESET,
)
from tools.research.phase11b.measured import SHORT_3_RESETS

# `VISUAL_PRESENTATION_RESET` is Phase 11b's own token, reused unchanged. Phase 11b defined it
# and could prove it for no reset; the creator has now confirmed exactly one.

#: The four answers, verbatim in category form. This is user ground truth, not a measurement,
#: and it is the only place any reset is reclassified as visual (task §28).
CREATOR_ANSWERS: dict[int, str] = {
    219354: VISUAL_PRESENTATION_RESET,  # back to X0 to show the whole avatar
    219525: SEMANTIC_RESET,  # a concessive pivot, the "even if..." kind
    219784: RHYTHM_REFRESH_RESET,  # X0 to regain room for X1 -> X2 -> X3 over the long phrase
    220442: RHYTHM_REFRESH_RESET,  # X0 to re-energise and be able to zoom again afterwards
}

#: Which Phase 11b conclusions the answers retire. Everything NOT listed here still stands.
SUPERSEDED_INTERPRETATION: tuple[str, ...] = (
    "four of Short 3's eleven resets are unexplained",
    "36% of this edit is a transcript-invisible ceiling",
    "the next task is four yes/no questions for the creator",
)

#: What D071 explicitly does NOT supersede (task §4).
PRESERVED: tuple[str, ...] = (
    "the blind protocol and the sealed checkpoint 46c5e56",
    "the P0-P3 strict metrics and their false positives and negatives",
    "the errors the blind predictions actually made",
    "that Short 3 was used for no tuning before it was unblinded",
)


def creator_grounded_short_3() -> dict[int, str]:
    """Short 3's taxonomy with the four answers applied. The historical dict is not mutated."""

    grounded = dict(SHORT_3_RESETS)
    for frame, reason in CREATOR_ANSWERS.items():
        if frame not in grounded:
            raise ValueError(f"{frame} is not a manual reset of Short 3")
        grounded[frame] = reason
    return grounded


def reclassified() -> dict[int, tuple[str, str]]:
    """`frame -> (historical reason, creator-grounded reason)` for what actually changed."""

    return {
        frame: (SHORT_3_RESETS[frame], reason)
        for frame, reason in CREATOR_ANSWERS.items()
        if SHORT_3_RESETS[frame] != reason
    }


def addressable(frames: dict[int, str]) -> dict[int, str]:
    """Drop the creator-confirmed visual resets — the only exclusion this research allows.

    Strict edit coverage counts every reset; addressable coverage counts the ones a
    text/audio system could in principle observe. No reset is excluded on the grounds that a
    model missed it (task §28).
    """

    return {f: r for f, r in frames.items() if r != VISUAL_PRESENTATION_RESET}


__all__ = [
    "AMBIGUOUS_RESET",
    "CREATOR_ANSWERS",
    "LOOP_RESET",
    "PRESERVED",
    "RHYTHM_REFRESH_RESET",
    "SEMANTIC_RESET",
    "SUPERSEDED_INTERPRETATION",
    "VISUAL_PRESENTATION_RESET",
    "addressable",
    "creator_grounded_short_3",
    "reclassified",
]
