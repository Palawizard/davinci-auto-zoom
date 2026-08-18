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
the move never completes. Symmetrically a reset asset (`X1_TO_X0` and its x2/x3 twins) only
needs its animation length to do its whole job; the Media Pool item's *native* length (42
frames for this user) is a property of the asset file, not a minimum the planner has to
honour. Nothing here reads it.

**Every transition prefers a real cut, and the nearest one.** When the edit cuts around the
same moment a transition happens, landing on that cut looks intentional. So every facecam
transition has a *raw audio anchor* — burst start for the entry, a recovery cue for a
promotion, burst end for the reset — and may move to a hard cut inside an asymmetric window
around it. Among the candidates the planner takes the one **closest** to the anchor, preferring
the later cut on an exact tie, and only if the whole chain still holds together (order,
animations, no overlap). With no candidate the transition stays exactly on its raw anchor: a
cut never creates a transition and never moves one on its own (D052).

The two windows are different because they were measured separately. The reset's
`[-120 ms, +350 ms]` is Phase 6's (the cut an editor uses sits 4-7 frames *before* a VAD end,
never after). The zoom-in window is symmetric and small, `[-120 ms, +120 ms]`: on
`DAZ_OUTPUT_MVP2` four manual entries sit exactly on a cut with the burst start 0-2 frames
away, and the nearest *non*-matching cut to any burst start is 62 frames away — an 8x margin.

**Promotions are earned by voice dynamics, not by elapsed time.** Phase 8's
`promote_to_face_x2_after_ms` / `..._x3_ms` are superseded (D049). Inside a burst the planner
reads the energy envelope's valleys (`domain/dynamics.py`): a dip in the voice followed by a
clear pick-up is a **promotion cue**, anchored on the pick-up. The first usable cue earns
`face_x2`, the second `face_x3`, and any further cue is ignored — the ladder tops out and
stays there until the reset. A cue is only usable if the previous transition has finished its
animation, and if the level it opens can be held long enough to be read.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from davinci_auto_zoom.domain.dynamics import (
    EnergyEnvelope,
    EnergySettings,
    VoiceValley,
    voice_valleys,
)
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
    promotion_from,
    reset_from,
)

#: Placement reasons. Short tokens: they are a table column and a JSON field, not prose.
#: Each names what decided the placement's own START frame, so entry, promotion and reset all
#: read the same way: direct on the audio anchor, or snapped to a cut in one direction.
REASON_ENTRY_DIRECT = "entry_direct"
REASON_ENTRY_SNAPPED_FORWARD = "entry_cut_snap_forward"
REASON_ENTRY_SNAPPED_BACKWARD = "entry_cut_snap_backward"
REASON_X1_HELD_TO_TIMELINE_END = "x1_held_to_timeline_end"
REASON_PROMOTED_DIRECT = "promoted_voice_recovery"
REASON_PROMOTED_SNAPPED_FORWARD = "promoted_cut_snap_forward"
REASON_PROMOTED_SNAPPED_BACKWARD = "promoted_cut_snap_backward"
REASON_RESET_DIRECT = "reset_direct"
REASON_RESET_SNAPPED_FORWARD = "reset_cut_snap_forward"
REASON_RESET_SNAPPED_BACKWARD = "reset_cut_snap_backward"

