"""Phase 9b: the ROI derivation, the spatial statistics, and the zoom-utility decision.

Every test here builds its activity grids by hand. No video file, no ffmpeg, no Resolve —
the same rule `test_dynamics.py` follows for the voice envelope, and the reason the negative
result of Phase 9b can be trusted: the measurements are checked against shapes whose answer
is known before the code runs.
"""

from __future__ import annotations

import pytest

from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.visual_episodes import (
    GAMEPLAY_ROI,
    ZOOM_NO_EVENT,
    ZOOM_NOT_PERSISTENT,
    ZOOM_TOO_GLOBAL,
    ActivityFrame,
    Roi,
    ZoomUtilitySettings,
    roi_mask,
    spatial_sample,
    visual_episode_features,
    zoom_utility,
)

COLUMNS, ROWS = 20, 10
SAMPLE_RATE = 10


def grid(frame: int, hot: set[tuple[int, int]], *, level: float = 1.0,
         floor: float = 0.0) -> ActivityFrame:
    """One activity grid where the `(column, row)` cells in `hot` carry `level`."""

    cells = tuple(
        level if (column, row) in hot else floor
        for row in range(ROWS)
        for column in range(COLUMNS)
    )
    return ActivityFrame(frame=frame, columns=COLUMNS, rows=ROWS, cells=cells)


def block(x0: int, x1: int, y0: int, y1: int) -> set[tuple[int, int]]:
    return {(column, row) for column in range(x0, x1) for row in range(y0, y1)}


# --- the ROI, derived from the Fusion transform ------------------------------------------


def test_the_gameplay_roi_is_the_central_eighty_percent() -> None:
    assert (GAMEPLAY_ROI.x0, GAMEPLAY_ROI.y0, GAMEPLAY_ROI.x1, GAMEPLAY_ROI.y1) == (
        pytest.approx((0.1, 0.1, 0.9, 0.9))
    )
    assert GAMEPLAY_ROI.area == pytest.approx(0.64)


@pytest.mark.parametrize(
    ("size", "offset", "expected"),
    [
        (1.0, 0.0, (0.0, 1.0)),  # X0 shows everything
        (1.5, 0.25, (0.0, 2 / 3)),  # FACE_X1
        (2.0, 0.5, (0.0, 0.5)),  # FACE_X2
        (2.5, 0.75, (0.0, 0.4)),  # FACE_X3
    ],
)
def test_the_facecam_ladder_comes_out_anchored_in_one_corner(
    size: float, offset: float, expected: tuple[float, float]
) -> None:
    """The check that makes the GAMEPLAY derivation credible rather than a guess (D064)."""

    roi = Roi.from_transform(size, offset, offset)
    assert (roi.x0, roi.x1) == pytest.approx(expected)
    # Y is flipped once, so a Fusion offset upward becomes the bottom of a top-down rectangle.
    assert (roi.y1, roi.y0) == pytest.approx((1.0 - expected[0], 1.0 - expected[1]))


def test_an_impossible_roi_is_refused_rather_than_clamped() -> None:
    with pytest.raises(ValueError, match="invalid roi"):
        Roi(x0=0.9, y0=0.0, x1=0.1, y1=1.0)
    with pytest.raises(ValueError, match="size must be"):
        Roi.from_transform(0.0)


def test_the_mask_covers_the_inside_cells_and_nothing_else() -> None:
    mask = roi_mask(Roi(0.0, 0.0, 0.5, 1.0), COLUMNS, ROWS)
    assert sum(mask) == COLUMNS * ROWS // 2
    assert all(mask[row * COLUMNS + column] for row in range(ROWS) for column in range(10))
    assert not any(
        mask[row * COLUMNS + column] for row in range(ROWS) for column in range(10, COLUMNS)
    )


# --- inside vs outside --------------------------------------------------------------------


def test_activity_outside_the_roi_does_not_count_as_activity_inside_it() -> None:
    mask = roi_mask(GAMEPLAY_ROI, COLUMNS, ROWS)
    corner = spatial_sample(grid(0, block(0, 2, 0, 1)), mask, 0.5)
    middle = spatial_sample(grid(0, block(9, 11, 4, 6)), mask, 0.5)
    assert corner.inside == 0.0
    assert corner.outside > 0.0
    assert corner.roi_ratio == 0.0
    assert middle.outside == 0.0
    assert middle.roi_ratio > 1.0


# --- concentration, scale, regions ----------------------------------------------------------


def test_a_small_moving_region_is_concentrated_and_a_full_frame_change_is_not() -> None:
    mask = roi_mask(GAMEPLAY_ROI, COLUMNS, ROWS)
    small = spatial_sample(grid(0, block(9, 11, 4, 6)), mask, 0.5)
    everywhere = spatial_sample(grid(0, block(0, COLUMNS, 0, ROWS)), mask, 0.5)
    assert small.active_fraction == pytest.approx(4 / 200)
    assert small.concentration == 1.0
    assert everywhere.active_fraction == 1.0
    assert everywhere.concentration == pytest.approx(0.1, abs=0.01)
    assert small.bbox_area < 0.05 < everywhere.bbox_area


