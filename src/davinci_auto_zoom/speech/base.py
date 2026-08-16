from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from davinci_auto_zoom.domain.models import SpeechSegment


class SpeechProvider(Protocol):
    """Boundary for any speech/transcription implementation."""

    def detect(self) -> Sequence[SpeechSegment]: ...