#: Why a valley that the *signal* qualified was still not used as a promotion. The signal's
#: own rejection reasons live in `domain/dynamics.py`; these are the edit's.
CUE_REJECTED_ANIMATION = "previous_animation_unfinished"
CUE_REJECTED_NO_ROOM = "no_room_before_reset"
CUE_REJECTED_AT_TOP = "already_at_face_x3"
CUE_REJECTED_NO_ASSET = "no_asset_for_next_level"


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
    #: The same idea for every zoom-IN anchor (entry and promotions), symmetric and small.
    #: A zoom-in anchor has no systematic bias the way a VAD end does, so there is no reason
    #: for the window to lean one way; 120 ms = 7 frames at 60 fps, measured in D052.
    zoom_cut_snap_window_ms: int = 120
    zoom_cut_snap_lookback_ms: int = 120

    # --- facecam level promotions (Phase 8c) -------------------------------------------
    # A promotion is earned by a dip in the voice followed by a clear pick-up, never by
    # elapsed time (D049). These four numbers describe what counts as such a dip; they are
    # relative to the burst's own voice level, so a change of microphone gain does not move
    # them. Calibrated on DAZ_OUTPUT_MVP2 — see .agent/reports/phase-08c-voice-dynamics-
    # analysis.txt and D051.
    promotion_min_drop_db: int = 20
    promotion_recovery_within_db: int = 6
    promotion_min_valley_ms: int = 30
    promotion_max_valley_ms: int = 650
    #: How long the level a promotion opens must survive, on top of its own animation. This
    #: is editorial and measured: no manual promotion in `DAZ_OUTPUT_MVP2` is held for fewer
    #: than 27 frames (450 ms), so 400 ms = 24 frames sits just below every hold the editor
    #: actually made while still refusing a level that would flash and read as a glitch. It is
    #: NOT Phase 8's `min_remaining_*`, which gated on time since the *burst* started.
    promotion_min_hold_ms: int = 400

    def __post_init__(self) -> None:
        for name in PLANNER_SETTING_KEYS:
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.promotion_max_valley_ms < self.promotion_min_valley_ms:
            raise ValueError(
                "promotion_max_valley_ms must be >= promotion_min_valley_ms, otherwise no "
                "valley can ever qualify"
            )
        if self.promotion_recovery_within_db >= self.promotion_min_drop_db:
            raise ValueError(
                "promotion_recovery_within_db must be < promotion_min_drop_db: the voice has "
                "to climb back above the line it fell under, or every valley recovers on its "
                "own first sample"
            )

    def to_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in PLANNER_SETTING_KEYS}


#: Field order is the config's key order, and the only list of them. Adding a setting in one
#: place and forgetting the validation loop is exactly the bug this avoids.
PLANNER_SETTING_KEYS: tuple[str, ...] = (
    "reset_after_silence_ms",
    "zoom_lead_in_ms",
    "zoom_lead_out_ms",
    "cut_snap_window_ms",
    "cut_snap_lookback_ms",
    "zoom_cut_snap_window_ms",
    "zoom_cut_snap_lookback_ms",
    "promotion_min_drop_db",
    "promotion_recovery_within_db",
    "promotion_min_valley_ms",
    "promotion_max_valley_ms",
    "promotion_min_hold_ms",
)

