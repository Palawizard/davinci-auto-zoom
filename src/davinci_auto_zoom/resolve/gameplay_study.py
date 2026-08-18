"""Phase 9a diagnostic: measure a project, read the human edit, and compare the two.

    creator voice   -> speech segments -> editorial bursts -> silence windows
    secondary audio -> energy envelope -> per-window features
    program video   -> motion envelope -> per-window features
    reference edit  -> state machine   -> manual gameplay episodes
                    -> candidate rule, ablation, entry/exit timing

**This command places nothing.** It never calls `AppendToTimeline`, never creates a preview
and never touches a gameplay asset except to read its name out of the config. What it does
mutate is exactly what `plan-probe` already mutates and restores: a scratch duplicate, one
render job at a time, and the Deliver page — three times instead of once, through the same
`render_voice_track` and therefore the same audit, cleanup and refusal behaviour.

The three renders are deliberately separate jobs rather than one clever multi-output job:
each is the proven single-job shape, and a failure in the third leaves the first two's
cleanup already done.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.gameplay import (
    MANUAL_X0,
    GameplayDecision,
    GameplayEpisode,
    GameplayPolicySettings,
    GameplayWindowFeatures,
    ManualCoverage,
    ReferenceStates,
    SecondaryAudioFeatures,
    SilenceWindow,
    decide_gameplay,
    manual_coverage,
    percentile,
    reference_states,
    secondary_audio_features,
    silence_windows,
    video_activity_features,
)
from davinci_auto_zoom.domain.models import Frame, FrameRange
from davinci_auto_zoom.domain.probe import VoiceRenderTarget
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.resolve.session import snapshot_project
from davinci_auto_zoom.resolve.voice_render import render_voice_track
from davinci_auto_zoom.speech.audio import normalized_audio
from davinci_auto_zoom.speech.energy import energy_envelope
from davinci_auto_zoom.vision import VisionSettings, motion_envelope

CONFIRM_FLAG = "--confirm-resolve-render-test"

#: Built-in Resolve preset used for the one video render. Checked against the installation's
#: actual preset list during preflight, exactly like `Audio Only`, never assumed present.
VIDEO_RENDER_PRESET = "H.264 Master"

#: Where a window's activity is measured *against*. Both are properties of the whole
#: timeline, so a window's numbers say "compared with the rest of this delivery" rather than
#: "compared with a constant somebody picked" (D051, generalised).
AUDIO_REFERENCE_PERCENTILE = 0.75
MOTION_REFERENCE_PERCENTILE = 0.5

#: The four ablation families of the phase brief, as (label, settings-overrides).
ABLATIONS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("A silence only", {"use_secondary_audio": False, "use_video": False}),
    ("B silence+audio", {"use_secondary_audio": True, "use_video": False}),
    ("C silence+video", {"use_secondary_audio": False, "use_video": True}),
    ("D silence+audio+video", {"use_secondary_audio": True, "use_video": True}),
)


class GameplayStudyRefused(RuntimeError):
    """The study will not run. Nothing was rendered and nothing was modified."""


@dataclass
class WindowRow:
    """One silence window, everything measured about it, and both verdicts."""

    window: SilenceWindow
    features: GameplayWindowFeatures
    coverage: ManualCoverage
    decision: GameplayDecision

    @property
    def manual_gameplay(self) -> bool:
        return self.coverage.verdict != MANUAL_X0

    @property
    def agrees(self) -> bool:
        return self.manual_gameplay == self.decision.use_gameplay

    def start_delta(self) -> int | None:
        """Proposed entry minus manual entry. `None` when one of the two does not exist."""

        if self.coverage.gameplay_start is None or self.decision.entry_frame is None:
            return None
        return self.decision.entry_frame - self.coverage.gameplay_start

    def end_delta(self) -> int | None:
        if self.coverage.gameplay_end is None or self.decision.exit_frame is None:
            return None
        return self.decision.exit_frame - self.coverage.gameplay_end

    def to_dict(self, frame_rate: Fraction) -> dict[str, Any]:
        return {
            **self.features.to_dict(frame_rate),
            "manual": self.coverage.to_dict(),
            "manual_gameplay": self.manual_gameplay,
            "manual_fraction": round(self.coverage.fraction(self.window), 3),
            "proposed": self.decision.to_dict(),
            "agrees": self.agrees,
            "start_delta": self.start_delta(),
            "end_delta": self.end_delta(),
        }


@dataclass
class CutTiming:
    """Where one manual gameplay boundary sits relative to the edit's own hard cuts."""

    episode_index: int
    role: str
    frame: Frame
    previous_cut: Frame | None
    next_cut: Frame | None
    #: Signed distance to the nearest cut either side; `None` when the timeline has none.
    nearest_delta: int | None
    on_cut: bool
    #: For an entry: how far into the silence window it sits. For an exit: how far from the
    #: next burst start. The two numbers the phase brief asks for, by boundary kind.
    delta_to_boundary: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_index": self.episode_index,
            "role": self.role,
            "frame": self.frame,
            "previous_cut": self.previous_cut,
            "next_cut": self.next_cut,
            "nearest_delta": self.nearest_delta,
            "on_cut": self.on_cut,
            "delta_to_boundary": self.delta_to_boundary,
        }


