"""Phase 9b: is there something on screen the GAMEPLAY zoom would actually help show?

Phase 9a asked "how much is moving" and got a negative answer (D056): motion amount,
secondary audio and silence length all have fully nested class ranges on the one reference
edit that exists. This module asks a different question — **motion topology** rather than
motion amount — and it asks it against the zoom's *measured* geometry rather than against a
guess about where the interesting part of the screen is.

Pure by construction, exactly like `gameplay.py`: no numpy, no ffmpeg, no Resolve object, no
filesystem, no clock. It consumes `ActivityFrame`s — a coarse grid of "how much did this cell
of the picture change" — and returns dataclasses.

## The ROI is derived, never assumed

`Roi.from_transform` turns the two numbers a Fusion `Transform` actually holds (its `Size` and
its `Center` offset) into the portion of the source image the state shows. For the measured
GAMEPLAY state — `Size = 1.25`, `Center` offset `(0, 0)` (D064) — that is the **central 80%**
of the frame, and nothing about it was chosen by this module.

That derivation is also the phase's most important negative: a centred 1.25x push-in is not a
spotlight on a screen region. It cannot magnify a corner HUD element — it crops the outer 10%
away. So "the interesting thing is inside the zoom's ROI" is a much weaker discriminator here
than the phase brief hoped, and the measurements say so rather than hiding it.

## Coordinates

Cells are row-major from the **top** of the picture, the order ffmpeg decodes in. `Roi` uses
the same convention, so `y0` is the top edge. Fusion's own Y axis points the other way; the
conversion happens once, in `from_transform`, and is symmetric for the GAMEPLAY state anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from davinci_auto_zoom.domain.models import Frame, FrameRange

#: Why `zoom_utility` said yes or no. Tokens, not prose: they are a report column.
ZOOM_LOCALIZED_EVENT = "localized_event_in_roi"
ZOOM_NO_EVENT = "nothing_new_visible"
ZOOM_TOO_GLOBAL = "event_covers_the_whole_frame"
ZOOM_OUTSIDE_ROI = "event_outside_the_zoom_roi"
ZOOM_NOT_PERSISTENT = "event_does_not_last"
ZOOM_NOT_NEW = "nothing_new_against_the_previous_seconds"


@dataclass(frozen=True, slots=True)
class ActivityFrame:
    """How much each cell of the picture changed, at one absolute timeline frame.

    `cells` is row-major, `rows * columns` long, values >= 0 in the same units as
    `vision.MotionPoint.motion` — a mean absolute difference of mean-subtracted luma.
    """

    frame: Frame
    columns: int
    rows: int
    cells: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.columns < 1 or self.rows < 1:
            raise ValueError("columns and rows must be >= 1")
        if len(self.cells) != self.columns * self.rows:
            raise ValueError(
                f"expected {self.columns * self.rows} cells, got {len(self.cells)}"
            )

    def cell(self, column: int, row: int) -> float:
        return self.cells[row * self.columns + column]

    @property
    def mean(self) -> float:
        return sum(self.cells) / len(self.cells)


@dataclass(frozen=True, slots=True)
class Roi:
    """A rectangle of the source image, in normalized `[0, 1]` coordinates, y from the top."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.x0 < self.x1 <= 1.0 and 0.0 <= self.y0 < self.y1 <= 1.0):
            raise ValueError(f"invalid roi {self!r}: need 0 <= a < b <= 1 on both axes")

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def contains(self, x: float, y: float) -> bool:
        """Is this normalized point inside? Half-open on the far edges, like a frame range."""

        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1

    @classmethod
    def from_transform(cls, size: float, center_x: float = 0.0, center_y: float = 0.0) -> Roi:
        """The region a Fusion `Transform` shows, from its `Size` and its `Center` *offset*.

        `size` is the scale (1.0 = untouched); `center_x`/`center_y` are the offsets the comp
        stores on the centre path, where `(0, 0)` means the tool's default centre `0.5` and Y
        points **up** as it does in Fusion. A Transform maps output `u` to source
        `(u - C) / size + 0.5` with `C = 0.5 + offset`, so the visible span is that expression
        at `u = 0` and `u = 1`, clamped to the image. The Y result is flipped once here so the
        returned `Roi` is top-down.

        Checked against the facecam ladder, which is the reason to trust it: `FACE_X1`
        (`1.5`, `+0.25`) comes out as `[0, 0.667]` on both axes and `FACE_X3` (`2.5`, `+0.75`)
        as `[0, 0.4]` — a family of rectangles all anchored in the corner where the facecam
        inset actually is (D064).
        """

        if size <= 0.0:
            raise ValueError("size must be > 0")

        def span(offset: float) -> tuple[float, float]:
            center = 0.5 + offset
            low = (0.0 - center) / size + 0.5
            high = (1.0 - center) / size + 0.5
            return max(0.0, min(1.0, low)), max(0.0, min(1.0, high))

        x0, x1 = span(center_x)
        bottom, top = span(center_y)
        return cls(x0=x0, y0=1.0 - top, x1=x1, y1=1.0 - bottom)

    def to_dict(self) -> dict[str, Any]:
        return {
            "x0": round(self.x0, 4),
            "y0": round(self.y0, 4),
            "x1": round(self.x1, 4),
            "y1": round(self.y1, 4),
            "area": round(self.area, 4),
        }


