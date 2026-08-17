"""Deterministic zoom planner: speech facts in, transition placements out.

Pure by construction — no Resolve object, no ONNX, no ffmpeg, no filesystem, no clock. The
same inputs always produce the same plan, which is what makes the whole thing testable and
what lets a later executor trust the plan instead of recomputing it.

    timeline range + fps + SpeechSegments + hard cuts + editorial settings + asset timing
      -> transition placements, with a reason for every one

The planner reasons in the **states and transitions** of `domain/transitions.py`, never in
clip names and no longer in a hard-wired x1/x0 pair. A burst produces a chain of placements:
one entry, zero or more promotions up the facecam ladder, and one reset whose asset depends on
which level the chain reached.

Four ideas do most of the work here:

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

**A reset prefers a real cut, and the nearest one.** When the creator stops talking and the
edit cuts around the same moment, returning to normal framing on that cut looks intentional.
So a reset may move to a hard cut inside an *asymmetric* window around it:
`[base_reset - cut_snap_lookback, base_reset + cut_snap_window]`. The lookback is small and
exists because a VAD boundary is not an editorial one — `speech_pad_ms` alone puts the
detected end slightly after the perceptual one, and measurement on real material (see
`.agent/reports/phase-06-cut-offset-diagnostic.txt`) found the matching cut 4-7 frames
*before* `burst.end` in 8 of 14 bursts and never after. Among the candidates the planner
takes the one **closest** to `base_reset`, preferring the later cut on an exact tie — but
only if the reset animation still fits entirely before the next zoom starts. With no candidate
it resets directly at `base_reset`; the lookback never moves a reset on its own.

**Promotions are earned by sustained speech, not by cuts.** Once a burst is zoomed, the level
climbs on elapsed talking time alone: `promote_to_face_x2_after_ms` into the burst, then
`promote_to_face_x3_after_ms`. Each promotion additionally requires that enough burst *remains*
(`min_remaining_after_face_x2_ms` / `..._x3_ms`), so a burst that stops a heartbeat after
crossing a threshold does not flash a tighter level nobody can read. Promotions are deliberately
**not** cut-snapped: measurement on `DAZ_OUTPUT_MVP2` found 1 of 6 manual promotions on a hard
cut, which is chance, while 8 of 14 manual resets sit on one (D047). Snapping stays where the
evidence is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from davinci_auto_zoom.domain.models import (
    Frame,
    FrameRange,
    SpeechSegment,
    normalize_speech_segments,
)
from davinci_auto_zoom.domain.transitions import (
    BY_ROLE,
    FACECAM_LADDER,
    REQUIRED_ROLES,
    ROLE_X0_TO_FACE_X1,
    ROLES,
    STATE_FACE_X1,
    Transition,
    promotion_from,
    reset_from,
)

#: Placement reasons. Short tokens: they are a table column and a JSON field, not prose.
REASON_X1_UNTIL_DIRECT_RESET = "x1_until_direct_reset"
REASON_X1_UNTIL_CUT_RESET = "x1_until_cut_snapped_reset"
REASON_X1_HELD_TO_TIMELINE_END = "x1_held_to_timeline_end"
REASON_PROMOTED_SUSTAINED = "promoted_sustained_speech"
REASON_RESET_DIRECT = "reset_direct"
REASON_RESET_SNAPPED_FORWARD = "reset_cut_snap_forward"
REASON_RESET_SNAPPED_BACKWARD = "reset_cut_snap_backward"


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
    #: How far *before* it. Small on purpose: a VAD end is a few frames late relative to the
    #: perceptual end of a phrase, so the cut an editor would use often sits just before it.
    #: Only ever used to snap onto a real cut — never to move a reset earlier by itself.
    cut_snap_lookback_ms: int = 120

    # --- facecam level promotions (Phase 8) --------------------------------------------
    # Talking time, measured from the start of the burst's zoom, that earns the next rung of
    # the ladder; and how much burst must still be left for that rung to be worth taking.
    # Defaults are calibrated on DAZ_OUTPUT_MVP2, not invented — see
    # .agent/reports/phase-08-mvp2-analysis.txt and D047.
    promote_to_face_x2_after_ms: int = 1000
    min_remaining_after_face_x2_ms: int = 350
    promote_to_face_x3_after_ms: int = 1800
    min_remaining_after_face_x3_ms: int = 500

    def __post_init__(self) -> None:
        for name in PLANNER_SETTING_KEYS:
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.promote_to_face_x3_after_ms <= self.promote_to_face_x2_after_ms:
            raise ValueError(
                "promote_to_face_x3_after_ms must be greater than "
                "promote_to_face_x2_after_ms: the ladder is climbed one rung at a time, so "
                "x3 cannot be earned before x2"
            )

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in PLANNER_SETTING_KEYS}

    def promotion_after_ms(self, transition: Transition) -> int:
        """Talking time that earns `transition`, by the state it leads to."""

        return {
            FACECAM_LADDER[1]: self.promote_to_face_x2_after_ms,
            FACECAM_LADDER[2]: self.promote_to_face_x3_after_ms,
        }[transition.to_state]

    def min_remaining_ms(self, transition: Transition) -> int:
        """Burst that must still be ahead for `transition` to be worth placing."""

        return {
            FACECAM_LADDER[1]: self.min_remaining_after_face_x2_ms,
            FACECAM_LADDER[2]: self.min_remaining_after_face_x3_ms,
        }[transition.to_state]


#: Field order is the config's key order, and the only list of them. Adding a setting in one
#: place and forgetting the validation loop is exactly the bug this avoids.
PLANNER_SETTING_KEYS: tuple[str, ...] = (
    "reset_after_silence_ms",
    "zoom_lead_in_ms",
    "zoom_lead_out_ms",
    "cut_snap_window_ms",
    "cut_snap_lookback_ms",
    "promote_to_face_x2_after_ms",
    "min_remaining_after_face_x2_ms",
    "promote_to_face_x3_after_ms",
    "min_remaining_after_face_x3_ms",
)


@dataclass(frozen=True, slots=True, init=False)
class AssetTiming:
    """How many frames each transition asset needs to finish its own animation.

    User metadata about user-built assets, in **frames**, because that is how the keyframes
    were authored. DAZ never opens the Fusion graph to discover these (D007): it is told.

    Emphatically *not* the Media Pool item's native duration. `X1_TO_X0` is 42 frames long in
    this user's bin and completes its move in 15; the planner needs the 15.

    The key set is also the planner's **capability list**: a transition with no timing here is
    a transition this user has not built an asset for, and the planner simply never places it.
    That is how "no x2/x3 configured" stays a configuration fact rather than a code path.
    """

    #: Sorted `(role, frames)` pairs — a tuple so the whole dataclass stays frozen and
    #: hashable, and so `to_dict()` is byte-stable for the plan fingerprint.
    transition_frames: tuple[tuple[str, int], ...]

    def __init__(self, transition_frames: Mapping[str, int]) -> None:
        pairs = tuple(sorted((str(k), int(v)) for k, v in transition_frames.items()))
        object.__setattr__(self, "transition_frames", pairs)

        unknown = sorted({role for role, _ in pairs} - set(ROLES))
        if unknown:
            raise ValueError(
                f"unknown transition role(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(ROLES)}"
            )
        missing = [role for role in REQUIRED_ROLES if role not in dict(pairs)]
        if missing:
            raise ValueError(
                f"transition role(s) {', '.join(missing)} have no animation length. "
                "Without them there is no zoom at all."
            )
        for role, frames in pairs:
            if frames < 1:
                raise ValueError(f"{role} animation length must be >= 1 frame, got {frames}")

    def __contains__(self, role: object) -> bool:
        return any(role == known for known, _ in self.transition_frames)

    def frames_for(self, role: str) -> int:
        """Animation length of one role. Raises for a role this config does not provide."""

        for known, frames in self.transition_frames:
            if known == role:
                return frames
        raise KeyError(f"no animation length configured for transition role {role!r}")

    @property
    def max_reset_frames(self) -> int:
        """Longest configured reset animation.

        The reset-fitting check runs *before* the planner knows which level the burst will
        reach, so it budgets for the longest reset it could need. Budgeting high is safe: the
        reset actually placed is never longer than this, so it always fits too.
        """

        return max(
            frames
            for role, frames in self.transition_frames
            if BY_ROLE[role].is_reset
        )

    def to_dict(self) -> dict[str, int]:
        return dict(self.transition_frames)


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
class AssetIdentity:
    """Which Media Pool item a role actually resolved to, by every stable id available.

    The configured name alone is a weak identity: two runs can find a *different* clip under
    the same name after a re-import or a bin edit. Phase 5 compares these before writing.
    """

    role: str
    name: str
    media_id: str | None = None
    unique_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "name": self.name,
            "media_id": self.media_id,
            "unique_id": self.unique_id,
        }


@dataclass(frozen=True, slots=True)
class PlanSource:
    """Everything an executor needs to check that this plan still fits reality.

    Recorded by the planner, validated by the executor before it is allowed to write
    (`domain/plan_validation.py`). The scalar fields identify *which* material was planned
    against; `structural_fingerprint` proves that material has not been re-cut since.
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
    #: The Media Pool items the roles resolved to, with their stable ids.
    asset_identities: tuple[AssetIdentity, ...] = ()
    #: `domain.fingerprint.source_fingerprint` of the voice/cut structure that was read.
    structural_fingerprint: str = ""

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
            "asset_identities": [identity.to_dict() for identity in self.asset_identities],
            "structural_fingerprint": self.structural_fingerprint,
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
    def role_counts(self) -> dict[str, int]:
        """How many instances of each configurable role the plan places, roles in graph order.

        Every role appears, including the ones this run placed zero of: a report where a
        missing line and a zero line look the same is a report that hides a regression.
        """

        return {role: len(self.of_role(role)) for role in ROLES}

    @property
    def zoom_placements(self) -> tuple[AssetPlacement, ...]:
        """Entries and promotions — everything that leaves the picture zoomed in."""

        return tuple(p for p in self.placements if not BY_ROLE[p.asset_role].is_reset)

    @property
    def reset_placements(self) -> tuple[AssetPlacement, ...]:
        """Returns to X0, whichever level they came down from."""

        return tuple(p for p in self.placements if BY_ROLE[p.asset_role].is_reset)

    @property
    def entry_placements(self) -> tuple[AssetPlacement, ...]:
        """One per zoom cycle: the `X0 -> FACE_X1` move that opens it."""

        return self.of_role(ROLE_X0_TO_FACE_X1)

    @property
    def promotion_placements(self) -> tuple[AssetPlacement, ...]:
        """Ladder climbs. Zero of these is the Phase 7 behaviour, and still valid."""

        return tuple(p for p in self.placements if BY_ROLE[p.asset_role].is_promotion)

    @property
    def top_state_counts(self) -> dict[str, int]:
        """How many zoom cycles peaked at each facecam level.

        A cycle is identified by its `burst_index`, and its peak is the last state any of its
        zoom placements reaches — which is exactly the level its reset asset has to come down
        from.
        """

        peaks: dict[int, str] = {}
        for placement in self.zoom_placements:
            transition = BY_ROLE[placement.asset_role]
            current = peaks.get(placement.burst_index)
            if current is None or FACECAM_LADDER.index(transition.to_state) > FACECAM_LADDER.index(
                current
            ):
                peaks[placement.burst_index] = transition.to_state
        counts = dict.fromkeys(FACECAM_LADDER, 0)
        for state in peaks.values():
            counts[state] += 1
        return counts

    @property
    def direct_resets(self) -> int:
        return sum(1 for p in self.reset_placements if p.reason == REASON_RESET_DIRECT)

    @property
    def forward_snapped_resets(self) -> int:
        return sum(
            1 for p in self.reset_placements if p.reason == REASON_RESET_SNAPPED_FORWARD
        )

    @property
    def backward_snapped_resets(self) -> int:
        return sum(
            1 for p in self.reset_placements if p.reason == REASON_RESET_SNAPPED_BACKWARD
        )

    @property
    def snapped_resets(self) -> int:
        """Total cut-snapped resets, kept as the sum of the two directions."""

        return self.forward_snapped_resets + self.backward_snapped_resets

    @property
    def zoomed_frames(self) -> int:
        """Frames spent away from X0. Entry and promotion clips are adjacent and cover the
        whole cycle between them, so summing them is the held time, not a double count."""

        return sum(p.duration_frames for p in self.zoom_placements)

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
                "role_counts": self.role_counts,
                "top_state_counts": self.top_state_counts,
                "zoom_cycles": len(self.entry_placements),
                "promotions": len(self.promotion_placements),
                "direct_resets": self.direct_resets,
                "cut_snapped_resets": self.snapped_resets,
                "forward_snapped_resets": self.forward_snapped_resets,
                "backward_snapped_resets": self.backward_snapped_resets,
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
            f"cut_snap=[-{self.settings.cut_snap_lookback_ms}ms, "
            f"+{self.settings.cut_snap_window_ms}ms] (nearest cut to the reset anchor wins)",
            f"  promote   : x2 after {self.settings.promote_to_face_x2_after_ms}ms "
            f"(>={self.settings.min_remaining_after_face_x2_ms}ms left), "
            f"x3 after {self.settings.promote_to_face_x3_after_ms}ms "
            f"(>={self.settings.min_remaining_after_face_x3_ms}ms left); never cut-snapped",
            "  assets    : "
            + ", ".join(f"{role}={frames}f" for role, frames in self.timing.transition_frames)
            + " (native Media Pool lengths are irrelevant here)",
            "",
            "  role                 start        end   frames  reason                      cut",
        ]
        for placement in self.placements:
            cut = "-" if placement.cut_frame is None else str(placement.cut_frame)
            lines.append(
                f"  {placement.asset_role:19} {placement.start_frame:>9} "
                f"{placement.end_frame:>10} {placement.duration_frames:>8}  "
                f"{placement.reason:26} {cut}"
            )
        peaks = self.top_state_counts
        lines.extend(
            [
                "",
                f"  speech segments : {len(self.speech_segments)}",
                f"  editorial bursts: {len(self.bursts)} "
                f"({self.merged_gaps} pause(s) bridged)",
                f"  zoom cycles     : {len(self.entry_placements)} "
                + ", ".join(f"peaking at {state}: {count}" for state, count in peaks.items()),
                "  placements      : "
                + ", ".join(f"{role}={count}" for role, count in self.role_counts.items()),
                f"  resets          : {len(self.reset_placements)} "
                f"({self.direct_resets} direct, {self.snapped_resets} cut-snapped = "
                f"{self.backward_snapped_resets} backward + "
                f"{self.forward_snapped_resets} forward)",
                f"  suppressed      : {self.suppressed_resets} reset(s) with no room, "
                f"{self.suppressed_cycles} cycle(s) shorter than the entry animation, "
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
    #: Usable cuts that lost the proximity ranking, nearest first. Trace material only.
    runners_up: tuple[Frame, ...] = ()


def _reset_reason(cut_frame: Frame | None, base_reset: Frame) -> str:
    """Which of the three reset outcomes this placement is, as a trace token."""

    if cut_frame is None:
        return REASON_RESET_DIRECT
    if cut_frame < base_reset:
        return REASON_RESET_SNAPPED_BACKWARD
    return REASON_RESET_SNAPPED_FORWARD


def _choose_reset(
    base_reset: Frame,
    floor: Frame,
    limit: Frame,
    cuts: Sequence[Frame],
    snap_window: int,
    lookback: int,
    x0_frames: int,
) -> _ResetChoice:
    """Where the reset goes: the usable hard cut nearest `base_reset`, else the direct point.

    Candidates live in the asymmetric window `[base_reset - lookback, base_reset + window]`
    and are ranked by `abs(cut - base_reset)`, a later cut winning an exact tie so the bias
    stays towards "after the speech". `floor` is where the x1 for this burst begins: a cut at
    or before it would leave no room for the zoom-in itself.

    `limit` is the frame the next zoom starts at (or the end of the timeline). A reset at `R`
    is only usable when the whole x0 animation fits before it: `R + x0_frames <= limit`. That
    is the *only* length rule — the asset's native Media Pool duration plays no part.
    """

    candidates = sorted(
        (c for c in cuts if base_reset - lookback <= c <= base_reset + snap_window and c > floor),
        key=lambda c: (abs(c - base_reset), -c),
    )
    usable = [c for c in candidates if c + x0_frames <= limit]
    rejected = tuple(sorted(c for c in candidates if c not in usable))
    if usable:
        return _ResetChoice(usable[0], usable[0], rejected, tuple(usable[1:]))
    if base_reset + x0_frames <= limit:
        return _ResetChoice(base_reset, None, rejected)
    return _ResetChoice(None, None, rejected)


@dataclass(frozen=True, slots=True)
class _ZoomChain:
    """The zoom-in side of one cycle: adjacent transition clips, and where they end up."""

    #: `(role, start_frame)` in placement order, entry first. Never empty.
    steps: tuple[tuple[str, Frame], ...]
    top_state: str
    #: Trace lines explaining every promotion taken and the first one refused.
    decisions: tuple[str, ...]


def _zoom_chain(
    *,
    burst_index: int,
    open_start: Frame,
    reset: Frame,
    timing: AssetTiming,
    settings: PlannerSettings,
    frame_rate: Fraction,
) -> _ZoomChain:
    """Climb the facecam ladder for one cycle, on elapsed talking time alone.

    Deterministic and total: the chain always starts with the entry transition, and stops at
    the first rung that fails any of four independent conditions — the user has no asset for
    it, the burst has not lasted long enough to earn it, too little burst remains for it to be
    readable, or placing it would leave a clip too short to finish its own animation. Because
    it stops rather than skips, `FACE_X3` can never appear without `FACE_X2` before it.
    """

    steps: list[tuple[str, Frame]] = [(ROLE_X0_TO_FACE_X1, open_start)]
    decisions: list[str] = []
    state = STATE_FACE_X1

    while (step := promotion_from(state)) is not None:
        if step.role not in timing:
            decisions.append(
                f"burst {burst_index}: no {step.to_state} — role {step.role!r} has no "
                "configured asset animation, so this project cannot make that move"
            )
            break
        after = frames_from_ms(settings.promotion_after_ms(step), frame_rate)
        remaining_needed = frames_from_ms(settings.min_remaining_ms(step), frame_rate)
        start = open_start + after
        remaining = reset - start
        if remaining < remaining_needed:
            decisions.append(
                f"burst {burst_index}: no {step.to_state} — the burst would reach the "
                f"{after}-frame promotion point at {start} with only {remaining} frame(s) "
                f"left before the reset at {reset}, and {remaining_needed} are required for "
                "the tighter level to be worth reading"
            )
            break
        previous_role, previous_start = steps[-1]
        held = start - previous_start
        if held < timing.frames_for(previous_role):
            decisions.append(
                f"burst {burst_index}: no {step.to_state} — it would cut {previous_role} "
                f"down to {held} frame(s), shorter than its own "
                f"{timing.frames_for(previous_role)}-frame animation"
            )
            break
        if remaining < timing.frames_for(step.role):
            decisions.append(
                f"burst {burst_index}: no {step.to_state} — {remaining} frame(s) remain and "
                f"{step.role} needs {timing.frames_for(step.role)} to finish its own move"
            )
            break
        decisions.append(
            f"burst {burst_index}: promoted to {step.to_state} at {start} "
            f"({after} frames of sustained speech after the zoom opened at {open_start}), "
            f"held for {remaining} frame(s) until the reset at {reset}"
        )
        steps.append((step.role, start))
        state = step.to_state

    return _ZoomChain(tuple(steps), state, tuple(decisions))


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
    """Turn speech facts into transition placements. Pure and deterministic."""

    settings = settings or PlannerSettings()
    reset_gate = frames_from_ms(settings.reset_after_silence_ms, frame_rate)
    lead_in = frames_from_ms(settings.zoom_lead_in_ms, frame_rate)
    lead_out = frames_from_ms(settings.zoom_lead_out_ms, frame_rate)
    snap_window = frames_from_ms(settings.cut_snap_window_ms, frame_rate)
    lookback = frames_from_ms(settings.cut_snap_lookback_ms, frame_rate)
    x1_min = timing.frames_for(ROLE_X0_TO_FACE_X1)
    # Budgeted before the level is known; see `AssetTiming.max_reset_frames`.
    x0_frames = timing.max_reset_frames
    cuts = hard_cuts_in_range(hard_cuts, timeline)

    decisions: list[str] = [
        f"timeline [{timeline.start}, {timeline.end}) = {timeline.duration} frames "
        f"@ {float(frame_rate):.6f} fps",
        f"gate reset_after_silence={settings.reset_after_silence_ms}ms = {reset_gate} frames; "
        f"lead_in={lead_in}f lead_out={lead_out}f "
        f"cut snap window=[-{lookback}f, +{snap_window}f] around the reset anchor "
        "(nearest cut wins, forward on a tie); "
        "transition animations "
        + " ".join(f"{role}={frames}f" for role, frames in timing.transition_frames),
        "promotion ladder: "
        + " -> ".join(FACECAM_LADDER)
        + f"; x2 after {settings.promote_to_face_x2_after_ms}ms with "
        f">={settings.min_remaining_after_face_x2_ms}ms left, "
        f"x3 after {settings.promote_to_face_x3_after_ms}ms with "
        f">={settings.min_remaining_after_face_x3_ms}ms left; promotions are never cut-snapped",
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
        choice = _choose_reset(
            base_reset, open_start, limit, cuts, snap_window, lookback, x0_frames
        )
        rejected_cuts += len(choice.rejected_cuts)
        for cut in choice.rejected_cuts:
            decisions.append(
                f"burst {index}: hard cut at {cut} ({cut - base_reset:+d}f) rejected — only "
                f"{limit - cut} frame(s) left before "
                f"{'the timeline end' if is_last else f'the next zoom at {limit}'}, "
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
            delta = choice.cut_frame - base_reset
            others = (
                ""
                if not choice.runners_up
                else "; farther candidate(s) "
                + ", ".join(
                    f"{c} ({c - base_reset:+d}f)" for c in choice.runners_up
                )
            )
            decisions.append(
                f"burst {index}: reset snapped "
                f"{'backward' if delta < 0 else 'forward'} from {base_reset} to hard cut "
                f"{choice.cut_frame} (delta={delta:+d}f, burst end {burst.end}, window "
                f"[-{lookback}f, +{snap_window}f], nearest cut to the anchor){others}"
            )
        else:
            decisions.append(
                f"burst {index}: direct reset at {base_reset} "
                f"(burst end {burst.end} + lead-out {lead_out}); no usable hard cut in the "
                f"[-{lookback}f, +{snap_window}f] window"
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

        chain = _zoom_chain(
            burst_index=open_index,
            open_start=open_start,
            reset=reset,
            timing=timing,
            settings=settings,
            frame_rate=frame_rate,
        )
        decisions.extend(chain.decisions)
        entry_reason = (
            REASON_X1_UNTIL_CUT_RESET
            if choice.cut_frame is not None
            else REASON_X1_UNTIL_DIRECT_RESET
        )
        # Each clip holds its state until the next one takes over; the last one holds it to
        # the reset. This is the whole reason a promotion needs no explicit end frame.
        boundaries = [start for _, start in chain.steps[1:]] + [reset]
        for position, ((role, start), end) in enumerate(
            zip(chain.steps, boundaries, strict=True)
        ):
            placements.append(
                AssetPlacement(
                    asset_role=role,
                    frames=FrameRange(start, end),
                    # Only the entry clip is the one the reset decision was about; a promotion
                    # was earned by speech and carries no cut.
                    reason=entry_reason if position == 0 else REASON_PROMOTED_SUSTAINED,
                    cut_frame=choice.cut_frame if position == 0 else None,
                    burst_index=open_index,
                    burst_count=index - open_index + 1,
                )
            )
        back = reset_from(chain.top_state)
        placements.append(
            AssetPlacement(
                asset_role=back.role,
                frames=FrameRange(reset, reset + timing.frames_for(back.role)),
                reason=_reset_reason(choice.cut_frame, base_reset),
                cut_frame=choice.cut_frame,
                burst_index=index,
            )
        )
        decisions.append(
            f"burst {index}: reset from {chain.top_state} uses {back.role} "
            f"({timing.frames_for(back.role)} frames)"
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
            # No reset here, so the chain simply holds to the end of the range. Promotions
            # still apply: a burst that runs out the timeline earned its levels like any other.
            chain = _zoom_chain(
                burst_index=open_index,
                open_start=open_start,
                reset=timeline.end,
                timing=timing,
                settings=settings,
                frame_rate=frame_rate,
            )
            decisions.extend(chain.decisions)
            boundaries = [start for _, start in chain.steps[1:]] + [timeline.end]
            for position, ((role, start), end) in enumerate(
                zip(chain.steps, boundaries, strict=True)
            ):
                placements.append(
                    AssetPlacement(
                        asset_role=role,
                        frames=FrameRange(start, end),
                        reason=(
                            REASON_X1_HELD_TO_TIMELINE_END
                            if position == 0
                            else REASON_PROMOTED_SUSTAINED
                        ),
                        burst_index=open_index,
                        burst_count=len(bursts) - open_index,
                    )
                )
            decisions.append(
                f"zoom [{span.start}, {span.end}) is held to the timeline end at "
                f"{chain.top_state}: the reset is not truncated and nothing is placed past "
                "the range"
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
    "REASON_PROMOTED_SUSTAINED",
    "REASON_RESET_DIRECT",
    "REASON_RESET_SNAPPED_BACKWARD",
    "REASON_RESET_SNAPPED_FORWARD",
    "REASON_X1_HELD_TO_TIMELINE_END",
    "REASON_X1_UNTIL_CUT_RESET",
    "REASON_X1_UNTIL_DIRECT_RESET",
    "AssetIdentity",
    "AssetPlacement",
    "AssetTiming",
    "PlanSource",
    "PlannerSettings",
    "ZoomPlan",
    "frames_from_ms",
    "hard_cuts_in_range",
    "plan_zooms",
]
