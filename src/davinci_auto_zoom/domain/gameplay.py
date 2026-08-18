"""Phase 9a: when does a region that would be X0 deserve to be GAMEPLAY instead?

Pure by construction, exactly like `planner.py` and `dynamics.py`: no Resolve object, no
numpy, no ffmpeg, no filesystem, no clock. Everything here takes plain `(frame, value)` pairs
and integers and returns dataclasses.

This module holds three separable things, and they are separate on purpose:

**1. Reading the human edit.** `reference_states()` walks the adjustment clips of a manual
timeline through `domain/transitions.py` and reconstructs the state sequence the editor
actually produced. A move outside the graph *raises*; it is never interpreted (D048). From
that sequence `GameplayEpisode`s fall out: not a list of clips, but the spans where the
picture was on the game, with what led in and what led out.

**2. The population.** `silence_windows()` turns the creator's speech bursts into the regions
where the facecam system is out — the would-be-X0 windows. Those, positive *and* negative, are
the dataset. A long window the human left at X0 is evidence, not a missing example.

**3. The decision.** `decide_gameplay()` answers two questions that must not be conflated
(D057): *whether* a window should be gameplay at all, and — only then — *where exactly* the
move starts and ends. A rule can be right about the first and 20 frames wrong about the
second, and a report that mixes them cannot tell you which.

## What Phase 9a measured, and what it refuses to pretend

The prior hypothesis was that the length of the creator's silence would be the main signal.
On `DAZ_OUTPUT_MVP3` **it is not** (D056): the shortest gap the editor turned into gameplay is
shorter than the longest gap they left at X0, and the single longest silence in the timeline
stayed X0. Gap length alone separates nothing, so no threshold on it is offered here as if it
did. What does separate is measured in `.agent/reports/phase-09a-mvp3-gameplay-analysis.txt`
and encoded in `GameplayPolicySettings` — with the honest note that it rests on one timeline.

Nothing in this module places an `AssetPlacement`. Phase 9a is a simulator: it produces
proposals a human reads next to the manual edit, and the production planner does not import
it. Turning a proposal into a placement is Phase 9b's job and it has not been done.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from davinci_auto_zoom.domain.models import Frame, FrameRange
from davinci_auto_zoom.domain.transitions import (
    BY_ROLE,
    STATE_GAMEPLAY,
    STATE_X0,
    ForbiddenTransition,
    Transition,
    is_allowed,
)

#: Verdicts for what the human did with one silence window.
MANUAL_GAMEPLAY = "gameplay"
MANUAL_X0 = "x0"
MANUAL_MIXED = "mixed"

#: Why `decide_gameplay` said yes or no. Tokens, not prose: they are a report column.
REASON_NOT_TALKING = "creator_not_talking"
REASON_SECONDARY_AUDIO = "world_is_talking"
REASON_VISUAL_ACTIVITY = "picture_is_busy"
REASON_LONG_SILENCE = "long_creator_silence"
REASON_TOO_SHORT = "window_shorter_than_the_move"
REASON_NO_SUPPORT = "no_supporting_activity"
REASON_TAIL = "timeline_tail"

#: How an anchor was finally placed.
ANCHOR_DIRECT = "direct"
ANCHOR_SNAPPED_BACKWARD = "cut_snap_backward"
ANCHOR_SNAPPED_FORWARD = "cut_snap_forward"


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile. Deliberately not numpy: this module stays importable alone.

    `fraction` is in `[0, 1]`. An empty sequence has no percentile and raises rather than
    inventing a zero that would silently read as "measured, and quiet".
    """

    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _in_range(pairs: Sequence[tuple[Frame, float]], window: FrameRange) -> list[float]:
    return [value for frame, value in pairs if window.start <= frame < window.end]


# ---------------------------------------------------------------------------------------
# 1. Reading the human edit
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ManualTransition:
    """One adjustment clip of a human timeline, read as a state change."""

    role: str
    from_state: str
    to_state: str
    start_frame: Frame
    end_frame: Frame

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def transition(self) -> Transition:
        return BY_ROLE[self.role]

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_frames": self.duration_frames,
        }


