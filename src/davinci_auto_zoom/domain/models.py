from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

Frame = int


@dataclass(frozen=True, slots=True, order=True)
class FrameRange:
    """Half-open timeline frame range [start, end)."""

    start: Frame
    end: Frame

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("start must be >= 0")
        if self.end <= self.start:
            raise ValueError("end must be > start")

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    frames: FrameRange
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


class ZoomState(StrEnum):
    NORMAL = "normal"
    FACECAM_X1 = "facecam_x1"
    # Future states belong here rather than in Resolve-specific code:
    # FACECAM_X2, FACECAM_X3, GAMEPLAY_*, etc.


class ZoomActionKind(StrEnum):
    ENTER = "enter"
    RESET = "reset"


@dataclass(frozen=True, slots=True)
class ZoomAction:
    frame: Frame
    kind: ZoomActionKind
    target: ZoomState
    asset_role: str
    reason: str

    def __post_init__(self) -> None:
        if self.frame < 0:
            raise ValueError("frame must be >= 0")
        if not self.asset_role.strip():
            raise ValueError("asset_role cannot be empty")
        if not self.reason.strip():
            raise ValueError("reason cannot be empty")
