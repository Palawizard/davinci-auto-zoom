from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.compare import added_generator_items, compare_timelines
from davinci_auto_zoom.domain.probe import VoiceRenderTarget, WriteProbeTarget
from davinci_auto_zoom.domain.snapshot import ProjectSnapshot
from davinci_auto_zoom.domain.speech_report import (
    compare_to_reference_zooms,
    reference_zooms,
)
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.resolve.capability_probe import probe_resolve
from davinci_auto_zoom.resolve.session import (
    ResolveUnavailableError,
    connect,
    current_project,
    snapshot_project,
)
from davinci_auto_zoom.resolve.voice_render import (
    CONFIRM_FLAG as VOICE_CONFIRM_FLAG,
)
from davinci_auto_zoom.resolve.voice_render import (
    VoiceRenderRefused,
    render_voice_track,
)
from davinci_auto_zoom.resolve.write_probe import (
    CONFIRM_FLAG,
    WriteProbeRefused,
    run_write_probe,
)
from davinci_auto_zoom.speech.audio import FfmpegError
from davinci_auto_zoom.speech.pipeline import SpeechResult, analyze_audio_file
from davinci_auto_zoom.speech.silero import SpeechEngineError

EXIT_OK = 0
EXIT_RESOLVE_UNAVAILABLE = 2
EXIT_BAD_REQUEST = 3
EXIT_PROBE_FAILED = 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="davinci-auto-zoom",
        description="Speech-driven zoom automation for DaVinci Resolve. "
        "Every command in this phase is strictly read-only.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON.")
        sub.add_argument("--config", type=Path, default=None, help="Path to config.toml.")
        return sub

    add("doctor", "Environment, connectivity and documented-vs-runtime capability matrix.")
    add("snapshot", "Read-only normalized snapshot of the open project and its timelines.")
    add("assets", "Read-only discovery of the configured asset bin and zoom assets.")

    compare = add("compare", "Read-only structural diff between two timelines.")
    compare.add_argument("input_timeline", help="Timeline without the automated zooms.")
    compare.add_argument("reference_timeline", help="Human reference timeline.")

    probe = add(
        "probe-write",
        "DEVELOPMENT ONLY. Phase 2 spike: WRITES to a scratch timeline it creates and "
        "deletes. Requires an explicit confirmation flag and exact expected names.",
    )
    probe.add_argument(
        CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required. Without it the probe refuses to run and nothing is modified.",
    )
    # Required and explicit on purpose: a write-capable command must never inherit a
    # partially-filled config and guess what it is allowed to touch.
    probe.add_argument("--project", required=True, help="Exact expected project name.")
    probe.add_argument(
        "--source-timeline", required=True, help="Timeline to duplicate. Never modified."
    )
    probe.add_argument(
        "--reference-timeline",
        required=True,
        help="Timeline holding correct manual instances. Read-only reference.",
    )

    speech_file = add(
        "speech-file",
        "Run the speech detector on a local audio file. No DaVinci Resolve involved.",
    )
    speech_file.add_argument("audio", type=Path, help="Audio file to analyse.")
    speech_file.add_argument(
        "--fps",
        default="60",
        help="Timeline frame rate the segments should be expressed in (default 60). "
        "NTSC decimals such as 29.97 are resolved to their exact 30000/1001 value.",
    )
    speech_file.add_argument(
        "--start-frame",
        type=int,
        default=0,
        help="Absolute timeline frame that sample 0 of the file corresponds to (default 0).",
    )
    speech_file.add_argument(
        "--keep-temp-audio",
        action="store_true",
        help="Keep the normalized 16 kHz WAV and print where it is.",
    )

    speech_probe = add(
        "speech-probe",
        "DEVELOPMENT ONLY. Phase 3 spike: renders the configured voice track through a "
        "scratch timeline it creates and deletes, then reports speech segments. Requires "
        "an explicit confirmation flag.",
    )
    speech_probe.add_argument(
        VOICE_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required. Without it the probe refuses to run and nothing is modified.",
    )
    # Same rule as probe-write (D015): a mutating command states its expectations rather
    # than inheriting whatever config happens to be lying around.
    speech_probe.add_argument("--project", required=True, help="Exact expected project name.")
    speech_probe.add_argument(
        "--source-timeline",
        required=True,
        help="Timeline whose voice track to analyse. Never modified; a duplicate is used.",
    )
    speech_probe.add_argument(
        "--reference-timeline",
        default=None,
        help="Optional human-edited timeline. Produces qualitative editing-reference "
        "diagnostics only — it is NOT ground truth for speech.",
    )
    speech_probe.add_argument(
        "--keep-temp-audio",
        action="store_true",
        help="Keep the rendered and normalized audio and print exactly where they are.",
    )
    return parser


def _emit(payload: dict[str, Any], text: str, as_json: bool) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True) if as_json else text)


def _snapshot(config: Config) -> ProjectSnapshot:
    resolve = connect()
    project = current_project(resolve)
    return snapshot_project(resolve, project, config)