@dataclass
class AblationResult:
    """One family's score, in the only terms that mean anything on 15 windows: counts."""

    label: str
    correct: int
    total: int
    false_positives: tuple[int, ...]
    false_negatives: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "correct": self.correct,
            "total": self.total,
            "false_positives": list(self.false_positives),
            "false_negatives": list(self.false_negatives),
        }


@dataclass
class GameplayStudyReport:
    """Everything the study observed. `to_dict()` is the `--json` payload."""

    project_name: str = ""
    source_timeline: str = ""
    reference_timeline: str | None = None
    frame_rate: str = ""
    timeline_start: int = 0
    timeline_end: int = 0

    #: One entry per render performed, each carrying its own full safety audit.
    renders: list[dict[str, Any]] = field(default_factory=list)

    speech_segments: int = 0
    bursts: int = 0
    hard_cuts: int = 0
    audio_reference_db: float | None = None
    motion_reference: float | None = None
    secondary_audio_tracks: tuple[int, ...] = ()

    reference: dict[str, Any] | None = None
    rows: list[WindowRow] = field(default_factory=list)
    entry_timing: list[CutTiming] = field(default_factory=list)
    exit_timing: list[CutTiming] = field(default_factory=list)
    ablations: list[AblationResult] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    total_seconds: float | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def frame_rate_fraction(self) -> Fraction:
        return Fraction(self.frame_rate) if self.frame_rate else Fraction(60)

    @property
    def clean(self) -> bool:
        """Every render restored everything it touched. The safety claim, not the science."""

        return all(render.get("clean") for render in self.renders)

    @property
    def succeeded(self) -> bool:
        return self.clean and self.error is None and bool(self.rows)

    @property
    def agreement(self) -> tuple[int, int]:
        return sum(1 for row in self.rows if row.agrees), len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        rate = self.frame_rate_fraction
        return {
            "project_name": self.project_name,
            "source_timeline": self.source_timeline,
            "reference_timeline": self.reference_timeline,
            "frame_rate": self.frame_rate,
            "timeline": {"start_frame": self.timeline_start, "end_frame": self.timeline_end},
            "renders": list(self.renders),
            "speech_segments": self.speech_segments,
            "bursts": self.bursts,
            "hard_cuts": self.hard_cuts,
            "audio_reference_db": self.audio_reference_db,
            "motion_reference": self.motion_reference,
            "secondary_audio_tracks": list(self.secondary_audio_tracks),
            "reference": self.reference,
            "windows": [row.to_dict(rate) for row in self.rows],
            "entry_timing": [t.to_dict() for t in self.entry_timing],
            "exit_timing": [t.to_dict() for t in self.exit_timing],
            "ablations": [a.to_dict() for a in self.ablations],
            "settings": self.settings,
            "agreement": {"correct": self.agreement[0], "total": self.agreement[1]},
            "clean": self.clean,
            "succeeded": self.succeeded,
            "total_seconds": self.total_seconds,
            "notes": list(self.notes),
            "error": self.error,
        }

    def to_text(self) -> str:
        rate = self.frame_rate_fraction
        lines = [
            "davinci-auto-zoom gameplay study (Phase 9a diagnostic — nothing is placed)",
            f"  project   : {self.project_name}",
            f"  source    : {self.source_timeline} "
            f"[{self.timeline_start}, {self.timeline_end}) @ {self.frame_rate} fps",
            f"  reference : {self.reference_timeline or '-'}",
            f"  renders   : {len(self.renders)} "
            f"({'all clean' if self.clean else 'NOT CLEAN — see the audit'})",
            f"  voice     : {self.speech_segments} segment(s) -> {self.bursts} burst(s), "
            f"{self.hard_cuts} hard cut(s)",
            f"  reference levels: audio p{int(AUDIO_REFERENCE_PERCENTILE * 100)} = "
            f"{self.audio_reference_db:.1f} dBFS, motion median = {self.motion_reference:.4f}"
            if self.audio_reference_db is not None and self.motion_reference is not None
            else "  reference levels: not measured",
        ]
        if self.reference:
            lines.append(
                f"  manual    : {len(self.reference['transitions'])} transition(s), "
                f"{len(self.reference['episodes'])} gameplay episode(s), "
                f"ends at {self.reference['final_state']}"
            )
            used = self.reference["roles_used"]
            lines.append(
                "  roles used: "
                + ", ".join(f"{role}={count}" for role, count in sorted(used.items()))
            )

        lines.append("")
        for row in self.rows:
            lines.extend(self._window_text(row, rate))

        correct, total = self.agreement
        lines.extend(
            [
                "",
                f"  candidate rule: {correct}/{total} window(s) classified as the human did",
                "",
                "  ablation (whether, not where):",
                "    family                 correct   false+                false-",
            ]
        )
        for ablation in self.ablations:
            lines.append(
                f"    {ablation.label:22} {ablation.correct:>3}/{ablation.total:<3}  "
                f"{str(list(ablation.false_positives)):20}  "
                f"{list(ablation.false_negatives)}"
            )
        lines.extend(["", "  manual gameplay entries, against the edit's own hard cuts:"])
        lines.extend(self._timing_text(self.entry_timing, "into the silence window"))
        lines.extend(["", "  manual gameplay exits:"])
        lines.extend(self._timing_text(self.exit_timing, "from the next burst start"))
        for note in self.notes:
            lines.append(f"  ! {note}")
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        return "\n".join(lines)

    def _window_text(self, row: WindowRow, rate: Fraction) -> list[str]:
        window, decision = row.window, row.decision
        edge = (
            " (timeline head)"
            if window.at_timeline_start
            else " (timeline tail)"
            if window.at_timeline_end
            else ""
        )
        lines = [
            f"  gap {window.index}",
            f"    [{window.start_frame}, {window.end_frame}) = "
            f"{window.duration_ms(rate) / 1000:.2f} s "
            f"({window.duration_frames} frames){edge}",
            "",
            f"    manual  : {row.coverage.verdict.upper()} "
            f"({row.coverage.fraction(window) * 100:.0f}% of the gap"
            + (
                f", [{row.coverage.gameplay_start}, {row.coverage.gameplay_end}))"
                if row.coverage.gameplay_start is not None
                else ")"
            ),
            f"    proposed: {'GAMEPLAY' if decision.use_gameplay else 'X0'}"
            f"   {'agrees' if row.agrees else 'DIVERGES'}",
            "",
            f"    secondary audio: active={row.features.audio.active_fraction * 100:.0f}% "
            f"mean={row.features.audio.mean_db:+.1f} dB "
            f"p90={row.features.audio.p90_db:+.1f} dB "
            f"range={row.features.audio.dynamic_range_db:.1f} dB "
            f"onsets={row.features.audio.onsets}",
            f"    video          : motion={row.features.video.mean_motion:.2f}x "
            f"p90={row.features.video.p90_motion:.2f}x "
            f"active={row.features.video.active_fraction * 100:.0f}% "
            f"cuts={row.features.video.hard_cuts} "
            f"({row.features.video.cut_density:.2f}/s)",
        ]
        if decision.use_gameplay:
            lines.extend(
                [
                    "",
                    f"    proposed entry: raw={decision.entry_anchor} "
                    f"cut={decision.entry_cut if decision.entry_cut is not None else '-'} "
                    f"final={decision.entry_frame} ({decision.entry_snap})"
                    + (
                        f"   manual={row.coverage.gameplay_start} "
                        f"delta={row.start_delta():+d}"
                        if row.start_delta() is not None
                        else ""
                    ),
                    f"    proposed exit : raw={decision.exit_anchor} "
                    f"cut={decision.exit_cut if decision.exit_cut is not None else '-'} "
                    f"final={decision.exit_frame} ({decision.exit_snap})"
                    + (
                        f"   manual={row.coverage.gameplay_end} delta={row.end_delta():+d}"
                        if row.end_delta() is not None
                        else ""
                    ),
                ]
            )
        lines.extend(["", f"    reason: {decision.reason}", ""])
        return lines

    def _timing_text(self, timings: list[CutTiming], boundary: str) -> list[str]:
        if not timings:
            return ["    (none)"]
        lines = [
            "    ep  role                    frame   prev cut   next cut  nearest  "
            + boundary
        ]
        for timing in timings:
            nearest = "-" if timing.nearest_delta is None else f"{timing.nearest_delta:+d}"
            boundary_delta = (
                "-" if timing.delta_to_boundary is None else f"{timing.delta_to_boundary:+d}"
            )
            lines.append(
                f"    {timing.episode_index:>2}  {timing.role:22} {timing.frame:>7}  "
                f"{str(timing.previous_cut):>9}  {str(timing.next_cut):>9}  "
                f"{nearest:>7}{'*' if timing.on_cut else ' '} {boundary_delta:>9}"
            )
        on_cut = sum(1 for t in timings if t.on_cut)
        deltas = [t.delta_to_boundary for t in timings if t.delta_to_boundary is not None]
        lines.append(f"    exactly on a cut: {on_cut}/{len(timings)}")
        if deltas:
            ordered = sorted(deltas)
            lines.append(
                f"    {boundary}: min/median/max = "
                f"{ordered[0]:+d} / {ordered[len(ordered) // 2]:+d} / {ordered[-1]:+d} frames"
            )
        return lines