#: Phase 8's duration-based promotion knobs. They no longer decide anything and are not
#: silently accepted: a config still carrying them gets an error naming what replaced them
#: (D049), because ignoring them would leave a user believing they still tune the edit.
SUPERSEDED_PLANNER_KEYS: dict[str, str] = {
    "promote_to_face_x2_after_ms": "promotion_min_drop_db / promotion_min_valley_ms",
    "min_remaining_after_face_x2_ms": "promotion_min_hold_ms",
    "promote_to_face_x3_after_ms": "promotion_min_drop_db / promotion_min_valley_ms",
    "min_remaining_after_face_x3_ms": "promotion_min_hold_ms",
}


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
    #: How the energy envelope was built. Part of the plan's identity because it changes the
    #: promotion cues, and therefore the plan (D051).
    energy_settings: EnergySettings = field(default_factory=EnergySettings)
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
            "energy_settings": self.energy_settings.to_dict(),
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
    #: Every valley the envelope produced inside a burst, used or not, with the reason.
    valleys: tuple[VoiceValley, ...] = ()
    #: One outcome per entry of `valleys`, in the same order: the state it opened
    #: (`face_x2` / `face_x3`), the signal's own rejection, or the edit's.
    cue_outcomes: tuple[str, ...] = ()
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
            # Only rungs of the ladder have a height; a reset lands at X0 and ranks
            # against nothing.
            if transition.to_state not in FACECAM_LADDER:
                continue
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

    def _snap_counts(self, direct: str, backward: str, forward: str) -> dict[str, int]:
        reasons = [p.reason for p in self.placements]
        return {
            "direct": reasons.count(direct),
            "backward": reasons.count(backward),
            "forward": reasons.count(forward),
        }

    @property
    def entry_snaps(self) -> dict[str, int]:
        """How the `x0 -> face_x1` starts were decided: raw anchor, or a cut either side."""

        return self._snap_counts(
            REASON_ENTRY_DIRECT, REASON_ENTRY_SNAPPED_BACKWARD, REASON_ENTRY_SNAPPED_FORWARD
        )

    @property
    def promotion_snaps(self) -> dict[str, int]:
        """The same, for every ladder climb."""

        return self._snap_counts(
            REASON_PROMOTED_DIRECT,
            REASON_PROMOTED_SNAPPED_BACKWARD,
            REASON_PROMOTED_SNAPPED_FORWARD,
        )

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
            "voice_valleys": [
                dict(valley.to_dict(), outcome=outcome)
                for valley, outcome in zip(self.valleys, self.cue_outcomes, strict=False)
            ],
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
                "entry_snaps": self.entry_snaps,
                "promotion_snaps": self.promotion_snaps,
                "valleys_found": len(self.valleys),
                "cues_used": sum(1 for o in self.cue_outcomes if o in FACECAM_LADDER),
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
            f"  zoom snap : [-{self.settings.zoom_cut_snap_lookback_ms}ms, "
            f"+{self.settings.zoom_cut_snap_window_ms}ms] around every zoom-in anchor",
            f"  promote   : voice valley >={self.settings.promotion_min_drop_db}dB deep, "
            f"{self.settings.promotion_min_valley_ms}-{self.settings.promotion_max_valley_ms}ms "
            f"long, recovering to within {self.settings.promotion_recovery_within_db}dB of the "
            f"burst's voice level and held >={self.settings.promotion_min_hold_ms}ms",
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
                f"  entries         : {len(self.entry_placements)} "
                f"({self.entry_snaps['direct']} direct, "
                f"{self.entry_snaps['backward']} backward + "
                f"{self.entry_snaps['forward']} forward cut-snapped)",
                f"  promotions      : {len(self.promotion_placements)} from "
                f"{len(self.valleys)} valley(s) "
                f"({self.promotion_snaps['direct']} direct, "
                f"{self.promotion_snaps['backward']} backward + "
                f"{self.promotion_snaps['forward']} forward cut-snapped)",
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
        if self.valleys:
            lines.append("")
            lines.append(
                "  voice dynamics — every valley found inside a zoom cycle, and what it did:"
            )
            lines.append(
                "    burst    valley        low  drop   rec  ms  recovery  outcome"
            )
            for valley, outcome in zip(self.valleys, self.cue_outcomes, strict=False):
                recovery = "-" if valley.recovery_frame is None else str(valley.recovery_frame)
                lines.append(
                    f"    {valley.burst_index:>5}  [{valley.low.start},{valley.low.end})"
                    f" {valley.low_frame:>10} {valley.drop_db:5.1f} {valley.recovery_db:5.1f} "
                    f"{valley.valley_ms:>3}  {recovery:>8}  {outcome}"
                )
        lines.append("")
        lines.append("  decision trace:")
        lines.extend(f"    {decision}" for decision in self.decisions)
        return "\n".join(lines)


def hard_cuts_in_range(cuts: Iterable[Frame], timeline: FrameRange) -> tuple[Frame, ...]:
    """Sorted, unique cuts that actually fall inside the analysed range."""

    return tuple(sorted({c for c in cuts if timeline.start <= c <= timeline.end}))


@dataclass(frozen=True, slots=True)
class Snap:
    """The outcome of trying to put one transition's start on a hard cut."""

    #: Where the transition actually starts: a cut, or the raw anchor when none was usable.
    frame: Frame
    #: The cut it landed on, or None when it stayed on its raw anchor.
    cut_frame: Frame | None
    #: In-window cuts the chain constraints refused. Reported, never silently dropped.
    rejected_cuts: tuple[Frame, ...] = ()
    #: Usable cuts that lost the proximity ranking, nearest first. Trace material only.
    runners_up: tuple[Frame, ...] = ()