#: The measured GAMEPLAY state: `Size = 1.25`, centre offset `(0, 0)` (D064). Derived, not
#: typed in — change the two numbers and the rectangle follows.
GAMEPLAY_TRANSFORM_SIZE = 1.25
GAMEPLAY_ROI = Roi.from_transform(GAMEPLAY_TRANSFORM_SIZE)


def roi_mask(roi: Roi, columns: int, rows: int) -> tuple[bool, ...]:
    """Which cells of a `columns x rows` grid fall inside `roi`, by their centres."""

    if columns < 1 or rows < 1:
        raise ValueError("columns and rows must be >= 1")
    return tuple(
        roi.contains((column + 0.5) / columns, (row + 0.5) / rows)
        for row in range(rows)
        for column in range(columns)
    )


# ---------------------------------------------------------------------------------------
# Spatial statistics of one sample
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpatialSample:
    """One instant of picture change, read spatially instead of as a single number."""

    frame: Frame
    #: Mean cell activity inside and outside the ROI, and over the whole frame.
    inside: float
    outside: float
    overall: float
    #: Share of cells at or above the activity threshold, and of those, how they sit.
    active_fraction: float
    #: Area of the bounding box of the active cells, as a share of the frame. 0 when none.
    bbox_area: float
    #: Share of the frame's total activity carried by the busiest tenth of its cells. High
    #: means "one thing moved"; near 0.1 means "everything moved a little".
    concentration: float
    #: Connected groups of active cells (4-neighbourhood). Two players = two regions.
    regions: int

    @property
    def roi_ratio(self) -> float:
        """Inside vs the whole frame. 1.0 = the ROI is exactly as busy as the picture."""

        return self.inside / self.overall if self.overall > 0.0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame": self.frame,
            "inside": round(self.inside, 5),
            "outside": round(self.outside, 5),
            "overall": round(self.overall, 5),
            "roi_ratio": round(self.roi_ratio, 3),
            "active_fraction": round(self.active_fraction, 3),
            "bbox_area": round(self.bbox_area, 3),
            "concentration": round(self.concentration, 3),
            "regions": self.regions,
        }


