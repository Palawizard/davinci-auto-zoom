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
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.plan_validation import build_plan_source, timeline_frame_rate
from davinci_auto_zoom.domain.planner import ZoomPlan, plan_zooms
from davinci_auto_zoom.domain.probe import (
    ApplyPreviewTarget,
    VoiceRenderTarget,
    WriteProbeTarget,
)
from davinci_auto_zoom.domain.snapshot import ProjectSnapshot
from davinci_auto_zoom.domain.speech_report import (
    PlanReferenceDiagnostics,
    ReferenceZoom,
    compare_plan_to_reference,
    compare_to_reference_zooms,
    merge_adjacent,
    reference_zooms,
)
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.transitions import ForbiddenTransition
from davinci_auto_zoom.resolve.capability_probe import probe_resolve
from davinci_auto_zoom.resolve.executor import (
    CONFIRM_FLAG as APPLY_CONFIRM_FLAG,
)
from davinci_auto_zoom.resolve.executor import (
    ApplyPreviewRefused,
    ApplyReport,
    apply_preview,
)
from davinci_auto_zoom.resolve.gameplay_study import (
    GameplayStudyRefused,
    run_gameplay_study,
)
from davinci_auto_zoom.resolve.owned_preview import (
    CLEAN,
    CLEAN_CONFIRM_FLAG,
    REBUILD,
    REBUILD_CONFIRM_FLAG,
    OwnedPreviewRefused,
    OwnedPreviewReport,
    run_owned_preview,
)
from davinci_auto_zoom.resolve.ownership_probe import (
    CONFIRM_FLAG as OWNERSHIP_CONFIRM_FLAG,
)
from davinci_auto_zoom.resolve.ownership_probe import (
    OwnershipProbeRefused,
    run_ownership_probe,
)
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
        "No command modifies an existing timeline of yours. The everyday commands "
        "(doctor, snapshot, assets, compare, speech-file) are strictly read-only. The "
        "development probes (probe-write, speech-probe, plan-probe) make temporary, "
        "opt-in changes on a scratch timeline they create and delete, restoring every "
        "piece of project state they touched. apply-preview is the one write-capable "
        "command that leaves something behind on purpose: it places the planned zooms on "
        "a NEW DAZ_AUTO_PREVIEW_* timeline duplicated from your source, keeps it on "
        "success for you to inspect, and deletes it again if anything fails.",
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

    plan_probe = add(
        "plan-probe",
        "Phase 4 dry run: voice render -> speech -> hard cuts -> deterministic zoom plan. "
        "Reports the plan and NEVER inserts a zoom clip. Uses the same temporary, opt-in "
        "render as speech-probe, so it requires the same confirmation flag.",
    )
    plan_probe.add_argument(
        VOICE_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required. Covers the temporary voice render only; no zoom is ever placed.",
    )
    plan_probe.add_argument("--project", required=True, help="Exact expected project name.")
    plan_probe.add_argument(
        "--source-timeline",
        required=True,
        help="Timeline to plan against. Never modified; a duplicate is rendered.",
    )
    plan_probe.add_argument(
        "--reference-timeline",
        default=None,
        help="Optional human-edited timeline. Produces a qualitative comparison only — the "
        "planner is never tuned to reproduce it.",
    )
    plan_probe.add_argument(
        "--keep-temp-audio",
        action="store_true",
        help="Keep the rendered and normalized audio and print exactly where they are.",
    )

    gameplay_study = add(
        "gameplay-study",
        "DEVELOPMENT DIAGNOSTIC. Phase 9a: measures the creator's silences, the secondary "
        "audio and the picture's motion, reads the GAMEPLAY decisions out of a human "
        "reference edit, and compares the two. Renders three times through the same "
        "temporary, opt-in scratch pipeline as plan-probe, so it needs the same "
        "confirmation flag. It NEVER places a gameplay clip and never creates a preview.",
    )
    gameplay_study.add_argument(
        VOICE_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required. Covers the temporary renders only; nothing is ever placed.",
    )
    gameplay_study.add_argument("--project", required=True, help="Exact expected project name.")
    gameplay_study.add_argument(
        "--source-timeline",
        required=True,
        help="Timeline to measure. Never modified; duplicates are rendered.",
    )
    gameplay_study.add_argument(
        "--reference-timeline",
        required=True,
        help="REQUIRED here: the human edit whose GAMEPLAY decisions are the study's "
        "subject. Read-only, and never written to.",
    )
    gameplay_study.add_argument(
        "--keep-temp-audio",
        action="store_true",
        help="Keep the rendered and normalized media and print exactly where they are.",
    )

    apply_preview = add(
        "apply-preview",
        "WRITE-CAPABLE. Phase 5: runs the full pipeline and places the planned zooms on a "
        "NEW timeline duplicated from the source. Your timelines are never modified, and on "
        "success the preview is deliberately KEPT for you to inspect.",
    )
    apply_preview.add_argument(
        APPLY_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed_apply",
        help="Required. Without it nothing is created and nothing is modified.",
    )
    apply_preview.add_argument(
        VOICE_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required as well: the pipeline needs the same temporary voice render as "
        "plan-probe.",
    )
    # Same rule as every other write-capable command (D015).
    apply_preview.add_argument("--project", required=True, help="Exact expected project name.")
    apply_preview.add_argument(
        "--source-timeline",
        required=True,
        help="Timeline to plan against and duplicate. Never modified.",
    )
    apply_preview.add_argument(
        "--reference-timeline",
        default=None,
        help="Optional human-edited timeline. Diagnostics only; never written to.",
    )
    apply_preview.add_argument(
        "--keep-temp-audio",
        action="store_true",
        help="Keep the rendered and normalized audio and print exactly where they are.",
    )

    ownership_probe = add(
        "probe-ownership",
        "DEVELOPMENT ONLY. Phase 7 spike: proves that TimelineItem markers work as "
        "persistent DAZ ownership on real generator clips. WRITES to scratch timelines it "
        "creates and deletes, restoring every piece of project state it touched.",
    )
    ownership_probe.add_argument(
        OWNERSHIP_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed_ownership",
        help="Required. Without it the probe refuses to run and nothing is modified.",
    )
    ownership_probe.add_argument("--project", required=True, help="Exact expected project name.")
    ownership_probe.add_argument(
        "--source-timeline", required=True, help="Timeline to duplicate. Never modified."
    )
    ownership_probe.add_argument(
        "--reference-timeline",
        default=None,
        help="Optional. Audited for changes alongside the source; never written to.",
    )

    clean = add(
        "clean-preview",
        "DESTRUCTIVE. Phase 7: removes from ONE named DAZ_AUTO_PREVIEW_* timeline exactly "
        "the clips DAZ can prove it created, and nothing else. Foreign clips are left "
        "untouched and reported. Refuses protected timelines, legacy previews with no "
        "ownership metadata, and any track whose ownership is not fully classifiable. A "
        "DAZ_RECOVERY_* copy is made before the first deletion.",
    )
    clean.add_argument(
        CLEAN_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed_clean",
        help="Required. Without it nothing is deleted and nothing is created.",
    )
    clean.add_argument("--project", required=True, help="Exact expected project name.")
    clean.add_argument(
        "--source-timeline",
        required=True,
        help="The timeline the preview was built from. Protected: never written to.",
    )
    clean.add_argument(
        "--reference-timeline",
        default=None,
        help="Optional human-edited timeline. Protected: never written to.",
    )
    clean.add_argument(
        "--preview-timeline",
        required=True,
        help="Exact name of the DAZ_AUTO_PREVIEW_* timeline to clean. No wildcards, no "
        "'latest': the one timeline you name is the only one that can be touched.",
    )

    rebuild = add(
        "rebuild-preview",
        "DESTRUCTIVE. Phase 7: recomputes the plan from the source, removes the DAZ-owned "
        "clips from ONE named DAZ_AUTO_PREVIEW_* timeline and re-applies the fresh plan onto "
        "it. Repeating it produces the same structural result with no duplicates. Refuses if "
        "the target track holds anything DAZ did not create.",
    )
    rebuild.add_argument(
        REBUILD_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed_rebuild",
        help="Required. Without it nothing is deleted and nothing is created.",
    )
    rebuild.add_argument(
        VOICE_CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required as well: recomputing the plan needs the same temporary voice render "
        "as plan-probe.",
    )
    rebuild.add_argument("--project", required=True, help="Exact expected project name.")
    rebuild.add_argument(
        "--source-timeline",
        required=True,
        help="Timeline to re-plan against. Protected: never written to.",
    )
    rebuild.add_argument(
        "--reference-timeline",
        default=None,
        help="Optional human-edited timeline. Diagnostics only; never written to.",
    )
    rebuild.add_argument(
        "--preview-timeline",
        required=True,
        help="Exact name of the DAZ_AUTO_PREVIEW_* timeline to rebuild.",
    )
    rebuild.add_argument(
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
                args.audio,
                directory / "voice_16k.wav",
                timebase,
                config.vad,
                energy_settings=config.energy,
            )
        except (FfmpegError, SpeechEngineError, ValueError) as exc:
            print(f"error: {exc}")
            return EXIT_BAD_REQUEST
        text = result.to_text()
        if args.keep_temp_audio:
            text += f"\n  TEMP AUDIO KEPT AT: {directory}"
        _emit(result.to_dict(), text, args.as_json)
    return EXIT_OK