def snap_to_cut(
    anchor: Frame,
    cuts: Sequence[Frame],
    *,
    lookback: int,
    forward: int,
    usable: Callable[[Frame], bool],
    eligible: Callable[[Frame], bool] | None = None,
) -> Snap:
    """Nearest usable hard cut to `anchor`, else `anchor` itself. One rule for every class.

    Candidates live in `[anchor - lookback, anchor + forward]` and are ranked by
    `abs(cut - anchor)`, a later cut winning an exact tie. `eligible` narrows the window
    silently (a cut that was never a candidate is not a rejection); `usable` is the chain
    constraint — order, animations, no overlap — and a cut it refuses is reported, so a nearer
    but unusable cut gives way to the next valid one rather than to nothing (D052).
    """

    candidates = sorted(
        (
            c
            for c in cuts
            if anchor - lookback <= c <= anchor + forward and (eligible is None or eligible(c))
        ),
        key=lambda c: (abs(c - anchor), -c),
    )
    ok = [c for c in candidates if usable(c)]
    rejected = tuple(sorted(c for c in candidates if c not in ok))
    if ok:
        return Snap(ok[0], ok[0], rejected, tuple(ok[1:]))
    return Snap(anchor, None, rejected)


def _snap_reason(cut_frame: Frame | None, anchor: Frame, direct: str, back: str, ahead: str) -> str:
    """Which of the three snapping outcomes a placement is, as a trace token."""

    if cut_frame is None:
        return direct
    return back if cut_frame < anchor else ahead


def _choose_reset(
    base_reset: Frame,
    floor: Frame,
    limit: Frame,
    cuts: Sequence[Frame],
    snap_window: int,
    lookback: int,
    x0_frames: int,
) -> Snap | None:
    """Where the reset goes: the usable hard cut nearest `base_reset`, else the direct point.

    `floor` is the earliest frame this cycle's zoom could start at: a cut at or before it
    would leave no room for the zoom-in itself. `limit` is the frame the next zoom starts at
    (or the end of the timeline); a reset at `R` is only usable when the whole x0 animation
    fits before it: `R + x0_frames <= limit`. That is the *only* length rule — the asset's
    native Media Pool duration plays no part. `None` means no reset fits at all.
    """

    snap = snap_to_cut(
        base_reset,
        cuts,
        lookback=lookback,
        forward=snap_window,
        eligible=lambda c: c > floor,
        usable=lambda c: c + x0_frames <= limit,
    )
    if snap.cut_frame is not None:
        return snap
    if base_reset + x0_frames <= limit:
        return snap
    return None


@dataclass(frozen=True, slots=True)
class _ZoomChain:
    """The zoom-in side of one cycle: adjacent transition clips, and where they end up."""

    #: `(role, start_frame, cut_frame, reason)` in placement order, entry first. Never empty.
    #: The entry's reason is a placeholder the caller replaces once it knows how the entry
    #: itself was snapped.
    steps: tuple[tuple[str, Frame, Frame | None, str], ...]
    top_state: str
    #: Trace lines explaining every promotion taken and every cue refused.
    decisions: tuple[str, ...]
    #: One outcome token per valley given to the chain, in the same order.
    outcomes: tuple[str, ...]
    rejected_cuts: int = 0


