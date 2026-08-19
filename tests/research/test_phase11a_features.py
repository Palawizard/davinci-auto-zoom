"""Acoustic features around a cut, discourse markers, and the confusion metrics.

Synthetic fixtures only — invented French, invented dB curves.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.domain.dynamics import EnergyPoint
from davinci_auto_zoom.domain.timebase import Timebase
from tools.research.phase11a.ablation import evaluate, table
from tools.research.phase11a.acoustic import cut_audio
from tools.research.phase11a.lexical import (
    AT_SEGMENT_END,
    AT_SEGMENT_START,
    CONSEQUENCE,
    MID_SEGMENT,
    find_markers,
    punctuation_evidence,
)
from tools.research.phase11a.words import word_from_asr

SIXTY = Timebase.from_timeline(60, 0)


def _words(*texts: str) -> tuple:  # type: ignore[type-arg]
    return tuple(
        word_from_asr(text, index * 0.2, index * 0.2 + 0.15, SIXTY)
        for index, text in enumerate(texts)
    )


# --------------------------------------------------------------------------- acoustic


def _flat(start: int, end: int, db: float) -> list[EnergyPoint]:
    return [EnergyPoint(frame, db) for frame in range(start, end)]


def test_a_cut_inside_speech_has_no_pause() -> None:
    envelope = _flat(0, 200, -20.0)
    audio = cut_audio(100, envelope, [(0, 200)], window_frames=30, bounds=(0, 200))
    assert audio.pause_frames == 0
    assert audio.silence_before == 0 and audio.silence_after == 0
    assert audio.pre_db == -20.0 and audio.post_db == -20.0
    assert audio.discontinuity_db == 0.0


def test_a_cut_in_a_gap_measures_the_whole_pause_and_both_sides() -> None:
    envelope = _flat(0, 200, -20.0)
    audio = cut_audio(100, envelope, [(0, 80), (140, 200)], window_frames=30, bounds=(0, 200))
    assert audio.pause_frames == 60
    assert audio.silence_before == 20 and audio.silence_after == 40


def test_discontinuity_is_post_minus_pre() -> None:
    envelope = _flat(0, 100, -30.0) + _flat(100, 200, -10.0)
    audio = cut_audio(100, envelope, [(0, 200)], window_frames=30, bounds=(0, 200))
    assert audio.discontinuity_db == pytest.approx(20.0)


def test_the_local_minimum_and_its_depth_are_reported() -> None:
    envelope = _flat(0, 95, -20.0) + [EnergyPoint(95, -55.0)] + _flat(96, 200, -20.0)
    audio = cut_audio(100, envelope, [(0, 200)], window_frames=30, bounds=(0, 200))
    assert audio.local_min_frame == 95 and audio.local_min_db == -55.0
    assert audio.dip_db == pytest.approx(35.0)


def test_a_window_never_reaches_past_the_island_bounds() -> None:
    # Points beyond frame 120 belong to another Short and must not be averaged in.
    envelope = _flat(100, 300, -20.0)
    audio = cut_audio(110, envelope, [(100, 120)], window_frames=60, bounds=(100, 120))
    assert audio.post_db == -20.0
    assert audio.local_min_frame is not None and audio.local_min_frame < 120


def test_a_cut_outside_its_island_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="outside its island"):
        cut_audio(500, [], [], window_frames=30, bounds=(0, 200))
    with pytest.raises(ValueError, match="window_frames"):
        cut_audio(100, [], [], window_frames=0, bounds=(0, 200))


def test_an_empty_window_reports_none_rather_than_silence() -> None:
    audio = cut_audio(100, [], [], window_frames=30, bounds=(0, 200))
    assert audio.pre_db is None and audio.post_db is None
    assert audio.discontinuity_db is None and audio.dip_db is None


# --------------------------------------------------------------------------- lexical


def test_a_marker_opening_the_words_after_a_cut_is_at_segment_start() -> None:
    (hit,) = find_markers(_words("donc", "ça", "change", "tout"), side="after")
    assert (hit.category, hit.phrase, hit.position) == (CONSEQUENCE, "donc", AT_SEGMENT_START)


def test_the_same_word_mid_clause_is_not_a_boundary() -> None:
    (hit,) = find_markers(_words("si", "tu", "donc", "regardes"), side="after")
    assert hit.position == MID_SEGMENT


def test_a_marker_closing_the_words_before_a_cut_is_at_segment_end() -> None:
    (hit,) = find_markers(_words("bon", "et", "donc"), side="before")
    assert (hit.phrase, hit.position) == ("et donc", AT_SEGMENT_END)


def test_a_two_word_marker_is_not_also_counted_as_its_last_word() -> None:
    hits = find_markers(_words("du", "coup", "ça", "change"), side="after")
    assert [hit.phrase for hit in hits] == ["du coup"]


def test_markers_are_case_and_punctuation_insensitive() -> None:
    (hit,) = find_markers(_words("Donc,", "après"), side="after")
    assert hit.phrase == "donc" and hit.position == AT_SEGMENT_START


def test_no_marker_means_no_hit_and_the_side_is_validated() -> None:
    assert find_markers(_words("je", "reprends", "la", "phrase"), side="after") == ()
    with pytest.raises(ValueError, match="side"):
        find_markers((), side="middle")


def test_punctuation_evidence_reads_the_asr_prediction_as_a_feature() -> None:
    evidence = punctuation_evidence(_words("terminé."), _words("Ensuite"))
    assert evidence.sentence_ends_before and evidence.capitalised_after and evidence.boundary
    mid = punctuation_evidence(_words("et"), _words("puis"))
    assert not mid.sentence_ends_before and not mid.capitalised_after and not mid.boundary
    clause = punctuation_evidence(_words("bon,"), _words("alors"))
    assert clause.clause_ends_before and not clause.boundary


def test_punctuation_evidence_survives_an_empty_side() -> None:
    assert punctuation_evidence((), ()).boundary is False


# --------------------------------------------------------------------------- ablation


def test_confusion_counts_and_the_two_error_kinds_separately() -> None:
    result = evaluate("demo", universe=[1, 2, 3, 4], predicted=[1, 2], actual=[2, 3])
    assert (result.confusion.true_positives, result.confusion.false_positives) == (1, 1)
    assert (result.confusion.false_negatives, result.confusion.true_negatives) == (1, 1)
    assert result.confusion.precision == pytest.approx(0.5)
    assert result.confusion.recall == pytest.approx(0.5)
    assert result.false_positive_frames == (1,) and result.false_negative_frames == (3,)


def test_predicting_everything_maximises_recall_and_wrecks_precision() -> None:
    result = evaluate("all", universe=range(10), predicted=range(10), actual=[1, 2])
    assert result.confusion.recall == 1.0
    assert result.confusion.precision == pytest.approx(0.2)
    # ...which is exactly why accuracy alone must never pick the model.
    assert result.confusion.accuracy == pytest.approx(0.2)


def test_predicting_nothing_is_scored_as_zero_not_as_undefined() -> None:
    result = evaluate("none", universe=range(10), predicted=[], actual=[1, 2])
    assert result.confusion.precision == 0.0 and result.confusion.recall == 0.0
    assert result.confusion.f1 == 0.0


def test_a_prediction_outside_the_evaluated_cuts_raises() -> None:
    with pytest.raises(ValueError, match="outside the evaluated universe"):
        evaluate("stray", universe=[1, 2], predicted=[99], actual=[1])


def test_the_table_keeps_the_order_it_was_given() -> None:
    first = evaluate("aaa", universe=[1], predicted=[], actual=[1])
    second = evaluate("bbb", universe=[1], predicted=[1], actual=[1])
    rendered = table([first, second])
    assert rendered.index("aaa") < rendered.index("bbb")