@dataclass(frozen=True, slots=True)
class GameplayEpisode:
    """One span the human edit spent on the game, and its editorial neighbourhood.

    `start_frame` is where the *move onto* the game begins — the first frame of the
    `*_to_gameplay` clip — not where its animation finishes. That is the frame an editor
    chose, and therefore the frame a policy has to reproduce.
    """

    index: int
    start_frame: Frame
    end_frame: Frame
    entry_role: str
    exit_role: str
    #: The state the picture was in before the move onto the game, and after the move off it.
    previous_state: str
    next_state: str
    #: Nearest creator-voice boundaries, or None at the ends of the timeline.
    previous_burst_end: Frame | None = None
    next_burst_start: Frame | None = None

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def frames_since_voice(self) -> int | None:
        """How long the creator had been quiet when the move onto the game happened.

        Negative when the editor moved to gameplay while the detector still called it speech
        — which happens, and is a fact about the burst extent rather than about gameplay.
        """

        if self.previous_burst_end is None:
            return None
        return self.start_frame - self.previous_burst_end

    @property
    def frames_until_voice(self) -> int | None:
        if self.next_burst_start is None:
            return None
        return self.next_burst_start - self.end_frame

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_frames": self.duration_frames,
            "entry_role": self.entry_role,
            "exit_role": self.exit_role,
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "previous_burst_end": self.previous_burst_end,
            "next_burst_start": self.next_burst_start,
            "frames_since_voice": self.frames_since_voice,
            "frames_until_voice": self.frames_until_voice,
        }