def test_two_separate_events_are_two_regions_and_no_event_is_none() -> None:
    mask = roi_mask(GAMEPLAY_ROI, COLUMNS, ROWS)
    two = spatial_sample(grid(0, block(3, 5, 3, 5) | block(14, 16, 6, 8)), mask, 0.5)
    quiet = spatial_sample(grid(0, set()), mask, 0.5)
    assert two.regions == 2
    assert two.bbox_area > 0.3  # the box spanning both is large; the parts are not
    assert quiet.regions == 0
    assert quiet.active_fraction == 0.0
    assert quiet.bbox_area == 0.0


# --- persistence and novelty ----------------------------------------------------------------


def _features(samples: list[ActivityFrame], window: FrameRange, **kwargs: object):
    mask = roi_mask(GAMEPLAY_ROI, COLUMNS, ROWS)
    settings = ZoomUtilitySettings()
    spatial = [spatial_sample(s, mask, 0.5) for s in samples]
    baseline = kwargs.get("baseline")
    baseline_spatial = (
        [spatial_sample(s, mask, 0.5) for s in baseline] if baseline else ()  # type: ignore[arg-type]
    )
    return visual_episode_features(
        spatial, window, SAMPLE_RATE, baseline=baseline_spatial, settings=settings
    )


def test_a_single_frame_spike_does_not_persist_but_a_stable_event_does() -> None:
    event = block(9, 11, 4, 6)
    spike = [grid(f, event if f == 12 else set()) for f in range(0, 60, 6)]
    stable = [grid(f, event) for f in range(0, 60, 6)]
    window = FrameRange(0, 60)

    assert _features(spike, window).persistence_seconds == pytest.approx(0.1)
    assert _features(stable, window).persistence_seconds == pytest.approx(1.0)
    assert _features(stable, window).onset == 0


def test_novelty_is_zero_for_a_repeat_and_positive_for_something_new() -> None:
    event = block(9, 11, 4, 6)
    quiet_baseline = [grid(f, set(), floor=0.001) for f in range(-60, 0, 6)]
    same_baseline = [grid(f, event) for f in range(-60, 0, 6)]
    window_samples = [grid(f, event) for f in range(0, 60, 6)]
    window = FrameRange(0, 60)

    assert _features(window_samples, window, baseline=same_baseline).novelty == 0.0
    assert _features(window_samples, window, baseline=quiet_baseline).novelty > 0.0
    # No baseline at all is "not measured", never "nothing new".
    assert _features(window_samples, window).novelty == 0.0


def test_a_window_with_no_samples_measures_nothing_rather_than_zero_activity() -> None:
    features = _features([grid(f, block(9, 11, 4, 6)) for f in range(0, 30, 6)],
                         FrameRange(1000, 1060))
    assert features.samples == 0
    assert features.onset is None
    decision = zoom_utility(features)
    assert not decision.zoom_worthy
    assert decision.reasons == (ZOOM_NO_EVENT,)


# --- the decision -----------------------------------------------------------------------------


def test_a_localized_persistent_event_inside_the_roi_is_a_candidate() -> None:
    samples = [grid(f, block(9, 12, 4, 7)) for f in range(0, 60, 6)]
    decision = zoom_utility(_features(samples, FrameRange(0, 60)))
    assert decision.zoom_worthy
    assert decision.onset == 0


def test_full_frame_activity_is_not_automatically_a_candidate() -> None:
    """Gap 10's shape: something is unmistakably happening, and the zoom adds nothing."""

    samples = [grid(f, block(0, COLUMNS, 0, ROWS)) for f in range(0, 60, 6)]
    decision = zoom_utility(_features(samples, FrameRange(0, 60)))
    assert not decision.zoom_worthy
    assert ZOOM_TOO_GLOBAL in decision.reasons


def test_a_picture_where_nothing_moves_is_never_a_candidate() -> None:
    """Gaps 1, 11 and 12: the friends are audible, the screen has nothing new on it."""

    samples = [grid(f, set()) for f in range(0, 60, 6)]
    decision = zoom_utility(_features(samples, FrameRange(0, 60)))
    assert not decision.zoom_worthy
    assert ZOOM_NO_EVENT in decision.reasons


def test_a_flicker_is_refused_for_lasting_too_briefly() -> None:
    samples = [grid(f, block(9, 11, 4, 6) if f == 30 else set()) for f in range(0, 60, 6)]
    decision = zoom_utility(_features(samples, FrameRange(0, 60)))
    assert not decision.zoom_worthy
    assert ZOOM_NOT_PERSISTENT in decision.reasons


def test_the_same_grids_always_give_the_same_answer() -> None:
    samples = [grid(f, block(9, 12, 4, 7)) for f in range(0, 60, 6)]
    first = _features(samples, FrameRange(0, 60))
    second = _features(list(samples), FrameRange(0, 60))
    assert first == second
    assert zoom_utility(first) == zoom_utility(second)


def test_a_grid_whose_cells_do_not_match_its_shape_is_refused() -> None:
    with pytest.raises(ValueError, match="expected 6 cells"):
        ActivityFrame(frame=0, columns=3, rows=2, cells=(0.0, 0.0))
    with pytest.raises(ValueError, match="same shape"):
        spatial_sample(grid(0, set()), (True, False), 0.5)