def _nearest_cuts(frame: Frame, cuts: tuple[Frame, ...]) -> tuple[Frame | None, Frame | None]:
    before = [c for c in cuts if c <= frame]
    after = [c for c in cuts if c >= frame]
    return (max(before) if before else None, min(after) if after else None)


def _cut_timing(
    episode: GameplayEpisode,
    frame: Frame,
    role: str,
    cuts: tuple[Frame, ...],
    boundary: Frame | None,
) -> CutTiming:
    previous_cut, next_cut = _nearest_cuts(frame, cuts)
    candidates = [c for c in (previous_cut, next_cut) if c is not None]
    nearest = min((c - frame for c in candidates), key=abs) if candidates else None
    return CutTiming(
        episode_index=episode.index,
        role=role,
        frame=frame,
        previous_cut=previous_cut,
        next_cut=next_cut,
        nearest_delta=nearest,
        on_cut=frame in cuts,
        delta_to_boundary=None if boundary is None else frame - boundary,
    )


def _render(
    resolve: Any,
    project: Any,
    config: Config,
    target: VoiceRenderTarget,
    directory: Path,
    report: GameplayStudyReport,
    label: str,
    protected: tuple[str, ...],
    **kwargs: Any,
) -> Path:
    """One render through the proven pipeline, with its audit folded into the study report."""

    render_report, produced = render_voice_track(
        resolve, project, config, target, directory, confirmed=True,
        protected_timelines=protected, **kwargs,
    )
    report.renders.append(
        {
            "label": label,
            "clean": render_report.clean,
            "error": render_report.error,
            "media_kind": render_report.media_kind,
            "kept_audio_tracks": list(render_report.kept_audio_tracks),
            "emptied_audio_tracks": list(render_report.removed_audio_tracks),
            "scratch_name": render_report.scratch_name,
            "scratch_absent_after_cleanup": render_report.scratch_absent_after_cleanup,
            "audit_checked": list(render_report.audit_checked),
            "audit_differences": list(render_report.audit_differences),
            "render_queue_restored": render_report.queue.only_our_job_removed,
            "delivery_unrestored": list(render_report.delivery.unrestored),
            "render_directory_removed": render_report.render_directory_removed,
            "render_seconds": render_report.render_seconds,
        }
    )
    if produced is None:
        raise GameplayStudyRefused(
            f"the {label} render failed and nothing downstream can be measured: "
            f"{render_report.error}"
        )
    if not render_report.clean:
        raise GameplayStudyRefused(
            f"the {label} render did not restore everything it touched, so the study stops "
            f"here rather than running two more: {render_report.audit_differences}"
        )
    return produced