@dataclass(frozen=True, slots=True)
class ReferenceStates:
    """A human timeline's adjustment track, reconstructed as a state machine."""

    transitions: tuple[ManualTransition, ...]
    episodes: tuple[GameplayEpisode, ...]
    #: The state the picture is in at the very end of the timeline.
    final_state: str

    def roles_used(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for transition in self.transitions:
            counts[transition.role] = counts.get(transition.role, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "transitions": [t.to_dict() for t in self.transitions],
            "episodes": [e.to_dict() for e in self.episodes],
            "final_state": self.final_state,
            "roles_used": self.roles_used(),
        }


def reference_states(
    items: Iterable[tuple[str, Frame, Frame]],
    role_of: dict[str, str],
    *,
    bursts: Sequence[FrameRange] = (),
    initial_state: str = STATE_X0,
) -> ReferenceStates:
    """Walk a human timeline's clips through the graph. Raises on a move it does not contain.

    `items` are `(clip_name, start, end)` in any order; `role_of` maps a Media Pool clip name
    to a transition role. A clip whose name is not in `role_of` is not ours to interpret and
    raises — silently skipping it would hide exactly the kind of surprise this analysis exists
    to find.

    The graph check is the point of this function. If the manual edit contains a state change
    Phase 9a did not authorise, the honest outcome is to stop and report it, not to guess what
    the editor meant.
    """

    ordered = sorted(items, key=lambda item: (item[1], item[2]))
    state = initial_state
    transitions: list[ManualTransition] = []
    episodes: list[GameplayEpisode] = []
    pending: tuple[Frame, str, str] | None = None

    for name, start, end in ordered:
        role = role_of.get(name)
        if role is None:
            raise ForbiddenTransition(
                f"clip {name!r} at [{start},{end}) has no configured transition role; "
                f"known clips: {', '.join(sorted(role_of)) or '(none)'}"
            )
        transition = BY_ROLE.get(role)
        if transition is None:
            raise ForbiddenTransition(f"{role!r} is not a transition role")
        if not is_allowed(state, transition.to_state):
            raise ForbiddenTransition(
                f"{name!r} at [{start},{end}) performs {state} -> {transition.to_state}, "
                "which is not in the state graph; the manual timeline uses a move Phase 9a "
                "has not authorised, so it is reported rather than interpreted"
            )
        if transition.from_state != state:
            raise ForbiddenTransition(
                f"{name!r} at [{start},{end}) is a {transition.from_state} -> "
                f"{transition.to_state} asset, but the picture is at {state}"
            )
        transitions.append(
            ManualTransition(role, transition.from_state, transition.to_state, start, end)
        )
        if transition.is_gameplay_entry:
            pending = (start, transition.from_state, role)
        elif pending is not None:
            entry_start, previous_state, entry_role = pending
            episodes.append(
                GameplayEpisode(
                    index=len(episodes),
                    start_frame=entry_start,
                    end_frame=start,
                    entry_role=entry_role,
                    exit_role=role,
                    previous_state=previous_state,
                    next_state=transition.to_state,
                    previous_burst_end=_previous_burst_end(bursts, entry_start),
                    next_burst_start=_next_burst_start(bursts, start),
                )
            )
            pending = None
        state = transition.to_state

    if pending is not None:
        raise ForbiddenTransition(
            f"the timeline ends on the game: the move at frame {pending[0]} is never left. "
            "Every gameplay episode must have an explicit exit transition."
        )
    return ReferenceStates(tuple(transitions), tuple(episodes), state)


def _previous_burst_end(bursts: Sequence[FrameRange], frame: Frame) -> Frame | None:
    """End of the last burst that had already begun. Beyond it, the creator is quiet."""

    ends = [b.end for b in bursts if b.start <= frame]
    return max(ends) if ends else None


def _next_burst_start(bursts: Sequence[FrameRange], frame: Frame) -> Frame | None:
    """Start of the next burst the creator's voice is heard in — **counting the one he may
    already be in**.

    Selecting on `b.start >= frame` would be wrong here and was, measurably: an editor who
    leaves gameplay four frames *after* the detector says the next burst began would then be
    compared against the burst after that, and a +4 frame agreement reads as -263. The
    question a gameplay exit answers is "when is he talking again", and if the answer is
    "he already is", that burst is the one.
    """

    starts = [b.start for b in bursts if b.end > frame]
    return min(starts) if starts else None


# ---------------------------------------------------------------------------------------
# 2. The population: every region the facecam system leaves at X0
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SilenceWindow:
    """One region where the creator is not talking, and the facecam is therefore out.

    This is the *candidate* population for gameplay, per the Phase 9a model: gameplay
    replaces X0, it is not an event independent of the facecam system.
    """

    index: int
    frames: FrameRange
    #: True at the two ends of the timeline, where one side has no burst at all. Those are
    #: not ordinary pauses in a conversation and are worth being able to tell apart.
    at_timeline_start: bool = False
    at_timeline_end: bool = False

    @property
    def start_frame(self) -> Frame:
        return self.frames.start

    @property
    def end_frame(self) -> Frame:
        return self.frames.end

    @property
    def duration_frames(self) -> int:
        return self.frames.duration

    def duration_ms(self, frame_rate: Fraction) -> int:
        return int(round(self.duration_frames * 1000 / float(frame_rate)))

    def to_dict(self, frame_rate: Fraction | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "index": self.index,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_frames": self.duration_frames,
            "at_timeline_start": self.at_timeline_start,
            "at_timeline_end": self.at_timeline_end,
        }
        if frame_rate is not None:
            data["duration_ms"] = self.duration_ms(frame_rate)
        return data


def silence_windows(
    timeline: FrameRange, bursts: Sequence[FrameRange]
) -> tuple[SilenceWindow, ...]:
    """Every gap between the creator's editorial bursts, plus the head and tail of the range.

    Empty gaps (two bursts that touch) are dropped: there is no region there to fill.
    """

    ordered = sorted(bursts, key=lambda b: (b.start, b.end))
    edges: list[tuple[Frame, Frame, bool, bool]] = []
    cursor = timeline.start
    for burst in ordered:
        if burst.start > cursor:
            edges.append((cursor, burst.start, cursor == timeline.start, False))
        cursor = max(cursor, burst.end)
    if cursor < timeline.end:
        edges.append((cursor, timeline.end, not ordered, True))
    return tuple(
        SilenceWindow(index, FrameRange(start, end), head, tail)
        for index, (start, end, head, tail) in enumerate(edges)
    )


@dataclass(frozen=True, slots=True)
class ManualCoverage:
    """What the human actually did with one silence window."""

    window_index: int
    verdict: str
    gameplay_frames: int
    episode_indexes: tuple[int, ...] = ()
    gameplay_start: Frame | None = None
    gameplay_end: Frame | None = None

    def fraction(self, window: SilenceWindow) -> float:
        return self.gameplay_frames / window.duration_frames if window.duration_frames else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_index": self.window_index,
            "verdict": self.verdict,
            "gameplay_frames": self.gameplay_frames,
            "episode_indexes": list(self.episode_indexes),
            "gameplay_start": self.gameplay_start,
            "gameplay_end": self.gameplay_end,
        }


