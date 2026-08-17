"""Phase 8c: voice dynamics — an objective energy envelope, and the cues read from it.

Phase 8 promoted `face_x1 -> face_x2 -> face_x3` on **elapsed talking time**: 1000 ms into
the burst, then 1800 ms. That rule is superseded (D049). It predicts the level from the length
of a burst, which is why it could not explain the 204-frame cycle the editor deliberately kept
at x2 while a 138-frame one went to x3. What the editor actually reacts to is *inside* the
burst: the voice drops or breaks for a moment, then picks back up, and the level climbs on the
pick-up.

So this module models exactly two things, and nothing editorial about zoom levels:

    EnergyEnvelope   short-time loudness of the voice track, in dB, on a regular hop
    VoiceValley      a dip in that envelope, and the recovery that ends it

The envelope is *built* in `speech/energy.py` from the same normalized 16 kHz PCM the VAD
already consumes — one render, one normalization, two readers (D050). It arrives here as plain
numbers: no numpy, no ONNX, no file. Everything below is pure and deterministic.

## Why dB, and why relative

A creator can speak loudly or quietly, change their gain, or run a compressor. A rule of the
form "RMS below 0.02" measures the microphone, not the performance. So every threshold here is
a **difference in dB** against the burst's own voice level (its 75th-percentile point), which
makes the whole detector invariant under a uniform gain change: multiplying every sample by `g`
shifts every point by the same `20*log10(g)` and cancels out of every comparison. There is a
test for that, and it holds exactly across two octaves in each direction.

## What a valley is

    reference   = 75th percentile of the envelope inside the burst   (the voice level)
    low         = reference - min_drop_db                            (the valley floor line)
    recovery    = reference - recovery_within_db                     (back to speaking)

A **valley** is a maximal run of points below `low`. Its **recovery anchor** is the first point
at or after that run which reaches `recovery`. A valley with no recovery inside the burst is
not a cue — the creator stopped talking, and that is a *reset*, not a promotion. The anchor is
the frame the voice comes back, which is where the editor puts the tighter level: it is never
the valley's floor and never the start of the dip.

The two duration bounds are asymmetric in nature. `min_valley_ms` is the real filter (a
30 ms dip is a plosive, not a breath). `max_valley_ms` is a guard: a burst can only contain
silences shorter than `reset_after_silence_ms` by construction, so on measured material it
never fires — it exists so that a longer gate cannot turn a genuine pause into a promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from davinci_auto_zoom.domain.models import Frame, FrameRange

#: Cue rejection reasons that belong to the *signal*. The planner adds its own on top
#: (ladder, animation room); keeping the two sets separate is what makes a diagnostic line
#: attributable to either the audio or the edit.
CUE_QUALIFIED = "qualified"
CUE_NO_RECOVERY = "no_recovery_before_burst_end"
CUE_VALLEY_TOO_SHORT = "valley_too_short"
CUE_VALLEY_TOO_LONG = "valley_too_long"


@dataclass(frozen=True, slots=True)
class EnergySettings:
    """How the envelope is *built*. Technical signal parameters, not editorial taste.

    Same split as `[speech.vad]` versus `[planner]` (D021): these describe how loudness is
    measured, never what a measurement deserves. They are recorded in the `PlanSource` all the
    same, because changing them changes the plan (D051).
    """

    #: Length of one analysis window. Long enough to average a pitch period at any voice,
    #: short enough that a syllable boundary is still visible.
    window_ms: int = 30
    #: Distance between consecutive windows, and therefore the envelope's time resolution.
    hop_ms: int = 10
    #: Moving average applied to the dB curve. Removes per-syllable jitter without moving a
    #: real onset: at 30 ms it spans 3 points.
    smoothing_ms: int = 30

    def __post_init__(self) -> None:
        for name in ENERGY_SETTING_KEYS:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1 ms")
        if self.hop_ms > self.window_ms:
            raise ValueError(
                "energy hop_ms must be <= window_ms, otherwise the envelope skips audio "
                f"between windows (hop={self.hop_ms}ms, window={self.window_ms}ms)"
            )

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in ENERGY_SETTING_KEYS}


#: Field order is the config's key order, and the only list of them.
ENERGY_SETTING_KEYS: tuple[str, ...] = ("window_ms", "hop_ms", "smoothing_ms")


@dataclass(frozen=True, slots=True)
class EnergyPoint:
    """One envelope sample: an absolute timeline frame, and loudness in dBFS."""

    frame: Frame
    db: float


@dataclass(frozen=True, slots=True)
class EnergyEnvelope:
    """Short-time loudness of the voice track over the analysed range.

    Deliberately small: at a 10 ms hop a minute of audio is 6000 floats, which is a plan-sized
    object. The PCM itself is never carried here and never reaches a `ZoomPlan`.
    """

    points: tuple[EnergyPoint, ...]
    settings: EnergySettings = EnergySettings()

    def __len__(self) -> int:
        return len(self.points)

    def within(self, span: FrameRange) -> tuple[EnergyPoint, ...]:
        """Points whose frame falls in the half-open range. Sorted, like `points`."""

        return tuple(p for p in self.points if span.start <= p.frame < span.end)

    def to_dict(self) -> dict[str, Any]:
        """Summary only. The full curve is diagnostics, not plan data."""

        levels = [p.db for p in self.points]
        return {
            "settings": self.settings.to_dict(),
            "point_count": len(self.points),
            "start_frame": self.points[0].frame if self.points else None,
            "end_frame": self.points[-1].frame if self.points else None,
            "min_db": min(levels) if levels else None,
            "max_db": max(levels) if levels else None,
        }


@dataclass(frozen=True, slots=True)
class VoiceValley:
    """A dip in the voice, its recovery, and whether that recovery is a promotion cue.

    Every valley found is returned, qualified or not: a report that only lists the cues it
    used cannot explain the promotion it did *not* make.
    """

    burst_index: int
    #: Frames of the run below the drop line, half-open.
    low: FrameRange
    #: Where the envelope reached its minimum inside that run.
    low_frame: Frame
    #: First frame at or after the dip where the voice is back at speaking level. `None` when
    #: it never comes back before the burst ends.
    recovery_frame: Frame | None
    reference_db: float
    min_db: float
    valley_ms: int
    status: str
    #: Level the voice had come back to at `recovery_frame`. `None` when it never did.
    recovery_level_db: float | None = None

    @property
    def drop_db(self) -> float:
        """How far below the burst's voice level the dip went."""

        return self.reference_db - self.min_db

    @property
    def recovery_db(self) -> float:
        """How far the voice climbed back from the floor of the dip."""

        if self.recovery_level_db is None:
            return 0.0
        return self.recovery_level_db - self.min_db

    @property
    def qualified(self) -> bool:
        return self.status == CUE_QUALIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "burst_index": self.burst_index,
            "valley_start_frame": self.low.start,
            "valley_end_frame": self.low.end,
            "valley_low_frame": self.low_frame,
            "recovery_frame": self.recovery_frame,
            "reference_db": round(self.reference_db, 2),
            "min_db": round(self.min_db, 2),
            "drop_db": round(self.drop_db, 2),
            "recovery_db": round(self.recovery_db, 2),
            "valley_ms": self.valley_ms,
            "status": self.status,
            "qualified": self.qualified,
        }


