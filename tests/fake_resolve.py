"""A minimal fake Resolve object graph.

It mirrors the shapes observed on DaVinci Resolve Studio 21.0.4.5 so adapter code can be
tested without Resolve installed. Notably it reproduces two real behaviours:

* generator items return None from GetMediaPoolItem() and GetSourceStartFrame(),
* GetIsTrackEnabled() reports False for any timeline that is not the current one.

Write-capable methods are implemented too, and every one of them appends to a shared
mutation log. That is what lets the tests assert the property that actually matters for
safety: **when a guard fails, the log is empty**. The fake's duration semantics are a
plausible stand-in, not evidence; only the live probe establishes what Resolve really does.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any


class MutationLog(list[str]):
    """Every write-capable fake call appends here. Tests assert on it directly."""

    def record(self, call: str) -> None:
        self.append(call)


#: A cut-down but realistically shaped exported Fusion composition.
COMP_TEMPLATE = """Composition {
\tCurrentTime = 0,
\tRenderRange = { 0, %(last)d },
\tTools = {
\t\tMediaOut1 = Saver {
\t\t\tInputs = {
\t\t\t\tInput = Input {
\t\t\t\t\tSourceOp = "Transform1",
\t\t\t\t},
\t\t\t},
\t\t},
\t\tMediaIn1 = Loader {
\t\t\tCustomData = { MediaProps = {
\t\t\t\t\tMEDIA_NAME = "%(name)s",
\t\t\t\t\tMEDIA_NUM_FRAMES = %(frames)d,
\t\t\t\t}, },
\t\t},
\t\tTransform1Size = BezierSpline {
\t\t\tKeyFrames = {
\t\t\t\t[0] = { %(from)s, RH = { 5, %(from)s } },
\t\t\t\t[15] = { %(to)s, LH = { -0.15, %(to)s } }
\t\t\t}
\t\t},
\t\tTransform1 = Transform {
\t\t\tInputs = {
\t\t\t\tSize = Input {
\t\t\t\t\tSourceOp = "Transform1Size",
\t\t\t\t},
\t\t\t},
\t\t}
\t},
}
"""


def comp_text(name: str, frames: int, zoom: tuple[str, str] = ("1", "1.5")) -> str:
    """An exported comp for `name` at `frames` frames, keyframed like the real assets."""

    return COMP_TEMPLATE % {
        "name": name,
        "frames": frames,
        "last": frames - 1,
        "from": zoom[0],
        "to": zoom[1],
    }


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
        track: tuple[str, int] = ("video", 1),
        comp: str | None = None,
    ) -> None:
        self._name = name
        self._start = start
        self._end = end
        self._media_pool_item = media_pool_item
        self._source_start_frame = source_start_frame
        self._fusion_comp_count = fusion_comp_count
        self._track = track
        self._comp = comp

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

    def GetFusionCompNameList(self) -> list[str]:
        return ["Composition 1"] * self._fusion_comp_count

    def GetTrackTypeAndIndex(self) -> list[Any]:
        return [self._track[0], self._track[1]]

    def ExportFusionComp(self, path: str, comp_index: int) -> bool:
        """Writes a file only; it does not modify the project, so it is not a mutation."""
        if not self._fusion_comp_count or self._comp is None:
            return False
        Path(path).write_text(self._comp, encoding="utf-8")
        return True


class FakeTimeline:
    def __init__(
        self,
        name: str,
        tracks: dict[tuple[str, int], tuple[str, str, list[FakeTimelineItem]]],
        start_frame: int = 216000,
        end_frame: int = 219555,
        frame_rate: float = 60.0,
        log: MutationLog | None = None,
        unique_id: str | None = None,
    ) -> None:
        self._name = name
        self._tracks = tracks
        self._start_frame = start_frame
        self._end_frame = end_frame
        self._frame_rate = frame_rate
        self._log = log if log is not None else MutationLog()
        self._unique_id = unique_id or f"uid-{name}"
        self.project: FakeProject | None = None
        self.is_current = False

    def GetName(self) -> str:
        return self._name

    def GetUniqueId(self) -> str:
        return self._unique_id

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

    # --- write-capable ---------------------------------------------------------------

    def DuplicateTimeline(self, name: str) -> FakeTimeline:
        self._log.record(f"DuplicateTimeline({name!r}) from {self._name!r}")
        duplicate = FakeTimeline(
            name,
            copy.deepcopy(self._tracks),
            self._start_frame,
            self._end_frame,
            self._frame_rate,
            self._log,
            unique_id=f"uid-copy-{name}",
        )
        if self.project is not None:
            self.project.add_timeline(duplicate)  # Resolve registers it in the project
        return duplicate

    def AddTrack(self, track_type: str, sub_track_type: Any = None) -> bool:
        self._log.record(f"AddTrack({track_type!r}) on {self._name!r}")
        index = self.GetTrackCount(track_type) + 1
        label = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}[track_type]
        self._tracks[(track_type, index)] = (f"{label} {index}", "", [])
        return True

    def add_item(self, track_type: str, index: int, item: FakeTimelineItem) -> None:
        """Test helper used by FakeMediaPool.AppendToTimeline."""
        self._tracks[(track_type, index)][2].append(item)


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
    def __init__(self, root: FakeFolder, log: MutationLog | None = None) -> None:
        self._root = root
        self._log = log if log is not None else MutationLog()
        self._current_folder = root
        self._selected: FakeMediaPoolItem | None = None
        self.project: FakeProject | None = None
        #: When True, the plain AppendToTimeline call fails unless a clip is selected,
        #: reproducing the pre-20.3.2 Resolve bug so the workaround path can be tested.
        self.requires_selection = False

    def GetRootFolder(self) -> FakeFolder:
        return self._root

    def GetCurrentFolder(self) -> FakeFolder:
        return self._current_folder

    def SetCurrentFolder(self, folder: FakeFolder) -> bool:
        self._current_folder = folder  # selection state, not project content
        return True

    def SetSelectedClip(self, clip: FakeMediaPoolItem) -> bool:
        self._selected = clip
        return True

    def AppendToTimeline(self, clip_infos: list[dict[str, Any]]) -> list[FakeTimelineItem]:
        assert self.project is not None
        timeline = self.project.GetCurrentTimeline()
        assert timeline is not None
        if self.requires_selection and self._selected is None:
            return []
        appended: list[FakeTimelineItem] = []
        for info in clip_infos:
            clip = info["mediaPoolItem"]
            name = clip.GetName()
            # Semantics measured on Studio 21.0.4.5: recordFrame absolute, endFrame
            # exclusive (see .agent/DECISIONS.md D013).
            start = int(info["recordFrame"])
            duration = int(info["endFrame"]) - int(info["startFrame"])
            index = int(info["trackIndex"])
            self._log.record(
                f"AppendToTimeline({name!r} -> {timeline.GetName()!r} "
                f"V{index} @{start} +{duration})"
            )
            item = FakeTimelineItem(
                name,
                start,
                start + duration,
                None,
                None,
                fusion_comp_count=1,
                track=("video", index),
                comp=comp_text(
                    name,
                    duration,
                    ("1.5", "1") if "X0" in name else ("1", "1.5"),
                ),
            )
            timeline.add_item("video", index, item)
            appended.append(item)
        return appended

    def DeleteTimelines(self, timelines: list[FakeTimeline]) -> bool:
        assert self.project is not None
        for timeline in timelines:
            self._log.record(f"DeleteTimelines({timeline.GetName()!r})")
            self.project.remove_timeline(timeline)
        return True


class FakeProject:
    def __init__(
        self,
        name: str,
        timelines: list[FakeTimeline],
        media_pool: FakeMediaPool,
        current_timeline: str | None = None,
        log: MutationLog | None = None,
    ) -> None:
        self._name = name
        self._timelines = timelines
        self._media_pool = media_pool
        self._log = log if log is not None else MutationLog()
        self.mutations = self._log
        for timeline in timelines:
            timeline.is_current = timeline.GetName() == current_timeline
            timeline.project = self
        self._current = current_timeline
        media_pool.project = self

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

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:
        # Switching the current timeline changes no content, but it is restored anyway.
        if timeline not in self._timelines:
            self._timelines.append(timeline)
        self._current = timeline.GetName()
        for candidate in self._timelines:
            candidate.is_current = candidate.GetName() == self._current
        return True

    def add_timeline(self, timeline: FakeTimeline) -> None:
        timeline.project = self
        self._timelines.append(timeline)

    def remove_timeline(self, timeline: FakeTimeline) -> None:
        if timeline in self._timelines:
            self._timelines.remove(timeline)


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
    """Two timelines shaped like DAZ_INPUT / DAZ_OUTPUT_MVP.

    `project.mutations` is the shared write log; it must stay empty for read-only paths.
    """

    log = MutationLog()

    source = FakeMediaPoolItem("source.mov", {"Type": "Video + Audio", "Frames": "304871"})
    x1 = FakeMediaPoolItem("FACE_X1", {"Type": "Generator", "Frames": "132"})
    x0 = FakeMediaPoolItem("FACE_X0_SMOOTH", {"Type": "Generator", "Frames": "42"})

    def media(start: int, end: int) -> FakeTimelineItem:
        return FakeTimelineItem("source.mov", start, end, source, source_start_frame=start - 100000)

    def generator(name: str, start: int, end: int) -> FakeTimelineItem:
        return FakeTimelineItem(
            name,
            start,
            end,
            None,
            None,
            fusion_comp_count=1,
            track=("video", 3),
            comp=comp_text(
                name, end - start, ("1.5", "1") if "X0" in name else ("1", "1.5")
            ),
        )

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
        [
            FakeTimeline("DAZ_INPUT", base_tracks, log=log),
            FakeTimeline("DAZ_OUTPUT_MVP", reference_tracks, log=log),
        ],
        FakeMediaPool(root, log),
        current_timeline="DAZ_OUTPUT_MVP",
        log=log,
    )
    return FakeResolve(project), project
