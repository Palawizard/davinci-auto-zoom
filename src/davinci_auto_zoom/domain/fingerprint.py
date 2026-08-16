"""A deterministic fingerprint of the timeline structure a plan actually depends on.

A plan is only valid for the material it was built from. Names, unique ids and frame ranges
are not enough to prove that: a user can re-cut V1, slip a clip on the voice track, or trim a
sentence, and keep the timeline's name, its unique id and its total duration. The plan then
describes an edit that no longer exists, and an executor that trusts the name alone would
place fifteen-frame resets on frames that moved.

So the fingerprint hashes the *observable structure the planner read*, and nothing else:

* the analysed frame range and frame rate,
* every item on the configured voice audio track,
* every item on the configured cut-reference video track.

Both tracks are hashed the same way — stable id when Resolve gives one, name, start, end,
duration — because the planner's two inputs are exactly "where is there voice" and "where are
the hard cuts".

**Honest limits.** This proves the structure the scripting API exposes, and only that:

* a Fairlight change (level, EQ, a fade, a plugin) that alters what the voice *sounds* like
  without moving a clip is invisible here, and so is an OFX/Fusion change on a video clip;
* per-track enable state is deliberately excluded — Resolve reports it falsely for any
  timeline that is not the current one (D009), so hashing it would make the fingerprint
  depend on which tab the user happens to have open;
* tracks the planner never reads (V2, A2, A3, subtitles) are excluded on purpose: a change
  there cannot change the plan, and a fingerprint that fails for irrelevant reasons trains
  people to bypass it.

Two identical fingerprints therefore mean "the structure the planner looked at is unchanged",
not "the project is unchanged".
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from davinci_auto_zoom.domain.snapshot import TimelineSnapshot, TrackSnapshot

#: Bumped whenever the hashed material changes shape, so an old plan fails loudly instead of
#: comparing equal by accident.
FINGERPRINT_VERSION = 1


def _item_material(track: TrackSnapshot | None) -> list[dict[str, Any]]:
    """Canonical, order-independent description of one track's items."""

    if track is None:
        return []
    items = [
        {
            # Empty ids are normalised away: "" and None both mean "no usable identity", and
            # they must not hash differently.
            "id": item.unique_id or None,
            "name": item.name,
            "start": item.start,
            "end": item.end,
            "duration": item.duration,
        }
        for item in track.items
    ]
    # Sorted, so the order Resolve happens to return items in is not part of the identity.
    return sorted(
        items,
        key=lambda item: (item["start"], item["end"], item["name"], item["id"] or ""),
    )


def fingerprint_material(
    timeline: TimelineSnapshot,
    *,
    voice_audio_track: int,
    cut_reference_video_track: int,
    frame_rate: str,
) -> dict[str, Any]:
    """The exact structure that gets hashed. Returned separately so it can be diffed."""

    return {
        "version": FINGERPRINT_VERSION,
        "frame_rate": frame_rate,
        "start_frame": timeline.start_frame,
        "end_frame": timeline.end_frame,
        "voice_audio_track": {
            "index": voice_audio_track,
            "items": _item_material(timeline.track("audio", voice_audio_track)),
        },
        "cut_reference_video_track": {
            "index": cut_reference_video_track,
            "items": _item_material(timeline.track("video", cut_reference_video_track)),
        },
    }


def source_fingerprint(
    timeline: TimelineSnapshot,
    *,
    voice_audio_track: int,
    cut_reference_video_track: int,
    frame_rate: str,
) -> str:
    """SHA-256 of the canonical material, as `sha256:<hex>`.

    Canonical serialization: JSON with sorted keys, no insignificant whitespace, UTF-8. The
    same timeline always produces the same string, on any machine and in any Python session.
    """

    material = fingerprint_material(
        timeline,
        voice_audio_track=voice_audio_track,
        cut_reference_video_track=cut_reference_video_track,
        frame_rate=frame_rate,
    )
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


__all__ = [
    "FINGERPRINT_VERSION",
    "fingerprint_material",
    "source_fingerprint",
]
