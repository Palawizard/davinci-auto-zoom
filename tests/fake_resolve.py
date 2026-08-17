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
import wave
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
        log: MutationLog | None = None,
    ) -> None:
        self._name = name
        self._start = start
        self._end = end
        self._media_pool_item = media_pool_item
        self._source_start_frame = source_start_frame
        self._fusion_comp_count = fusion_comp_count
        self._track = track
        self._comp = comp
        self._log = log if log is not None else MutationLog()
        #: local frame -> marker information, exactly the shape GetMarkers() returns.
        self._markers: dict[int, dict[str, Any]] = {}

        # Failure switches for the ownership unhappy paths.
        self.add_marker_outcome: bool | None = True  # None = raise
        self.markers_readable = True
        #: Rewrites the customData a re-read reports, to simulate Resolve storing something
        #: other than what was handed to AddMarker.
        self.corrupt_readback: str | None = None

    def GetName(self) -> str:
        return self._name

    # --- markers -----------------------------------------------------------------------

    def GetMarkers(self) -> dict[float, dict[str, Any]]:
        if not self.markers_readable:
            raise RuntimeError("GetMarkers() failed")
        return {
            float(frame): {
                **info,
                "customData": self.corrupt_readback
                if self.corrupt_readback is not None and info.get("customData")
                else info.get("customData", ""),
            }
            for frame, info in sorted(self._markers.items())
        }

    def AddMarker(
        self,
        frame_id: float,
        color: str,
        name: str,
        note: str,
        duration: float,
        custom_data: str = "",
    ) -> bool:
        if self.add_marker_outcome is None:
            raise RuntimeError("AddMarker() failed")
        if not self.add_marker_outcome:
            return False
        frame = int(frame_id)
        if frame in self._markers or not 0 <= frame < self.GetDuration():
            return False  # Resolve refuses a second marker on an occupied frame
        self._log.record(f"AddMarker({self._name!r} @{self._start} local={frame})")
        self._markers[frame] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": float(duration),
            "customData": custom_data,
        }
        return True

    def add_user_marker(self, frame: int, custom_data: str = "", duration: int = 1) -> None:
        """Test helper: a marker that was already there before DAZ ever saw the item."""

        self._markers[frame] = {
            "color": "Blue",
            "name": f"Marker {frame}",
            "note": "",
            "duration": float(duration),
            "customData": custom_data,
        }

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
        self.sample_rate = 48000
        #: Per-track enable state. Real Resolve only reports this truthfully for the current
        #: timeline (D009), which `GetIsTrackEnabled` below reproduces.
        self.track_enabled: dict[tuple[str, int], bool] = dict.fromkeys(tracks, True)
        #: Failure switch for the destructive path. None = raise.
        self.delete_clips_outcome: bool | None = True

    def GetName(self) -> str:
        return self._name

    def GetUniqueId(self) -> str:
        return self._unique_id

    def GetSetting(self, key: str) -> Any:
        if key == "timelineFrameRate":
            return self._frame_rate
        if key == "timelineSampleRate":
            return self.sample_rate
        return ""

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
        # The README documents 1 <= index <= GetTrackCount; asking beyond that yields
        # nothing rather than raising, which is what callers guard against.
        track = self._tracks.get((track_type, index))
        return track[2] if track is not None else []

    def GetIsTrackEnabled(self, track_type: str, index: int) -> bool:
        # Matches the real API quirk: False for any non-current timeline.
        return self.is_current and self.track_enabled.get((track_type, index), True)

    def GetIsTrackLocked(self, track_type: str, index: int) -> bool:
        return False

    def enabled_audio_tracks(self) -> list[int]:
        return sorted(
            index
            for (kind, index), state in self.track_enabled.items()
            if kind == "audio" and state
        )

    # --- write-capable ---------------------------------------------------------------

    def SetTrackEnable(self, track_type: str, index: int, enabled: bool) -> bool:
        """Reproduces the 21.0.4.5 behaviour measured in D017: reports success, changes
        nothing observable. Present so a regression back to this API is caught by a test."""

        self._log.record(
            f"SetTrackEnable({track_type!r}, {index}, {enabled}) on {self._name!r}"
        )
        return True

    def DeleteTrack(self, track_type: str, index: int) -> bool:
        if (track_type, index) not in self._tracks:
            return False
        self._log.record(f"DeleteTrack({track_type!r}, {index}) on {self._name!r}")
        remaining = [
            (kind, i) for (kind, i) in sorted(self._tracks) if kind == track_type and i != index
        ]
        kept = [self._tracks[key] for key in remaining]
        for key in list(self._tracks):
            if key[0] == track_type:
                del self._tracks[key]
                self.track_enabled.pop(key, None)
        # Resolve renumbers the surviving tracks; the names travel with them.
        for new_index, value in enumerate(kept, start=1):
            self._tracks[(track_type, new_index)] = value
            self.track_enabled[(track_type, new_index)] = True
        return True

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
        duplicate.sample_rate = self.sample_rate
        duplicate.track_enabled = dict(self.track_enabled)
        # deepcopy would give every copied item a detached mutation log; re-link them so the
        # shared log stays the single record of everything that was written.
        for _, _, contents in duplicate._tracks.values():
            for item in contents:
                item._log = self._log
        if self.project is not None:
            self.project.add_timeline(duplicate)  # Resolve registers it in the project
        return duplicate

    def AddTrack(self, track_type: str, sub_track_type: Any = None) -> bool:
        self._log.record(f"AddTrack({track_type!r}) on {self._name!r}")
        index = self.GetTrackCount(track_type) + 1
        label = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle"}[track_type]
        self._tracks[(track_type, index)] = (f"{label} {index}", "", [])
        self.track_enabled[(track_type, index)] = True
        return True

    def DeleteClips(self, items: list[FakeTimelineItem], ripple: bool = False) -> bool:
        """The exact call signature the README documents: `DeleteClips([items], Bool)`.

        Reproduces the behaviour **measured** on Studio 21.0.4.5 (D042), which the README
        does not mention: like `MediaPool.AppendToTimeline`, this only acts on the *current*
        timeline. Called on any other timeline it returns False and deletes nothing.

        The ripple flag is recorded verbatim so a test can assert DAZ never lets Resolve
        ripple a timeline while removing adjustment-layer-style clips.
        """

        if self.delete_clips_outcome is None:
            raise RuntimeError("DeleteClips() failed")
        names = ", ".join(f"{i.GetName()}@{i.GetStart()}" for i in items)
        self._log.record(f"DeleteClips([{names}], ripple={ripple}) on {self._name!r}")
        if not self.is_current:
            return False
        if not self.delete_clips_outcome:
            return False
        for item in items:
            for _, _, contents in self._tracks.values():
                if item in contents:
                    contents.remove(item)
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
                log=self._log,
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
        # Held by object, not by name: Resolve has no trouble with two timelines sharing a
        # name, so identity checks must be expressible against this fake.
        self._current: FakeTimeline | None = next(
            (t for t in timelines if t.GetName() == current_timeline), None
        )
        media_pool.project = self

        # Deliver page state, mirroring what the live API exposes.
        self.render_format = "mov"
        self.render_codec = "ProRes422HQ"
        self.render_mode = 1
        self.render_settings: dict[str, Any] = {}
        self.rendering_in_progress = False
        self._render_presets: list[str] = ["H.264 Master", "Audio Only"]
        self._saved_presets: dict[str, tuple[str, str, int]] = {
            "Audio Only": ("unknown", "", 1)
        }
        self._render_jobs: list[dict[str, Any]] = []
        self._job_status: dict[str, str] = {}
        self._job_counter = 0

        # Failure switches for the unhappy paths.
        self.can_save_render_preset = True
        self.accept_render_settings = True
        self.render_outcome = "Complete"
        #: Override the rendered WAV length to simulate a truncated/padded render.
        self.rendered_wav_seconds: float | None = None

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
        return self._current

    def SetCurrentTimeline(self, timeline: FakeTimeline) -> bool:
        # Switching the current timeline changes no content, but it is restored anyway.
        if timeline not in self._timelines:
            self._timelines.append(timeline)
        self._current = timeline
        for candidate in self._timelines:
            candidate.is_current = candidate is timeline
        return True

    def add_timeline(self, timeline: FakeTimeline) -> None:
        timeline.project = self
        self._timelines.append(timeline)

    def remove_timeline(self, timeline: FakeTimeline) -> None:
        if timeline in self._timelines:
            self._timelines.remove(timeline)

    # --- Deliver page / render queue --------------------------------------------------
    #
    # Shaped after the live API on Studio 21.0.4.5: SetRenderSettings is write-only, the
    # only readable Deliver state is format/codec/mode, and a render preset round-trip is
    # the documented way to snapshot the rest. `rendered_wav_seconds` makes the fake write
    # a real (silent) WAV so the whole render -> ffmpeg -> VAD chain can be tested.

    def GetRenderPresetList(self) -> list[str]:
        return list(self._render_presets)

    def SaveAsNewRenderPreset(self, name: str) -> bool:
        if name in self._render_presets or not self.can_save_render_preset:
            return False
        self._log.record(f"SaveAsNewRenderPreset({name!r})")
        self._render_presets.append(name)
        self._saved_presets[name] = (self.render_format, self.render_codec, self.render_mode)
        return True

    def LoadRenderPreset(self, name: str) -> bool:
        if name not in self._render_presets:
            return False
        self._log.record(f"LoadRenderPreset({name!r})")
        self.render_format, self.render_codec, self.render_mode = self._saved_presets.get(
            name, ("unknown", "", 1)
        )
        return True

    def DeleteRenderPreset(self, name: str) -> bool:
        if name not in self._render_presets:
            return False
        self._log.record(f"DeleteRenderPreset({name!r})")
        self._render_presets.remove(name)
        self._saved_presets.pop(name, None)
        return True

    def GetCurrentRenderFormatAndCodec(self) -> dict[str, str]:
        return {"format": self.render_format, "codec": self.render_codec}

    def SetCurrentRenderFormatAndCodec(self, render_format: str, codec: str) -> bool:
        self._log.record(f"SetCurrentRenderFormatAndCodec({render_format!r}, {codec!r})")
        self.render_format, self.render_codec = render_format, codec
        return True

    def GetCurrentRenderMode(self) -> int:
        return self.render_mode

    def SetCurrentRenderMode(self, mode: int) -> bool:
        self._log.record(f"SetCurrentRenderMode({mode})")
        self.render_mode = int(mode)
        return True

    def SetRenderSettings(self, settings: dict[str, Any]) -> bool:
        if not self.accept_render_settings:
            return False
        self._log.record("SetRenderSettings(...)")
        self.render_settings = dict(settings)
        return True

    def IsRenderingInProgress(self) -> bool:
        return self.rendering_in_progress

    def GetRenderJobList(self) -> list[dict[str, Any]]:
        return [dict(job) for job in self._render_jobs]

    def AddRenderJob(self) -> str:
        self._job_counter += 1
        job_id = f"job-{self._job_counter:04d}"
        self._log.record(f"AddRenderJob() -> {job_id!r}")
        timeline = self.GetCurrentTimeline()
        self._render_jobs.append(
            {
                "JobId": job_id,
                "TimelineName": timeline.GetName() if timeline else None,
                "settings": dict(self.render_settings),
                # Recorded so tests can assert isolation happened *before* the job existed.
                "enabled_audio_tracks": timeline.enabled_audio_tracks() if timeline else [],
            }
        )
        self._job_status[job_id] = "Ready"
        return job_id

    def DeleteRenderJob(self, job_id: str) -> bool:
        for job in list(self._render_jobs):
            if job["JobId"] == job_id:
                self._log.record(f"DeleteRenderJob({job_id!r})")
                self._render_jobs.remove(job)
                self._job_status.pop(job_id, None)
                return True
        return False

    def DeleteAllRenderJobs(self) -> bool:  # pragma: no cover - must never be called
        raise AssertionError("DeleteAllRenderJobs would destroy the user's render queue")

    def StartRendering(self, *job_ids: str) -> bool:
        """Varargs only, on purpose: the keyword overload hung on 21.0.4.5 (D018)."""
        self._log.record(f"StartRendering({job_ids!r})")
        for job_id in job_ids:
            job = next((j for j in self._render_jobs if j["JobId"] == job_id), None)
            if job is None:
                return False
            if self.render_outcome != "Complete":
                self._job_status[job_id] = self.render_outcome
                continue
            self._write_fake_render(job)
            self._job_status[job_id] = "Complete"
        return True

    def GetRenderJobStatus(self, job_id: str) -> dict[str, Any]:
        return {"JobStatus": self._job_status.get(job_id, "Unknown"), "CompletionPercentage": 100}

    def _write_fake_render(self, job: dict[str, Any]) -> None:
        """Write a silent 48 kHz stereo WAV the length of the rendered timeline."""

        settings = job["settings"]
        directory = Path(settings.get("TargetDir", ""))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{settings.get('CustomName', 'render')}.wav"
        timeline = next(
            (t for t in self._timelines if t.GetName() == job["TimelineName"]), None
        )
        seconds = self.rendered_wav_seconds
        if seconds is None and timeline is not None:
            frames = timeline.GetEndFrame() - timeline.GetStartFrame()
            seconds = frames / float(timeline.GetSetting("timelineFrameRate"))
        rate = 48000
        count = int(round((seconds or 0.0) * rate))
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\x00\x00\x00\x00" * count)
        job["output"] = str(path)


