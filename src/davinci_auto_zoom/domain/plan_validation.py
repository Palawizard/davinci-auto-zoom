"""Is this plan still valid for what is in Resolve right now?

Phase 4 recorded a `PlanSource` next to every plan and validated nothing. This module is the
missing half, and it is pure: it re-derives a `PlanSource` from a *fresh* snapshot and
compares it, field by field, with the one the plan carries.

The comparison is deliberately total and deliberately strict. Every recorded field is
checked, and any single difference is a refusal — there is no "close enough" tier, because
the executor's whole safety argument is that it inserts what the planner decided against the
material the planner read. If either half of that sentence has moved, the correct answer is
to re-plan, not to place clips and hope.

Nothing here mutates anything, and nothing here talks to Resolve.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from davinci_auto_zoom.domain.dynamics import EnergySettings
from davinci_auto_zoom.domain.fingerprint import source_fingerprint
from davinci_auto_zoom.domain.planner import (
    AssetIdentity,
    PlannerSettings,
    PlanSource,
)
from davinci_auto_zoom.domain.snapshot import AssetSnapshot, TimelineSnapshot
from davinci_auto_zoom.domain.timebase import frame_rate_fraction


def timeline_frame_rate(timeline: TimelineSnapshot) -> str:
    """The timeline's exact frame rate as a canonical string, NTSC decimals resolved.

    Both the planning run and the validating run derive it the same way from the same
    Resolve setting, so `"60.0"` and `"60"` can never look like a rate change.
    """

    return str(frame_rate_fraction(timeline.frame_rate))


def asset_identities(
    assets: Mapping[str, str], found: Sequence[AssetSnapshot]
) -> tuple[AssetIdentity, ...]:
    """Resolve configured role -> name against the snapshot, recording every stable id.

    A role whose asset is missing or ambiguous still produces an entry, with empty ids: the
    preflight is what refuses such a project, and swallowing the role here would make the
    two halves of the comparison disagree about how many roles exist.
    """

    identities: list[AssetIdentity] = []
    for role, name in sorted(assets.items()):
        matches = [asset for asset in found if asset.name == name]
        match = matches[0] if len(matches) == 1 else None
        identities.append(
            AssetIdentity(
                role=role,
                name=name,
                media_id=match.media_id if match else None,
                unique_id=match.unique_id if match else None,
            )
        )
    return tuple(identities)


def build_plan_source(
    *,
    project: str,
    timeline: TimelineSnapshot,
    frame_rate: str,
    voice_audio_track: int,
    cut_reference_video_track: int,
    zoom_video_track: int,
    assets: Mapping[str, str],
    asset_transition_frames: Mapping[str, int],
    planner_settings: PlannerSettings,
    energy_settings: EnergySettings,
    found_assets: Sequence[AssetSnapshot] = (),
) -> PlanSource:
    """The single place a `PlanSource` is built, used both when planning and when validating.

    Sharing one constructor is what makes the later comparison meaningful: a difference can
    only come from Resolve, never from two call sites disagreeing about how to fill a field.
    """

    return PlanSource(
        project=project,
        timeline=timeline.name,
        timeline_unique_id=timeline.unique_id,
        start_frame=timeline.start_frame,
        end_frame=timeline.end_frame,
        frame_rate=frame_rate,
        voice_audio_track=voice_audio_track,
        cut_reference_video_track=cut_reference_video_track,
        zoom_video_track=zoom_video_track,
        assets=tuple(sorted(assets.items())),
        asset_transition_frames=tuple(sorted(asset_transition_frames.items())),
        planner_settings=planner_settings,
        energy_settings=energy_settings,
        asset_identities=asset_identities(assets, found_assets),
        structural_fingerprint=source_fingerprint(
            timeline,
            voice_audio_track=voice_audio_track,
            cut_reference_video_track=cut_reference_video_track,
            frame_rate=frame_rate,
        ),
    )


def _scalar_differences(recorded: PlanSource, current: PlanSource) -> list[str]:
    fields: tuple[tuple[str, object, object], ...] = (
        ("project", recorded.project, current.project),
        ("timeline", recorded.timeline, current.timeline),
        ("timeline_unique_id", recorded.timeline_unique_id, current.timeline_unique_id),
        ("start_frame", recorded.start_frame, current.start_frame),
        ("end_frame", recorded.end_frame, current.end_frame),
        ("frame_rate", recorded.frame_rate, current.frame_rate),
        ("voice_audio_track", recorded.voice_audio_track, current.voice_audio_track),
        (
            "cut_reference_video_track",
            recorded.cut_reference_video_track,
            current.cut_reference_video_track,
        ),
        ("zoom_video_track", recorded.zoom_video_track, current.zoom_video_track),
        ("assets", dict(recorded.assets), dict(current.assets)),
        (
            "asset_transition_frames",
            dict(recorded.asset_transition_frames),
            dict(current.asset_transition_frames),
        ),
        (
            "planner_settings",
            recorded.planner_settings.to_dict(),
            current.planner_settings.to_dict(),
        ),
        (
            "energy_settings",
            recorded.energy_settings.to_dict(),
            current.energy_settings.to_dict(),
        ),
    )
    return [
        f"{name}: plan has {before!r}, Resolve now has {after!r}"
        for name, before, after in fields
        if before != after
    ]


def _asset_identity_differences(recorded: PlanSource, current: PlanSource) -> list[str]:
    before = {identity.role: identity for identity in recorded.asset_identities}
    after = {identity.role: identity for identity in current.asset_identities}
    differences: list[str] = []
    for role in sorted(set(before) | set(after)):
        if role not in before:
            differences.append(f"asset role {role!r} was not recorded in the plan")
        elif role not in after:
            differences.append(f"asset role {role!r} no longer resolves in the project")
        elif before[role] != after[role]:
            differences.append(
                f"asset role {role!r}: plan resolved {before[role].to_dict()!r}, "
                f"Resolve now resolves {after[role].to_dict()!r}"
            )
    return differences


def plan_source_mismatches(recorded: PlanSource, current: PlanSource) -> tuple[str, ...]:
    """Every reason this plan may not be applied to `current`. Empty means cleared to write.

    The structural fingerprint is reported last and separately, because it answers a
    different question from the scalar fields: they say *which* timeline, it says whether
    that timeline has been re-cut since (see `domain/fingerprint.py`).
    """

    differences = _scalar_differences(recorded, current)
    differences.extend(_asset_identity_differences(recorded, current))
    if recorded.structural_fingerprint != current.structural_fingerprint:
        differences.append(
            "source structural fingerprint changed: plan was built against "
            f"{recorded.structural_fingerprint or '(none recorded)'}, the timeline now "
            f"hashes to {current.structural_fingerprint}. The voice track or the "
            "cut-reference track has been edited since the plan was made; re-run the plan."
        )
    return tuple(differences)


def validate_plan_source(
    recorded: PlanSource | None, current: PlanSource
) -> tuple[str, ...]:
    """`plan_source_mismatches`, plus the fail-closed answer for a plan with no source."""

    if recorded is None:
        return ("the plan carries no PlanSource, so it cannot be validated against Resolve",)
    return plan_source_mismatches(recorded, current)


__all__ = [
    "asset_identities",
    "build_plan_source",
    "plan_source_mismatches",
    "timeline_frame_rate",
    "validate_plan_source",
]