def _bbox_area(active: Sequence[bool], columns: int, rows: int) -> float:
    xs = [i % columns for i, on in enumerate(active) if on]
    ys = [i // columns for i, on in enumerate(active) if on]
    if not xs:
        return 0.0
    return ((max(xs) - min(xs) + 1) / columns) * ((max(ys) - min(ys) + 1) / rows)


def _regions(active: Sequence[bool], columns: int, rows: int) -> int:
    """Connected components of active cells, 4-neighbourhood. Iterative: no recursion depth."""

    seen = [False] * len(active)
    count = 0
    for start in range(len(active)):
        if not active[start] or seen[start]:
            continue
        count += 1
        stack = [start]
        seen[start] = True
        while stack:
            index = stack.pop()
            column, row = index % columns, index // columns
            for neighbour_column, neighbour_row in (
                (column - 1, row), (column + 1, row), (column, row - 1), (column, row + 1)
            ):
                if not (0 <= neighbour_column < columns and 0 <= neighbour_row < rows):
                    continue
                neighbour = neighbour_row * columns + neighbour_column
                if active[neighbour] and not seen[neighbour]:
                    seen[neighbour] = True
                    stack.append(neighbour)
    return count


def _concentration(values: Sequence[float]) -> float:
    """Share of the total carried by the busiest tenth of the cells."""

    total = sum(values)
    if total <= 0.0:
        return 0.0
    top = max(1, len(values) // 10)
    return sum(sorted(values, reverse=True)[:top]) / total


def spatial_sample(
    activity: ActivityFrame, mask: Sequence[bool], active_threshold: float
) -> SpatialSample:
    """Read one activity grid spatially. `active_threshold` is an absolute cell level.

    It is absolute here on purpose and relative one level up: the caller derives it from the
    timeline's own median cell activity, so a gain or a codec change cannot move it (D051).
    """

    if len(mask) != len(activity.cells):
        raise ValueError("mask and activity grid must have the same shape")
    inside_cells = [v for v, on in zip(activity.cells, mask, strict=True) if on]
    outside_cells = [v for v, on in zip(activity.cells, mask, strict=True) if not on]
    active = tuple(value >= active_threshold for value in activity.cells)
    return SpatialSample(
        frame=activity.frame,
        inside=sum(inside_cells) / len(inside_cells) if inside_cells else 0.0,
        outside=sum(outside_cells) / len(outside_cells) if outside_cells else 0.0,
        overall=activity.mean,
        active_fraction=sum(active) / len(active),
        bbox_area=_bbox_area(active, activity.columns, activity.rows),
        concentration=_concentration(activity.cells),
        regions=_regions(active, activity.columns, activity.rows),
    )


# ---------------------------------------------------------------------------------------
# Window features: topology over time
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VisualEpisodeFeatures:
    """The spatial reading of one window. Every value is a mean or a share, never a raw dB.

    `persistence_seconds` and `onset` are the two that carry time; the rest describe the
    shape of the activity, not its amount.
    """

    samples: int = 0
    roi_activity: float = 0.0
    outside_activity: float = 0.0
    roi_ratio: float = 0.0
    active_cell_fraction: float = 0.0
    #: The busiest single sample of the window. "Did anything ever happen here" is a question
    #: about the peak; "is the whole shot moving" is a question about the mean, and averaging
    #: the two together makes a one-frame flicker indistinguishable from a small steady event.
    peak_active_cell_fraction: float = 0.0
    bbox_area: float = 0.0
    concentration: float = 0.0
    regions: float = 0.0
    #: Longest uninterrupted stretch of samples that were localized and busy, in seconds.
    persistence_seconds: float = 0.0
    #: How different this window's average picture-change map is from the seconds before it.
    novelty: float = 0.0
    #: First frame of that stretch, or None when the window never had one.
    onset: Frame | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "roi_activity": round(self.roi_activity, 5),
            "outside_activity": round(self.outside_activity, 5),
            "roi_ratio": round(self.roi_ratio, 3),
            "active_cell_fraction": round(self.active_cell_fraction, 3),
            "peak_active_cell_fraction": round(self.peak_active_cell_fraction, 3),
            "bbox_area": round(self.bbox_area, 3),
            "concentration": round(self.concentration, 3),
            "regions": round(self.regions, 2),
            "persistence_seconds": round(self.persistence_seconds, 2),
            "novelty": round(self.novelty, 4),
            "onset": self.onset,
        }


@dataclass(frozen=True, slots=True)
class ZoomUtilitySettings:
    """When would magnifying the picture actually help? Thresholds, never a fitted score.

    All of them are shape thresholds except `active_cell_threshold_ratio`, which is how much
    busier than the timeline's typical cell a cell has to be before it counts as moving.
    """

    active_cell_threshold_ratio: float = 2.0
    #: A localized event covers *some* of the frame but not most of it. Both bounds matter:
    #: below the first there is nothing to look at, above the second the zoom adds nothing
    #: because the event is already the whole shot (the gap-10 case).
    min_active_cell_fraction: float = 0.02
    max_active_cell_fraction: float = 0.35
    #: The busiest tenth of the cells must carry this much of the change for it to be "one
    #: thing happening somewhere" rather than "the whole picture drifting".
    min_concentration: float = 0.30
    #: The activity has to be at least this much of the frame's total, inside the ROI.
    min_roi_ratio: float = 1.0
    #: A single-frame flicker is not a reason to zoom.
    min_persistence_seconds: float = 0.5
    #: How much the window's activity map must differ from the seconds before it.
    min_novelty: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_cell_threshold_ratio": self.active_cell_threshold_ratio,
            "min_active_cell_fraction": self.min_active_cell_fraction,
            "max_active_cell_fraction": self.max_active_cell_fraction,
            "min_concentration": self.min_concentration,
            "min_roi_ratio": self.min_roi_ratio,
            "min_persistence_seconds": self.min_persistence_seconds,
            "min_novelty": self.min_novelty,
        }