def _planned_zooms(plan: ZoomPlan) -> list[ReferenceZoom]:
    """The plan's zoom-in placements in the same shape as a reference timeline's clips."""

    return [
        ReferenceZoom(p.asset_role, p.start_frame, p.end_frame) for p in plan.zoom_placements
    ]


def _build_plan(
    args: Any, config: Config, resolve: Any, project: Any, report: Any, result: SpeechResult
) -> tuple[ZoomPlan, PlanReferenceDiagnostics | None]:
    """Pure planning on top of the probe's results, plus an optional qualitative comparison.

    The only Resolve work here is one read-only snapshot: the planner itself never sees a
    Resolve object, and this function places nothing on any timeline.
    """

    assert config.asset_timing is not None  # checked before the render
    snapshot = snapshot_project(resolve, project, config)
    timeline = snapshot.timeline(args.source_timeline)
    if timeline is None:  # pragma: no cover - the render already proved it exists
        raise ValueError(f"timeline {args.source_timeline!r} disappeared during the run")

    timeline_range = FrameRange(timeline.start_frame, timeline.end_frame)
    # Built through the same constructor the executor validates against, fingerprint
    # included, so a later mismatch can only mean Resolve changed (D030).
    source = build_plan_source(
        project=snapshot.project_name,
        timeline=timeline,
        frame_rate=timeline_frame_rate(timeline),
        voice_audio_track=config.voice_audio_track,
        cut_reference_video_track=config.cut_reference_video_track,
        zoom_video_track=config.zoom_video_track,
        assets=config.assets,
        asset_transition_frames=config.asset_timing.to_dict(),
        planner_settings=config.planner,
        energy_settings=config.energy,
        found_assets=snapshot.assets,
    )
    plan = plan_zooms(
        timeline=timeline_range,
        frame_rate=result.timebase.frame_rate,
        speech_segments=result.segments,
        timing=config.asset_timing,
        hard_cuts=timeline.hard_cuts(config.cut_reference_video_track),
        settings=config.planner,
        source=source,
        energy=result.energy,
    )

    if not args.reference_timeline:
        return plan, None
    reference = snapshot.timeline(args.reference_timeline)
    if reference is None:
        report.notes.append(
            f"reference timeline {args.reference_timeline!r} not found; plan comparison skipped"
        )
        return plan, None
    # Cycles, not clips, on both sides: a promoted burst is several adjacent transition clips
    # and one zoom as far as a viewer (and a comparison) is concerned.
    comparison = compare_plan_to_reference(
        [(z.start, z.end) for z in merge_adjacent(_planned_zooms(plan))],
        [(p.start_frame, p.end_frame) for p in plan.reset_placements],
        merge_adjacent(reference_zooms(reference, *config.asset_names(reset=False))),
        reference_zooms(reference, *config.asset_names(reset=True)),
        args.reference_timeline,
    )
    return plan, comparison


