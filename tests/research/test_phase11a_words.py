"""Word timing: the seconds -> frame mapping, unaligned tokens, and cut context.

Every fixture is synthetic French. Nothing spoken in `bluescreen 2` appears here.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.domain.timebase import Timebase
from tools.research.phase11a.structure import Clip, content_islands
from tools.research.phase11a.words import (
    ALIGNED,
    PARTIALLY_ALIGNED,
    UNALIGNED,
    alignment_ratios,
    cut_context,
    normalize,
    retimed,
    word_from_asr,
    words_in_island,
)

SIXTY = Timebase.from_timeline(60, 216000)
#: A rounded NTSC rate, to prove the conversion goes through the project's exact Fraction
#: rather than 59.94 taken literally.
NTSC = Timebase.from_timeline("59.94", 0)


def _word(text: str, start: float | None, end: float | None, timebase: Timebase = SIXTY):
    # type: ignore[no-untyped-def]
    return word_from_asr(text, start, end, timebase)


def test_start_is_floored_and_end_is_ceiled_onto_absolute_frames() -> None:
    word = _word("bonjour", 1.0, 1.5)
    assert (word.start_frame, word.end_frame) == (216060, 216090)
    # 1.008 s is 60.48 frames: the start floors down, the end ceils up, so the range always
    # covers every instant the aligner attributed to the word.
    partial = _word("salut", 1.008, 1.492)
    assert (partial.start_frame, partial.end_frame) == (216060, 216090)


def test_a_word_shorter_than_a_frame_still_gets_a_non_empty_range() -> None:
    word = _word("euh", 2.0, 2.0)
    assert (word.start_frame, word.end_frame) == (216120, 216121)


def test_the_timeline_origin_is_carried_not_assumed_to_be_zero() -> None:
    assert _word("un", 0.0, 0.5).start_frame == 216000
    assert word_from_asr("un", 0.0, 0.5, Timebase.from_timeline(60, 0)).start_frame == 0


def test_a_fractional_rate_uses_the_exact_ntsc_value() -> None:
    # At a full hour, 59.94 taken literally would drift by several frames against 60000/1001.
    word = word_from_asr("test", 3600.0, 3600.1, NTSC)
    assert word.start_frame == 215784  # floor(3600 * 60000/1001)
    assert word.start_frame != 215784 + 216  # not 3600 * 59.94 either


def test_an_unaligned_word_gets_no_invented_timestamp() -> None:
    word = _word("inconnu", None, None)
    assert word.alignment_status == UNALIGNED
    assert word.start_frame is None and word.end_frame is None
    half = _word("moitié", 1.0, None)
    assert half.alignment_status == PARTIALLY_ALIGNED
    assert half.start_frame == 216060 and half.end_frame is None


def test_alignment_ratios_count_every_status() -> None:
    words = [_word("a", 0.0, 0.1), _word("b", 0.2, 0.3), _word("c", None, None)]
    ratios = alignment_ratios(words)
    assert ratios[ALIGNED] == pytest.approx(2 / 3)
    assert ratios[UNALIGNED] == pytest.approx(1 / 3)
    assert alignment_ratios([])[ALIGNED] == 0.0


def test_the_mapping_is_a_function_of_the_stored_seconds() -> None:
    word = _word("traçable", 3.14159, 3.5)
    assert retimed(word, SIXTY) == word


def test_normalize_lowercases_and_strips_punctuation_but_keeps_accents() -> None:
    assert normalize("Donc,") == "donc"
    assert normalize("«Après»") == "après"
    assert normalize("d’accord") == "d'accord"
    assert normalize("  Voilà !  ") == "voilà"


def test_cut_context_returns_n_words_each_side() -> None:
    words = tuple(_word(f"m{i}", i * 0.5, i * 0.5 + 0.4) for i in range(10))
    # word 4 spans [2.0, 2.4) s = frames [216120, 216144); the cut sits after it.
    context = cut_context(words, 216150, count=2)
    assert [w.text for w in context.before] == ["m3", "m4"]
    assert [w.text for w in context.after] == ["m5", "m6"]
    assert context.split_word is None


def test_a_cut_exactly_on_a_word_boundary_splits_cleanly() -> None:
    words = (_word("avant", 0.0, 1.0), _word("après", 1.0, 2.0))
    context = cut_context(words, 216060, count=3)
    assert [w.text for w in context.before] == ["avant"]
    assert [w.text for w in context.after] == ["après"]
    assert context.split_word is None


def test_a_cut_inside_a_word_is_surfaced_rather_than_assigned_to_a_side() -> None:
    words = (_word("avant", 0.0, 1.0), _word("coupé", 1.0, 2.0), _word("après", 2.0, 3.0))
    context = cut_context(words, 216090, count=3)
    assert [w.text for w in context.before] == ["avant"]
    assert [w.text for w in context.after] == ["après"]
    assert context.split_word is not None and context.split_word.text == "coupé"


def test_words_with_no_timing_are_skipped_by_the_context_builder() -> None:
    words = (_word("avant", 0.0, 1.0), _word("perdu", None, None), _word("après", 1.0, 2.0))
    context = cut_context(words, 216060, count=3)
    assert [w.text for w in context.before] == ["avant"]
    assert [w.text for w in context.after] == ["après"]


def test_context_never_crosses_an_island_boundary() -> None:
    islands = content_islands(
        [Clip(216000, 216100), Clip(216500, 216600)], min_gap_frames=30
    )
    short_a = _word("premier", 0.5, 1.0)  # frames 216030..216060
    short_b = _word("second", 8.4, 8.6)  # frames 216504..216516
    scoped = words_in_island([short_a, short_b], islands[1])
    assert [w.text for w in scoped] == ["second"]
    # The first cut of Short B therefore cannot see a single word of Short A.
    context = cut_context(scoped, 216510, count=5)
    assert context.before == () and [w.text for w in context.after] == []
    assert context.split_word is not None and context.split_word.text == "second"


def test_context_count_must_be_positive() -> None:
    with pytest.raises(ValueError, match="count"):
        cut_context((), 0, count=0)
