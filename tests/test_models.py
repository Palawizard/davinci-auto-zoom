import pytest

from davinci_auto_zoom.domain.models import FrameRange, SpeechSegment


def test_frame_range_is_half_open() -> None:
    frames = FrameRange(10, 25)
    assert frames.duration == 15


def test_invalid_frame_range_is_rejected() -> None:
    with pytest.raises(ValueError):
        FrameRange(10, 10)


def test_invalid_confidence_is_rejected() -> None:
    with pytest.raises(ValueError):
        SpeechSegment(FrameRange(0, 1), confidence=1.1)