def _snapshot_text(snapshot: ProjectSnapshot) -> str:
    lines = [
        f"{snapshot.product_name} {snapshot.resolve_version}",
        f"project  : {snapshot.project_name} @ {snapshot.frame_rate} fps",
        f"current  : {snapshot.current_timeline or '-'}",
    ]
    for timeline in snapshot.timelines:
        lines.append(
            f"\ntimeline {timeline.name} "
            f"[{timeline.start_frame}, {timeline.end_frame}) tc={timeline.start_timecode}"
            f"{' (current)' if timeline.is_current else ''}"
        )
        for track in timeline.tracks:
            enabled = "?" if track.enabled is None else ("on" if track.enabled else "off")
            lines.append(
                f"  {track.track_type[0].upper()}{track.index} {track.name!r:14} "
                f"{track.sub_type or '-':8} enabled={enabled:3} items={len(track.items)}"
            )
        cuts = timeline.edit_boundaries()
        lines.append(f"  edit boundaries on V1: {len(cuts)}")
    return "\n".join(lines) + _assets_text(snapshot)


def _assets_text(snapshot: ProjectSnapshot) -> str:
    found = "found" if snapshot.asset_bin_found else "MISSING"
    lines = [f"\nasset bin {snapshot.asset_bin!r}: {found}"]
    for asset in snapshot.assets:
        role = asset.role or "-"
        lines.append(
            f"  {asset.name!r:18} role={role:12} type={asset.clip_type!r:12} "
            f"frames={asset.frames} id={asset.media_id}"
        )
    for warning in snapshot.warnings:
        lines.append(f"  ! {warning}")
    return "\n".join(lines)


def _temp_directory(stack: ExitStack, keep: bool) -> Path:
    """A private working directory, removed on exit unless the user asked to keep it.

    Nothing is ever written inside the repository or the user's project folder.
    """

    if keep:
        return Path(tempfile.mkdtemp(prefix="daz-speech-"))
    return Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="daz-speech-")))


def _speech_file(args: Any) -> int:
    if not args.audio.is_file():
        print(f"error: audio file not found: {args.audio}")
        return EXIT_BAD_REQUEST
    config = args.config_object
    with ExitStack() as stack:
        directory = _temp_directory(stack, args.keep_temp_audio)
        try:
            timebase = Timebase.from_timeline(args.fps, args.start_frame)
            result = analyze_audio_file(
                args.audio, directory / "voice_16k.wav", timebase, config.vad
            )
        except (FfmpegError, SpeechEngineError, ValueError) as exc:
            print(f"error: {exc}")
            return EXIT_BAD_REQUEST
        text = result.to_text()
        if args.keep_temp_audio:
            text += f"\n  TEMP AUDIO KEPT AT: {directory}"
        _emit(result.to_dict(), text, args.as_json)
    return EXIT_OK


def _speech_probe(args: Any, config: Config) -> int:
    try:
        resolve = connect()
        project = current_project(resolve)
    except ResolveUnavailableError as exc:
        print(f"error: {exc}")
        return EXIT_RESOLVE_UNAVAILABLE

    target = VoiceRenderTarget(
        project=args.project,
        source_timeline=args.source_timeline,
        voice_audio_track=config.voice_audio_track,
    )
    protected = (args.reference_timeline,) if args.reference_timeline else ()

    with ExitStack() as stack:
        directory = _temp_directory(stack, args.keep_temp_audio)
        try:
            report, rendered = render_voice_track(
                resolve,
                project,
                config,
                target,
                directory,
                confirmed=args.confirmed,
                protected_timelines=protected,
            )
        except VoiceRenderRefused as exc:
            print(f"refused: {exc}")
            return EXIT_BAD_REQUEST

        report.temp_files_kept = args.keep_temp_audio
        result: SpeechResult | None = None
        if rendered is not None:
            try:
                result = _analyze_rendered(
                    args, config, resolve, project, report, rendered, directory
                )
            except (FfmpegError, SpeechEngineError, ValueError) as exc:
                report.error = f"{type(exc).__name__}: {exc}"

        payload = report.to_dict()
        text = report.to_text()
        if result is not None:
            payload["speech"] = result.to_dict()
            text += "\n\n" + result.to_text()
        _emit(payload, text, args.as_json)
        succeeded = report.succeeded and result is not None
    return EXIT_OK if succeeded else EXIT_PROBE_FAILED