#: A window this much covered by gameplay counts as fully gameplay rather than mixed. The
#: point is to keep a 3-frame overlap of a neighbouring episode from being called "mixed".
FULL_COVERAGE = 0.9


def manual_coverage(
    window: SilenceWindow, episodes: Sequence[GameplayEpisode]
) -> ManualCoverage:
    """Classify one window against the manual episodes that overlap it.

    An episode is counted by its *overlap* with the window, so an episode that starts inside
    a burst (which happens — see the burst-extent divergence) contributes only the part that
    is actually in the window.
    """

    overlapping = [
        episode
        for episode in episodes
        if episode.start_frame < window.end_frame and episode.end_frame > window.start_frame
    ]
    covered = sum(
        min(e.end_frame, window.end_frame) - max(e.start_frame, window.start_frame)
        for e in overlapping
    )
    if not overlapping:
        verdict = MANUAL_X0
    elif covered >= FULL_COVERAGE * window.duration_frames:
        verdict = MANUAL_GAMEPLAY
    else:
        verdict = MANUAL_MIXED
    return ManualCoverage(
        window_index=window.index,
        verdict=verdict,
        gameplay_frames=covered,
        episode_indexes=tuple(e.index for e in overlapping),
        gameplay_start=min((e.start_frame for e in overlapping), default=None),
        gameplay_end=max((e.end_frame for e in overlapping), default=None),
    )


# ---------------------------------------------------------------------------------------
# 3. Objective features, all relative
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SecondaryAudioFeatures:
    """What everything-except-the-creator's-voice was doing inside one window.

    Every level is a **difference in dB against a timeline-wide reference**, never an
    absolute dBFS value, so raising or lowering the whole mix cannot change a decision (the
    rule `dynamics.py` already follows for the voice, D051).
    """

    #: Mean and 90th-percentile level, relative to the reference (dB, usually negative).
    mean_db: float
    p90_db: float
    #: Loudest minus quietest inside the window: how much it moves, not how loud it is.
    dynamic_range_db: float
    #: Share of the window at least `active_within_db` under the reference.
    active_fraction: float
    #: Sudden rises: consecutive samples climbing by at least `onset_db`.
    onsets: int
    #: Mean level of the first, middle and last third — does the activity arrive or leave?
    thirds_db: tuple[float, float, float]
    #: Number of envelope samples the window contained. Zero means "not measured".
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_db": round(self.mean_db, 2),
            "p90_db": round(self.p90_db, 2),
            "dynamic_range_db": round(self.dynamic_range_db, 2),
            "active_fraction": round(self.active_fraction, 3),
            "onsets": self.onsets,
            "thirds_db": [round(t, 2) for t in self.thirds_db],
            "samples": self.samples,
        }


EMPTY_AUDIO = SecondaryAudioFeatures(0.0, 0.0, 0.0, 0.0, 0, (0.0, 0.0, 0.0), 0)