def _plan_reference_text(diagnostics: PlanReferenceDiagnostics) -> str:
    return "\n".join(
        [
            "",
            f"  plan vs {diagnostics.reference_timeline} "
            "(qualitative only — the planner is NOT tuned to reproduce a human edit):",
            f"    planned x1 / manual x1     : {diagnostics.planned_x1} / "
            f"{diagnostics.manual_x1}",
            f"    planned x0 / manual x0     : {diagnostics.planned_x0} / "
            f"{diagnostics.manual_x0}",
            f"    planned x1 matching manual : {diagnostics.matched_x1}",
            f"    planned with no manual zoom: {diagnostics.planned_x1_without_manual}",
            f"    manual with no planned zoom: {diagnostics.manual_x1_without_planned}",
            f"    start offset (planned-manual): min/med/max = "
            f"{diagnostics.start_offset_frames['min']}/"
            f"{diagnostics.start_offset_frames['median']}/"
            f"{diagnostics.start_offset_frames['max']} frames",
            f"    duration ratio             : min/med/max = "
            f"{diagnostics.duration_ratio_percent['min']}/"
            f"{diagnostics.duration_ratio_percent['median']}/"
            f"{diagnostics.duration_ratio_percent['max']} %",
            f"    reset offset (planned-manual): min/med/max = "
            f"{diagnostics.reset_offset_frames['min']}/"
            f"{diagnostics.reset_offset_frames['median']}/"
            f"{diagnostics.reset_offset_frames['max']} frames",
        ]
    )