@dataclass(frozen=True, slots=True)
class ZoomUtilityDecision:
    """Would the GAMEPLAY zoom earn its place here? One boolean and the reasons for it."""

    zoom_worthy: bool
    reasons: tuple[str, ...]
    onset: Frame | None = None

    @property
    def reason(self) -> str:
        return "+".join(self.reasons) if self.reasons else ZOOM_NO_EVENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "zoom_worthy": self.zoom_worthy,
            "reason": self.reason,
            "reasons": list(self.reasons),
            "onset": self.onset,
        }


def _localized(sample: SpatialSample, settings: ZoomUtilitySettings) -> bool:
    """One sample's shape test: busy enough, small enough, concentrated, and in the ROI."""

    return (
        settings.min_active_cell_fraction <= sample.active_fraction
        <= settings.max_active_cell_fraction
        and sample.concentration >= settings.min_concentration
        and sample.roi_ratio >= settings.min_roi_ratio
    )


def visual_episode_features(
    samples: Sequence[SpatialSample],
    window: FrameRange,
    sample_rate: int,
    *,
    baseline: Sequence[SpatialSample] = (),
    settings: ZoomUtilitySettings | None = None,
) -> VisualEpisodeFeatures:
    """Summarise the spatial samples inside `window`, and find the event's onset.

    `baseline` is the samples of the seconds *before* the window; novelty is how far the
    window's mean activity level moved away from it. With no baseline, novelty is 0 — "not
    measured", never "nothing new".
    """

    settings = settings or ZoomUtilitySettings()
    inside = [s for s in samples if window.start <= s.frame < window.end]
    if not inside:
        return VisualEpisodeFeatures()

    seconds_per_sample = 1.0 / sample_rate
    run = 0
    best_run = 0
    onset: Frame | None = None
    best_onset: Frame | None = None
    for sample in inside:
        if _localized(sample, settings):
            run += 1
            if onset is None:
                onset = sample.frame
            if run > best_run:
                best_run, best_onset = run, onset
        else:
            run, onset = 0, None

    mean_overall = sum(s.overall for s in inside) / len(inside)
    novelty = 0.0
    if baseline:
        before = sum(s.overall for s in baseline) / len(baseline)
        novelty = abs(mean_overall - before) / before if before > 0.0 else 0.0

    return VisualEpisodeFeatures(
        samples=len(inside),
        roi_activity=sum(s.inside for s in inside) / len(inside),
        outside_activity=sum(s.outside for s in inside) / len(inside),
        roi_ratio=sum(s.roi_ratio for s in inside) / len(inside),
        active_cell_fraction=sum(s.active_fraction for s in inside) / len(inside),
        peak_active_cell_fraction=max(s.active_fraction for s in inside),
        bbox_area=sum(s.bbox_area for s in inside) / len(inside),
        concentration=sum(s.concentration for s in inside) / len(inside),
        regions=sum(s.regions for s in inside) / len(inside),
        persistence_seconds=best_run * seconds_per_sample,
        novelty=novelty,
        onset=best_onset,
    )