def secondary_audio_features(
    envelope: Sequence[tuple[Frame, float]],
    window: SilenceWindow,
    reference_db: float,
    *,
    active_within_db: float = 12.0,
    onset_db: float = 6.0,
) -> SecondaryAudioFeatures:
    """Window statistics of a dBFS envelope, expressed against `reference_db`."""

    levels = _in_range(envelope, window.frames)
    if not levels:
        return EMPTY_AUDIO
    relative = [level - reference_db for level in levels]
    third = max(1, len(relative) // 3)
    thirds = (
        sum(relative[:third]) / third,
        sum(relative[third : 2 * third]) / max(1, len(relative[third : 2 * third])),
        sum(relative[-third:]) / third,
    )
    onsets = sum(
        1
        for previous, current in zip(relative, relative[1:], strict=False)
        if current - previous >= onset_db
    )
    return SecondaryAudioFeatures(
        mean_db=sum(relative) / len(relative),
        p90_db=percentile(relative, 0.9),
        dynamic_range_db=max(relative) - min(relative),
        active_fraction=sum(1 for r in relative if r >= -active_within_db) / len(relative),
        onsets=onsets,
        thirds_db=thirds,
        samples=len(relative),
    )


@dataclass(frozen=True, slots=True)
class VideoActivityFeatures:
    """How much the picture moved inside one window. Normalised, never absolute."""

    #: Mean and 90th-percentile motion, as a ratio of the timeline-wide reference.
    mean_motion: float
    p90_motion: float
    #: Share of samples above the reference. "Busier than this timeline's typical moment".
    active_fraction: float
    #: Spread of the motion values, same normalisation. A steady pan and a burst of action
    #: can share a mean and differ entirely here.
    variance: float
    #: Hard cuts per second of window, from the timeline's own edit — not from the pixels.
    cut_density: float
    hard_cuts: int = 0
    samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_motion": round(self.mean_motion, 3),
            "p90_motion": round(self.p90_motion, 3),
            "active_fraction": round(self.active_fraction, 3),
            "variance": round(self.variance, 4),
            "cut_density": round(self.cut_density, 3),
            "hard_cuts": self.hard_cuts,
            "samples": self.samples,
        }


EMPTY_VIDEO = VideoActivityFeatures(0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)


def video_activity_features(
    envelope: Sequence[tuple[Frame, float]],
    window: SilenceWindow,
    reference_motion: float,
    hard_cuts: Sequence[Frame],
    frame_rate: Fraction,
) -> VideoActivityFeatures:
    """Window statistics of a motion envelope, normalised by `reference_motion`."""

    values = _in_range(envelope, window.frames)
    cuts = [c for c in hard_cuts if window.start_frame <= c < window.end_frame]
    seconds = window.duration_frames / float(frame_rate)
    density = len(cuts) / seconds if seconds else 0.0
    if not values or reference_motion <= 0.0:
        return VideoActivityFeatures(0.0, 0.0, 0.0, 0.0, density, len(cuts), len(values))
    scaled = [value / reference_motion for value in values]
    mean = sum(scaled) / len(scaled)
    variance = sum((s - mean) ** 2 for s in scaled) / len(scaled)
    return VideoActivityFeatures(
        mean_motion=mean,
        p90_motion=percentile(scaled, 0.9),
        active_fraction=sum(1 for s in scaled if s >= 1.0) / len(scaled),
        variance=variance,
        cut_density=density,
        hard_cuts=len(cuts),
        samples=len(scaled),
    )


@dataclass(frozen=True, slots=True)
class GameplayWindowFeatures:
    """Everything `decide_gameplay` is allowed to look at, and nothing else.

    Deliberately neutral data — a window, two feature bundles and the frame rate. No Resolve
    object, no reference timeline, no burst index. A policy that cannot see which window
    number it is cannot special-case one (D058).
    """

    window: SilenceWindow
    audio: SecondaryAudioFeatures = EMPTY_AUDIO
    video: VideoActivityFeatures = EMPTY_VIDEO

    def to_dict(self, frame_rate: Fraction | None = None) -> dict[str, Any]:
        return {
            "window": self.window.to_dict(frame_rate),
            "audio": self.audio.to_dict(),
            "video": self.video.to_dict(),
        }