def _apply_target(args: Any, config: Config) -> ApplyPreviewTarget:
    """The explicit expectations every write-capable command states rather than infers (D015)."""

    return ApplyPreviewTarget(
        project=args.project,
        source_timeline=args.source_timeline,
        reference_timeline=getattr(args, "reference_timeline", None),
        voice_audio_track=config.voice_audio_track,
        cut_reference_video_track=config.cut_reference_video_track,
        zoom_video_track=config.zoom_video_track,
        asset_bin=config.asset_bin,
        assets=tuple(sorted(config.assets.items())),
    )


def _apply_preview(
    args: Any, config: Config, resolve: Any, project: Any, zoom_plan: ZoomPlan
) -> tuple[dict[str, Any], str, bool]:
    """Hand the finished plan to the executor. Returns (payload, text, succeeded).

    A refusal is a normal, reportable outcome — the guards are the point of the command — so
    it is turned into text here instead of a traceback. Nothing was created when it happens.
    """

    target = _apply_target(args, config)
    try:
        report: ApplyReport = apply_preview(
            resolve, project, config, target, zoom_plan, confirmed=args.confirmed_apply
        )
    except ApplyPreviewRefused as exc:
        return {"refused": str(exc)}, f"refused: {exc}", False
    return report.to_dict(), report.to_text(), report.succeeded


def _owned_preview(
    args: Any,
    config: Config,
    resolve: Any,
    project: Any,
    *,
    operation: str,
    confirmed: bool,
    zoom_plan: ZoomPlan | None = None,
) -> tuple[dict[str, Any], str, bool]:
    """Hand a named preview to `clean-preview` / `rebuild-preview`. Returns (payload, text, ok).

    A refusal is a normal, reportable outcome — the guards are the whole point of these two
    commands — so it becomes text here rather than a traceback. Nothing was deleted when it
    happens.
    """

    try:
        report: OwnedPreviewReport = run_owned_preview(
            resolve,
            project,
            config,
            _apply_target(args, config),
            args.preview_timeline,
            operation=operation,
            confirmed=confirmed,
            plan=zoom_plan,
        )
    except OwnedPreviewRefused as exc:
        return {"refused": str(exc)}, f"refused: {exc}", False
    return report.to_dict(), report.to_text(), report.succeeded


def _gameplay_study(
    args: Any,
    config: Config,
    resolve: Any,
    project: Any,
    directory: Path,
    zoom_plan: ZoomPlan,
    speech_segments: int,
) -> tuple[dict[str, Any], str, bool]:
    """Run the Phase 9a diagnostic on top of the plan the pipeline just produced.

    The bursts and hard cuts come from the plan rather than being recomputed, so the study
    and the facecam planner can never disagree about where the creator was talking.

    A refusal is a normal, reportable outcome — the study needs a reference edit and a clean
    render, and saying so is more useful than a traceback. Nothing is placed either way.
    """

    try:
        study = run_gameplay_study(
            resolve,
            project,
            config,
            VoiceRenderTarget(
                project=args.project,
                source_timeline=args.source_timeline,
                voice_audio_track=config.voice_audio_track,
            ),
            directory,
            reference_timeline=args.reference_timeline,
            bursts=zoom_plan.bursts,
            speech_segments=speech_segments,
            timeline=zoom_plan.timeline,
            frame_rate=zoom_plan.frame_rate,
            timeline_frame_rate=str(zoom_plan.frame_rate),
            confirmed=args.confirmed,
        )
    except (GameplayStudyRefused, ForbiddenTransition) as exc:
        return {"refused": str(exc)}, f"refused: {exc}", False
    return study.to_dict(), study.to_text(), study.succeeded


