from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from davinci_auto_zoom.domain.models import Frame

# Heuristic classification labels, not authoritative Resolve types. Only structural facts
# live here; what the clip visually does is opaque to us (it lives in the user's Fusion comp).
ITEM_KIND_MEDIA = "media"
ITEM_KIND_LIKELY_GENERATOR = "likely-generator"


@dataclass(frozen=True, slots=True)
class TimelineItemSnapshot:
    name: str
    start: Frame
    end: Frame
    unique_id: str | None = None
    media_pool_item_name: str | None = None
    source_start_frame: int | None = None
    fusion_comp_count: int = 0

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def probable_kind(self) -> str:
        """**Heuristic** classification, for reporting only — never for edit decisions.

        Observed zoom generators expose neither a media pool item nor source frames, but
        Resolve gives no documented guarantee that the converse holds: other item types
        (titles, transitions, compound/Fusion clips, future item kinds) could present the
        same shape. Identifying DAZ's own zoom assets relies on the dedicated zoom video
        track plus the configured role names, not on this property.
        """
        if self.media_pool_item_name is None and self.source_start_frame is None:
            return ITEM_KIND_LIKELY_GENERATOR
        return ITEM_KIND_MEDIA


@dataclass(frozen=True, slots=True)
class TrackSnapshot:
    track_type: str  # "video" | "audio" | "subtitle"
    index: int  # 1-based, as in the Resolve API
    name: str
    sub_type: str = ""
    enabled: bool | None = None  # None when not trustworthy (non-current timeline)
    locked: bool | None = None
    items: tuple[TimelineItemSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    name: str
    unique_id: str | None
    frame_rate: float
    start_frame: Frame
    end_frame: Frame
    start_timecode: str
    is_current: bool
    tracks: tuple[TrackSnapshot, ...] = ()

    def track(self, track_type: str, index: int) -> TrackSnapshot | None:
        for candidate in self.tracks:
            if candidate.track_type == track_type and candidate.index == index:
                return candidate
        return None

    def tracks_of(self, track_type: str) -> tuple[TrackSnapshot, ...]:
        return tuple(t for t in self.tracks if t.track_type == track_type)

    def edit_boundaries(self, video_track_index: int = 1) -> tuple[Frame, ...]:
        """Frames where a cut exists on the given video track (clip starts and ends)."""
        track = self.track("video", video_track_index)
        if track is None:
            return ()
        frames: set[Frame] = set()
        for item in track.items:
            frames.add(item.start)
            frames.add(item.end)
        return tuple(sorted(frames))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    """A Media Pool item that is a candidate zoom asset."""

    name: str
    bin_path: str
    clip_type: str
    frames: int | None
    media_id: str | None
    unique_id: str | None
    role: str | None = None  # configured semantic role, when matched


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    resolve_version: str
    product_name: str
    project_name: str
    frame_rate: float
    current_timeline: str | None
    timelines: tuple[TimelineSnapshot, ...] = ()
    asset_bin: str = ""
    asset_bin_found: bool = False
    assets: tuple[AssetSnapshot, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def timeline(self, name: str) -> TimelineSnapshot | None:
        for candidate in self.timelines:
            if candidate.name == name:
                return candidate
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def frames_to_ms(frames: int, frame_rate: float) -> float:
    return frames * 1000.0 / frame_rate


def ms_to_frames(milliseconds: float, frame_rate: float) -> int:
    return round(milliseconds * frame_rate / 1000.0)
