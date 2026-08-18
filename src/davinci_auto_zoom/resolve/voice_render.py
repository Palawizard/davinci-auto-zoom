"""Phase 3 integration spike: isolate the configured voice track and render it to audio.

    configured Resolve voice track -> isolated temporary audio -> 16 kHz mono PCM
    -> Silero VAD -> SpeechSegments in absolute timeline frames

The hard part is not the VAD, it is getting *the audio the user actually hears on that
track* without damaging anything. Rebuilding the track from its source files would throw
away cuts, trims, fades, levels and every clip/track treatment; only Resolve can render what
Resolve plays. So this module renders — and a render touches three pieces of persistent user
state (the timeline, the render queue, the Deliver settings), each of which is snapshotted
and restored.

Safety model, in order:

1. fail-closed preflight — one mismatch aborts *before* any mutating call (D015);
2. the Deliver page state is captured into a temporary render preset, the only documented
   way to read it back, since `SetRenderSettings` has no getter. Saving that preset is
   itself a mutation, so it happens *inside* the `try/finally` and fails closed: if the
   snapshot cannot be taken, no render setting is touched at all;
3. every timeline mutation happens on a `DAZ_AUDIO_SCRATCH_*` duplicate created by this run;
   the configured source timeline is never touched, and none of its tracks are ever removed;
4. exactly one render job is created; pre-existing jobs are recorded and never deleted
   (`DeleteAllRenderJobs` is deliberately not used);
5. `try/finally` unwinds in reverse: delete our job, restore Deliver state, restore the
   current timeline, delete only the scratch timeline this run created;
6. a post-run audit re-reads the protected timelines, the render queue and the Deliver state.

Every Resolve method used here is documented in the Developer/Scripting README installed with
the running build (verified for Studio 21.0.4.5): `Timeline.DuplicateTimeline`,
`Timeline.DeleteTrack`, `Timeline.GetTrackName`, `Project.SetCurrentTimeline`,
`Project.SaveAsNewRenderPreset`, `Project.LoadRenderPreset`, `Project.DeleteRenderPreset`,
`Project.GetRenderPresetList`, `Project.SetRenderSettings`, `Project.AddRenderJob`,
`Project.StartRendering`, `Project.IsRenderingInProgress`, `Project.GetRenderJobStatus`,
`Project.GetRenderJobList`, `Project.DeleteRenderJob`, `Project.GetCurrentRenderMode`,
`Project.SetCurrentRenderMode`, `Project.GetCurrentRenderFormatAndCodec`,
`MediaPool.DeleteTimelines`, plus read-only getters.
"""

from __future__ import annotations

import shutil
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.probe import (
    VoiceRenderTarget,
    signature_differences,
    structural_signature,
    voice_render_preflight_failures,
)
from davinci_auto_zoom.resolve.session import (
    find_timeline,
    snapshot_project,
    snapshot_timeline,
)

SCRATCH_PREFIX = "DAZ_AUDIO_SCRATCH_"
RESTORE_PRESET_PREFIX = "DAZ_RENDER_RESTORE_"
RENDER_DIR_PREFIX = "DAZ_RENDER_TMP_"
RENDER_BASENAME = "daz_voice"

#: Opt-in token. Distinct from the Phase 2 write probe's flag so neither can be triggered by
#: muscle memory for the other.
CONFIRM_FLAG = "--confirm-resolve-render-test"

#: How long to wait for Resolve to finish an audio-only render before giving up and cleaning
#: up. Audio-only renders of an hour-long timeline take seconds; this is a hang guard.
RENDER_TIMEOUT_SECONDS = 900
RENDER_POLL_SECONDS = 0.5

#: How long a queued job may sit without Resolve reporting any render activity before the
#: probe declares it stuck. Long enough for Resolve to pick a job up, short enough that a
#: blocked application does not cost a quarter of an hour before it is reported.
STALL_GRACE_SECONDS = 30.0

TERMINAL_JOB_STATUSES = frozenset({"Complete", "Failed", "Cancelled"})


class VoiceRenderRefused(RuntimeError):
    """Raised before any mutation when the probe's guards are not satisfied."""


class VoiceRenderFailed(RuntimeError):
    """Raised when the render itself did not produce usable audio. Cleanup still runs."""


def scratch_timeline_name(now: datetime | None = None, token: str | None = None) -> str:
    """Unique name for a timeline created by *this* run. Never reused, never guessed."""

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{SCRATCH_PREFIX}{stamp}_{token or uuid.uuid4().hex[:8]}"


def restore_preset_name(token: str | None = None) -> str:
    return f"{RESTORE_PRESET_PREFIX}{token or uuid.uuid4().hex[:8]}"


def media_storage_volumes(resolve: Any) -> tuple[str, ...]:
    """Directories Resolve is willing to read and write. Read-only."""

    storage = resolve.GetMediaStorage()
    if storage is None:
        return ()
    return tuple(str(volume) for volume in (storage.GetMountedVolumeList() or ()))