class FakeMediaStorage:
    """Resolve only renders into these locations; anything else is refused (D024)."""

    def __init__(self, volumes: list[str] | None = None) -> None:
        self.volumes = volumes if volumes is not None else []

    def GetMountedVolumeList(self) -> list[str]:
        return list(self.volumes)


class FakeResolve:
    def __init__(self, project: FakeProject) -> None:
        self._project = project
        self.media_storage = FakeMediaStorage()

    def GetMediaStorage(self) -> FakeMediaStorage:
        return self.media_storage

    def GetCurrentPage(self) -> str:
        return "edit"

    def GetVersionString(self) -> str:
        return "21.0.4.5"

    def GetProductName(self) -> str:
        return "DaVinci Resolve Studio"

    def GetProjectManager(self) -> FakeResolve:
        return self

    def GetCurrentProject(self) -> FakeProject:
        return self._project


def build_test_project(audio_tracks: int = 1) -> tuple[FakeResolve, FakeProject]:
    """Two timelines shaped like DAZ_INPUT / DAZ_OUTPUT_MVP.

    `project.mutations` is the shared write log; it must stay empty for read-only paths.
    Pass `audio_tracks=3` for the A1/A2/A3 shape the real test project has, which is what
    voice-track isolation needs in order to have something to isolate *from*.
    """

    log = MutationLog()

    source = FakeMediaPoolItem("source.mov", {"Type": "Video + Audio", "Frames": "304871"})
    # The Phase 8 bin: three promotion generators that animate then hold, and one reset
    # generator per facecam level. Native lengths are the real ones and are used by nothing.
    zoom_assets = [
        FakeMediaPoolItem(name, {"Type": "Generator", "Frames": frames})
        for name, frames in (
            ("FACE_X1", "132"),
            ("FACE_X2", "132"),
            ("FACE_X3", "132"),
            ("X1_TO_X0", "42"),
            ("X2_TO_X0", "42"),
            ("X3_TO_X0", "42"),
        )
    ]

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
    }
    for index in range(1, audio_tracks + 1):
        # Distinct item counts per track so a test can tell which one survived isolation.
        base_tracks[("audio", index)] = (f"Audio {index}", "stereo", list(a1) * index)
    reference_tracks = dict(base_tracks)
    reference_tracks[("video", 3)] = (
        "Video 3",
        "",
        [
            generator("FACE_X1", 216045, 216132),
            generator("X1_TO_X0", 216132, 216174),
            generator("FACE_X1", 216350, 216400),
        ],
    )

    root = FakeFolder("Master", [source], [FakeFolder("DAVINCI_AUTO_ZOOM", zoom_assets)])
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
