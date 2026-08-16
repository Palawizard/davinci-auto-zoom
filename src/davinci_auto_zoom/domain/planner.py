"""Deterministic MVP zoom planner: speech facts in, asset placements out.

Pure by construction — no Resolve object, no ONNX, no ffmpeg, no filesystem, no clock. The
same inputs always produce the same plan, which is what makes the whole thing testable and
what lets a later executor trust the plan instead of recomputing it.

    timeline range + fps + SpeechSegments + hard cuts + editorial settings + asset timing
      -> FACE_X1 / FACE_X0 placements, with a reason for every one

Three ideas do most of the work here:

**Speech regions are not editorial bursts.** The VAD reports where the creator made speech
sounds (D021). The planner groups those regions into *bursts*: consecutive regions separated
by less than `reset_after_silence_ms` belong to the same burst, because a zoom that pops out
and back in across a breath is worse than one that simply stays. `reset_after_silence_ms` is
therefore a **gate** deciding whether a pause deserves a reset — not a delay to wait out. The
planner runs offline and already knows how long every pause lasts, so it never plans
`x0_start = speech_end + 650 ms`.

**An asset instance is longer than its animation.** `FACE_X1` is an entry animation followed
by a *hold*: the zoom stays where the animation left it for as long as the clip lasts (D014).
So an x1 placement spans `[x1_start, x0_start)` — 15 frames or 900, whatever the burst needs —
and its only hard constraint is that it must last at least as long as its own animation, or
the move never completes. Symmetrically `FACE_X0_SMOOTH` only needs its animation length to
do its whole job; the Media Pool item's *native* length (42 frames for this user) is a
property of the asset file, not a minimum the planner has to honour. Nothing here reads it.

**A reset prefers a real cut.** When the creator stops talking and the edit cuts shortly
after, returning to normal framing on that cut looks intentional. So a reset may be pushed
forward to the last hard cut inside the snap window — but only if the x0 animation still fits
entirely before the next zoom starts.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from davinci_auto_zoom.domain.models import (
    Frame,
    FrameRange,
    SpeechSegment,
    normalize_speech_segments,
)

#: Semantic roles. The planner never sees a Media Pool clip name (D007/D008).
ROLE_FACECAM_X1 = "facecam_x1"
ROLE_RESET_X0 = "reset_x0"

#: Placement reasons. Short tokens: they are a table column and a JSON field, not prose.
REASON_X1_UNTIL_DIRECT_RESET = "x1_until_direct_reset"
REASON_X1_UNTIL_CUT_RESET = "x1_until_cut_snapped_reset"
REASON_X1_HELD_TO_TIMELINE_END = "x1_held_to_timeline_end"
REASON_RESET_DIRECT = "reset_direct"
REASON_RESET_SNAPPED = "reset_snapped_to_cut"


def frames_from_ms(milliseconds: int, frame_rate: Fraction) -> int:
    """Exact ms -> frames, rounded half up. `Fraction` so no float error creeps in."""

    if milliseconds < 0:
        raise ValueError("milliseconds must be >= 0")
    exact = Fraction(milliseconds, 1000) * frame_rate
    return int(exact + Fraction(1, 2))


@dataclass(frozen=True, slots=True)
class PlannerSettings:
    """Editorial timing, in milliseconds — taste, not detector tuning (D021).

    Defaults are the MVP baseline: enter exactly on the burst, reset exactly at its end. The
    old scaffold's 80/120 ms lead-in/out were guesses with nothing behind them and are now 0;
    they stay configurable because a user may well want a little anticipation later.
    """

    #: Gate: a silence at least this long may end the zoom. A shorter one is bridged.
    reset_after_silence_ms: int = 650
    zoom_lead_in_ms: int = 0
    zoom_lead_out_ms: int = 0
    #: How far past the reset point the planner may look for a hard cut to land on.
    cut_snap_window_ms: int = 350

    def __post_init__(self) -> None:
        for name in (
            "reset_after_silence_ms",
            "zoom_lead_in_ms",
            "zoom_lead_out_ms",
            "cut_snap_window_ms",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")

    def to_dict(self) -> dict[str, int]:
        return {
            "reset_after_silence_ms": self.reset_after_silence_ms,
            "zoom_lead_in_ms": self.zoom_lead_in_ms,
            "zoom_lead_out_ms": self.zoom_lead_out_ms,
            "cut_snap_window_ms": self.cut_snap_window_ms,
        }


@dataclass(frozen=True, slots=True)
class AssetTiming:
    """How many frames each user asset needs to finish its own animation.

    User metadata about user-built assets, in **frames**, because that is how the keyframes
    were authored. DAZ never opens the Fusion graph to discover these (D007): it is told.

    Emphatically *not* the Media Pool item's native duration. `FACE_X0_SMOOTH` is 42 frames
    long in this user's bin and completes its move in 15; the planner needs the 15.
    """

    facecam_x1_transition_frames: int
    reset_x0_transition_frames: int

    def __post_init__(self) -> None:
        for name in ("facecam_x1_transition_frames", "reset_x0_transition_frames"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")

    def to_dict(self) -> dict[str, int]:
        return {
            ROLE_FACECAM_X1: self.facecam_x1_transition_frames,
            ROLE_RESET_X0: self.reset_x0_transition_frames,
        }


@dataclass(frozen=True, slots=True)
class AssetPlacement:
    """One asset instance, fully resolved: role, absolute half-open range, and why.

    Complete on purpose. Phase 5 must be able to insert exactly this — `AppendToTimeline`
    with `recordFrame=start_frame` and `endFrame - startFrame = duration_frames` (D013) —
    without re-deriving a single timing decision.
    """

    asset_role: str
    frames: FrameRange
    reason: str
    #: The hard cut this placement was snapped to, when it was.
    cut_frame: Frame | None = None
    #: First editorial burst this placement serves, and how many it spans.
    burst_index: int = 0
    burst_count: int = 1

    @property
    def start_frame(self) -> Frame:
        return self.frames.start

    @property
    def end_frame(self) -> Frame:
        return self.frames.end

    @property
    def duration_frames(self) -> int:
        return self.frames.duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_role": self.asset_role,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_frames": self.duration_frames,
            "reason": self.reason,
            "cut_frame": self.cut_frame,
            "burst_index": self.burst_index,
            "burst_count": self.burst_count,
        }


@dataclass(frozen=True, slots=True)
class PlanSource:
    """Everything a future executor needs to check that this plan still fits reality.

    Recorded, not validated: deciding whether a plan may still be applied is Phase 5's job.
    """

    project: str
    timeline: str
    timeline_unique_id: str | None
    start_frame: Frame
    end_frame: Frame
    frame_rate: str
    voice_audio_track: int
    cut_reference_video_track: int
    zoom_video_track: int
    #: role -> Media Pool clip name, as configured.
    assets: tuple[tuple[str, str], ...] = ()
    asset_transition_frames: tuple[tuple[str, int], ...] = ()
    planner_settings: PlannerSettings = field(default_factory=PlannerSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "timeline": self.timeline,
            "timeline_unique_id": self.timeline_unique_id,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "frame_rate": self.frame_rate,
            "voice_audio_track": self.voice_audio_track,
            "cut_reference_video_track": self.cut_reference_video_track,
            "zoom_video_track": self.zoom_video_track,
            "assets": dict(self.assets),
            "asset_transition_frames": dict(self.asset_transition_frames),
            "planner_settings": self.planner_settings.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ZoomPlan:
    """The plan, its inputs, and the reasoning that connects them."""

    timeline: FrameRange
    frame_rate: Fraction
    settings: PlannerSettings
    timing: AssetTiming
    speech_segments: tuple[FrameRange, ...]
    bursts: tuple[FrameRange, ...]
    placements: tuple[AssetPlacement, ...]
    decisions: tuple[str, ...]
    merged_gaps: int = 0
    suppressed_resets: int = 0
    suppressed_cycles: int = 0
    rejected_cuts: int = 0
    source: PlanSource | None = None

    def of_role(self, role: str) -> tuple[AssetPlacement, ...]:
        return tuple(p for p in self.placements if p.asset_role == role)

    @property
    def x1_placements(self) -> tuple[AssetPlacement, ...]:
        return self.of_role(ROLE_FACECAM_X1)

    @property
    def x0_placements(self) -> tuple[AssetPlacement, ...]:
        return self.of_role(ROLE_RESET_X0)

    @property
    def direct_resets(self) -> int:
        return sum(1 for p in self.x0_placements if p.reason == REASON_RESET_DIRECT)

    @property
    def snapped_resets(self) -> int:
        return sum(1 for p in self.x0_placements if p.reason == REASON_RESET_SNAPPED)

    @property
    def zoomed_frames(self) -> int:
        return sum(p.duration_frames for p in self.x1_placements)

    @property
    def zoom_coverage(self) -> float:
        return self.zoomed_frames / self.timeline.duration if self.timeline.duration else 0.0

    @property
    def overlaps(self) -> tuple[str, ...]:
        """Placements that collide. Must always be empty; checked, never assumed."""

        problems: list[str] = []
        ordered = sorted(self.placements, key=lambda p: (p.start_frame, p.end_frame))
        if [p.to_dict() for p in ordered] != [p.to_dict() for p in self.placements]:
            problems.append("placements are not sorted by start frame")
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.start_frame < earlier.end_frame:
                problems.append(
                    f"{earlier.asset_role} [{earlier.start_frame},{earlier.end_frame}) "
                    f"overlaps {later.asset_role} "
                    f"[{later.start_frame},{later.end_frame})"
                )
        for placement in self.placements:
            if (
                placement.start_frame < self.timeline.start
                or placement.end_frame > self.timeline.end
            ):
                problems.append(
                    f"{placement.asset_role} [{placement.start_frame},"
                    f"{placement.end_frame}) leaves the timeline range "
                    f"[{self.timeline.start},{self.timeline.end})"
                )
        return tuple(problems)

    @property
    def valid(self) -> bool:
        return not self.overlaps

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict() if self.source else None,
            "timeline": {"start_frame": self.timeline.start, "end_frame": self.timeline.end},
            "frame_rate": str(self.frame_rate),
            "settings": self.settings.to_dict(),
            "asset_transition_frames": self.timing.to_dict(),
            "speech_segments": [
                {"start_frame": s.start, "end_frame": s.end, "duration_frames": s.duration}
                for s in self.speech_segments
            ],
            "bursts": [
                {"start_frame": b.start, "end_frame": b.end, "duration_frames": b.duration}
                for b in self.bursts
            ],
            "placements": [p.to_dict() for p in self.placements],
            "decisions": list(self.decisions),
            "diagnostics": {
                "speech_segment_count": len(self.speech_segments),
                "burst_count": len(self.bursts),
                "merged_gaps": self.merged_gaps,
                "facecam_x1_count": len(self.x1_placements),
                "reset_x0_count": len(self.x0_placements),
                "direct_resets": self.direct_resets,
                "cut_snapped_resets": self.snapped_resets,
                "suppressed_resets": self.suppressed_resets,
                "suppressed_cycles": self.suppressed_cycles,
                "rejected_cuts": self.rejected_cuts,
                "zoomed_frames": self.zoomed_frames,
                "zoom_coverage": self.zoom_coverage,
                "overlaps": list(self.overlaps),
                "valid": self.valid,
            },
        }

    def to_text(self) -> str:
        lines = [
            f"zoom plan for [{self.timeline.start}, {self.timeline.end}) "
            f"@ {float(self.frame_rate):.6f} fps",
            f"  gate      : reset_after_silence={self.settings.reset_after_silence_ms}ms "
            f"lead_in={self.settings.zoom_lead_in_ms}ms "
            f"lead_out={self.settings.zoom_lead_out_ms}ms "
            f"cut_snap_window={self.settings.cut_snap_window_ms}ms",
            f"  assets    : x1 animation={self.timing.facecam_x1_transition_frames} frames, "
            f"x0 animation={self.timing.reset_x0_transition_frames} frames "
            "(native Media Pool lengths are irrelevant here)",
            "",
            "  role         start        end   frames  reason                      cut",
        ]
        for placement in self.placements:
            cut = "-" if placement.cut_frame is None else str(placement.cut_frame)
            lines.append(
                f"  {placement.asset_role:11} {placement.start_frame:>9} "
                f"{placement.end_frame:>10} {placement.duration_frames:>8}  "
                f"{placement.reason:26} {cut}"
            )
        lines.extend(
            [
                "",
                f"  speech segments : {len(self.speech_segments)}",
                f"  editorial bursts: {len(self.bursts)} "
                f"({self.merged_gaps} pause(s) bridged)",
                f"  facecam_x1      : {len(self.x1_placements)}",
                f"  reset_x0        : {len(self.x0_placements)} "
                f"({self.direct_resets} direct, {self.snapped_resets} cut-snapped)",
                f"  suppressed      : {self.suppressed_resets} reset(s) with no room, "
                f"{self.suppressed_cycles} cycle(s) shorter than the x1 animation, "
                f"{self.rejected_cuts} cut(s) rejected as too late",
                f"  zoom coverage   : {self.zoomed_frames} frames "
                f"({self.zoom_coverage * 100:.1f}% of the range)",
                f"  overlaps        : {', '.join(self.overlaps) or 'none'}",
            ]
        )
        lines.append("")
        lines.append("  decision trace:")
        lines.extend(f"    {decision}" for decision in self.decisions)
        return "\n".join(lines)


def hard_cuts_in_range(cuts: Iterable[Frame], timeline: FrameRange) -> tuple[Frame, ...]:
    """Sorted, unique cuts that actually fall inside the analysed range."""

    return tuple(sorted({c for c in cuts if timeline.start <= c <= timeline.end}))


@dataclass(frozen=True, slots=True)
class _ResetChoice:
    frame: Frame | None
    cut_frame: Frame | None
    rejected_cuts: tuple[Frame, ...]


def _choose_reset(
    base_reset: Frame,
    limit: Frame,
    cuts: Sequence[Frame],
    snap_window: int,
    x0_frames: int,
) -> _ResetChoice:
    """Where the reset goes: the last usable hard cut in the window, else the direct point.

    `limit` is the frame the next zoom starts at (or the end of the timeline). A reset at `R`
    is only usable when the whole x0 animation fits before it: `R + x0_frames <= limit`. That
    is the *only* length rule — the asset's native Media Pool duration plays no part.
    """

    candidates = [c for c in cuts if base_reset <= c <= base_reset + snap_window and c < limit]
    usable = [c for c in candidates if c + x0_frames <= limit]
    rejected = tuple(c for c in candidates if c not in usable)
    if usable:
        # The last one: the reset should land on the final cut of the little flurry that
        # follows the sentence, not the first.
        return _ResetChoice(usable[-1], usable[-1], rejected)
    if base_reset + x0_frames <= limit:
        return _ResetChoice(base_reset, None, rejected)
    return _ResetChoice(None, None, rejected)


def plan_zooms(
    *,
    timeline: FrameRange,
    frame_rate: Fraction,
    speech_segments: Iterable[SpeechSegment],
    timing: AssetTiming,
    hard_cuts: Iterable[Frame] = (),
    settings: PlannerSettings | None = None,
    source: PlanSource | None = None,
) -> ZoomPlan:
    """Turn speech facts into FACE_X1 / FACE_X0 placements. Pure and deterministic."""

    settings = settings or PlannerSettings()
    reset_gate = frames_from_ms(settings.reset_after_silence_ms, frame_rate)
    lead_in = frames_from_ms(settings.zoom_lead_in_ms, frame_rate)
    lead_out = frames_from_ms(settings.zoom_lead_out_ms, frame_rate)
    snap_window = frames_from_ms(settings.cut_snap_window_ms, frame_rate)
    x1_min = timing.facecam_x1_transition_frames
    x0_frames = timing.reset_x0_transition_frames
    cuts = hard_cuts_in_range(hard_cuts, timeline)

    decisions: list[str] = [
        f"timeline [{timeline.start}, {timeline.end}) = {timeline.duration} frames "
        f"@ {float(frame_rate):.6f} fps",
        f"gate reset_after_silence={settings.reset_after_silence_ms}ms = {reset_gate} frames; "
        f"lead_in={lead_in}f lead_out={lead_out}f snap_window={snap_window}f; "
        f"x1 animation={x1_min}f x0 animation={x0_frames}f",
        f"{len(cuts)} hard cut(s) on the reference video track inside the range",
    ]

    # 1. Speech regions, normalized and clipped to the range we are allowed to plan in.
    segments: list[FrameRange] = []
    for segment in normalize_speech_segments(speech_segments):
        start = max(segment.start_frame, timeline.start)
        end = min(segment.end_frame, timeline.end)
        if end <= start:
            decisions.append(
                f"speech [{segment.start_frame}, {segment.end_frame}) is outside the "
                "timeline range and was dropped"
            )
            continue
        segments.append(FrameRange(start, end))
    decisions.append(f"{len(segments)} speech segment(s) after normalization and clipping")

    # 2. Editorial bursts: a pause shorter than the gate is not worth leaving the zoom for.
    bursts: list[FrameRange] = []
    merged_gaps = 0
    for speech in segments:
        if bursts:
            gap = speech.start - bursts[-1].end
            if gap < reset_gate:
                merged_gaps += 1
                decisions.append(
                    f"gap of {gap} frames at {bursts[-1].end} is below the {reset_gate}-frame "
                    f"gate: speech [{speech.start}, {speech.end}) joins the burst starting "
                    f"at {bursts[-1].start} (x1 stays up across the pause)"
                )
                bursts[-1] = FrameRange(bursts[-1].start, speech.end)
                continue
            decisions.append(
                f"gap of {gap} frames at {bursts[-1].end} reaches the {reset_gate}-frame "
                "gate: a reset may be planned"
            )
        bursts.append(speech)
    decisions.append(f"{len(bursts)} editorial burst(s)")

    # 3. One x1 per burst — held open across any pause where the x0 does not fit.
    placements: list[AssetPlacement] = []
    suppressed_resets = 0
    suppressed_cycles = 0
    rejected_cuts = 0
    open_start: Frame | None = None
    open_index = 0

    for index, burst in enumerate(bursts):
        if open_start is None:
            open_start = max(timeline.start, burst.start - lead_in)
            open_index = index
        is_last = index == len(bursts) - 1
        limit = timeline.end if is_last else max(timeline.start, bursts[index + 1].start - lead_in)
        base_reset = min(burst.end + lead_out, timeline.end)
        choice = _choose_reset(base_reset, limit, cuts, snap_window, x0_frames)
        rejected_cuts += len(choice.rejected_cuts)
        for cut in choice.rejected_cuts:
            decisions.append(
                f"burst {index}: hard cut at {cut} rejected — only {limit - cut} frame(s) left "
                f"before {'the timeline end' if is_last else f'the next zoom at {limit}'}, "
                f"the x0 animation needs {x0_frames}"
            )

        if choice.frame is None:
            suppressed_resets += 1
            where = "the timeline end" if is_last else f"the next burst's zoom at {limit}"
            decisions.append(
                f"burst {index}: no reset — the x0 animation needs {x0_frames} frames and "
                f"only {limit - base_reset} remain before {where}; x1 stays active"
                + ("" if is_last else " across the pause")
            )
            continue

        reset = choice.frame
        if choice.cut_frame is not None:
            decisions.append(
                f"burst {index}: reset snapped forward from {base_reset} to hard cut "
                f"{choice.cut_frame} (+{choice.cut_frame - base_reset} frames, window "
                f"{snap_window})"
            )
        else:
            decisions.append(
                f"burst {index}: direct reset at {base_reset} "
                f"(burst end {burst.end} + lead-out {lead_out}); no usable hard cut in the "
                f"{snap_window}-frame window"
            )

        span = FrameRange(open_start, reset)
        if span.duration < x1_min:
            suppressed_cycles += 1
            decisions.append(
                f"burst {index}: cycle dropped — x1 would run [{span.start}, {span.end}) = "
                f"{span.duration} frame(s), shorter than its own {x1_min}-frame animation, so "
                "the zoom would be cut off mid-move; no x1 and no x0 are placed"
            )
            open_start = None
            continue

        placements.append(
            AssetPlacement(
                asset_role=ROLE_FACECAM_X1,
                frames=span,
                reason=(
                    REASON_X1_UNTIL_CUT_RESET
                    if choice.cut_frame is not None
                    else REASON_X1_UNTIL_DIRECT_RESET
                ),
                cut_frame=choice.cut_frame,
                burst_index=open_index,
                burst_count=index - open_index + 1,
            )
        )
        placements.append(
            AssetPlacement(
                asset_role=ROLE_RESET_X0,
                frames=FrameRange(reset, reset + x0_frames),
                reason=(
                    REASON_RESET_SNAPPED
                    if choice.cut_frame is not None
                    else REASON_RESET_DIRECT
                ),
                cut_frame=choice.cut_frame,
                burst_index=index,
            )
        )
        open_start = None

    if open_start is not None:
        span = FrameRange(open_start, timeline.end)
        if span.duration < x1_min:
            suppressed_cycles += 1
            decisions.append(
                f"final x1 [{span.start}, {span.end}) = {span.duration} frame(s) is shorter "
                f"than its {x1_min}-frame animation and was dropped"
            )
        else:
            placements.append(
                AssetPlacement(
                    asset_role=ROLE_FACECAM_X1,
                    frames=span,
                    reason=REASON_X1_HELD_TO_TIMELINE_END,
                    burst_index=open_index,
                    burst_count=len(bursts) - open_index,
                )
            )
            decisions.append(
                f"x1 [{span.start}, {span.end}) is held to the timeline end: the reset is not "
                "truncated and nothing is placed past the range"
            )

    plan = ZoomPlan(
        timeline=timeline,
        frame_rate=frame_rate,
        settings=settings,
        timing=timing,
        speech_segments=tuple(segments),
        bursts=tuple(bursts),
        placements=tuple(placements),
        decisions=tuple(decisions),
        merged_gaps=merged_gaps,
        suppressed_resets=suppressed_resets,
        suppressed_cycles=suppressed_cycles,
        rejected_cuts=rejected_cuts,
        source=source,
    )
    return plan


__all__ = [
    "REASON_RESET_DIRECT",
    "REASON_RESET_SNAPPED",
    "REASON_X1_HELD_TO_TIMELINE_END",
    "REASON_X1_UNTIL_CUT_RESET",
    "REASON_X1_UNTIL_DIRECT_RESET",
    "ROLE_FACECAM_X1",
    "ROLE_RESET_X0",
    "AssetPlacement",
    "AssetTiming",
    "PlanSource",
    "PlannerSettings",
    "ZoomPlan",
    "frames_from_ms",
    "hard_cuts_in_range",
    "plan_zooms",
]
