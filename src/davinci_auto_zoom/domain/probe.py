"""Pure decision logic for the write-capable Phase 2 probe.

Nothing here talks to Resolve. It answers two questions that must be decidable without a
live application, and therefore testable:

* may the probe write at all (fail-closed preflight), and
* did anything change in the timelines that must stay untouched (post-run audit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from davinci_auto_zoom.domain.snapshot import (
    AssetSnapshot,
    ProjectSnapshot,
    TimelineSnapshot,
)


@dataclass(frozen=True, slots=True)
class WriteProbeTarget:
    """Exactly what the probe expects to find before it is allowed to write.

    These are explicit arguments rather than product-wide rules: a general command must not
    inherit the probe's narrow expectations about one specific test project.
    """

    project: str
    source_timeline: str
    reference_timeline: str
    asset_bin: str
    assets: tuple[tuple[str, str], ...]  # (role, media pool clip name)
    expected_clip_type: str = "Generator"
    source_video_track: int = 1

    @property
    def asset_names(self) -> tuple[str, ...]:
        return tuple(name for _, name in self.assets)

    @property
    def protected_timelines(self) -> tuple[str, ...]:
        return (self.source_timeline, self.reference_timeline)


def preflight_failures(
    snapshot: ProjectSnapshot, target: WriteProbeTarget
) -> tuple[str, ...]:
    """Every reason the probe must refuse to write. Empty means "cleared to write".

    Read-only commands may warn and continue; a write-capable command may not. Callers must
    treat a non-empty result as fatal *before* the first mutating call.
    """

    failures: list[str] = []

    if snapshot.project_name != target.project:
        failures.append(
            f"open project is {snapshot.project_name!r}, expected {target.project!r}"
        )

    source = snapshot.timeline(target.source_timeline)
    reference = snapshot.timeline(target.reference_timeline)
    if source is None:
        failures.append(f"source timeline {target.source_timeline!r} not found")
    if reference is None:
        failures.append(f"reference timeline {target.reference_timeline!r} not found")
    if source is not None and reference is not None:
        if source.name == reference.name:
            failures.append("source and reference timelines are the same timeline")
        elif source.unique_id is not None and source.unique_id == reference.unique_id:
            failures.append(
                f"source and reference share unique id {source.unique_id!r}; "
                "they are not distinct objects"
            )

    if len(snapshot.asset_bin_paths) != 1:
        failures.append(
            f"asset bin {target.asset_bin!r} must resolve to exactly one bin, "
            f"found {len(snapshot.asset_bin_paths)}: {list(snapshot.asset_bin_paths)}"
        )

    for role, name in target.assets:
        matches = [asset for asset in snapshot.assets if asset.name == name]
        if len(matches) != 1:
            failures.append(
                f"asset {name!r} (role {role!r}) must be found exactly once, "
                f"found {len(matches)}"
            )
            continue
        asset = matches[0]
        if asset.clip_type != target.expected_clip_type:
            failures.append(
                f"asset {name!r} (role {role!r}) has type {asset.clip_type!r}, "
                f"expected {target.expected_clip_type!r}"
            )
        if not asset.frames:
            failures.append(f"asset {name!r} reports no native frame count")

    if source is not None:
        track = source.track("video", target.source_video_track)
        if track is None:
            failures.append(
                f"{target.source_timeline!r} has no video track "
                f"{target.source_video_track}"
            )
        elif not track.items:
            failures.append(
                f"{target.source_timeline!r} video track "
                f"{target.source_video_track} is empty; the expected structure is missing"
            )

    return tuple(failures)


@dataclass(frozen=True, slots=True)
class VoiceRenderTarget:
    """What the Phase 3 speech probe expects before it may create a temporary render.

    Like `WriteProbeTarget` these are explicit arguments, not ambient config: the probe
    duplicates a timeline, toggles tracks on the duplicate and queues a render job, and none
    of that may happen because a config file happened to be lying around.
    """

    project: str
    source_timeline: str
    voice_audio_track: int
    #: Built-in Resolve render preset used to get an audio-only export. Verified present
    #: during preflight rather than assumed, because presets are installation state.
    render_preset: str = "Audio Only"


def voice_render_preflight_failures(
    snapshot: ProjectSnapshot,
    target: VoiceRenderTarget,
    *,
    render_presets: tuple[str, ...],
    rendering_in_progress: bool,
    media_storage_volumes: tuple[str, ...] = ("",),
) -> tuple[str, ...]:
    """Every reason the speech probe must refuse to touch Resolve. Empty means cleared.

    Fail-closed, exactly like `preflight_failures` (D015): the caller raises before the first
    mutating call rather than warning and continuing.
    """

    failures: list[str] = []

    if snapshot.project_name != target.project:
        failures.append(
            f"open project is {snapshot.project_name!r}, expected {target.project!r}"
        )

    source = snapshot.timeline(target.source_timeline)
    if source is None:
        failures.append(f"source timeline {target.source_timeline!r} not found")
    else:
        if source.end_frame <= source.start_frame:
            failures.append(
                f"{target.source_timeline!r} has an empty frame range "
                f"[{source.start_frame}, {source.end_frame})"
            )
        audio_tracks = source.tracks_of("audio")
        if target.voice_audio_track < 1:
            failures.append(
                f"voice_audio_track must be a 1-based index, got {target.voice_audio_track}"
            )
        elif target.voice_audio_track > len(audio_tracks):
            failures.append(
                f"voice_audio_track {target.voice_audio_track} does not exist: "
                f"{target.source_timeline!r} has {len(audio_tracks)} audio track(s). "
                "Configure [resolve].voice_audio_track (A1 is 1)."
            )
        else:
            voice = source.track("audio", target.voice_audio_track)
            if voice is not None and not voice.items:
                failures.append(
                    f"audio track A{target.voice_audio_track} of "
                    f"{target.source_timeline!r} is empty; it cannot be the voice track"
                )

    if rendering_in_progress:
        failures.append(
            "Resolve is already rendering. The probe would queue a job into a busy queue "
            "and could not tell its own render apart from yours."
        )

    wanted = target.render_preset
    if wanted not in render_presets:
        failures.append(
            f"render preset {wanted!r} is not available in this "
            f"installation; found {len(render_presets)} preset(s)"
        )

    if not any(volume for volume in media_storage_volumes):
        # Resolve refuses to render anywhere outside its Media Storage ("Render Path
        # Inaccessible"), so with no volumes there is nowhere legal to put the audio.
        failures.append(
            "Resolve reports no mounted Media Storage volume, so there is no directory it "
            "will accept as a render target. Add one in Preferences > System > Media Storage."
        )

    return tuple(failures)


@dataclass(frozen=True, slots=True)
class ApplyPreviewTarget:
    """What the Phase 5 executor expects before it may create a preview timeline.

    Same rule as the two probes (D015): the expectations are explicit arguments, never
    ambient config, because this is the first command that leaves something behind.
    """

    project: str
    source_timeline: str
    #: Optional, diagnostics only. Never written to, never used to decide anything.
    reference_timeline: str | None
    voice_audio_track: int
    cut_reference_video_track: int
    zoom_video_track: int
    asset_bin: str
    assets: tuple[tuple[str, str], ...]  # (role, media pool clip name)

    @property
    def asset_names(self) -> tuple[str, ...]:
        return tuple(name for _, name in self.assets)

    @property
    def protected_timelines(self) -> tuple[str, ...]:
        """Timelines that must be byte-for-structure identical before and after the run."""

        names = [self.source_timeline]
        if self.reference_timeline:
            names.append(self.reference_timeline)
        return tuple(dict.fromkeys(names))


def apply_preview_preflight_failures(
    snapshot: ProjectSnapshot, target: ApplyPreviewTarget
) -> tuple[str, ...]:
    """Every reason the executor must refuse. Empty means "cleared to create a preview".

    Fail-closed, and evaluated *before* the source timeline is duplicated, so a refusal costs
    the user nothing at all — not even a leftover preview to delete.
    """

    failures: list[str] = []

    if snapshot.project_name != target.project:
        failures.append(
            f"open project is {snapshot.project_name!r}, expected {target.project!r}"
        )

    source = snapshot.timeline(target.source_timeline)
    if source is None:
        failures.append(f"source timeline {target.source_timeline!r} not found")
    if target.reference_timeline and snapshot.timeline(target.reference_timeline) is None:
        failures.append(
            f"reference timeline {target.reference_timeline!r} not found"
        )
    if target.reference_timeline == target.source_timeline:
        failures.append("source and reference timelines are the same timeline")

    if len(snapshot.asset_bin_paths) != 1:
        failures.append(
            f"asset bin {target.asset_bin!r} must resolve to exactly one bin, "
            f"found {len(snapshot.asset_bin_paths)}: {list(snapshot.asset_bin_paths)}"
        )

    for role, name in target.assets:
        matches = [asset for asset in snapshot.assets if asset.name == name]
        if len(matches) != 1:
            failures.append(
                f"asset {name!r} (role {role!r}) must be found exactly once, "
                f"found {len(matches)}"
            )

    if source is not None:
        if target.zoom_video_track < 1:
            failures.append(
                f"zoom_video_track must be a 1-based index, got {target.zoom_video_track}"
            )
        for label, index, kind in (
            ("voice_audio_track", target.voice_audio_track, "audio"),
            ("cut_reference_video_track", target.cut_reference_video_track, "video"),
        ):
            if source.track(kind, index) is None:
                failures.append(
                    f"{label} {index} does not exist on {target.source_timeline!r} "
                    f"({len(source.tracks_of(kind))} {kind} track(s))"
                )

    return tuple(failures)


def structural_signature(timeline: TimelineSnapshot) -> dict[str, Any]:
    """A comparable structural signature of a timeline.

    Deliberately *not* a byte-for-byte guarantee: the scripting API exposes structure, not
    project bytes. Enable/lock state and "is current" are excluded because they legitimately
    change when the probe switches the current timeline and restores it, and because Resolve
    only reports them truthfully for the current timeline (D009).
    """

    return {
        "name": timeline.name,
        "unique_id": timeline.unique_id,
        "frame_rate": timeline.frame_rate,
        "start_frame": timeline.start_frame,
        "end_frame": timeline.end_frame,
        "start_timecode": timeline.start_timecode,
        "track_count": len(timeline.tracks),
        "tracks": [
            {
                "track_type": track.track_type,
                "index": track.index,
                "name": track.name,
                "sub_type": track.sub_type,
                "item_count": len(track.items),
                "items": [
                    {
                        "name": item.name,
                        "unique_id": item.unique_id,
                        "start": item.start,
                        "end": item.end,
                        "duration": item.duration,
                        "fusion_comp_count": item.fusion_comp_count,
                    }
                    for item in track.items
                ],
            }
            for track in timeline.tracks
        ],
    }


def asset_signature(assets: tuple[AssetSnapshot, ...]) -> list[dict[str, Any]]:
    return [
        {
            "name": asset.name,
            "bin_path": asset.bin_path,
            "clip_type": asset.clip_type,
            "frames": asset.frames,
            "media_id": asset.media_id,
            "unique_id": asset.unique_id,
        }
        for asset in sorted(assets, key=lambda asset: (asset.bin_path, asset.name))
    ]


def signature_differences(
    label: str, before: Any, after: Any, path: str = ""
) -> tuple[str, ...]:
    """Human-readable differences between two signatures, recursing into dicts/lists."""

    where = f"{label}{path}"
    if type(before) is not type(after):
        return (f"{where}: type {type(before).__name__} -> {type(after).__name__}",)
    if isinstance(before, dict):
        differences: list[str] = []
        for key in sorted(set(before) | set(after)):
            if key not in before:
                differences.append(f"{where}.{key}: added")
            elif key not in after:
                differences.append(f"{where}.{key}: removed")
            else:
                differences.extend(
                    signature_differences(label, before[key], after[key], f"{path}.{key}")
                )
        return tuple(differences)
    if isinstance(before, list):
        if len(before) != len(after):
            return (f"{where}: {len(before)} entries -> {len(after)} entries",)
        differences = []
        for index, (left, right) in enumerate(zip(before, after, strict=True)):
            differences.extend(
                signature_differences(label, left, right, f"{path}[{index}]")
            )
        return tuple(differences)
    if before != after:
        return (f"{where}: {before!r} -> {after!r}",)
    return ()


__all__ = [
    "ApplyPreviewTarget",
    "VoiceRenderTarget",
    "WriteProbeTarget",
    "apply_preview_preflight_failures",
    "asset_signature",
    "preflight_failures",
    "signature_differences",
    "structural_signature",
    "voice_render_preflight_failures",
]