def run_gameplay_study(
    resolve: Any,
    project: Any,
    config: Config,
    target: VoiceRenderTarget,
    directory: Path,
    *,
    reference_timeline: str | None,
    bursts: tuple[FrameRange, ...],
    speech_segments: int,
    timeline: FrameRange,
    frame_rate: Fraction,
    timeline_frame_rate: str,
    confirmed: bool,
    settings: GameplayPolicySettings | None = None,
    vision: VisionSettings | None = None,
) -> GameplayStudyReport:
    """Measure, read the human edit, decide, and compare. Never places anything.

    The creator's voice has already been analysed by the caller (it is the same render
    `plan-probe` does, and the same bursts the facecam planner uses — measuring them twice
    would risk two different answers). What this adds is the two renders the facecam pipeline
    has never needed, and the reading of the reference timeline.
    """

    started = time.perf_counter()
    settings = settings or GameplayPolicySettings()
    report = GameplayStudyReport(
        project_name=str(project.GetName()),
        source_timeline=target.source_timeline,
        reference_timeline=reference_timeline,
        frame_rate=str(frame_rate),
        timeline_start=timeline.start,
        timeline_end=timeline.end,
        speech_segments=speech_segments,
        bursts=len(bursts),
        settings=settings.to_dict(),
    )
    if not confirmed:
        raise GameplayStudyRefused(
            f"the gameplay study renders from Resolve and requires {CONFIRM_FLAG} to run"
        )
    if reference_timeline is None:
        raise GameplayStudyRefused(
            "the gameplay study compares against a human edit and needs "
            "--reference-timeline; without one there is nothing to measure against"
        )

    snapshot = snapshot_project(resolve, project, config)
    reference = snapshot.timeline(reference_timeline)
    if reference is None:
        raise GameplayStudyRefused(f"reference timeline {reference_timeline!r} not found")
    source = snapshot.timeline(target.source_timeline)
    if source is None:
        raise GameplayStudyRefused(f"source timeline {target.source_timeline!r} not found")
    # The same call on the same track the facecam planner uses, so the two can never disagree
    # about where the edit cuts.
    hard_cuts = tuple(source.hard_cuts(config.cut_reference_video_track))
    report.hard_cuts = len(hard_cuts)

    protected = (reference_timeline,)
    audio_track_count = len(source.tracks_of("audio"))
    secondary = tuple(
        index
        for index in range(1, audio_track_count + 1)
        if index != target.voice_audio_track
    )
    report.secondary_audio_tracks = secondary
    if not secondary:
        raise GameplayStudyRefused(
            f"{target.source_timeline!r} has only the voice track A{target.voice_audio_track}, "
            "so there is no secondary audio to measure"
        )
    report.notes.append(
        "secondary audio is A"
        + "+A".join(str(index) for index in secondary)
        + " combined; which of them carries the game and which carries other people is NOT "
        "claimed — the track names in this project are positional"
    )

    timebase = Timebase.from_timeline(timeline_frame_rate, timeline.start)

    # --- the two renders the facecam pipeline never needed --------------------------------
    rendered = _render(
        resolve, project, config, target, directory, report,
        "secondary audio", protected, keep_audio_tracks=secondary,
    )
    audio = normalized_audio(rendered, directory / "secondary_16k.wav")
    audio_envelope = [
        (point.frame, point.db)
        for point in energy_envelope(audio.samples, timebase, config.energy).points
    ]

    video_file = _render(
        resolve, project, config, target, directory, report,
        "program video", protected,
        keep_audio_tracks=tuple(range(1, audio_track_count + 1)),
        export_video=True, render_preset=VIDEO_RENDER_PRESET,
    )
    motion = [
        (point.frame, point.motion)
        for point in motion_envelope(video_file, timebase, vision).points
    ]

    if not audio_envelope or not motion:
        raise GameplayStudyRefused(
            "one of the two envelopes came back empty, so no window can be measured"
        )
    report.audio_reference_db = percentile(
        [level for _, level in audio_envelope], AUDIO_REFERENCE_PERCENTILE
    )
    report.motion_reference = percentile(
        [value for _, value in motion], MOTION_REFERENCE_PERCENTILE
    )

    # --- the human edit, through the graph -------------------------------------------------
    zoom_track = reference.track("video", config.zoom_video_track)
    if zoom_track is None or not zoom_track.items:
        raise GameplayStudyRefused(
            f"{reference_timeline!r} has nothing on V{config.zoom_video_track}, so there is "
            "no manual edit to read"
        )
    role_of = {name: role for role, name in config.assets.items()}
    states = reference_states(
        [(str(item.name), item.start, item.end) for item in zoom_track.items],
        role_of,
        bursts=bursts,
    )
    report.reference = states.to_dict()

    # --- the population, its features, and both verdicts ------------------------------------
    windows = silence_windows(timeline, bursts)
    for window in windows:
        features = GameplayWindowFeatures(
            window=window,
            audio=_audio_features(audio_envelope, window, report, settings),
            video=video_activity_features(
                motion, window, report.motion_reference, hard_cuts, frame_rate
            ),
        )
        report.rows.append(
            WindowRow(
                window=window,
                features=features,
                coverage=manual_coverage(window, states.episodes),
                decision=decide_gameplay(features, hard_cuts, frame_rate, settings),
            )
        )

    report.ablations = _ablations(report.rows, hard_cuts, frame_rate, settings)
    report.entry_timing, report.exit_timing = _timings(states, windows, hard_cuts)
    report.total_seconds = time.perf_counter() - started
    return report


