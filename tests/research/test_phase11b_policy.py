"""The frozen candidates, the blind record, and the guarantee that no transcript leaks out."""

from __future__ import annotations

import json

import pytest

from davinci_auto_zoom.domain.transitions import STATE_FACE_X2, STATE_FACE_X3, STATE_X0
from tools.research.phase11a.structure import Clip, content_islands
from tools.research.phase11b.annotations import BLIND_SHORT_3, DEVELOPMENT
from tools.research.phase11b.policy import (
    APPOSITION,
    DISCOURSE_BOUNDARY,
    NEW_STEP,
    SAME_THOUGHT_CONTINUES,
    BlindPredictions,
    CutPrediction,
    SemanticAnnotation,
    baseline_policy,
    combined_policy,
    semantic_policy,
)
from tools.research.phase11b.simulate import RhythmContext


def context(frame: int, state: str, *, last: bool = False) -> RhythmContext:
    return RhythmContext(
        island_index=2,
        cut_index=0,
        frame=frame,
        state=state,
        frames_in_state=40,
        frames_at_x3=40 if state == STATE_FACE_X3 else None,
        frames_in_cycle=None if state == STATE_X0 else 80,
        frames_since_reset=120,
        cuts_in_cycle=2,
        transitions_in_cycle=2,
        is_last_cut_of_island=last,
    )


BOUNDARY = (SemanticAnnotation(100, 2, 0, DISCOURSE_BOUNDARY, NEW_STEP, "high"),)
CONTINUATION = (SemanticAnnotation(200, 2, 1, SAME_THOUGHT_CONTINUES, APPOSITION, "high"),)


def test_p0_resets_at_a_boundary_and_nowhere_else() -> None:
    decide = semantic_policy(BOUNDARY + CONTINUATION)
    assert decide(context(100, STATE_FACE_X2)) == "SEMANTIC_RESET"
    assert decide(context(200, STATE_FACE_X2)) is None


def test_every_candidate_is_gated_by_the_state_graph() -> None:
    for decide in (
        semantic_policy(BOUNDARY),
        combined_policy(BOUNDARY, rhythm=True, loop=True),
        baseline_policy(),
    ):
        assert decide(context(100, STATE_X0, last=True)) is None


def test_rhythm_only_adds_face_x3_cuts() -> None:
    p0 = semantic_policy(CONTINUATION)
    p2 = combined_policy(CONTINUATION, rhythm=True, loop=False)
    assert p0(context(200, STATE_FACE_X3)) is None
    assert p2(context(200, STATE_FACE_X3)) == "RHYTHM_REFRESH_RESET"
    assert p2(context(200, STATE_FACE_X2)) is None


def test_loop_override_owns_the_last_cut_and_outranks_the_other_reasons() -> None:
    p3 = combined_policy(BOUNDARY, rhythm=True, loop=True)
    assert p3(context(100, STATE_FACE_X3, last=True)) == "LOOP_RESET"
    assert p3(context(100, STATE_FACE_X3)) == "SEMANTIC_RESET"


def test_ambiguous_never_predicts_a_reset() -> None:
    annotation = SemanticAnnotation(300, 2, 2, "AMBIGUOUS", "whatever", "low")
    assert not annotation.predicts_reset
    assert semantic_policy((annotation,))(context(300, STATE_FACE_X3)) is None


def test_a_subtype_from_the_wrong_class_is_refused() -> None:
    with pytest.raises(ValueError):
        SemanticAnnotation(1, 0, 0, DISCOURSE_BOUNDARY, APPOSITION, "high")
    with pytest.raises(ValueError):
        SemanticAnnotation(1, 0, 0, SAME_THOUGHT_CONTINUES, NEW_STEP, "high")


def test_the_frozen_annotations_cover_every_cut_exactly_once() -> None:
    for frozen, expected in ((DEVELOPMENT, 28), (BLIND_SHORT_3, 14)):
        frames = [a.frame for a in frozen]
        assert len(frames) == expected
        assert len(set(frames)) == expected
        assert frames == sorted(frames)


def test_the_blind_record_serialises_deterministically_and_carries_no_text() -> None:
    row = CutPrediction(
        island_index=2,
        cut_index=0,
        frame=100,
        semantic_class=DISCOURSE_BOUNDARY,
        semantic_subtype=NEW_STEP,
        confidence="high",
        state={"P0": STATE_FACE_X2},
        predicted={"P0": "SEMANTIC_RESET"},
    )
    predictions = BlindPredictions(
        timeline="Timeline 1 copy",
        island_index=2,
        island_start=0,
        island_end=600,
        hard_cuts=(100,),
        rows=(row,),
        reset_frames={"P0": (100,)},
        entry_frames={"P0": (136,)},
    )
    first = predictions.to_json()
    assert first == predictions.to_json()

    payload = json.loads(first)
    vocabulary = {
        "island_index", "cut_index", "frame", "semantic_class", "semantic_subtype",
        "confidence", "state", "predicted",
    }
    assert set(payload["rows"][0]) == vocabulary
    # Every string that survives is a frame, a state, a rubric token or a timeline name.
    allowed = {
        "Timeline 1 copy", "P0", DISCOURSE_BOUNDARY, NEW_STEP, "high",
        STATE_FACE_X2, "SEMANTIC_RESET",
    }
    strings = {
        value
        for row in payload["rows"]
        for value in (*row.values(), *row["state"].values(), *row["predicted"].values())
        if isinstance(value, str)
    } | {payload["timeline"]}
    assert strings <= allowed


def test_the_first_three_islands_are_the_study_and_the_fourth_is_excluded() -> None:
    """Four Shorts with real gaps between them; only the first three may ever be used."""

    clips = [
        Clip(0, 100), Clip(100, 200),
        Clip(400, 500), Clip(500, 600),
        Clip(900, 1000), Clip(1000, 1100),
        Clip(1400, 1500), Clip(1500, 1600),
    ]
    islands = content_islands(clips, min_gap_frames=30)
    assert len(islands) == 4
    from tools.research.phase11b.study import BLIND_ISLAND, DEVELOPMENT_ISLANDS

    used = {*DEVELOPMENT_ISLANDS, BLIND_ISLAND}
    assert used == {0, 1, 2}
    assert 3 not in used
    assert islands[3].start == 1400
    # No state, cut or context ever crosses an island boundary.
    assert islands[2].hard_cuts == (1000,)
    assert islands[2].last_hard_cut == 1000