def create_render_directory(
    volumes: tuple[str, ...], token: str | None = None
) -> Path:
    """A fresh, uniquely named directory Resolve will accept as a render target.

    Resolve refuses any render path outside its configured Media Storage — the GUI raises a
    modal *"Render Path Inaccessible — Please select a render path from within the media
    storage"* and the scripting API just returns an empty job id (D024). A system temp
    directory is therefore not usable for the render itself, only for everything after it.

    The first writable volume wins. The directory carries a run-unique name so cleanup can
    delete it without ever touching user media.
    """

    name = f"{RENDER_DIR_PREFIX}{token or uuid.uuid4().hex[:8]}"
    attempted: list[str] = []
    for volume in volumes:
        candidate = Path(volume) / name
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            attempted.append(f"{volume}: {exc.strerror or exc}")
            continue
        return candidate
    raise VoiceRenderFailed(
        "could not create a render directory inside any Resolve Media Storage volume. "
        f"Tried: {attempted or list(volumes) or ['(no volumes configured)']}. Resolve will "
        "not render outside Media Storage; add a writable location in "
        "Preferences > System > Media Storage."
    )


def remove_render_directory(directory: Path | None, report: VoiceRenderReport) -> None:
    """Delete the render directory, but only one this run created."""

    if directory is None:
        return
    if not directory.name.startswith(RENDER_DIR_PREFIX):
        report.notes.append(f"refusing to delete {directory}: not a run-created directory")
        return
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        report.notes.append(f"could not delete render directory {directory}: {exc}")
        return
    report.render_directory_removed = True


@dataclass
class DeliveryState:
    """The parts of the Deliver page this build lets us read back.

    `SetRenderSettings` is write-only, so format/codec/mode are the only fields the API
    exposes directly. Everything else is captured indirectly by saving a render preset, which
    is why `preset` matters more than it looks.
    """

    render_format: str | None = None
    render_codec: str | None = None
    render_mode: int | None = None
    preset: str | None = None
    #: True once format/codec/mode were read. Until then there is nothing to restore, and
    #: comparing the live Deliver page against unset fields would invent false differences.
    captured: bool = False
    preset_saved: bool = False
    preset_restored: bool | None = None
    preset_deleted: bool | None = None
    format_restored: bool | None = None
    mode_restored: bool | None = None
    unrestored: tuple[str, ...] = ()


@dataclass
class RenderQueueState:
    job_ids_before: tuple[str, ...] = ()
    job_ids_after: tuple[str, ...] = ()
    our_job_id: str | None = None
    our_job_deleted: bool | None = None
    job_status: str | None = None
    preexisting_jobs_preserved: bool | None = None

    @property
    def only_our_job_removed(self) -> bool:
        return set(self.job_ids_before) == set(self.job_ids_after)


@dataclass
class RenderedAudio:
    """The file Resolve produced and the file ffmpeg turned it into."""

    rendered_path: str | None = None
    rendered_bytes: int | None = None
    normalized_path: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    sample_count: int | None = None
    duration_seconds: float | None = None
    expected_frames: int | None = None
    expected_seconds: float | None = None
    delta_seconds: float | None = None
    delta_frames: float | None = None
    tolerance_frames: float | None = None
    duration_ok: bool | None = None