# ---------------------------------------------------------------------------------------
# 4. The candidate rule, and the two questions it must keep apart
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GameplayPolicySettings:
    """The candidate gameplay rule, as thresholds a human can read and argue with.

    No learned model and no weighted score (D059). Every number here is a threshold on one
    measured quantity, chosen from the middle of a plateau in
    `.agent/reports/phase-09a-gameplay-policy-comparison.txt` rather than from the value that
    happens to score best on one timeline.
    """

    #: A window shorter than this cannot hold a move onto the game and back off it and still
    #: read as an intention. Structural, not taste: two animations plus something to see.
    min_window_ms: int = 500
    #: **The measured candidate.** A window where the creator is not talking is gameplay
    #: unless something excludes it. This is the default because on `DAZ_OUTPUT_MVP3` it
    #: matches the best score any measured signal reached (11/15) while having no fitted
    #: parameter at all, and because it is the one statement of the rule a human can check:
    #: "when he stops talking, show the game; not at the outro" (D056).
    gameplay_by_default: bool = True
    #: Enabled signals, used only when `gameplay_by_default` is off. Turning one off is how
    #: the A/B/C/D ablation is run — the same code path, not a second implementation. They
    #: default to **on** so that switching the baseline off gives the fullest variant, but on
    #: the reference material neither of them beats the baseline (D056).
    use_secondary_audio: bool = True
    use_video: bool = True
    #: The world (game, friends) is doing something: this much of the window at most
    #: `audio_active_within_db` under the timeline's own reference level.
    audio_active_fraction: float = 0.5
    audio_active_within_db: float = 12.0
    #: The picture is busier than this timeline's typical moment, for this much of the window.
    video_active_fraction: float = 0.5
    #: A silence at least this long is treated as gameplay even with no supporting signal.
    #: Deliberately high: on the reference material the longest silence stayed X0, so this is
    #: a backstop, not the main rule.
    long_silence_ms: int = 100_000
    #: The head and tail of a timeline are not pauses in a conversation. The tail in
    #: particular is where an outro lives, and the reference edit leaves it at X0.
    gameplay_at_timeline_tail: bool = False
    #: Snap windows for the gameplay move, measured separately from the facecam ones (D060).
    entry_snap_lookback_ms: int = 120
    entry_snap_forward_ms: int = 120
    exit_snap_lookback_ms: int = 120
    exit_snap_forward_ms: int = 120

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_window_ms": self.min_window_ms,
            "gameplay_by_default": self.gameplay_by_default,
            "use_secondary_audio": self.use_secondary_audio,
            "use_video": self.use_video,
            "audio_active_fraction": self.audio_active_fraction,
            "audio_active_within_db": self.audio_active_within_db,
            "video_active_fraction": self.video_active_fraction,
            "long_silence_ms": self.long_silence_ms,
            "gameplay_at_timeline_tail": self.gameplay_at_timeline_tail,
            "entry_snap_lookback_ms": self.entry_snap_lookback_ms,
            "entry_snap_forward_ms": self.entry_snap_forward_ms,
            "exit_snap_lookback_ms": self.exit_snap_lookback_ms,
            "exit_snap_forward_ms": self.exit_snap_forward_ms,
        }