def percentile(values: tuple[float, ...] | list[float], fraction: float) -> float:
    """Linear-interpolated percentile, `numpy.percentile`'s default method.

    Written out rather than imported: `domain/` stays free of numpy (that is the boundary the
    whole architecture rests on), and this is five lines that are exactly reproducible.
    """

    if not values:
        raise ValueError("percentile of an empty sequence")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def voice_valleys(
    envelope: EnergyEnvelope,
    burst: FrameRange,
    *,
    burst_index: int,
    min_drop_db: float,
    recovery_within_db: float,
    min_valley_ms: int,
    max_valley_ms: int,
) -> tuple[VoiceValley, ...]:
    """Every dip-and-recovery inside one burst, in time order, qualified or not.

    Deterministic and total. The scan never revisits a point: once a valley's recovery is
    found, the next one is looked for after it, so two cues can never share a recovery and the
    result is stable under any re-run with the same inputs.
    """

    points = envelope.within(burst)
    if len(points) < 3:
        return ()
    reference = percentile([p.db for p in points], 0.75)
    low_line = reference - min_drop_db
    recovery_line = reference - recovery_within_db
    hop = envelope.settings.hop_ms

    valleys: list[VoiceValley] = []
    index = 0
    while index < len(points):
        if points[index].db >= low_line:
            index += 1
            continue
        start = index
        end = index
        while end < len(points) and points[end].db < low_line:
            end += 1
        run = points[start:end]
        floor = min(run, key=lambda p: p.db)
        recovery = end
        while recovery < len(points) and points[recovery].db < recovery_line:
            recovery += 1

        valley_ms = (end - start) * hop
        recovered = points[recovery] if recovery < len(points) else None
        if recovered is None:
            status = CUE_NO_RECOVERY
        elif valley_ms < min_valley_ms:
            status = CUE_VALLEY_TOO_SHORT
        elif valley_ms > max_valley_ms:
            status = CUE_VALLEY_TOO_LONG
        else:
            status = CUE_QUALIFIED

        valleys.append(
            VoiceValley(
                burst_index=burst_index,
                low=FrameRange(run[0].frame, max(run[-1].frame + 1, run[0].frame + 1)),
                low_frame=floor.frame,
                recovery_frame=None if recovered is None else recovered.frame,
                reference_db=reference,
                min_db=floor.db,
                valley_ms=valley_ms,
                status=status,
                recovery_level_db=None if recovered is None else recovered.db,
            )
        )
        index = recovery + 1

    return tuple(valleys)


__all__ = [
    "CUE_NO_RECOVERY",
    "CUE_QUALIFIED",
    "CUE_VALLEY_TOO_LONG",
    "CUE_VALLEY_TOO_SHORT",
    "ENERGY_SETTING_KEYS",
    "EnergyEnvelope",
    "EnergyPoint",
    "EnergySettings",
    "VoiceValley",
    "percentile",
    "voice_valleys",
]
