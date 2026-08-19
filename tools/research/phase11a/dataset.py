"""One research record per hard cut, assembled from the four independent sources.

Provenance is carried explicitly on every field, because the whole point of the ablation is to
find out *which kind of information* explains the creator's resets:

    STRUCTURAL        clip and island geometry
    ACOUSTIC          energy envelope and VAD
    LEXICAL           discourse markers in the aligned transcript
    ASR PUNCTUATION   the model's own punctuation prediction
    USER RULE         the loop reset the creator described
    AGENT SEMANTIC    the coding agent's own reading of the text (research only)

The transcript itself never leaves the local dataset. What a report may quote is the frame,
the island, the counters and the categories — never the sentences (`AGENTS.md`, "User media").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.research.phase11a.acoustic import CutAudio
from tools.research.phase11a.lexical import MarkerHit, PunctuationEvidence
from tools.research.phase11a.manual import ManualReset
from tools.research.phase11a.structure import Clip
from tools.research.phase11a.words import CutContext

#: Agent semantic annotation vocabulary (research only, never a runtime dependency).
SAME_THOUGHT_CONTINUES = "SAME_THOUGHT_CONTINUES"
DISCOURSE_BOUNDARY = "DISCOURSE_BOUNDARY"
AMBIGUOUS = "AMBIGUOUS"

#: Optional finer reason, when the annotator can name one.
NEW_TOPIC = "NEW_TOPIC"
CONCLUSION_OR_CONSEQUENCE = "CONCLUSION_OR_CONSEQUENCE"
NEW_STEP = "NEW_STEP"
TAKE_SPLICE = "TAKE_SPLICE"
HESITATION_CLEANUP = "HESITATION_CLEANUP"
LOOP_END = "LOOP_END"


@dataclass(frozen=True, slots=True)
class CutResearchExample:
    """Everything measured about one hard cut, plus the label the creator's edit provides."""

    # ---------------------------------------------------------------- STRUCTURAL
    frame: int
    content_island: int
    #: Index of the cut inside its island, so the report can name a cut without a transcript.
    cut_index: int
    previous_clip: Clip
    next_clip: Clip
    is_last_hard_cut_of_island: bool

    # ---------------------------------------------------------------- LABEL
    manual_reset_near_cut: bool
    reset_role: str | None
    reset_frame: int | None
    reset_delta: int | None
    #: USER RULE: the creator places this one to make the Short loop.
    is_loop_reset: bool

    # ---------------------------------------------------------------- FEATURES
    #: LEXICAL / local-only. The words themselves stay here and are never reported.
    context: CutContext
    markers_before: tuple[MarkerHit, ...]
    markers_after: tuple[MarkerHit, ...]
    punctuation: PunctuationEvidence
    audio: CutAudio

    # ---------------------------------------------------------------- AGENT SEMANTIC
    semantic_label: str = AMBIGUOUS
    semantic_reason: str | None = None
    #: Free-text note kept for the local dataset only.
    notes: tuple[str, ...] = field(default=())

    @property
    def cut_splits_a_word(self) -> bool:
        """ASR says a word straddles this cut. Tight splice, or imperfect alignment."""

        return self.context.split_word is not None

    @property
    def semantic_predicts_reset(self) -> bool:
        return self.semantic_label == DISCOURSE_BOUNDARY

    @property
    def has_boundary_marker(self) -> bool:
        """A marker in the position that actually reads as a boundary, not just present."""

        from tools.research.phase11a.lexical import AT_SEGMENT_END, AT_SEGMENT_START

        return any(hit.position == AT_SEGMENT_START for hit in self.markers_after) or any(
            hit.position == AT_SEGMENT_END for hit in self.markers_before
        )


def label_from_resets(
    cut_frame: int,
    resets: tuple[ManualReset, ...],
    *,
    window_frames: int,
) -> ManualReset | None:
    """The manual reset attributable to this cut, or None.

    `window_frames` is a *measured* parameter, not a constant borrowed from the gaming
    profile's ±120 ms cut snapping. The reference's own reset-to-cut distribution decides it,
    and the study reports that distribution before choosing.
    """

    if window_frames < 0:
        raise ValueError("window_frames must be >= 0")
    candidates = [r for r in resets if abs(r.start - cut_frame) <= window_frames]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (abs(r.start - cut_frame), r.start))


__all__ = [
    "AMBIGUOUS",
    "CONCLUSION_OR_CONSEQUENCE",
    "DISCOURSE_BOUNDARY",
    "HESITATION_CLEANUP",
    "LOOP_END",
    "NEW_STEP",
    "NEW_TOPIC",
    "SAME_THOUGHT_CONTINUES",
    "TAKE_SPLICE",
    "CutResearchExample",
    "label_from_resets",
]