@dataclass(frozen=True, slots=True)
class GameplayDecision:
    """One proposal. `use_gameplay` is question A; the anchors are question B."""

    window_index: int
    use_gameplay: bool
    reasons: tuple[str, ...]
    #: Where the move would start and end before any cut snapping.
    entry_anchor: Frame | None = None
    exit_anchor: Frame | None = None
    #: Where it would actually go, and how that was decided.
    entry_frame: Frame | None = None
    exit_frame: Frame | None = None
    entry_cut: Frame | None = None
    exit_cut: Frame | None = None
    entry_snap: str = ANCHOR_DIRECT
    exit_snap: str = ANCHOR_DIRECT

    @property
    def reason(self) -> str:
        return "+".join(self.reasons) if self.reasons else REASON_NO_SUPPORT

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_index": self.window_index,
            "use_gameplay": self.use_gameplay,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "entry_anchor": self.entry_anchor,
            "exit_anchor": self.exit_anchor,
            "entry_frame": self.entry_frame,
            "exit_frame": self.exit_frame,
            "entry_cut": self.entry_cut,
            "exit_cut": self.exit_cut,
            "entry_snap": self.entry_snap,
            "exit_snap": self.exit_snap,
        }


def _snap(
    anchor: Frame, cuts: Sequence[Frame], lookback: int, forward: int
) -> tuple[Frame, Frame | None, str]:
    """Nearest hard cut to `anchor` inside an asymmetric window, else `anchor` itself.

    Same ranking rule as the facecam snapper (`planner.snap_to_cut`): closest wins, a later
    cut takes an exact tie. It is *not* the same window — those were measured for the
    facecam and are not assumed to transfer (D060).
    """

    candidates = sorted(
        (c for c in cuts if anchor - lookback <= c <= anchor + forward),
        key=lambda c: (abs(c - anchor), -c),
    )
    if not candidates:
        return anchor, None, ANCHOR_DIRECT
    cut = candidates[0]
    if cut == anchor:
        return cut, cut, ANCHOR_DIRECT
    return cut, cut, ANCHOR_SNAPPED_BACKWARD if cut < anchor else ANCHOR_SNAPPED_FORWARD


def _ms_to_frames(milliseconds: int, frame_rate: Fraction) -> int:
    return int(Fraction(milliseconds, 1000) * frame_rate + Fraction(1, 2))


def decide_gameplay(
    features: GameplayWindowFeatures,
    hard_cuts: Sequence[Frame],
    frame_rate: Fraction,
    settings: GameplayPolicySettings | None = None,
) -> GameplayDecision:
    """Should this window be gameplay, and if so exactly where does the move start and end?

    The two questions are answered in that order and never merged. Question A is a set of
    readable conditions on the window's own measured activity; question B is a raw anchor
    (the boundaries of the silence itself) plus one snap to a nearby hard cut. A `False`
    decision carries no anchors at all, so a report can never quietly show a proposed frame
    for a window the rule declined.
    """

    settings = settings or GameplayPolicySettings()
    window = features.window
    reasons: list[str] = []

    # --- question A: gameplay at all? ---------------------------------------------------
    minimum = _ms_to_frames(settings.min_window_ms, frame_rate)
    if window.duration_frames < minimum:
        return GameplayDecision(window.index, False, (REASON_TOO_SHORT,))
    if window.at_timeline_end and not settings.gameplay_at_timeline_tail:
        return GameplayDecision(window.index, False, (REASON_TAIL,))

    if settings.gameplay_by_default:
        reasons.append(REASON_NOT_TALKING)
    else:
        if (
            settings.use_secondary_audio
            and features.audio.samples
            and features.audio.active_fraction >= settings.audio_active_fraction
        ):
            reasons.append(REASON_SECONDARY_AUDIO)
        if (
            settings.use_video
            and features.video.samples
            and features.video.active_fraction >= settings.video_active_fraction
        ):
            reasons.append(REASON_VISUAL_ACTIVITY)
        if window.duration_frames >= _ms_to_frames(settings.long_silence_ms, frame_rate):
            reasons.append(REASON_LONG_SILENCE)

    if not reasons:
        return GameplayDecision(window.index, False, (REASON_NO_SUPPORT,))

    # --- question B: where exactly? -----------------------------------------------------
    entry_anchor, exit_anchor = window.start_frame, window.end_frame
    entry_frame, entry_cut, entry_snap = _snap(
        entry_anchor,
        hard_cuts,
        _ms_to_frames(settings.entry_snap_lookback_ms, frame_rate),
        _ms_to_frames(settings.entry_snap_forward_ms, frame_rate),
    )
    exit_frame, exit_cut, exit_snap = _snap(
        exit_anchor,
        hard_cuts,
        _ms_to_frames(settings.exit_snap_lookback_ms, frame_rate),
        _ms_to_frames(settings.exit_snap_forward_ms, frame_rate),
    )
    # A snap that inverted or collapsed the episode is worse than no snap.
    if exit_frame <= entry_frame:
        entry_frame, entry_cut, entry_snap = entry_anchor, None, ANCHOR_DIRECT
        exit_frame, exit_cut, exit_snap = exit_anchor, None, ANCHOR_DIRECT

    return GameplayDecision(
        window_index=window.index,
        use_gameplay=True,
        reasons=tuple(reasons),
        entry_anchor=entry_anchor,
        exit_anchor=exit_anchor,
        entry_frame=entry_frame,
        exit_frame=exit_frame,
        entry_cut=entry_cut,
        exit_cut=exit_cut,
        entry_snap=entry_snap,
        exit_snap=exit_snap,
    )