def _zoom_chain(
    *,
    burst_index: int,
    open_start: Frame,
    open_cut: Frame | None,
    reset: Frame,
    timing: AssetTiming,
    settings: PlannerSettings,
    frame_rate: Fraction,
    valleys: Sequence[VoiceValley],
    cuts: Sequence[Frame],
) -> _ZoomChain:
    """Climb the facecam ladder for one cycle, one qualifying voice recovery at a time.

    Deterministic and total. The chain always starts with the entry transition. Each valley is
    considered in time order, and a promotion happens only when all of these hold: the ladder
    has a rung left, the user has an asset for it, the signal qualified the valley, the
    previous transition has finished its animation by the recovery frame, and the new level
    can be held for its own animation *and* `promotion_min_hold_ms` before the reset.

    A cue that fails is skipped, not stretched — the next qualifying one may still promote.
    A cue that fails because the ladder is at the top ends the loop. Because the ladder is
    climbed rung by rung, `FACE_X3` can never appear without `FACE_X2` before it.
    """

    steps: list[tuple[str, Frame, Frame | None, str]] = [
        (ROLE_X0_TO_FACE_X1, open_start, open_cut, REASON_ENTRY_DIRECT)
    ]
    decisions: list[str] = []
    outcomes: list[str] = []
    state = STATE_FACE_X1
    hold = max(frames_from_ms(settings.promotion_min_hold_ms, frame_rate), 0)
    lookback = frames_from_ms(settings.zoom_cut_snap_lookback_ms, frame_rate)
    forward = frames_from_ms(settings.zoom_cut_snap_window_ms, frame_rate)
    rejected_cuts = 0

    for valley in valleys:
        step = promotion_from(state)
        if step is None:
            outcomes.append(CUE_REJECTED_AT_TOP)
            decisions.append(
                f"burst {burst_index}: cue at {valley.recovery_frame} ignored — the ladder is "
                f"already at {state} and there is no rung above it"
            )
            continue
        if step.role not in timing:
            outcomes.append(CUE_REJECTED_NO_ASSET)
            decisions.append(
                f"burst {burst_index}: no {step.to_state} — role {step.role!r} has no "
                "configured asset animation, so this project cannot make that move"
            )
            continue
        if not valley.qualified or valley.recovery_frame is None:
            outcomes.append(valley.status)
            decisions.append(
                f"burst {burst_index}: valley [{valley.low.start}, {valley.low.end}) rejected "
                f"by the signal — {valley.status} (drop {valley.drop_db:.1f}dB, "
                f"{valley.valley_ms}ms below the line)"
            )
            continue

        anchor = valley.recovery_frame
        previous_role, previous_start = steps[-1][0], steps[-1][1]
        needed_before = timing.frames_for(previous_role)
        needed_after = max(timing.frames_for(step.role), hold)

        def fits(
            frame: Frame,
            since: Frame = previous_start,
            before: int = needed_before,
            after: int = needed_after,
        ) -> bool:
            return frame - since >= before and reset - frame >= after

        if not fits(anchor):
            if anchor - previous_start < needed_before:
                outcomes.append(CUE_REJECTED_ANIMATION)
                decisions.append(
                    f"burst {burst_index}: cue at {anchor} rejected — it would cut "
                    f"{previous_role} down to {anchor - previous_start} frame(s), shorter than "
                    f"its own {needed_before}-frame animation. The cue is not moved to make it "
                    "fit; the next qualifying one may still promote"
                )
            else:
                outcomes.append(CUE_REJECTED_NO_ROOM)
                decisions.append(
                    f"burst {burst_index}: cue at {anchor} rejected — only {reset - anchor} "
                    f"frame(s) remain before the reset at {reset}, and {step.to_state} needs "
                    f"{needed_after} to finish its move and be held long enough to read"
                )
            continue

        snap = snap_to_cut(
            anchor, cuts, lookback=lookback, forward=forward, usable=fits
        )
        rejected_cuts += len(snap.rejected_cuts)
        for cut in snap.rejected_cuts:
            decisions.append(
                f"burst {burst_index}: hard cut at {cut} ({cut - anchor:+d}f from the cue) "
                f"rejected for {step.to_state} — it does not leave both animations room"
            )
        start = snap.frame
        outcomes.append(step.to_state)
        where = (
            f"directly on the recovery (no hard cut in [-{lookback}f, +{forward}f])"
            if snap.cut_frame is None
            else f"snapped to hard cut {snap.cut_frame} ({snap.cut_frame - anchor:+d}f)"
        )
        decisions.append(
            f"burst {burst_index}: promoted to {step.to_state} at {start} — the voice dropped "
            f"{valley.drop_db:.1f}dB for {valley.valley_ms}ms in "
            f"[{valley.low.start}, {valley.low.end}) and came back at {anchor} "
            f"(+{valley.recovery_db:.1f}dB); {where}; held for {reset - start} frame(s)"
        )
        steps.append(
            (
                step.role,
                start,
                snap.cut_frame,
                _snap_reason(
                    snap.cut_frame,
                    anchor,
                    REASON_PROMOTED_DIRECT,
                    REASON_PROMOTED_SNAPPED_BACKWARD,
                    REASON_PROMOTED_SNAPPED_FORWARD,
                ),
            )
        )
        state = step.to_state

    return _ZoomChain(tuple(steps), state, tuple(decisions), tuple(outcomes), rejected_cuts)


