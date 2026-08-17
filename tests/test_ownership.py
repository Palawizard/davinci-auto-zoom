"""The ownership format and the classifier — the whole Phase 7 safety argument, on data.

The single most important test in this file is
`test_a_manual_clip_identical_to_a_daz_clip_is_never_owned`: everything else exists to keep
that one honest.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from davinci_auto_zoom.domain.ownership import (
    AMBIGUOUS,
    NAMESPACE,
    OWNED,
    SCHEMA,
    STALE,
    UNOWNED,
    MarkerSnapshot,
    OwnedItemSnapshot,
    OwnershipExpectations,
    OwnershipFormatError,
    build_record,
    claims_ownership,
    classify_item,
    classify_track,
    free_marker_frame,
    parse,
    placement_id,
    serialize,
)

FINGERPRINT = "sha256:" + "a" * 64
PREVIEW = "uid-preview-1"
ASSETS = {"x0_to_face_x1": "FACE_X1", "face_x1_to_x0": "X1_TO_X0"}


def record(role: str = "x0_to_face_x1", start: int = 216132, end: int = 216174, **kwargs):
    return build_record(
        preview_id=kwargs.pop("preview_id", PREVIEW),
        role=role,
        asset=kwargs.pop("asset", ASSETS[role]),
        start=start,
        end=end,
        source_fingerprint=kwargs.pop("source_fingerprint", FINGERPRINT),
    )


def item(
    name: str = "FACE_X1",
    start: int = 216132,
    end: int = 216174,
    custom_data: str | None = None,
    markers: tuple[MarkerSnapshot, ...] | None = None,
) -> OwnedItemSnapshot:
    if markers is None:
        markers = (
            (MarkerSnapshot(frame=0, custom_data=custom_data),) if custom_data else ()
        )
    return OwnedItemSnapshot(
        name=name, start=start, end=end, unique_id=f"uid-{name}-{start}",
        track_index=3, markers=markers,
    )


def expectations(**kwargs) -> OwnershipExpectations:
    return OwnershipExpectations(
        preview_id=kwargs.pop("preview_id", PREVIEW),
        source_fingerprint=kwargs.pop("source_fingerprint", FINGERPRINT),
        assets=kwargs.pop("assets", ASSETS),
    )


# --- format -------------------------------------------------------------------------------


def test_serialize_parse_round_trip():
    original = record()
    assert parse(serialize(original)) == original


def test_serialization_is_deterministic_and_canonical():
    first, second = record(), record()
    assert serialize(first) == serialize(second)
    # Sorted keys, no insignificant whitespace: the same record is the same bytes anywhere.
    assert serialize(first) == json.dumps(
        json.loads(serialize(first)), sort_keys=True, separators=(",", ":")
    )


def test_the_record_carries_the_namespace_and_schema_verbatim():
    payload = json.loads(serialize(record()))
    assert payload["namespace"] == NAMESPACE
    assert payload["schema"] == SCHEMA


def test_placement_id_is_deterministic_and_free_of_any_clock():
    assert placement_id(
        source_fingerprint=FINGERPRINT, role="face_x1_to_x0", start=10, end=25, asset="X"
    ) == placement_id(
        source_fingerprint=FINGERPRINT, role="face_x1_to_x0", start=10, end=25, asset="X"
    )


@pytest.mark.parametrize(
    "changed",
    [
        {"start": 11},
        {"end": 26},
        {"role": "x0_to_face_x1"},
        {"asset": "OTHER"},
        {"source_fingerprint": "sha256:" + "b" * 64},
    ],
)
def test_every_hashed_input_changes_the_placement_id(changed):
    base: dict[str, Any] = dict(
        source_fingerprint=FINGERPRINT, role="face_x1_to_x0", start=10, end=25, asset="X"
    )
    assert placement_id(**base) != placement_id(**{**base, **changed})


def test_the_preview_a_placement_lands_in_is_not_part_of_its_identity():
    # A placement belongs to a plan, not to whichever preview happens to hold it.
    left = record(preview_id="uid-preview-1")
    right = record(preview_id="uid-preview-2")
    assert left.placement_id == right.placement_id


def test_a_wrong_namespace_is_refused():
    payload = json.loads(serialize(record()))
    payload["namespace"] = "some-other-tool"
    with pytest.raises(OwnershipFormatError, match="namespace"):
        parse(json.dumps(payload))


def test_an_unknown_schema_is_refused_rather_than_guessed():
    payload = json.loads(serialize(record()))
    payload["schema"] = SCHEMA + 1
    with pytest.raises(OwnershipFormatError, match="schema"):
        parse(json.dumps(payload))


def test_invalid_json_is_refused():
    with pytest.raises(OwnershipFormatError, match="not valid JSON"):
        parse('{"namespace": "davinci-auto-zoom", ')


def test_a_json_scalar_is_refused():
    with pytest.raises(OwnershipFormatError, match="expected an object"):
        parse('"davinci-auto-zoom"')


def test_an_invalid_role_name_is_refused():
    payload = json.loads(serialize(record()))
    payload["role"] = "Not A Role"
    with pytest.raises(OwnershipFormatError, match="not a valid role name"):
        parse(json.dumps(payload))


def test_a_missing_field_is_refused():
    payload = json.loads(serialize(record()))
    del payload["preview_id"]
    with pytest.raises(OwnershipFormatError, match="missing field 'preview_id'"):
        parse(json.dumps(payload))


def test_an_unexpected_field_is_refused():
    payload = json.loads(serialize(record()))
    payload["surprise"] = 1
    with pytest.raises(OwnershipFormatError, match="unexpected field"):
        parse(json.dumps(payload))


def test_a_boolean_does_not_pass_as_a_frame_number():
    payload = json.loads(serialize(record()))
    payload["start"] = True
    with pytest.raises(OwnershipFormatError, match="expected int"):
        parse(json.dumps(payload))


def test_a_backwards_frame_range_is_refused():
    payload = json.loads(serialize(record()))
    payload["start"], payload["end"] = payload["end"], payload["start"]
    with pytest.raises(OwnershipFormatError, match="forward range"):
        parse(json.dumps(payload))


def test_a_tampered_placement_id_is_refused():
    payload = json.loads(serialize(record()))
    payload["start"] += 1  # frames edited, digest left behind
    with pytest.raises(OwnershipFormatError, match="placement_id"):
        parse(json.dumps(payload))


def test_claims_ownership_is_generous_on_purpose():
    # Anything mentioning the namespace must reach the parser, so corruption surfaces as
    # ambiguity instead of being mistaken for a user marker.
    assert claims_ownership('{"namespace":"davinci-auto-zoom"')
    assert not claims_ownership("chapter 3")
    assert not claims_ownership("")


# --- classifier ---------------------------------------------------------------------------


def test_valid_owned_x1():
    verdict = classify_item(item(custom_data=serialize(record())), expectations())
    assert verdict.state == OWNED
    assert verdict.record is not None and verdict.record.role == "x0_to_face_x1"
    assert verdict.problems == ()


def test_valid_owned_x0():
    data = serialize(record("face_x1_to_x0", 216174, 216189))
    verdict = classify_item(
        item("X1_TO_X0", 216174, 216189, custom_data=data), expectations()
    )
    assert verdict.state == OWNED


def test_a_manual_clip_identical_to_a_daz_clip_is_never_owned():
    """The rule the whole phase exists for.

    Same name, same track, same frames, same duration, same asset — and no marker. It is the
    user's, and DAZ must say so.
    """

    daz = item(custom_data=serialize(record()))
    manual = item()  # byte-identical in every observable way except the marker
    assert (manual.name, manual.start, manual.end) == (daz.name, daz.start, daz.end)
    assert classify_item(daz, expectations()).state == OWNED
    assert classify_item(manual, expectations()).state == UNOWNED


def test_an_ordinary_user_marker_does_not_make_an_item_owned():
    user_marker = MarkerSnapshot(
        frame=0, custom_data="scene 4 retake", color="Blue", name="Marker 1"
    )
    assert classify_item(item(markers=(user_marker,)), expectations()).state == UNOWNED


def test_a_malformed_daz_marker_is_ambiguous_not_unowned():
    verdict = classify_item(
        item(custom_data='{"namespace":"davinci-auto-zoom","schema":'), expectations()
    )
    assert verdict.state == AMBIGUOUS
    assert verdict.blocks_deletion


def test_two_identical_daz_markers_are_owned_with_a_warning():
    data = serialize(record())
    verdict = classify_item(
        item(
            markers=(
                MarkerSnapshot(frame=0, custom_data=data),
                MarkerSnapshot(frame=5, custom_data=data),
            )
        ),
        expectations(),
    )
    assert verdict.state == OWNED
    assert any("identical DAZ markers" in w for w in verdict.warnings)


def test_two_contradictory_daz_markers_are_ambiguous():
    verdict = classify_item(
        item(
            markers=(
                MarkerSnapshot(frame=0, custom_data=serialize(record())),
                MarkerSnapshot(
                    frame=5, custom_data=serialize(record("face_x1_to_x0", 216174, 216189))
                ),
            )
        ),
        expectations(),
    )
    assert verdict.state == AMBIGUOUS
    assert "different ownership claims" in verdict.problems[0]


def test_a_marker_from_another_preview_is_stale_not_owned():
    data = serialize(record(preview_id="uid-some-other-preview"))
    verdict = classify_item(item(custom_data=data), expectations())
    assert verdict.state == STALE
    assert verdict.blocks_deletion


def test_a_marker_planned_from_different_source_material_is_stale():
    data = serialize(record(source_fingerprint="sha256:" + "b" * 64))
    verdict = classify_item(item(custom_data=data), expectations())
    assert verdict.state == STALE


def test_clean_can_skip_the_fingerprint_check_that_rebuild_enforces():
    data = serialize(record(source_fingerprint="sha256:" + "b" * 64))
    lenient = classify_item(
        item(custom_data=data), expectations(source_fingerprint=None)
    )
    assert lenient.state == OWNED


def test_an_unconfigured_role_is_ambiguous():
    data = serialize(record())
    verdict = classify_item(
        item(custom_data=data), expectations(assets={"face_x1_to_x0": "X1_TO_X0"})
    )
    assert verdict.state == AMBIGUOUS
    assert "not configured" in verdict.problems[0]


def test_a_role_pointing_at_a_different_configured_asset_is_ambiguous():
    data = serialize(record())
    verdict = classify_item(
        item(custom_data=data), expectations(assets={**ASSETS, "x0_to_face_x1": "OTHER_X1"})
    )
    assert verdict.state == AMBIGUOUS
    assert "configured for asset" in verdict.problems[0]


def test_a_clip_renamed_away_from_its_record_is_ambiguous():
    verdict = classify_item(
        item(name="SOMETHING_ELSE", custom_data=serialize(record())), expectations()
    )
    assert verdict.state == AMBIGUOUS


def test_a_moved_item_stays_owned_and_says_it_moved():
    data = serialize(record(start=216132, end=216174))
    verdict = classify_item(item(start=216500, end=216542, custom_data=data), expectations())
    assert verdict.state == OWNED
    assert any("has been moved" in w for w in verdict.warnings)


# --- track-level gate ---------------------------------------------------------------------


def test_a_foreign_clip_does_not_block_deletion_but_a_corrupt_one_does():
    owned = item(custom_data=serialize(record()))
    foreign = item("USER_TITLE", 216300, 216400)
    track = classify_track(3, [owned, foreign], expectations())
    assert len(track.owned) == 1 and len(track.unowned) == 1
    assert track.deletion_blockers() == ()

    corrupt = item(custom_data='{"namespace":"davinci-auto-zoom"}')
    blocked = classify_track(3, [owned, foreign, corrupt], expectations())
    assert len(blocked.deletion_blockers()) == 1


# --- marker placement ---------------------------------------------------------------------


def test_a_free_item_gets_local_frame_zero():
    assert free_marker_frame(42, ()) == 0


def test_an_existing_user_marker_at_zero_is_never_overwritten():
    assert free_marker_frame(42, (MarkerSnapshot(frame=0, custom_data="mine"),)) == 1


def test_a_long_user_marker_pushes_daz_past_its_whole_span():
    markers = (MarkerSnapshot(frame=0, custom_data="mine", duration=5),)
    assert free_marker_frame(42, markers) == 5


def test_a_fully_marked_short_reset_has_nowhere_safe_to_go():
    markers = tuple(MarkerSnapshot(frame=f, custom_data="mine") for f in range(15))
    assert free_marker_frame(15, markers) is None


def test_the_chosen_frame_always_stays_inside_the_item():
    frame = free_marker_frame(15, (MarkerSnapshot(frame=0, custom_data="x", duration=3),))
    assert frame is not None and 0 <= frame < 15