@dataclass(frozen=True, slots=True)
class GameplayEpisodeProposal:
    """A decision turned into the shape a future planner would consume — and nothing more.

    This is where Phase 9a deliberately stops (D061). It names the roles the move *would*
    use, which requires knowing the state the picture is in on each side, and it stops there:
    no `AssetPlacement`, no asset name, no frame count, no timeline. Phase 9b turns this into
    a plan; nothing in Phase 9a does.
    """

    window_index: int
    start_frame: Frame
    end_frame: Frame
    entry_role: str
    exit_role: str
    reason: str

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_index": self.window_index,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_frames": self.duration_frames,
            "entry_role": self.entry_role,
            "exit_role": self.exit_role,
            "reason": self.reason,
        }


def propose_episode(
    decision: GameplayDecision,
    from_state: str,
    to_state: str,
) -> GameplayEpisodeProposal | None:
    """The roles a proposed episode would need, or None when the rule declined the window.

    Raises `ForbiddenTransition` if either side is not a legal move, which is the point: a
    proposal that cannot be expressed in the graph must not be silently downgraded.
    """

    if not decision.use_gameplay:
        return None
    assert decision.entry_frame is not None and decision.exit_frame is not None
    from davinci_auto_zoom.domain.transitions import transition

    return GameplayEpisodeProposal(
        window_index=decision.window_index,
        start_frame=decision.entry_frame,
        end_frame=decision.exit_frame,
        entry_role=transition(from_state, STATE_GAMEPLAY).role,
        exit_role=transition(STATE_GAMEPLAY, to_state).role,
        reason=decision.reason,
    )


__all__ = [
    "ANCHOR_DIRECT",
    "ANCHOR_SNAPPED_BACKWARD",
    "ANCHOR_SNAPPED_FORWARD",
    "EMPTY_AUDIO",
    "EMPTY_VIDEO",
    "FULL_COVERAGE",
    "MANUAL_GAMEPLAY",
    "MANUAL_MIXED",
    "MANUAL_X0",
    "REASON_LONG_SILENCE",
    "REASON_NOT_TALKING",
    "REASON_NO_SUPPORT",
    "REASON_SECONDARY_AUDIO",
    "REASON_TAIL",
    "REASON_TOO_SHORT",
    "REASON_VISUAL_ACTIVITY",
    "GameplayDecision",
    "GameplayEpisode",
    "GameplayEpisodeProposal",
    "GameplayPolicySettings",
    "GameplayWindowFeatures",
    "ManualCoverage",
    "ManualTransition",
    "ReferenceStates",
    "SecondaryAudioFeatures",
    "SilenceWindow",
    "VideoActivityFeatures",
    "decide_gameplay",
    "manual_coverage",
    "percentile",
    "propose_episode",
    "reference_states",
    "secondary_audio_features",
    "silence_windows",
    "video_activity_features",
]