@dataclass
class VoiceRenderReport:
    """Everything the probe observed. `to_dict()` is the `--json` payload."""

    resolve_version: str = ""
    product_name: str = ""
    project_name: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    preflight_failures: tuple[str, ...] = ()
    ffmpeg_version: str | None = None

    timeline_name: str | None = None
    timeline_frame_rate: str | None = None
    timeline_start_frame: int | None = None
    timeline_end_frame: int | None = None
    timeline_sample_rate: int | None = None

    #: "audio" (the Phase 3 voice render and its Phase 9a secondary-audio twin) or "video".
    #: Only the success rule differs; every safety guarantee is shared.
    media_kind: str = "audio"
    #: The audio tracks left on the scratch timeline. `(1,)` is the classic voice render.
    kept_audio_tracks: tuple[int, ...] = ()

    wrote: bool = False
    scratch_name: str | None = None
    scratch_unique_id: str | None = None
    voice_track_index: int | None = None
    voice_track_name: str | None = None
    audio_tracks_before: int | None = None
    audio_tracks_after: int | None = None
    removed_audio_tracks: tuple[int, ...] = ()
    previous_current_timeline: str | None = None
    restored_current_timeline: str | None = None
    scratch_deleted: bool | None = None
    scratch_absent_after_cleanup: bool | None = None

    delivery: DeliveryState = field(default_factory=DeliveryState)
    queue: RenderQueueState = field(default_factory=RenderQueueState)
    audio: RenderedAudio = field(default_factory=RenderedAudio)

    render_seconds: float | None = None
    ffmpeg_seconds: float | None = None
    total_seconds: float | None = None

    temp_directory: str | None = None
    temp_files_kept: bool = False
    media_storage_volumes: tuple[str, ...] = ()
    render_directory: str | None = None
    render_directory_removed: bool = False

    audit_checked: tuple[str, ...] = ()
    audit_differences: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def clean(self) -> bool:
        """True when the originals, the render queue and the Deliver state came back intact."""

        return (
            not self.audit_differences
            and self.scratch_absent_after_cleanup is not False
            and self.queue.preexisting_jobs_preserved is not False
            and self.queue.our_job_deleted is not False
            and not self.delivery.unrestored
        )

    @property
    def succeeded(self) -> bool:
        if self.media_kind == "video":
            # A video render has no VAD downstream, so there is no normalized waveform and
            # no duration check to pass — only a file, and a clean restoration.
            return self.clean and self.error is None and bool(self.audio.rendered_path)
        return (
            self.clean
            and self.error is None
            and self.audio.duration_ok is True
            and bool(self.audio.normalized_path)
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["clean"] = self.clean
        data["succeeded"] = self.succeeded
        return data

    def to_text(self) -> str:
        audio = self.audio
        lines = [
            "davinci-auto-zoom voice render (Phase 3 spike)",
            f"  product   : {self.product_name} {self.resolve_version}",
            f"  project   : {self.project_name}",
            f"  timeline  : {self.timeline_name} "
            f"[{self.timeline_start_frame}, {self.timeline_end_frame}) "
            f"@ {self.timeline_frame_rate} fps",
            f"  voice     : A{self.voice_track_index} (configured, not detected)",
            f"  scratch   : {self.scratch_name or '-'}",
            f"  ffmpeg    : {self.ffmpeg_version or '-'}",
        ]
        if self.preflight_failures:
            lines.append("  PREFLIGHT REFUSED:")
            lines.extend(f"    - {failure}" for failure in self.preflight_failures)
        if self.audio_tracks_before is not None:
            removed = ", ".join(f"A{index}" for index in self.removed_audio_tracks) or "-"
            lines.append(
                f"  isolation : scratch audio tracks {self.audio_tracks_before} -> "
                f"{self.audio_tracks_after}, deleted [{removed}], kept "
                f"{self.voice_track_name!r}"
            )
        lines.extend(
            [
                f"  render    : job={self.queue.our_job_id} status={self.queue.job_status} "
                f"in {self.render_seconds:.1f}s"
                if self.render_seconds is not None
                else f"  render    : job={self.queue.our_job_id} status={self.queue.job_status}",
                f"  rendered  : {audio.rendered_path or '-'} ({audio.rendered_bytes} bytes)",
                f"  normalized: {audio.sample_rate} Hz x{audio.channels} "
                f"{audio.sample_count} samples = {audio.duration_seconds:.3f}s"
                if audio.duration_seconds is not None
                else "  normalized: -",
                f"  expected  : {audio.expected_frames} frames = "
                f"{audio.expected_seconds:.3f}s  delta={audio.delta_seconds:+.4f}s "
                f"({audio.delta_frames:+.3f} frames, tolerance "
                f"{audio.tolerance_frames}) -> {'ok' if audio.duration_ok else 'MISMATCH'}"
                if audio.expected_seconds is not None and audio.delta_seconds is not None
                else "  expected  : -",
                f"  cleanup   : restored_timeline={self.restored_current_timeline} "
                f"scratch_deleted={self.scratch_deleted} "
                f"absent={self.scratch_absent_after_cleanup} "
                f"render_dir_removed={self.render_directory_removed}",
                f"  queue     : before={len(self.queue.job_ids_before)} "
                f"after={len(self.queue.job_ids_after)} "
                f"our_job_deleted={self.queue.our_job_deleted} "
                f"preexisting_preserved={self.queue.preexisting_jobs_preserved}",
                f"  delivery  : preset_saved={self.delivery.preset_saved} "
                f"restored={self.delivery.preset_restored} "
                f"deleted={self.delivery.preset_deleted} "
                f"format_restored={self.delivery.format_restored} "
                f"mode_restored={self.delivery.mode_restored}",
            ]
        )
        if self.delivery.unrestored:
            lines.append("  DELIVER STATE NOT RESTORED:")
            lines.extend(f"    - {item}" for item in self.delivery.unrestored)
        lines.append(f"  audit     : {', '.join(self.audit_checked) or '-'}")
        if self.audit_differences:
            lines.append("  AUDIT DIFFERENCES:")
            lines.extend(f"    - {difference}" for difference in self.audit_differences)
        else:
            lines.append("  audit differences: none")
        if self.temp_files_kept:
            lines.append(f"  TEMP AUDIO KEPT AT: {self.temp_directory}")
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        lines.extend(f"  note: {note}" for note in self.notes)
        lines.append(f"  RESULT: {'PASS' if self.succeeded else 'FAIL'}")
        return "\n".join(lines)


def _capture_delivery(project: Any, report: VoiceRenderReport) -> None:
    """Read back everything the API allows, then snapshot the rest into a temp preset.

    **This is the first mutating call of the run** — `SaveAsNewRenderPreset` adds a preset to
    the user's Deliver page — so it is made inside the `try/finally` that guarantees cleanup,
    and the state object is published on the report *before* the call, so an exception mid-way
    still leaves cleanup something to undo.

    It is also fail-closed: if the snapshot cannot be taken, the Deliver page could not be put
    back afterwards, so nothing may load a preset or touch render settings (D020).
    """

    current = project.GetCurrentRenderFormatAndCodec() or {}
    state = DeliveryState(
        render_format=current.get("format"),
        render_codec=current.get("codec"),
        render_mode=project.GetCurrentRenderMode(),
        preset=restore_preset_name(),
        captured=True,
    )
    report.delivery = state
    state.preset_saved = bool(project.SaveAsNewRenderPreset(state.preset))
    if not state.preset_saved:
        report.notes.append(
            f"SaveAsNewRenderPreset({state.preset!r}) failed, so the Deliver page could not "
            "be snapshotted; the run was refused before any render setting was changed"
        )
        raise VoiceRenderFailed(
            f"SaveAsNewRenderPreset({state.preset!r}) failed. Only format/codec/mode are "
            "readable through the API, so without that preset the rest of the Deliver page "
            "(target directory, file name, export flags, audio settings) could not be "
            "restored after the render. Refusing to change render settings."
        )


def _restore_delivery(project: Any, state: DeliveryState, report: VoiceRenderReport) -> None:
    """Undo the Deliver page changes and *verify* the undo, recording what did not come back."""

    if not state.captured:
        # Nothing was read, so nothing was changed either: the run stopped before or during
        # the snapshot. Comparing against unset fields would report invented differences.
        return

    unrestored: list[str] = []
    if state.preset:
        # Deletion is attempted whenever the preset actually exists, not only when the save
        # was recorded as successful: a failure *after* Resolve created it must not leak it.
        existing = state.preset in tuple(project.GetRenderPresetList() or ())
        if state.preset_saved:
            with suppress(Exception):
                state.preset_restored = bool(project.LoadRenderPreset(state.preset))
            if not state.preset_restored:
                unrestored.append(f"render preset {state.preset!r} could not be re-loaded")
        if existing:
            with suppress(Exception):
                state.preset_deleted = bool(project.DeleteRenderPreset(state.preset))
            if not state.preset_deleted:
                unrestored.append(
                    f"temporary render preset {state.preset!r} still exists; delete it from "
                    "the Deliver page preset list"
                )

    with suppress(Exception):
        if state.render_mode is not None:
            project.SetCurrentRenderMode(state.render_mode)
    with suppress(Exception):
        if state.render_format and state.render_format != "unknown" and state.render_codec:
            project.SetCurrentRenderFormatAndCodec(state.render_format, state.render_codec)

    current = project.GetCurrentRenderFormatAndCodec() or {}
    state.format_restored = (
        current.get("format") == state.render_format
        and current.get("codec") == state.render_codec
    )
    if not state.format_restored:
        unrestored.append(
            f"render format/codec is {current.get('format')!r}/{current.get('codec')!r}, "
            f"was {state.render_format!r}/{state.render_codec!r}"
        )
    mode = project.GetCurrentRenderMode()
    state.mode_restored = mode == state.render_mode
    if not state.mode_restored:
        unrestored.append(f"render mode is {mode}, was {state.render_mode}")
    state.unrestored = tuple(unrestored)
    if unrestored:
        report.notes.append(
            "the Deliver page did not fully return to its previous state; the differences "
            "are listed above and were not silently ignored"
        )


def _job_ids(project: Any) -> tuple[str, ...]:
    return tuple(
        str(job.get("JobId"))
        for job in (project.GetRenderJobList() or [])
        if job.get("JobId") is not None
    )


def _silence_audio_tracks(
    scratch: Any, keep: tuple[int, ...], report: VoiceRenderReport
) -> None:
    """Leave `keep` audible on the *scratch* timeline by **emptying** every other track.

    Phase 9a needs the complement of the voice track — "everything the viewer hears when A1
    is muted" — and `_isolate_voice_track`'s `DeleteTrack` cannot express it. Measured on
    Studio 21.0.4.5: deleting A1 renumbers the survivors *and* renames them, so the track
    that was `Audio 2` comes back as `Audio 1`. In a project where every audio track carries
    the same 21 items (this one), the post-condition check can then no longer tell which
    track survived — and it correctly refused to render rather than guess.

    Emptying has the property `SetTrackEnable` lacks (D017) and `DeleteTrack` loses here: its
    effect is observable *and* positional. The dropped tracks end with zero items while the
    kept tracks keep their index, their name and their exact item count, so the proof that
    the right audio was rendered survives the operation.
    """

    count = int(scratch.GetTrackCount("audio") or 0)
    if not keep:
        raise VoiceRenderFailed("refusing to silence every audio track: nothing would render")
    missing = [index for index in keep if not 1 <= index <= count]
    if missing:
        raise VoiceRenderFailed(
            f"audio track(s) {missing} do not exist on the scratch timeline "
            f"({count} audio tracks)"
        )
    before = {
        index: (
            str(scratch.GetTrackName("audio", index)),
            len(scratch.GetItemListInTrack("audio", index) or []),
        )
        for index in range(1, count + 1)
    }
    report.voice_track_name = before[min(keep)][0]
    report.audio_tracks_before = count

    emptied: list[int] = []
    for index in range(1, count + 1):
        if index in keep:
            continue
        items = scratch.GetItemListInTrack("audio", index) or []
        if items and not scratch.DeleteClips(list(items), False):
            raise VoiceRenderFailed(
                f"DeleteClips on audio track A{index} of the scratch timeline failed; "
                "refusing to render a mix that may still contain it"
            )
        emptied.append(index)
    report.removed_audio_tracks = tuple(emptied)
    report.audio_tracks_after = int(scratch.GetTrackCount("audio") or 0)

    problems: list[str] = []
    for index in range(1, count + 1):
        name = str(scratch.GetTrackName("audio", index))
        items = len(scratch.GetItemListInTrack("audio", index) or [])
        if index in keep:
            if (name, items) != before[index]:
                problems.append(
                    f"kept track A{index} changed from {before[index]} to {(name, items)}"
                )
        elif items:
            problems.append(f"silenced track A{index} still holds {items} item(s)")
    if problems:
        raise VoiceRenderFailed(
            "the scratch timeline's audio is not what was asked for, so the wrong mix would "
            "have been analysed: " + "; ".join(problems)
        )


def _isolate_voice_track(
    scratch: Any, keep: tuple[int, ...], report: VoiceRenderReport
) -> None:
    """Leave only the voice track on the *scratch* timeline, by deleting the others.

    `SetTrackEnable` is deliberately **not** used. Measured on Studio 21.0.4.5 (D017): it
    returns `True` for both audio and video tracks while `GetIsTrackEnabled` keeps reporting
    `True` afterwards, so there is no way to confirm the mute actually happened. Rendering
    audio we cannot prove is isolated would mean silently analysing the game mix as if it
    were the creator's voice.

    `DeleteTrack` has the property the enable flag lacks: its effect is *observable*.
    `GetTrackCount("audio")` drops, and the surviving track's name and item count can be
    matched against what was captured before. It is only ever applied to a duplicate this
    run created and deletes; the configured source timeline keeps all of its tracks.

    It is only used to keep **one** track, and specifically the lowest-numbered one it needs
    to survive. Deleting a lower-indexed track renumbers *and* renames the survivors on
    Studio 21.0.4.5, which destroys the evidence this function's post-condition rests on;
    `_silence_audio_tracks` exists for every other set.
    """

    count = int(scratch.GetTrackCount("audio") or 0)
    if not keep:
        raise VoiceRenderFailed("refusing to delete every audio track: nothing would render")
    missing = [index for index in keep if not 1 <= index <= count]
    if missing:
        raise VoiceRenderFailed(
            f"audio track(s) {missing} do not exist on the scratch timeline "
            f"({count} audio tracks)"
        )
    # Captured before any deletion, in ascending index order: this is the evidence the
    # survivors are checked against afterwards.
    expected = [
        (
            str(scratch.GetTrackName("audio", index)),
            len(scratch.GetItemListInTrack("audio", index) or []),
        )
        for index in sorted(keep)
    ]
    report.voice_track_name = expected[0][0]
    report.audio_tracks_before = count

    # Descending, so deleting a track never renumbers one still to be deleted.
    removed: list[int] = []
    for index in range(count, 0, -1):
        if index in keep:
            continue
        if not scratch.DeleteTrack("audio", index):
            raise VoiceRenderFailed(
                f"DeleteTrack('audio', {index}) failed on the scratch timeline; refusing to "
                "render audio that may still contain other tracks"
            )
        removed.append(index)
    report.removed_audio_tracks = tuple(sorted(removed))

    remaining = int(scratch.GetTrackCount("audio") or 0)
    report.audio_tracks_after = remaining
    if remaining != len(keep):
        raise VoiceRenderFailed(
            f"expected exactly {len(keep)} audio track(s) on the scratch timeline after "
            f"isolation, found {remaining}"
        )
    surviving = [
        (
            str(scratch.GetTrackName("audio", index)),
            len(scratch.GetItemListInTrack("audio", index) or []),
        )
        for index in range(1, remaining + 1)
    ]
    if surviving != expected:
        raise VoiceRenderFailed(
            f"the surviving audio track(s) are {surviving}, expected {expected}; the wrong "
            "track would have been analysed"
        )


def _render_audio(
    project: Any,
    target_directory: Path,
    report: VoiceRenderReport,
    preset: str,
    *,
    timeout_seconds: float,
    poll_seconds: float,
    export_video: bool = False,
) -> Path:
    """Queue exactly one render job, run it, and return the file it produced.

    `export_video` swaps the audio-only export for a video-only one. Everything that makes
    the render *safe* — one job, restored Deliver state, scratch timeline, temp directory —
    is identical, which is the whole reason this is a flag rather than a second module.
    """

    if not project.LoadRenderPreset(preset):
        raise VoiceRenderFailed(f"LoadRenderPreset({preset!r}) failed")
    # Single clip: one continuous file for the whole timeline rather than one per edit.
    project.SetCurrentRenderMode(1)
    settings: dict[str, Any] = {
        # The whole timeline, ignoring any mark in/out the user left behind.
        "SelectAllFrames": True,
        "TargetDir": str(target_directory),
        "CustomName": RENDER_BASENAME,
        "ExportVideo": export_video,
        "ExportAudio": not export_video,
        "ExportSubtitle": False,
        # Full extents / frame handles would change the file's start instant, which would
        # silently offset every speech frame downstream.
        "UseFullExtents": False,
        "AddFrameHandles": 0,
        # NOT set: "ReplaceExistingFilesInPlace". It is documented, but Studio 21.0.4.5
        # rejects it — SetRenderSettings is all-or-nothing, so one unsupported key fails the
        # whole call (D019). Nothing is lost: the target is a fresh empty temp directory, so
        # there is never an existing file to replace.
        #
        # Also NOT set: FormatWidth/FormatHeight. Rendering small would save a few seconds
        # and put an untested key in an all-or-nothing call; ffmpeg downscales for free.
    }
    if not export_video:
        # 48 kHz 24-bit is Resolve's lossless-ish default; ffmpeg does the 16 kHz downsample
        # afterwards, so nothing here is the VAD's input format.
        settings["AudioBitDepth"] = 24
        settings["AudioSampleRate"] = int(report.timeline_sample_rate or 48000)
    if not project.SetRenderSettings(settings):
        raise VoiceRenderFailed(
            f"SetRenderSettings returned False for {settings!r}. The call is all-or-nothing: "
            "a single key this Resolve build does not accept fails the whole dict."
        )

    job_id = project.AddRenderJob()
    if not job_id:
        raise VoiceRenderFailed("AddRenderJob() returned no job id")
    report.queue.our_job_id = str(job_id)

    started = time.perf_counter()
    # The plain varargs overload, deliberately: the Blackmagic bridge is a C wrapper and
    # `StartRendering([id], isInteractiveMode=False)` hung indefinitely on 21.0.4.5 (D018).
    # `StartRendering(jobId1, jobId2, ...)` is documented and takes no keyword.
    if not project.StartRendering(job_id):
        raise VoiceRenderFailed(f"StartRendering({job_id!r}) returned False")

    status: dict[str, Any] = {}
    stalled_polls = 0
    max_stalled_polls = max(int(STALL_GRACE_SECONDS / poll_seconds), 1) if poll_seconds else 1
    while time.perf_counter() - started < timeout_seconds:
        status = project.GetRenderJobStatus(job_id) or {}
        state = str(status.get("JobStatus", ""))
        if state in TERMINAL_JOB_STATUSES:
            break
        if project.IsRenderingInProgress() or state == "Rendering":
            stalled_polls = 0
        else:
            # Neither started nor finished. Resolve legitimately takes a moment to pick a
            # job up, but a job that never moves would otherwise spin until the timeout.
            stalled_polls += 1
            if stalled_polls > max_stalled_polls:
                raise VoiceRenderFailed(
                    f"render job {job_id!r} never started: status stayed {state!r} and "
                    f"Resolve reported no render in progress for {STALL_GRACE_SECONDS:.0f}s. "
                    "A modal dialog in the Resolve window will do this; check the app."
                )
        time.sleep(poll_seconds)
    report.render_seconds = time.perf_counter() - started
    report.queue.job_status = str(status.get("JobStatus", "unknown"))

    if report.queue.job_status != "Complete":
        raise VoiceRenderFailed(
            f"render job {job_id!r} ended as {report.queue.job_status!r} "
            f"after {report.render_seconds:.1f}s: {status}"
        )

    produced = sorted(p for p in target_directory.iterdir() if p.is_file())
    if len(produced) != 1:
        raise VoiceRenderFailed(
            f"expected exactly one rendered file in {target_directory}, found "
            f"{[p.name for p in produced]}"
        )
    return produced[0]


def _protected_signatures(project: Any, names: tuple[str, ...]) -> dict[str, Any]:
    signatures: dict[str, Any] = {}
    for name in names:
        timeline = find_timeline(project, name)
        if timeline is not None:
            signatures[name] = structural_signature(
                snapshot_timeline(timeline, is_current=False)
            )
    return signatures


def _cleanup(
    project: Any,
    media_pool: Any,
    previous_timeline: Any,
    scratch: Any,
    report: VoiceRenderReport,
) -> None:
    """Unwind in reverse order of setup. Never raises; records what it could not undo."""

    # 1. Our render job first: it references the scratch timeline we are about to delete.
    if report.queue.our_job_id:
        try:
            report.queue.our_job_deleted = bool(
                project.DeleteRenderJob(report.queue.our_job_id)
            )
        except Exception as exc:
            report.queue.our_job_deleted = False
            report.notes.append(f"DeleteRenderJob raised {exc!r}")
        if not report.queue.our_job_deleted:
            report.notes.append(
                f"RENDER JOB LEFT IN THE QUEUE: {report.queue.our_job_id!r}. No other job "
                "was touched; remove it from the Deliver page."
            )

    # 2. Deliver page state, as recorded on the report (the snapshot itself is a mutation and
    #    may have failed halfway, so cleanup reads the live state rather than a passed copy).
    _restore_delivery(project, report.delivery, report)

    # 3. The timeline the user had open.
    if previous_timeline is not None:
        with suppress(Exception):
            project.SetCurrentTimeline(previous_timeline)
    current = project.GetCurrentTimeline()
    report.restored_current_timeline = str(current.GetName()) if current else None

    # 4. Only the scratch timeline this run created.
    if scratch is not None:
        name = report.scratch_name or ""
        if not name.startswith(SCRATCH_PREFIX):
            report.notes.append(f"refusing to delete timeline {name!r}: not a scratch name")
            report.scratch_deleted = False
        else:
            try:
                report.scratch_deleted = bool(media_pool.DeleteTimelines([scratch]))
            except Exception as exc:
                report.scratch_deleted = False
                report.notes.append(f"DeleteTimelines raised {exc!r}")
            report.scratch_absent_after_cleanup = find_timeline(project, name) is None
            if not report.scratch_absent_after_cleanup:
                report.notes.append(
                    f"SCRATCH TIMELINE LEFT BEHIND: {name!r}. It was not deleted; no other "
                    "timeline was touched. Delete it manually after inspection."
                )

    # 5. The render queue as a whole.
    report.queue.job_ids_after = _job_ids(project)
    report.queue.preexisting_jobs_preserved = set(report.queue.job_ids_before).issubset(
        report.queue.job_ids_after
    )
    if not report.queue.preexisting_jobs_preserved:
        lost = sorted(set(report.queue.job_ids_before) - set(report.queue.job_ids_after))
        report.notes.append(f"PRE-EXISTING RENDER JOBS DISAPPEARED: {lost}")


def render_voice_track(
    resolve: Any,
    project: Any,
    config: Config,
    target: VoiceRenderTarget,
    temp_directory: Path,
    *,
    confirmed: bool,
    now: datetime | None = None,
    timeout_seconds: float = RENDER_TIMEOUT_SECONDS,
    poll_seconds: float = RENDER_POLL_SECONDS,
    protected_timelines: tuple[str, ...] = (),
    keep_audio_tracks: tuple[int, ...] | None = None,
    export_video: bool = False,
    render_preset: str | None = None,
) -> tuple[VoiceRenderReport, Path | None]:
    """Render the configured voice track in isolation. Returns the report and the audio file.

    The audio path is `None` when the run failed; the report always explains why and cleanup
    has always run.

    The three optional arguments exist for Phase 9a and default to Phase 3's exact behaviour:
    `keep_audio_tracks` chooses which audio tracks survive on the scratch duplicate (`None`
    means the configured voice track alone), `export_video` swaps the audio export for a
    video one, and `render_preset` overrides the target's `Audio Only`. Nothing about the
    safety model is optional or overridable — the scratch timeline, the single render job,
    the captured/restored Deliver state and the post-run audit are the same code either way.
    """

    from davinci_auto_zoom.speech.audio import FfmpegError, ffmpeg_version

    overall_started = time.perf_counter()
    keep = tuple(sorted(set(keep_audio_tracks or (target.voice_audio_track,))))
    preset = render_preset or target.render_preset
    report = VoiceRenderReport(
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
        target=asdict(target),
        voice_track_index=target.voice_audio_track,
        temp_directory=str(temp_directory),
        media_kind="video" if export_video else "audio",
        kept_audio_tracks=keep,
    )

    if not confirmed:
        raise VoiceRenderRefused(
            f"this probe renders from Resolve and requires {CONFIRM_FLAG} to run"
        )

    # ffmpeg is checked before anything is touched: discovering it is missing *after*
    # duplicating a timeline and queueing a render would be a pointless mutation.
    try:
        report.ffmpeg_version = ffmpeg_version()
    except FfmpegError as exc:
        raise VoiceRenderRefused(str(exc)) from exc

    report.media_storage_volumes = media_storage_volumes(resolve)
    snapshot = snapshot_project(resolve, project, config)
    report.preflight_failures = voice_render_preflight_failures(
        snapshot,
        target,
        render_presets=tuple(project.GetRenderPresetList() or ()),
        rendering_in_progress=bool(project.IsRenderingInProgress()),
        media_storage_volumes=report.media_storage_volumes,
        required_preset=preset,
    )
    if report.preflight_failures:
        raise VoiceRenderRefused(
            "voice render preflight failed, nothing was modified:\n"
            + "\n".join(f"  - {failure}" for failure in report.preflight_failures)
        )

    source_snapshot = snapshot.timeline(target.source_timeline)
    assert source_snapshot is not None  # guaranteed by preflight
    source = find_timeline(project, target.source_timeline)
    assert source is not None
    report.timeline_name = source_snapshot.name
    report.timeline_frame_rate = str(source.GetSetting("timelineFrameRate"))
    report.timeline_start_frame = source_snapshot.start_frame
    report.timeline_end_frame = source_snapshot.end_frame
    sample_rate = source.GetSetting("timelineSampleRate")
    report.timeline_sample_rate = int(sample_rate) if sample_rate else 48000

    audit_names = tuple(dict.fromkeys((target.source_timeline, *protected_timelines)))
    before = _protected_signatures(project, audit_names)
    report.queue.job_ids_before = _job_ids(project)

    media_pool = project.GetMediaPool()
    previous_timeline = project.GetCurrentTimeline()
    report.previous_current_timeline = (
        str(previous_timeline.GetName()) if previous_timeline else None
    )

    scratch: Any = None
    rendered: Path | None = None
    render_directory: Path | None = None
    scratch_name = scratch_timeline_name(now)
    try:
        # First mutation of the run, and therefore the first statement inside the block that
        # guarantees cleanup: saving the restore preset already writes to the user's project.
        _capture_delivery(project, report)

        # Resolve will only render inside its Media Storage, never into a system temp dir.
        render_directory = create_render_directory(report.media_storage_volumes)
        report.render_directory = str(render_directory)

        scratch = source.DuplicateTimeline(scratch_name)
        if scratch is None:
            raise VoiceRenderFailed(f"DuplicateTimeline({scratch_name!r}) returned None")
        report.wrote = True
        report.scratch_name = str(scratch.GetName())
        report.scratch_unique_id = scratch.GetUniqueId()
        if report.scratch_unique_id == source_snapshot.unique_id:
            raise VoiceRenderFailed("the duplicated timeline is not a distinct object")

        if not project.SetCurrentTimeline(scratch):
            raise VoiceRenderFailed("SetCurrentTimeline(scratch) failed")

        # One track and it is the configured voice: the Phase 3 path, byte for byte. Any
        # other set goes through emptying, because deleting a lower-indexed track renumbers
        # and renames the survivors and the proof of what was rendered is lost with it.
        if keep == (target.voice_audio_track,):
            _isolate_voice_track(scratch, keep, report)
        else:
            _silence_audio_tracks(scratch, keep, report)
        produced = _render_audio(
            project,
            render_directory,
            report,
            preset,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            export_video=export_video,
        )
        # Move it out of the user's Media Storage immediately, so cleanup can remove that
        # directory and everything downstream works on our own private copy.
        rendered = temp_directory / produced.name
        shutil.move(str(produced), str(rendered))
        report.audio.rendered_path = str(rendered)
        report.audio.rendered_bytes = rendered.stat().st_size
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        rendered = None
    finally:
        _cleanup(project, media_pool, previous_timeline, scratch, report)
        remove_render_directory(render_directory, report)

    after = _protected_signatures(project, audit_names)
    report.audit_checked = audit_names
    differences: list[str] = []
    for key in sorted(set(before) | set(after)):
        differences.extend(signature_differences(key, before.get(key), after.get(key)))
    report.audit_differences = tuple(differences)
    report.total_seconds = time.perf_counter() - overall_started
    return report, rendered


__all__ = [
    "CONFIRM_FLAG",
    "RESTORE_PRESET_PREFIX",
    "SCRATCH_PREFIX",
    "DeliveryState",
    "RenderQueueState",
    "RenderedAudio",
    "VoiceRenderFailed",
    "VoiceRenderRefused",
    "VoiceRenderReport",
    "render_voice_track",
    "restore_preset_name",
    "scratch_timeline_name",
]