def _burst_valleys(
    energy: EnergyEnvelope | None,
    span: FrameRange,
    burst_index: int,
    settings: PlannerSettings,
) -> tuple[VoiceValley, ...]:
    """Voice valleys inside one zoom cycle, or none at all when there is no envelope.

    The span searched is the *cycle* — from where the zoom opens to where it resets — not the
    speech burst. A promotion is a change to a picture that is already zoomed, so a dip before
    the zoom exists cannot earn one.
    """

    if energy is None:
        return ()
    return voice_valleys(
        energy,
        span,
        burst_index=burst_index,
        min_drop_db=settings.promotion_min_drop_db,
        recovery_within_db=settings.promotion_recovery_within_db,
        min_valley_ms=settings.promotion_min_valley_ms,
        max_valley_ms=settings.promotion_max_valley_ms,
    )


def plan_zooms(
    *,
    timeline: FrameRange,
    frame_rate: Fraction,
    speech_segments: Iterable[SpeechSegment],
    timing: AssetTiming,
    hard_cuts: Iterable[Frame] = (),
    settings: PlannerSettings | None = None,
    source: PlanSource | None = None,
    energy: EnergyEnvelope | None = None,
) -> ZoomPlan:
    """Turn speech facts into transition placements. Pure and deterministic.

    `energy` is the voice-dynamics envelope from `speech/energy.py`. Without it there are no
    promotion cues and the plan is a one-level edit — which is a legitimate result (it is what
    Phases 4-7 produced), not a silent degradation: the decision trace says so.
    """

    settings = settings or PlannerSettings()
    reset_gate = frames_from_ms(settings.reset_after_silence_ms, frame_rate)
    lead_in = frames_from_ms(settings.zoom_lead_in_ms, frame_rate)
    lead_out = frames_from_ms(settings.zoom_lead_out_ms, frame_rate)
    snap_window = frames_from_ms(settings.cut_snap_window_ms, frame_rate)
    lookback = frames_from_ms(settings.cut_snap_lookback_ms, frame_rate)
    zoom_window = frames_from_ms(settings.zoom_cut_snap_window_ms, frame_rate)
    zoom_lookback = frames_from_ms(settings.zoom_cut_snap_lookback_ms, frame_rate)
    x1_min = timing.frames_for(ROLE_X0_TO_FACE_X1)
    # Budgeted before the level is known; see `AssetTiming.max_reset_frames`.
    x0_frames = timing.max_reset_frames
    cuts = hard_cuts_in_range(hard_cuts, timeline)

    decisions: list[str] = [
        f"timeline [{timeline.start}, {timeline.end}) = {timeline.duration} frames "
        f"@ {float(frame_rate):.6f} fps",
        f"gate reset_after_silence={settings.reset_after_silence_ms}ms = {reset_gate} frames; "
        f"lead_in={lead_in}f lead_out={lead_out}f "
        f"cut snap window=[-{lookback}f, +{snap_window}f] around a reset anchor and "
        f"[-{zoom_lookback}f, +{zoom_window}f] around a zoom-in anchor "
        "(nearest cut wins, forward on a tie); "
        "transition animations "
        + " ".join(f"{role}={frames}f" for role, frames in timing.transition_frames),
        "promotion ladder: "
        + " -> ".join(FACECAM_LADDER)
        + "; each rung is earned by a voice valley of at least "
        f"{settings.promotion_min_valley_ms}ms and {settings.promotion_min_drop_db}dB followed "
        f"by a recovery to within {settings.promotion_recovery_within_db}dB of the burst's own "
        f"voice level, held for at least {settings.promotion_min_hold_ms}ms",
        f"{len(cuts)} hard cut(s) on the reference video track inside the range",
        (
            f"energy envelope: {len(energy)} point(s), {energy.settings.to_dict()}"
            if energy is not None
            else "no energy envelope supplied: no promotion is possible, every cycle stays at "
            "face_x1"
        ),
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
    open_raw: Frame | None = None
    open_index = 0
    floor = timeline.start
    valleys: list[VoiceValley] = []
    outcomes: list[str] = []

    for index, burst in enumerate(bursts):
        if open_raw is None:
            open_raw = max(timeline.start, burst.start - lead_in)
            open_index = index
        is_last = index == len(bursts) - 1
        limit = timeline.end if is_last else max(timeline.start, bursts[index + 1].start - lead_in)
        base_reset = min(burst.end + lead_out, timeline.end)
        # The reset is chosen first, against the *earliest* frame this cycle's entry could be
        # snapped to: the entry's own snap then knows where the cycle ends, and can refuse a
        # cut that would leave the entry animation no room (D052).
        choice = _choose_reset(
            base_reset,
            max(floor, open_raw - zoom_lookback),
            limit,
            cuts,
            snap_window,
            lookback,
            x0_frames,
        )
        if choice is not None:
            rejected_cuts += len(choice.rejected_cuts)
            for cut in choice.rejected_cuts:
                decisions.append(
                    f"burst {index}: hard cut at {cut} ({cut - base_reset:+d}f) rejected — only "
                    f"{limit - cut} frame(s) left before "
                    f"{'the timeline end' if is_last else f'the next zoom at {limit}'}, "
                    f"the x0 animation needs {x0_frames}"
                )

        if choice is None:
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

        def entry_fits(frame: Frame, since: Frame = floor, until: Frame = reset) -> bool:
            """An entry may not overlap the previous cycle, nor lose its own animation."""

            return frame >= since and until - frame >= x1_min

        entry = snap_to_cut(
            open_raw, cuts, lookback=zoom_lookback, forward=zoom_window, usable=entry_fits
        )
        rejected_cuts += len(entry.rejected_cuts)
        for cut in entry.rejected_cuts:
            decisions.append(
                f"burst {open_index}: hard cut at {cut} ({cut - open_raw:+d}f from the burst "
                "start) rejected for the entry — it would overlap the previous cycle or leave "
                f"the {x1_min}-frame entry animation no room before the reset at {reset}"
            )
        open_start = entry.frame
        if entry.cut_frame is not None:
            decisions.append(
                f"burst {open_index}: entry snapped "
                f"{'backward' if entry.cut_frame < open_raw else 'forward'} from {open_raw} to "
                f"hard cut {entry.cut_frame} ({entry.cut_frame - open_raw:+d}f, window "
                f"[-{zoom_lookback}f, +{zoom_window}f], nearest cut to the anchor)"
            )
        else:
            decisions.append(
                f"burst {open_index}: entry at the raw burst start {open_raw}; no usable hard "
                f"cut in the [-{zoom_lookback}f, +{zoom_window}f] window"
            )

        span = FrameRange(open_start, reset)
        if span.duration < x1_min:
            suppressed_cycles += 1
            decisions.append(
                f"burst {index}: cycle dropped — x1 would run [{span.start}, {span.end}) = "
                f"{span.duration} frame(s), shorter than its own {x1_min}-frame animation, so "
                "the zoom would be cut off mid-move; no x1 and no x0 are placed"
            )
            open_raw = None
            continue

        cycle_valleys = _burst_valleys(energy, span, open_index, settings)
        chain = _zoom_chain(
            burst_index=open_index,
            open_start=open_start,
            open_cut=entry.cut_frame,
            reset=reset,
            timing=timing,
            settings=settings,
            frame_rate=frame_rate,
            valleys=cycle_valleys,
            cuts=cuts,
        )
        decisions.extend(chain.decisions)
        rejected_cuts += chain.rejected_cuts
        valleys.extend(cycle_valleys)
        outcomes.extend(chain.outcomes)
        entry_reason = _snap_reason(
            entry.cut_frame,
            open_raw,
            REASON_ENTRY_DIRECT,
            REASON_ENTRY_SNAPPED_BACKWARD,
            REASON_ENTRY_SNAPPED_FORWARD,
        )
        # Each clip holds its state until the next one takes over; the last one holds it to
        # the reset. This is the whole reason a promotion needs no explicit end frame.
        boundaries = [begin for _r, begin, _c, _n in chain.steps[1:]] + [reset]
        for position, ((role, begin, snapped, why), end) in enumerate(
            zip(chain.steps, boundaries, strict=True)
        ):
            placements.append(
                AssetPlacement(
                    asset_role=role,
                    frames=FrameRange(begin, end),
                    reason=entry_reason if position == 0 else why,
                    cut_frame=snapped,
                    burst_index=open_index,
                    burst_count=index - open_index + 1,
                )
            )
        back = reset_from(chain.top_state)
        placements.append(
            AssetPlacement(
                asset_role=back.role,
                frames=FrameRange(reset, reset + timing.frames_for(back.role)),
                reason=_snap_reason(
                    choice.cut_frame,
                    base_reset,
                    REASON_RESET_DIRECT,
                    REASON_RESET_SNAPPED_BACKWARD,
                    REASON_RESET_SNAPPED_FORWARD,
                ),
                cut_frame=choice.cut_frame,
                burst_index=index,
            )
        )
        decisions.append(
            f"burst {index}: reset from {chain.top_state} uses {back.role} "
            f"({timing.frames_for(back.role)} frames)"
        )
        floor = reset + timing.frames_for(back.role)
        open_raw = None

    if open_raw is not None:
        # No reset closes this one, so the entry stays on its raw anchor: there is no cycle
        # end to validate a snapped entry against, and a cut may not shorten a zoom that the
        # timeline itself already truncates.
        open_start = open_raw
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
            cycle_valleys = _burst_valleys(energy, span, open_index, settings)
            chain = _zoom_chain(
                burst_index=open_index,
                open_start=open_start,
                open_cut=None,
                reset=timeline.end,
                timing=timing,
                settings=settings,
                frame_rate=frame_rate,
                valleys=cycle_valleys,
                cuts=cuts,
            )
            decisions.extend(chain.decisions)
            rejected_cuts += chain.rejected_cuts
            valleys.extend(cycle_valleys)
            outcomes.extend(chain.outcomes)
            boundaries = [begin for _r, begin, _c, _n in chain.steps[1:]] + [timeline.end]
            for position, ((role, begin, snapped, why), end) in enumerate(
                zip(chain.steps, boundaries, strict=True)
            ):
                placements.append(
                    AssetPlacement(
                        asset_role=role,
                        frames=FrameRange(begin, end),
                        reason=REASON_X1_HELD_TO_TIMELINE_END if position == 0 else why,
                        cut_frame=snapped,
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
        valleys=tuple(valleys),
        cue_outcomes=tuple(outcomes),
        merged_gaps=merged_gaps,
        suppressed_resets=suppressed_resets,
        suppressed_cycles=suppressed_cycles,
        rejected_cuts=rejected_cuts,
        source=source,
    )
    return plan


__all__ = [
    "REASON_ENTRY_DIRECT",
    "REASON_ENTRY_SNAPPED_BACKWARD",
    "REASON_ENTRY_SNAPPED_FORWARD",
    "REASON_PROMOTED_DIRECT",
    "REASON_PROMOTED_SNAPPED_BACKWARD",
    "REASON_PROMOTED_SNAPPED_FORWARD",
    "REASON_RESET_DIRECT",
    "REASON_RESET_SNAPPED_BACKWARD",
    "REASON_RESET_SNAPPED_FORWARD",
    "REASON_X1_HELD_TO_TIMELINE_END",
    "SUPERSEDED_PLANNER_KEYS",
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