def zoom_utility(
    features: VisualEpisodeFeatures, settings: ZoomUtilitySettings | None = None
) -> ZoomUtilityDecision:
    """Does this window hold a localized, lasting, new visual event inside the zoom's ROI?

    The reasons are the point. A `False` decision always names *which* of the four ways it
    failed, because the four hard negatives of Phase 9a fail in different ways and a rule that
    cannot tell them apart has not explained anything (gaps 1 and 11/12 have nothing to see;
    gap 10 has something to see that the zoom does not improve).
    """

    settings = settings or ZoomUtilitySettings()
    reasons: list[str] = []
    if features.samples == 0 or features.peak_active_cell_fraction < (
        settings.min_active_cell_fraction
    ):
        # Nothing to look at is one reason, not four: the other tests would only describe
        # the absence in three more ways.
        return ZoomUtilityDecision(False, (ZOOM_NO_EVENT,), features.onset)
    if features.active_cell_fraction > settings.max_active_cell_fraction:
        reasons.append(ZOOM_TOO_GLOBAL)
    if features.concentration < settings.min_concentration and not reasons:
        reasons.append(ZOOM_TOO_GLOBAL)
    if features.roi_ratio < settings.min_roi_ratio:
        reasons.append(ZOOM_OUTSIDE_ROI)
    if features.persistence_seconds < settings.min_persistence_seconds:
        reasons.append(ZOOM_NOT_PERSISTENT)
    if settings.min_novelty > 0.0 and features.novelty < settings.min_novelty:
        reasons.append(ZOOM_NOT_NEW)
    if reasons:
        return ZoomUtilityDecision(False, tuple(reasons), features.onset)
    return ZoomUtilityDecision(True, (ZOOM_LOCALIZED_EVENT,), features.onset)


@dataclass(frozen=True, slots=True)
class VisualWindowAnnotation:
    """One window's study labels: what the measurements claim, and what a human observed.

    **These are study labels, not runtime inputs.** Nothing in DAZ consumes an annotation to
    make a decision; the point is to be able to say, in one row, where a claim came from —
    which is the difference between "the feature says the event is global" and "a person
    looked and there was no event at all". On Phase 9b's material those two disagree on three
    of the four hard negatives, and that disagreement is the phase's result (D066).

    The `observed_*` fields are `None` until a human or the agent fills them in; `None` means
    "not looked at", never "false".
    """

    window_index: int
    #: Derived from the numbers, by `from_features`.
    new_visible_event: bool
    event_inside_roi: bool
    event_localized: bool
    event_persistent: bool
    zoom_would_help: bool
    reason: str
    #: Filled by the visual review, in the report rather than by any code path.
    observed_new_event: bool | None = None
    observed_zoom_would_help: bool | None = None
    observed_nothing_new_visible: bool | None = None
    observed_audio_topic_not_visible: bool | None = None
    notes: str = ""

    @classmethod
    def from_features(
        cls,
        window_index: int,
        features: VisualEpisodeFeatures,
        settings: ZoomUtilitySettings | None = None,
    ) -> VisualWindowAnnotation:
        settings = settings or ZoomUtilitySettings()
        decision = zoom_utility(features, settings)
        return cls(
            window_index=window_index,
            new_visible_event=(
                features.peak_active_cell_fraction >= settings.min_active_cell_fraction
            ),
            event_inside_roi=features.roi_ratio >= settings.min_roi_ratio,
            event_localized=(
                features.active_cell_fraction <= settings.max_active_cell_fraction
                and features.concentration >= settings.min_concentration
            ),
            event_persistent=(
                features.persistence_seconds >= settings.min_persistence_seconds
            ),
            zoom_would_help=decision.zoom_worthy,
            reason=decision.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_index": self.window_index,
            "new_visible_event": self.new_visible_event,
            "event_inside_roi": self.event_inside_roi,
            "event_localized": self.event_localized,
            "event_persistent": self.event_persistent,
            "zoom_would_help": self.zoom_would_help,
            "reason": self.reason,
            "observed_new_event": self.observed_new_event,
            "observed_zoom_would_help": self.observed_zoom_would_help,
            "observed_nothing_new_visible": self.observed_nothing_new_visible,
            "observed_audio_topic_not_visible": self.observed_audio_topic_not_visible,
            "notes": self.notes,
        }


__all__ = [
    "GAMEPLAY_ROI",
    "GAMEPLAY_TRANSFORM_SIZE",
    "ZOOM_LOCALIZED_EVENT",
    "ZOOM_NOT_NEW",
    "ZOOM_NOT_PERSISTENT",
    "ZOOM_NO_EVENT",
    "ZOOM_OUTSIDE_ROI",
    "ZOOM_TOO_GLOBAL",
    "ActivityFrame",
    "Roi",
    "SpatialSample",
    "VisualEpisodeFeatures",
    "VisualWindowAnnotation",
    "ZoomUtilityDecision",
    "ZoomUtilitySettings",
    "roi_mask",
    "spatial_sample",
    "visual_episode_features",
    "zoom_utility",
]
