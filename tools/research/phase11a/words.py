"""Word-level transcript as a neutral research record, and its mapping onto Resolve frames.

WhisperX emits seconds relative to the start of the rendered audio. The render starts exactly
at the timeline's own first frame (that is what `voice_render` guarantees), so the conversion
is the project's existing `Timebase` policy applied to seconds instead of samples: **floor the
start, ceil the end**, half-open `[start_frame, end_frame)`. The conversion runs once, through
`fractions.Fraction`, and the original seconds are kept on the record so every frame number
stays traceable back to the ASR output.

A word whose alignment the model could not place is never given an invented timestamp. It
keeps `alignment_status` and `None` frames, and the unaligned ratio is a reported number.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, replace
from fractions import Fraction

from davinci_auto_zoom.domain.timebase import Timebase
from tools.research.phase11a.structure import ContentIsland

ALIGNED = "aligned"
PARTIALLY_ALIGNED = "partially_aligned"
UNALIGNED = "unaligned"

#: Characters stripped when normalising a French token for lexical matching. The apostrophe
#: is deliberately NOT here: "d'accord" is one token and splitting it would invent a word.
_PUNCTUATION = "".join(
    chr(c) for c in range(0x2000) if unicodedata.category(chr(c)).startswith("P")
).replace("'", "").replace("’", "")


def normalize(text: str) -> str:
    """Lowercase, strip surrounding punctuation, normalise the apostrophe. Accents are kept.

    French discourse markers are distinguished by their accents (`à` vs `a`), so folding them
    away would merge tokens the study needs to tell apart.
    """

    folded = unicodedata.normalize("NFC", text).strip().lower()
    folded = folded.replace("’", "'")
    return folded.strip(_PUNCTUATION + " ")


@dataclass(frozen=True, slots=True)
class AlignedWord:
    """One transcript token. `None` timings mean the aligner did not place it — not zero."""

    text: str
    normalized_text: str
    start_seconds: float | None
    end_seconds: float | None
    start_frame: int | None
    end_frame: int | None
    alignment_status: str

    @property
    def is_aligned(self) -> bool:
        return self.alignment_status == ALIGNED

    def covers(self, frame: int) -> bool:
        if self.start_frame is None or self.end_frame is None:
            return False
        return self.start_frame <= frame < self.end_frame


def classify_alignment(start_seconds: float | None, end_seconds: float | None) -> str:
    if start_seconds is None and end_seconds is None:
        return UNALIGNED
    if start_seconds is None or end_seconds is None:
        return PARTIALLY_ALIGNED
    return ALIGNED


def word_from_asr(
    text: str,
    start_seconds: float | None,
    end_seconds: float | None,
    timebase: Timebase,
) -> AlignedWord:
    """Build one record, converting seconds to absolute frames exactly once."""

    status = classify_alignment(start_seconds, end_seconds)
    start_frame = _floor_frame(start_seconds, timebase)
    end_frame = _ceil_frame(end_seconds, timebase)
    if start_frame is not None and end_frame is not None and end_frame <= start_frame:
        # Sub-frame words are real at 60 fps; widening keeps the range half-open and
        # non-empty without moving the start the aligner reported.
        end_frame = start_frame + 1
    return AlignedWord(
        text=text,
        normalized_text=normalize(text),
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        start_frame=start_frame,
        end_frame=end_frame,
        alignment_status=status,
    )


def _offset_frames(seconds: float, timebase: Timebase) -> Fraction:
    return Fraction(seconds).limit_denominator(1_000_000) * timebase.frame_rate


def _floor_frame(seconds: float | None, timebase: Timebase) -> int | None:
    if seconds is None:
        return None
    return timebase.start_frame + math.floor(_offset_frames(seconds, timebase))


def _ceil_frame(seconds: float | None, timebase: Timebase) -> int | None:
    if seconds is None:
        return None
    return timebase.start_frame + math.ceil(_offset_frames(seconds, timebase))


def alignment_ratios(words: list[AlignedWord]) -> dict[str, float]:
    total = len(words)
    if total == 0:
        return {ALIGNED: 0.0, PARTIALLY_ALIGNED: 0.0, UNALIGNED: 0.0}
    counts = {ALIGNED: 0, PARTIALLY_ALIGNED: 0, UNALIGNED: 0}
    for word in words:
        counts[word.alignment_status] = counts.get(word.alignment_status, 0) + 1
    return {status: count / total for status, count in counts.items()}


@dataclass(frozen=True, slots=True)
class CutContext:
    """The words on either side of a cut, never reaching past the island's own boundary."""

    before: tuple[AlignedWord, ...]
    after: tuple[AlignedWord, ...]
    #: A word the cut falls strictly inside. Either a very tight splice or a bad alignment —
    #: this module reports it and takes no position on which.
    split_word: AlignedWord | None


def words_in_island(words: list[AlignedWord], island: ContentIsland) -> tuple[AlignedWord, ...]:
    """Aligned words whose start falls inside the island. The gap between Shorts drops out."""

    return tuple(
        word for word in words if word.start_frame is not None and island.contains(word.start_frame)
    )


def cut_context(
    words: tuple[AlignedWord, ...],
    cut_frame: int,
    *,
    count: int,
) -> CutContext:
    """`count` words each side of `cut_frame`. `words` must already be island-scoped.

    A word is "before" when it ends at or before the cut and "after" when it starts at or
    after it; a word straddling the cut belongs to neither and is surfaced separately, because
    calling it either would silently pick an interpretation.
    """

    if count <= 0:
        raise ValueError("count must be > 0")
    before: list[AlignedWord] = []
    after: list[AlignedWord] = []
    split: AlignedWord | None = None
    for word in words:
        if word.start_frame is None or word.end_frame is None:
            continue
        if word.end_frame <= cut_frame:
            before.append(word)
        elif word.start_frame >= cut_frame:
            after.append(word)
        elif split is None or abs(word.start_frame - cut_frame) < abs(
            split.start_frame - cut_frame  # type: ignore[operator]
        ):
            split = word
    return CutContext(
        before=tuple(before[-count:]),
        after=tuple(after[:count]),
        split_word=split,
    )


def retimed(word: AlignedWord, timebase: Timebase) -> AlignedWord:
    """Re-derive the frames from the stored seconds. Used to prove the mapping is a function."""

    return replace(
        word,
        start_frame=_floor_frame(word.start_seconds, timebase),
        end_frame=_ceil_frame(word.end_seconds, timebase),
    )


__all__ = [
    "ALIGNED",
    "PARTIALLY_ALIGNED",
    "UNALIGNED",
    "AlignedWord",
    "CutContext",
    "alignment_ratios",
    "classify_alignment",
    "cut_context",
    "normalize",
    "retimed",
    "word_from_asr",
    "words_in_island",
]
