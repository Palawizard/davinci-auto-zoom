"""A minimal fake Resolve object graph.

It mirrors the shapes observed on DaVinci Resolve Studio 21.0.4.5 so adapter code can be
tested without Resolve installed. Notably it reproduces two real behaviours:

* generator items return None from GetMediaPoolItem() and GetSourceStartFrame(),
* GetIsTrackEnabled() reports False for any timeline that is not the current one.
"""

from __future__ import annotations

from typing import Any


class FakeMediaPoolItem:
    def __init__(self, name: str, properties: dict[str, str] | None = None) -> None:
        self._name = name
        self._properties = properties or {}

    def GetName(self) -> str:
        return self._name

    def GetClipProperty(self, key: str | None = None) -> Any:
        return self._properties if key is None else self._properties.get(key, "")

    def GetMediaId(self) -> str:
        return f"media-{self._name}"

    def GetUniqueId(self) -> str:
        return f"uid-{self._name}"


class FakeTimelineItem:
    def __init__(
        self,
        name: str,
        start: int,
        end: int,
        media_pool_item: FakeMediaPoolItem | None = None,
        source_start_frame: int | None = None,
        fusion_comp_count: int = 0,
    ) -> None:
        self._name = name
        self._start = start
        self._end = end
        self._media_pool_item = media_pool_item
        self._source_start_frame = source_start_frame
        self._fusion_comp_count = fusion_comp_count

    def GetName(self) -> str:
        return self._name

    def GetStart(self) -> int:
        return self._start

    def GetEnd(self) -> int:
        return self._end

    def GetDuration(self) -> int:
        return self._end - self._start

    def GetUniqueId(self) -> str:
        return f"uid-{self._name}-{self._start}"

    def GetMediaPoolItem(self) -> FakeMediaPoolItem | None:
        return self._media_pool_item

    def GetSourceStartFrame(self) -> int | None:
        return self._source_start_frame

    def GetFusionCompCount(self) -> int:
        return self._fusion_comp_count


class FakeTimeline:
    def __init__(
        self,
        name: str,
        tracks: dict[tuple[str, int], tuple[str, str, list[FakeTimelineItem]]],
        start_frame: int = 216000,
        end_frame: int = 219555,
        frame_rate: float = 60.0,
    ) -> None:
        self._name = name
        self._tracks = tracks
        self._start_frame = start_frame
        self._end_frame = end_frame
        self._frame_rate = frame_rate
        self.is_current = False

    def GetName(self) -> str:
        return self._name

    def GetUniqueId(self) -> str:
        return f"uid-{self._name}"

    def GetSetting(self, key: str) -> Any:
        return self._frame_rate if key == "timelineFrameRate" else ""

    def GetStartFrame(self) -> int:
        return self._start_frame

    def GetEndFrame(self) -> int:
        return self._end_frame

    def GetStartTimecode(self) -> str:
        return "01:00:00:00"

    def GetTrackCount(self, track_type: str) -> int:
        return sum(1 for (kind, _) in self._tracks if kind == track_type)

    def GetTrackName(self, track_type: str, index: int) -> str:
        return self._tracks[(track_type, index)][0]

    def GetTrackSubType(self, track_type: str, index: int) -> str:
        return self._tracks[(track_type, index)][1]

    def GetItemListInTrack(self, track_type: str, index: int) -> list[FakeTimelineItem]:
        return self._tracks[(track_type, index)][2]

    def GetIsTrackEnabled(self, track_type: str, index: int) -> bool:
        return self.is_current  # matches the real API quirk

    def GetIsTrackLocked(self, track_type: str, index: int) -> bool:
        return False


class FakeFolder:
    def __init__(
        self,
        name: str,
        clips: list[FakeMediaPoolItem] | None = None,
        subfolders: list[FakeFolder] | None = None,
    ) -> None:
        self._name = name
        self._clips = clips or []
        self._subfolders = subfolders or []

    def GetName(self) -> str:
        return self._name

    def GetClipList(self) -> list[FakeMediaPoolItem]:
        return self._clips

    def GetSubFolderList(self) -> list[FakeFolder]:
        return self._subfolders


class FakeMediaPool:
    def __init__(self, root: FakeFolder) -> None:
        self._root = root

    def GetRootFolder(self) -> FakeFolder:
        return self._root


class FakeProject:
    def __init__(
        self,
        name: str,
        timelines: list[FakeTimeline],
        media_pool: FakeMediaPool,
        current_timeline: str | None = None,
    ) -> None:
        self._name = name
        self._timelines = timelines
        self._media_pool = media_pool
        for timeline in timelines:
            timeline.is_current = timeline.GetName() == current_timeline
        self._current = current_timeline

    def GetName(self) -> str:
        return self._name

    def GetSetting(self, key: str) -> Any:
        return 60.0 if key == "timelineFrameRate" else ""

    def GetMediaPool(self) -> FakeMediaPool:
        return self._media_pool

    def GetTimelineCount(self) -> int:
        return len(self._timelines)

    def GetTimelineByIndex(self, index: int) -> FakeTimeline:
        return self._timelines[index - 1]

    def GetCurrentTimeline(self) -> FakeTimeline | None:
        return next((t for t in self._timelines if t.GetName() == self._current), None)


class FakeResolve:
    def __init__(self, project: FakeProject) -> None:
        self._project = project

    def GetVersionString(self) -> str:
        return "21.0.4.5"

    def GetProductName(self) -> str:
        return "DaVinci Resolve Studio"

    def GetProjectManager(self) -> FakeResolve:
        return self

    def GetCurrentProject(self) -> FakeProject:
        return self._project


def build_test_project() -> tuple[FakeResolve, FakeProject]:
    """Two timelines shaped like DAZ_INPUT / DAZ_OUTPUT_MVP."""

    source = FakeMediaPoolItem("source.mov", {"Type": "Video + Audio", "Frames": "304871"})
    x1 = FakeMediaPoolItem("FACE_X1", {"Type": "Generator", "Frames": "132"})
    x0 = FakeMediaPoolItem("FACE_X0_SMOOTH", {"Type": "Generator", "Frames": "42"})

    def media(start: int, end: int) -> FakeTimelineItem:
        return FakeTimelineItem("source.mov", start, end, source, source_start_frame=start - 100000)

    def generator(name: str, start: int, end: int) -> FakeTimelineItem:
        return FakeTimelineItem(name, start, end, None, None, fusion_comp_count=1)

    v1 = [media(216000, 216132), media(216132, 216300), media(216300, 216500)]
    a1 = [media(216000, 216500)]

    base_tracks: dict[tuple[str, int], tuple[str, str, list[FakeTimelineItem]]] = {
        ("video", 1): ("Video 1", "", v1),
        ("video", 2): ("Video 2", "", []),
        ("audio", 1): ("Audio 1", "stereo", a1),
    }
    reference_tracks = dict(base_tracks)
    reference_tracks[("video", 3)] = (
        "Video 3",
        "",
        [
            generator("FACE_X1", 216045, 216132),
            generator("FACE_X0_SMOOTH", 216132, 216174),
            generator("FACE_X1", 216350, 216400),
        ],
    )

    root = FakeFolder("Master", [source], [FakeFolder("DAVINCI_AUTO_ZOOM", [x1, x0])])
    project = FakeProject(
        "davinci-auto-zoom-test",
        [FakeTimeline("DAZ_INPUT", base_tracks), FakeTimeline("DAZ_OUTPUT_MVP", reference_tracks)],
        FakeMediaPool(root),
        current_timeline="DAZ_OUTPUT_MVP",
    )
    return FakeResolve(project), project
