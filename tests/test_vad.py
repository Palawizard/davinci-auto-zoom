"""Post-processing tests. No model, no numpy, no ffmpeg: just probabilities in, ranges out."""

import pytest

from davinci_auto_zoom.domain.vad import (
    WINDOW_SAMPLES,
    SampleSegment,
    VadSettings,
    segments_from_probabilities,
)

SR = 16000
WINDOWS_PER_SECOND = SR / WINDOW_SAMPLES  # 31.25


def probs(*runs: tuple[float, float]) -> list[float]:
    """Build a probability sequence from (probability, seconds) runs."""

    out: list[float] = []
    for value, seconds in runs:
        out.extend([value] * round(seconds * WINDOWS_PER_SECOND))
    return out


def total(sequence: list[float]) -> int:
    return len(sequence) * WINDOW_SAMPLES


def test_all_silence_produces_no_segments():
    sequence = probs((0.01, 5.0))
    assert segments_from_probabilities(sequence, total(sequence)) == ()


def test_all_speech_produces_one_segment_spanning_everything():
    sequence = probs((0.95, 5.0))
    segments = segments_from_probabilities(sequence, total(sequence))
    assert len(segments) == 1
    assert segments[0].start == 0
    assert segments[0].end == total(sequence)


def test_one_speech_region_is_found_where_it_is():
    sequence = probs((0.01, 2.0), (0.95, 3.0), (0.01, 2.0))
    settings = VadSettings(speech_pad_ms=0)
    segments = segments_from_probabilities(sequence, total(sequence), settings)
    assert len(segments) == 1
    assert segments[0].start == pytest.approx(2.0 * SR, abs=WINDOW_SAMPLES)
    assert segments[0].end == pytest.approx(5.0 * SR, abs=WINDOW_SAMPLES)


def test_results_are_sorted_non_overlapping_and_non_empty():
    sequence = probs(
        (0.01, 1.0), (0.9, 1.0), (0.01, 1.0), (0.9, 1.0), (0.01, 1.0), (0.9, 1.0), (0.01, 1.0)
    )
    segments = segments_from_probabilities(sequence, total(sequence))
    assert len(segments) == 3
    for segment in segments:
        assert segment.start < segment.end
    for earlier, later in zip(segments, segments[1:], strict=False):
        assert earlier.end <= later.start


def test_short_blip_is_rejected_by_min_speech():
    # 150 ms of speech, below the 250 ms default floor.
    sequence = probs((0.01, 1.0), (0.9, 0.15), (0.01, 1.0))
    assert segments_from_probabilities(sequence, total(sequence)) == ()


def test_min_silence_merges_a_micro_pause_but_not_a_long_one():
    short_pause = probs((0.9, 1.0), (0.01, 0.05), (0.9, 1.0), (0.01, 1.0))
    long_pause = probs((0.9, 1.0), (0.01, 0.5), (0.9, 1.0), (0.01, 1.0))
    settings = VadSettings(min_silence_ms=100, speech_pad_ms=0)
    assert len(segments_from_probabilities(short_pause, total(short_pause), settings)) == 1
    assert len(segments_from_probabilities(long_pause, total(long_pause), settings)) == 2


def test_editorial_650ms_pause_is_not_merged_by_the_vad():
    """A 650 ms pause stays two segments. Deciding to bridge it is the planner's call."""

    sequence = probs((0.9, 1.0), (0.01, 0.65), (0.9, 1.0), (0.01, 0.5))
    segments = segments_from_probabilities(sequence, total(sequence), VadSettings())
    assert len(segments) == 2


def test_padding_widens_segments_without_making_them_overlap():
    sequence = probs((0.01, 1.0), (0.9, 1.0), (0.01, 0.4), (0.9, 1.0), (0.01, 1.0))
    unpadded = segments_from_probabilities(
        sequence, total(sequence), VadSettings(speech_pad_ms=0)
    )
    padded = segments_from_probabilities(
        sequence, total(sequence), VadSettings(speech_pad_ms=200)
    )
    assert len(unpadded) == len(padded) == 2
    assert padded[0].start < unpadded[0].start
    assert padded[-1].end > unpadded[-1].end
    assert padded[0].end <= padded[1].start


def test_padding_never_runs_past_the_audio():
    sequence = probs((0.9, 1.0))
    segments = segments_from_probabilities(
        sequence, total(sequence), VadSettings(speech_pad_ms=500)
    )
    assert segments[0].start == 0
    assert segments[0].end == total(sequence)


def test_hysteresis_uses_the_negative_threshold_to_exit():
    settings = VadSettings(threshold=0.5, speech_pad_ms=0, min_silence_ms=0)
    # 0.4 is below the entry threshold but above the 0.35 exit threshold, so once speech has
    # started this stretch does not end it.
    sequence = probs((0.9, 1.0), (0.4, 1.0), (0.9, 1.0), (0.01, 1.0))
    assert len(segments_from_probabilities(sequence, total(sequence), settings)) == 1
    # ...but it never starts speech on its own.
    only_grey = probs((0.4, 3.0))
    assert segments_from_probabilities(only_grey, total(only_grey), settings) == ()


def test_speech_running_to_the_end_of_the_file_is_closed_at_the_last_sample():
    sequence = probs((0.01, 1.0), (0.9, 2.0))
    segments = segments_from_probabilities(sequence, total(sequence))
    assert segments[-1].end == total(sequence)


def test_empty_probabilities_give_no_segments():
    assert segments_from_probabilities([], 0) == ()


def test_settings_reject_impossible_values():
    for kwargs in (
        {"threshold": 0.0},
        {"threshold": 1.0},
        {"threshold": 1.5},
        {"min_speech_ms": -1},
        {"min_silence_ms": -1},
        {"speech_pad_ms": -5},
        {"neg_threshold": 0.9},  # above threshold
        {"neg_threshold": 0.0},
    ):
        with pytest.raises(ValueError):
            VadSettings(**kwargs)


def test_default_negative_threshold_follows_silero():
    assert VadSettings(threshold=0.5).effective_neg_threshold == pytest.approx(0.35)
    assert VadSettings(threshold=0.1).effective_neg_threshold == pytest.approx(0.01)
    assert VadSettings(threshold=0.5, neg_threshold=0.2).effective_neg_threshold == 0.2


def test_sample_segment_validates_its_range():
    with pytest.raises(ValueError):
        SampleSegment(10, 10)
    with pytest.raises(ValueError):
        SampleSegment(-1, 10)
    assert SampleSegment(10, 30).duration == 20