def _audio_features(
    envelope: list[tuple[Frame, float]],
    window: SilenceWindow,
    report: GameplayStudyReport,
    settings: GameplayPolicySettings,
) -> SecondaryAudioFeatures:
    assert report.audio_reference_db is not None
    return secondary_audio_features(
        envelope,
        window,
        report.audio_reference_db,
        active_within_db=settings.audio_active_within_db,
    )


def _ablations(
    rows: list[WindowRow],
    hard_cuts: tuple[Frame, ...],
    frame_rate: Fraction,
    settings: GameplayPolicySettings,
) -> list[AblationResult]:
    """The same decision code with signals switched off, never a second implementation.

    Every family runs with `gameplay_by_default` **off**, because the point of the ablation is
    to ask what the signals explain on their own. The candidate rule's own score is reported
    separately, next to them.
    """

    results: list[AblationResult] = []
    for label, overrides in ABLATIONS:
        variant = replace(settings, gameplay_by_default=False, **overrides)
        false_positives: list[int] = []
        false_negatives: list[int] = []
        correct = 0
        for row in rows:
            decision = decide_gameplay(row.features, hard_cuts, frame_rate, variant)
            if decision.use_gameplay == row.manual_gameplay:
                correct += 1
            elif decision.use_gameplay:
                false_positives.append(row.window.index)
            else:
                false_negatives.append(row.window.index)
        results.append(
            AblationResult(
                label, correct, len(rows), tuple(false_positives), tuple(false_negatives)
            )
        )
    return results