def _analyze_rendered(
    args: Any,
    config: Config,
    resolve: Any,
    project: Any,
    report: Any,
    rendered: Path,
    directory: Path,
) -> SpeechResult:
    """Normalize + analyse the rendered file, and fold the numbers back into the report."""

    timebase = Timebase.from_timeline(
        report.timeline_frame_rate, report.timeline_start_frame or 0
    )
    expected_frames = (report.timeline_end_frame or 0) - (report.timeline_start_frame or 0)
    result = analyze_audio_file(
        rendered,
        directory / "voice_16k.wav",
        timebase,
        config.vad,
        expected_frames=expected_frames,
    )
    audio, check = result.audio, result.duration
    report.audio.normalized_path = str(audio.path)
    report.audio.sample_rate = audio.sample_rate
    report.audio.channels = 1
    report.audio.sample_count = audio.sample_count
    report.audio.duration_seconds = audio.duration_seconds
    report.ffmpeg_seconds = result.ffmpeg_seconds
    if check is not None:
        report.audio.expected_frames = check.expected_frames
        report.audio.expected_seconds = check.expected_seconds
        report.audio.delta_seconds = check.delta_seconds
        report.audio.delta_frames = check.delta_frames
        report.audio.tolerance_frames = check.tolerance_frames
        report.audio.duration_ok = check.ok

    if not args.reference_timeline:
        return result
    # Read-only, and explicitly a diagnostic: the human edit is taste, not ground truth.
    snapshot = snapshot_project(resolve, project, config)
    reference = snapshot.timeline(args.reference_timeline)
    if reference is None:
        report.notes.append(
            f"reference timeline {args.reference_timeline!r} not found; "
            "editing-reference diagnostics skipped"
        )
        return result
    return replace(
        result,
        reference=compare_to_reference_zooms(
            result.segments,
            reference_zooms(reference, config.assets.get("facecam_x1", "")),
            reference_zooms(reference, config.assets.get("reset_x0", "")),
            args.reference_timeline,
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        config = Config.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return EXIT_BAD_REQUEST

    if args.command == "speech-file":
        args.config_object = config
        return _speech_file(args)

    if args.command == "speech-probe":
        return _speech_probe(args, config)

    if args.command == "probe-write":
        try:
            resolve = connect()
            project = current_project(resolve)
        except ResolveUnavailableError as exc:
            print(f"error: {exc}")
            return EXIT_RESOLVE_UNAVAILABLE
        target = WriteProbeTarget(
            project=args.project,
            source_timeline=args.source_timeline,
            reference_timeline=args.reference_timeline,
            asset_bin=config.asset_bin,
            assets=tuple(sorted(config.assets.items())),
        )
        try:
            probe = run_write_probe(
                resolve, project, config, target, confirmed=args.confirmed
            )
        except WriteProbeRefused as exc:
            print(f"refused: {exc}")
            return EXIT_BAD_REQUEST
        _emit(probe.to_dict(), probe.to_text(), args.as_json)
        return EXIT_OK if probe.succeeded else EXIT_PROBE_FAILED

    if args.command == "doctor":
        report = probe_resolve()
        _emit(report.to_dict(), report.to_text(), args.as_json)
        return EXIT_OK if report.connected else EXIT_RESOLVE_UNAVAILABLE

    try:
        snapshot = _snapshot(config)
    except ResolveUnavailableError as exc:
        print(f"error: {exc}")
        return EXIT_RESOLVE_UNAVAILABLE

    if args.command == "snapshot":
        _emit(snapshot.to_dict(), _snapshot_text(snapshot), args.as_json)
        return EXIT_OK

    if args.command == "assets":
        payload = {
            "asset_bin": snapshot.asset_bin,
            "asset_bin_found": snapshot.asset_bin_found,
            "assets": [asdict(a) for a in snapshot.assets],
            "warnings": list(snapshot.warnings),
        }
        _emit(payload, _assets_text(snapshot).lstrip(), args.as_json)
        return EXIT_OK

    if args.command == "compare":
        left = snapshot.timeline(args.input_timeline)
        right = snapshot.timeline(args.reference_timeline)
        missing = [
            name
            for name, value in ((args.input_timeline, left), (args.reference_timeline, right))
            if value is None
        ]
        if left is None or right is None:
            available = ", ".join(t.name for t in snapshot.timelines)
            print(f"error: timeline(s) not found: {', '.join(missing)}. Available: {available}")
            return EXIT_BAD_REQUEST

        comparison = compare_timelines(left, right)
        added = added_generator_items(comparison)
        text = [
            f"{comparison.input_timeline} -> {comparison.reference_timeline}",
            f"shared tracks        : {', '.join(comparison.shared_tracks) or '-'}",
            f"reference-only tracks: {', '.join(comparison.reference_only_tracks) or '-'}",
            f"input-only tracks    : {', '.join(comparison.input_only_tracks) or '-'}",
            f"cuts on V1           : {comparison.cut_count}",
            f"added video items    : {len(added)} "
            f"(start on a cut: {comparison.starts_on_cut}, end on a cut: {comparison.ends_on_cut})",
            "",
        ]
        # Offsets are None when the source track has no cut at all, so they cannot be
        # formatted with a numeric spec.
        def offset(value: int | None) -> str:
            return "    ?" if value is None else f"{value:+5}"

        for item in added:
            text.append(
                f"  {item.name:16} V{item.track_index} [{item.start},{item.end}) "
                f"dur={item.duration:4} startΔcut={offset(item.start_offset_to_nearest_cut)} "
                f"endΔcut={offset(item.end_offset_to_nearest_cut)} gap={item.gap_from_previous}"
            )
        _emit(comparison.to_dict(), "\n".join(text), args.as_json)
        return EXIT_OK

    raise AssertionError(f"Unhandled command: {args.command}")
