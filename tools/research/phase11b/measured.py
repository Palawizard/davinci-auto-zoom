"""What the unblinding actually found in Short 3. Written AFTER the checkpoint commit.

Nothing in `annotations.py`, `policy.py` or `simulate.py` was touched to produce these numbers;
this module only records the manual edit so the report can be regenerated and tested.

The taxonomy below is assigned by evidence, in this order, and each reset gets exactly one:

    LOOP_RESET             it is the last hard cut of the island
    SEMANTIC_RESET         the frozen rubric called its anchoring cut DISCOURSE_BOUNDARY
    RHYTHM_REFRESH_RESET   the rubric called it SAME_THOUGHT_CONTINUES, so the reset happened
                           while the sentence was still running

VISUAL_PRESENTATION_RESET is NOT used. The creator warned that some Short 3 resets exist to
show the avatar in full, and four resets here are unexplained by text — but the composited
picture (source footage + the Fusion zoom on V2) cannot be observed in this environment, and
the source footage alone is one continuous full-body avatar shot whose framing never changes.
"The model missed it" is not evidence that a reset was visual, so no reset is excluded and no
transcript-addressable subset is reported (task §26).
"""

from __future__ import annotations

from tools.research.phase11b.evaluate import (
    LOOP_RESET,
    RHYTHM_REFRESH_RESET,
    SEMANTIC_RESET,
)

#: Every manual reset in Short 3, `frame -> category`. Eleven of them; four are not on a cut.
SHORT_3_RESETS: dict[int, str] = {
    219097: SEMANTIC_RESET,  # off cut: 82 frames before 219179, entry lands 14 after the cut
    219224: SEMANTIC_RESET,
    219354: RHYTHM_REFRESH_RESET,
    219525: RHYTHM_REFRESH_RESET,
    219659: SEMANTIC_RESET,
    219784: RHYTHM_REFRESH_RESET,  # off cut: 40 frames after 219744
    219999: SEMANTIC_RESET,
    220243: SEMANTIC_RESET,  # off cut: 73 frames before 220316, entry lands ON the cut
    220442: RHYTHM_REFRESH_RESET,  # off cut: no hard cut within 126 frames
    220608: SEMANTIC_RESET,
    220679: LOOP_RESET,
}

#: The resets a cut-anchored candidate can possibly hit: those sitting exactly on a hard cut.
SHORT_3_ON_CUT: tuple[int, ...] = (219224, 219354, 219525, 219659, 219999, 220608, 220679)
SHORT_3_OFF_CUT: tuple[int, ...] = (219097, 219784, 220243, 220442)

#: The creator's FACE_X1 entries in Short 3, for the re-entry comparison.
SHORT_3_ENTRIES: tuple[int, ...] = (
    219040, 219193, 219253, 219388, 219555, 219702, 219823, 220031, 220316, 220484, 220656,
)

__all__ = ["SHORT_3_ENTRIES", "SHORT_3_OFF_CUT", "SHORT_3_ON_CUT", "SHORT_3_RESETS"]