def _timings(
    states: ReferenceStates,
    windows: tuple[SilenceWindow, ...],
    hard_cuts: tuple[Frame, ...],
) -> tuple[list[CutTiming], list[CutTiming]]:
    """Where each manual gameplay boundary sits, separated by the role that performed it."""

    entries: list[CutTiming] = []
    exits: list[CutTiming] = []
    for episode in states.episodes:
        containing = next(
            (
                w
                for w in windows
                if w.start_frame <= episode.start_frame < w.end_frame
            ),
            None,
        )
        entries.append(
            _cut_timing(
                episode,
                episode.start_frame,
                episode.entry_role,
                hard_cuts,
                containing.start_frame if containing else None,
            )
        )
        exits.append(
            _cut_timing(
                episode,
                episode.end_frame,
                episode.exit_role,
                hard_cuts,
                episode.next_burst_start,
            )
        )
    return entries, exits


__all__ = [
    "ABLATIONS",
    "AUDIO_REFERENCE_PERCENTILE",
    "CONFIRM_FLAG",
    "MOTION_REFERENCE_PERCENTILE",
    "VIDEO_RENDER_PRESET",
    "AblationResult",
    "CutTiming",
    "GameplayStudyRefused",
    "GameplayStudyReport",
    "WindowRow",
    "run_gameplay_study",
]
