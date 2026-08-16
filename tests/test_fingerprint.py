"""The source fingerprint must catch a re-cut and ignore everything it does not read."""

from __future__ import annotations

from dataclasses import replace

from davinci_auto_zoom.domain.fingerprint import fingerprint_material, source_fingerprint
from davinci_auto_zoom.domain.snapshot import (
    TimelineItemSnapshot,
    TimelineSnapshot,
    TrackSnapshot,
)

VOICE = 1
CUTS = 1


def _item(name: str, start: int, end: int, unique_id: str | None = None) -> TimelineItemSnapshot:
    return TimelineItemSnapshot(
        name=name, start=start, end=end, unique_id=unique_id or f"uid-{name}-{start}"
    )


def _timeline(
    *,
    v1: tuple[TimelineItemSnapshot, ...] | None = None,
    a1: tuple[TimelineItemSnapshot, ...] | None = None,
    v2: tuple[TimelineItemSnapshot, ...] = (),
    a2: tuple[TimelineItemSnapshot, ...] = (),
    start: int = 216000,
    end: int = 219555,
    frame_rate: float = 60.0,
    enabled: bool | None = None,
) -> TimelineSnapshot:
    v1 = v1 if v1 is not None else (_item("a.mov", 216000, 216132), _item("b.mov", 216132, 216300))
    a1 = a1 if a1 is not None else (_item("voice.wav", 216000, 216500),)
    return TimelineSnapshot(
        name="DAZ_INPUT",
        unique_id="uid-DAZ_INPUT",
        frame_rate=frame_rate,
        start_frame=start,
        end_frame=end,
        start_timecode="01:00:00:00",
        is_current=False,
        tracks=(
            TrackSnapshot("video", 1, "Video 1", enabled=enabled, items=v1),
            TrackSnapshot("video", 2, "Video 2", enabled=enabled, items=v2),
            TrackSnapshot("audio", 1, "Audio 1", enabled=enabled, items=a1),
            TrackSnapshot("audio", 2, "Audio 2", enabled=enabled, items=a2),
        ),
    )


def _hash(timeline: TimelineSnapshot, frame_rate: str = "60") -> str:
    return source_fingerprint(
        timeline,
        voice_audio_track=VOICE,
        cut_reference_video_track=CUTS,
        frame_rate=frame_rate,
    )


def test_the_fingerprint_is_deterministic() -> None:
    assert _hash(_timeline()) == _hash(_timeline())
    assert _hash(_timeline()).startswith("sha256:")


def test_the_item_order_resolve_happens_to_return_is_not_part_of_the_identity() -> None:
    forward = (_item("a.mov", 216000, 216132), _item("b.mov", 216132, 216300))
    assert _hash(_timeline(v1=forward)) == _hash(_timeline(v1=tuple(reversed(forward))))


def test_a_changed_cut_changes_the_fingerprint() -> None:
    """The plan snapped resets to these boundaries; moving one invalidates it."""

    recut = (_item("a.mov", 216000, 216150), _item("b.mov", 216150, 216300))
    assert _hash(_timeline(v1=recut)) != _hash(_timeline())


def test_a_moved_or_trimmed_voice_item_changes_the_fingerprint() -> None:
    moved = (_item("voice.wav", 216010, 216510),)
    trimmed = (_item("voice.wav", 216000, 216400),)
    baseline = _hash(_timeline())
    assert _hash(_timeline(a1=moved)) != baseline
    assert _hash(_timeline(a1=trimmed)) != baseline


def test_a_replaced_item_with_the_same_frames_still_changes_the_fingerprint() -> None:
    """Same range, different clip: the identity is hashed, not only the geometry."""

    replaced = (_item("other.wav", 216000, 216500, unique_id="uid-other"),)
    assert _hash(_timeline(a1=replaced)) != _hash(_timeline())


def test_the_range_and_frame_rate_are_part_of_the_fingerprint() -> None:
    baseline = _hash(_timeline())
    assert _hash(_timeline(end=219000)) != baseline
    assert _hash(_timeline(), frame_rate="30000/1001") != baseline


def test_tracks_the_planner_never_reads_are_ignored() -> None:
    """A change on V2 or A2 cannot change the plan, so it must not fail the check."""

    noisy = _timeline(v2=(_item("overlay.png", 216000, 216100),), a2=(_item("music", 0, 10),))
    assert _hash(noisy) == _hash(_timeline())


def test_the_unreliable_track_enable_flag_is_ignored() -> None:
    """`GetIsTrackEnabled` lies for any non-current timeline (D009); hashing it would too."""

    assert _hash(_timeline(enabled=True)) == _hash(_timeline(enabled=None))
    assert _hash(_timeline(enabled=False)) == _hash(_timeline(enabled=None))


def test_an_empty_unique_id_hashes_like_no_id_at_all() -> None:
    without = _timeline(a1=(replace(_item("voice.wav", 216000, 216500), unique_id=None),))
    empty = _timeline(a1=(replace(_item("voice.wav", 216000, 216500), unique_id=""),))
    assert _hash(without) == _hash(empty)


def test_the_hashed_material_names_exactly_the_two_tracks_the_planner_reads() -> None:
    material = fingerprint_material(
        _timeline(), voice_audio_track=1, cut_reference_video_track=1, frame_rate="60"
    )
    assert set(material) == {
        "version",
        "frame_rate",
        "start_frame",
        "end_frame",
        "voice_audio_track",
        "cut_reference_video_track",
    }
    assert material["voice_audio_track"]["items"][0]["name"] == "voice.wav"


def test_a_missing_configured_track_hashes_as_empty_rather_than_raising() -> None:
    """A stale index must produce a mismatch, not a crash mid-validation."""

    assert source_fingerprint(
        _timeline(), voice_audio_track=9, cut_reference_video_track=1, frame_rate="60"
    ) != _hash(_timeline())