def _speech_probe(
    args: Any,
    config: Config,
    *,
    plan: bool = False,
    apply_plan: bool = False,
    rebuild: bool = False,
    gameplay: bool = False,
) -> int:
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
        succeeded = report.succeeded and result is not None
        if result is not None:
            payload["speech"] = result.to_dict()
            text += "\n\n" + result.to_text()
            if plan:
                zoom_plan, comparison = _build_plan(
                    args, config, resolve, project, report, result
                )
                payload["plan"] = zoom_plan.to_dict()
                text += "\n\n" + zoom_plan.to_text()
                if comparison is not None:
                    payload["plan_reference"] = comparison.to_dict()
                    text += "\n" + _plan_reference_text(comparison)
                # A plan with overlapping placements is a bug, not a result.
                succeeded = succeeded and zoom_plan.valid
                if gameplay and succeeded:
                    study_payload, study_text, studied = _gameplay_study(
                        args, config, resolve, project, directory, zoom_plan,
                        len(result.segments),
                    )
                    payload["gameplay_study"] = study_payload
                    text += "\n\n" + study_text
                    succeeded = studied
                elif apply_plan and succeeded:
                    apply_payload, apply_text, applied = _apply_preview(
                        args, config, resolve, project, zoom_plan
                    )
                    payload["apply"] = apply_payload
                    text += "\n\n" + apply_text
                    succeeded = applied
                elif rebuild and succeeded:
                    rebuild_payload, rebuild_text, rebuilt = _owned_preview(
                        args,
                        config,
                        resolve,
                        project,
                        operation=REBUILD,
                        confirmed=args.confirmed_rebuild,
                        zoom_plan=zoom_plan,
                    )
                    payload["rebuild"] = rebuild_payload
                    text += "\n\n" + rebuild_text
                    succeeded = rebuilt
                elif apply_plan or rebuild or gameplay:
                    text += (
                        "\n\nrefused: the pipeline did not produce a usable plan, so nothing "
                        "was created and nothing was deleted"
                    )
        _emit(payload, text, args.as_json)
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
        energy_settings=config.energy,
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
            merge_adjacent(reference_zooms(reference, *config.asset_names(reset=False))),
            reference_zooms(reference, *config.asset_names(reset=True)),
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

    if args.command == "clean-preview":
        try:
            resolve = connect()
            project = current_project(resolve)
        except ResolveUnavailableError as exc:
            print(f"error: {exc}")
            return EXIT_RESOLVE_UNAVAILABLE
        if not args.confirmed_clean:
            # Checked before Resolve is touched further: the flag is the whole opt-in.
            print(
                f"refused: clean-preview deletes clips from an existing timeline and "
                f"requires {CLEAN_CONFIRM_FLAG}. Nothing was modified."
            )
            return EXIT_BAD_REQUEST
        clean_payload, clean_text, cleaned = _owned_preview(
            args, config, resolve, project, operation=CLEAN, confirmed=True
        )
        _emit(clean_payload, clean_text, args.as_json)
        return EXIT_OK if cleaned else EXIT_PROBE_FAILED

    if args.command in ("plan-probe", "apply-preview", "rebuild-preview", "gameplay-study"):
        if config.asset_timing is None:
            print(
                "error: the planner needs to know how long your zoom assets take to animate. "
                "Add [assets.transition_frames] to your config (see config.example.toml): "
                "these are frame counts for YOUR assets, and the tool will not guess them."
            )
            return EXIT_BAD_REQUEST
        if args.command == "plan-probe":
            return _speech_probe(args, config, plan=True)
        if args.command == "gameplay-study":
            return _speech_probe(args, config, plan=True, gameplay=True)
        if args.command == "rebuild-preview":
            if not args.confirmed_rebuild:
                print(
                    f"refused: rebuild-preview deletes clips from an existing timeline and "
                    f"requires {REBUILD_CONFIRM_FLAG}. Nothing was modified."
                )
                return EXIT_BAD_REQUEST
            return _speech_probe(args, config, plan=True, rebuild=True)
        if not args.confirmed_apply:
            # Checked before Resolve is even contacted: the flag is the whole opt-in.
            print(
                f"refused: apply-preview creates a new timeline in your project and "
                f"requires {APPLY_CONFIRM_FLAG}. Nothing was modified."
            )
            return EXIT_BAD_REQUEST
        return _speech_probe(args, config, plan=True, apply_plan=True)

    if args.command == "probe-ownership":
        try:
            resolve = connect()
            project = current_project(resolve)
        except ResolveUnavailableError as exc:
            print(f"error: {exc}")
            return EXIT_RESOLVE_UNAVAILABLE
        try:
            ownership = run_ownership_probe(
                resolve,
                project,
                config,
                _apply_target(args, config),
                confirmed=args.confirmed_ownership,
            )
        except OwnershipProbeRefused as exc:
            print(f"refused: {exc}")
            return EXIT_BAD_REQUEST
        _emit(ownership.to_dict(), ownership.to_text(), args.as_json)
        return EXIT_OK if ownership.succeeded else EXIT_PROBE_FAILED

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
