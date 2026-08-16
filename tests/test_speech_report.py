from fractions import Fraction

import pytest

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.models import (
    FrameRange,
    SpeechSegment,
    normalize_speech_segments,
)
from davinci_auto_zoom.domain.speech_report import (
    ReferenceZoom,
    compare_to_reference_zooms,
    reference_zooms,
    segment_rows,
    segment_statistics,
    threshold_stability,
)
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.vad import WINDOW_SAMPLES, VadSettings
from davinci_auto_zoom.resolve.session import snapshot_project
from tests.fake_resolve import build_test_project


def segment(start: int, end: int) -> SpeechSegment:
    return SpeechSegment(FrameRange(start, end))


def test_statistics_describe_the_shape_of_a_segmentation():
    stats = segment_statistics([segment(0, 100), segment(200, 260), segment(400, 900)], 1000)
    assert stats.segment_count == 3
    assert stats.speech_frames == 100 + 60 + 500
    assert stats.speech_ratio == pytest.approx(0.66)
    assert stats.duration_frames == {"min": 60, "median": 100, "max": 500}
    assert stats.gap_frames == {"min": 100, "median": 120, "max": 140}


def test_statistics_of_nothing_are_empty_not_a_crash():
    stats = segment_statistics([], 1000)
    assert stats.segment_count == 0
    assert stats.speech_ratio == 0.0
    assert stats.duration_frames == {"min": None, "median": None, "max": None}


def test_threshold_stability_reuses_one_set_of_probabilities():
    # A clear speech plateau: the segmentation should barely move between 0.4 and 0.6.
    sequence = [0.02] * 40 + [0.95] * 120 + [0.02] * 40
    total = len(sequence) * WINDOW_SAMPLES
    trials = threshold_stability(sequence, total, VadSettings(), (0.4, 0.5, 0.6))
    assert [trial.threshold for trial in trials] == [0.4, 0.5, 0.6]
    for trial in trials:
        assert trial.segment_count == 1
        assert trial.matched_segments == 1
        assert trial.boundary_shift_samples["max"] == 0


def test_threshold_stability_exposes_an_unstable_region():
    # Probabilities sitting right at 0.5: a small threshold change flips the verdict.
    sequence = [0.02] * 20 + [0.5] * 120 + [0.02] * 20
    total = len(sequence) * WINDOW_SAMPLES
    low, _, high = threshold_stability(sequence, total, VadSettings(), (0.4, 0.5, 0.6))
    assert low.segment_count == 1
    assert high.segment_count == 0


def test_reference_diagnostics_are_alignment_not_accuracy():
    segments = [segment(1000, 1200), segment(2000, 2300), segment(5000, 5100)]
    enters = [ReferenceZoom("FACE_X1", 1010, 1150), ReferenceZoom("FACE_X1", 9000, 9100)]
    resets = [ReferenceZoom("FACE_X0_SMOOTH", 2350, 2392)]

    diagnostics = compare_to_reference_zooms(segments, enters, resets, "DAZ_OUTPUT_MVP")
    assert diagnostics.zoom_count == 2
    assert diagnostics.zooms_overlapping_speech == 1
    assert diagnostics.zooms_without_speech == 1
    assert diagnostics.speech_without_zoom == 2
    assert diagnostics.speech_start_to_zoom_start_frames["median"] == 10
    assert diagnostics.speech_end_to_reset_start_frames["median"] == 50
    # The caveat travels with the numbers so a reader cannot mistake them for a score.
    assert "NOT VAD accuracy" in diagnostics.to_dict()["caveat"]


def test_reference_zooms_are_found_by_name_on_any_video_track():
    resolve, project = build_test_project()
    snapshot = snapshot_project(resolve, project, Config())
    reference = snapshot.timeline("DAZ_OUTPUT_MVP")
    assert reference is not None
    assert len(reference_zooms(reference, "FACE_X1")) == 2
    assert len(reference_zooms(reference, "FACE_X0_SMOOTH")) == 1
    assert reference_zooms(reference, "NOT_AN_ASSET") == ()


def test_segment_rows_carry_frames_seconds_and_timecode():
    timebase = Timebase.from_timeline(60, 216000)
    rows = segment_rows([segment(216060, 216120)], timebase)
    assert rows[0]["start_frame"] == 216060
    assert rows[0]["start_seconds"] == pytest.approx(1.0)
    assert rows[0]["duration_seconds"] == pytest.approx(1.0)
    assert rows[0]["start_timecode"] == "01:00:01:00"
    assert rows[0]["end_timecode"] == "01:00:02:00"


def test_normalization_sorts_merges_and_keeps_the_lowest_confidence():
    merged = normalize_speech_segments(
        [
            SpeechSegment(FrameRange(500, 600), 0.9),
            SpeechSegment(FrameRange(100, 300), 0.8),
            SpeechSegment(FrameRange(250, 400), 0.4),  # overlaps the previous one
        ]
    )
    assert [(s.start_frame, s.end_frame) for s in merged] == [(100, 400), (500, 600)]
    assert merged[0].confidence == pytest.approx(0.4)


def test_touching_segments_are_one_region():
    merged = normalize_speech_segments([segment(100, 200), segment(200, 300)])
    assert [(s.start_frame, s.end_frame) for s in merged] == [(100, 300)]


def test_normalization_of_nothing_is_nothing():
    assert normalize_speech_segments([]) == ()


def test_a_segment_cannot_be_empty_or_backwards():
    with pytest.raises(ValueError):
        FrameRange(100, 100)
    with pytest.raises(ValueError):
        FrameRange(100, 50)
    with pytest.raises(ValueError):
        SpeechSegment(FrameRange(0, 10), confidence=1.5)


def test_segment_exposes_the_half_open_convention():
    one = segment(216000, 216060)
    assert one.start_frame == 216000
    assert one.end_frame == 216060
    assert one.duration_frames == 60
    assert Fraction(one.duration_frames, 60) == 1  # one second at 60 fps
