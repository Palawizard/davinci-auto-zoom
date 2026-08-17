"""Phase 7 spike: does a TimelineItem marker actually work as persistent DAZ ownership?

Nothing in Phase 7 may be built on the assumption that `TimelineItem.AddMarker` behaves on
our **Generator** items the way the README describes for clips in general. Generators expose
no Media Pool item and no source frames (see `domain/snapshot.probable_kind`), and that is
exactly the sort of item type an API surface quietly treats differently. So this probe proves
it on the real thing, on a scratch timeline, before `apply-preview` depends on it.

The eight criteria the mechanism has to satisfy, and where each is checked:

1. attached to the TimelineItem instance, not the shared Media Pool asset — `asset_isolation`
2. re-readable afterwards — `immediate_readback`
3. carries a namespace and structured data — `record_round_trip`
4. independent of name and position — proven by construction: both inserted items carry the
   same asset names as any manual clip would, and only the tagged ones classify as owned
5. works on both shapes of transition — one `x0_to_face_x1` and one `face_x1_to_x0` are
   inserted and tagged
6. survives a timeline switch and re-read — `survives_timeline_switch`
7. needs no UI automation — true of every call here
8. destroys no existing user data — `user_marker_preserved`, which puts a marker on the item
   *before* DAZ tags it and requires it back untouched afterwards

Plus one observation that is not a criterion but defines future semantics:
`duplicate_timeline_behaviour` records what Resolve does with the markers when the timeline is
duplicated (D038).

Safety: everything happens on `DAZ_SCRATCH_OWNERSHIP_*` timelines this run creates. They are
deleted in a `finally`, the user's active timeline is restored and proven, and the protected
timelines are audited before and after. `SaveProject()` is never called.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.ownership import (
    AMBIGUOUS,
    OWNED,
    STALE,
    UNOWNED,
    OwnershipExpectations,
    build_record,
    claims_ownership,
    classify_item,
    serialize,
)
from davinci_auto_zoom.domain.planner import AssetPlacement
from davinci_auto_zoom.domain.probe import (
    ApplyPreviewTarget,
    apply_preview_preflight_failures,
    signature_differences,
)
from davinci_auto_zoom.resolve.executor import (
    _protected_signatures,
    _timeline_identity,
    prepare_target_track,
    restore_previous_timeline,
)
from davinci_auto_zoom.resolve.ownership import (
    read_markers,
    snapshot_owned_item,
    snapshot_owned_track,
    tag_item,
)
from davinci_auto_zoom.resolve.session import (
    find_asset_items,
    find_timeline,
    snapshot_project,
)

#: Same family as the other scratch prefixes, so the existing "no scratch left behind" audit
#: covers it without being taught a new name.
SCRATCH_PREFIX = "DAZ_SCRATCH_OWNERSHIP_"

CONFIRM_FLAG = "--confirm-ownership-probe"

#: A marker the probe puts on a created item *before* tagging it, standing in for a user's.
#: It must come back untouched, at the frame DAZ would otherwise have preferred.
SENTINEL_CUSTOM_DATA = "daz-probe-sentinel-user-marker"


class OwnershipProbeRefused(RuntimeError):
    """Raised before any mutation when the probe's guards are not satisfied."""


def scratch_timeline_name(token: str | None = None) -> str:
    return f"{SCRATCH_PREFIX}{token or uuid.uuid4().hex[:8]}"


@dataclass
class Check:
    """One named question the probe answers, with the evidence attached."""

    name: str
    ok: bool = False
    detail: str = ""

    def line(self) -> str:
        return f"  [{'PASS' if self.ok else 'FAIL'}] {self.name:28} {self.detail}"


@dataclass
class OwnershipProbeReport:
    resolve_version: str = ""
    product_name: str = ""
    project_name: str = ""
    preflight_failures: tuple[str, ...] = ()

    scratch_name: str | None = None
    scratch_unique_id: str | None = None
    duplicate_name: str | None = None
    duplicate_unique_id: str | None = None
    wrote: bool = False

    checks: list[Check] = field(default_factory=list)
    #: Free-form observations that are recorded, not judged — `DuplicateTimeline` semantics
    #: above all. Documenting what Resolve does is the point; it is not a pass/fail.
    observations: list[str] = field(default_factory=list)

    previous_current_timeline: str | None = None
    previous_current_timeline_unique_id: str | None = None
    restored_current_timeline: str | None = None
    restored_current_timeline_unique_id: str | None = None
    current_timeline_restored: bool | None = None

    scratch_deleted: bool | None = None
    scratch_absent_after_cleanup: bool | None = None
    audit_checked: tuple[str, ...] = ()
    audit_differences: tuple[str, ...] = ()
    cleanup_failures: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, ok, detail))
        return ok

    @property
    def succeeded(self) -> bool:
        return (
            self.error is None
            and not self.preflight_failures
            and bool(self.checks)
            and all(check.ok for check in self.checks)
            and self.current_timeline_restored is True
            and self.scratch_absent_after_cleanup is True
            and not self.audit_differences
            and not self.cleanup_failures
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["succeeded"] = self.succeeded
        return data

    def to_text(self) -> str:
        lines = [
            "davinci-auto-zoom probe-ownership (Phase 7)",
            f"  product   : {self.product_name} {self.resolve_version}",
            f"  project   : {self.project_name}",
            f"  scratch   : {self.scratch_name or '-'} (wrote={self.wrote})",
        ]
        if self.preflight_failures:
            lines.append("  PREFLIGHT REFUSED:")
            lines.extend(f"    - {failure}" for failure in self.preflight_failures)
        lines.extend(check.line() for check in self.checks)
        if self.observations:
            lines.append("  observations (recorded, not judged):")
            lines.extend(f"    - {note}" for note in self.observations)
        lines.extend(
            [
                f"  restored  : {self.restored_current_timeline} "
                f"(was {self.previous_current_timeline}) "
                f"proven={self.current_timeline_restored}",
                f"  cleanup   : deleted={self.scratch_deleted} "
                f"absent={self.scratch_absent_after_cleanup}",
                f"  audit     : {', '.join(self.audit_checked) or '-'}",
            ]
        )
        if self.audit_differences:
            lines.append("  AUDIT DIFFERENCES:")
            lines.extend(f"    - {difference}" for difference in self.audit_differences)
        else:
            lines.append("  audit differences: none")
        if self.cleanup_failures:
            lines.append("  CLEANUP FAILURES:")
            lines.extend(f"    - {failure}" for failure in self.cleanup_failures)
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        lines.extend(f"  note: {note}" for note in self.notes)
        lines.append(f"  RESULT: {'PASS' if self.succeeded else 'FAIL'}")
        return "\n".join(lines)


def _placements(start: int, assets: dict[str, str]) -> tuple[AssetPlacement, ...]:
    """One item of each role, back to back. The reset is deliberately short (15 frames).

    Fifteen frames is the real reset length, and it is the case where "just put the marker at
    local frame 0" is most likely to collide with something. Probing the comfortable case only
    would prove nothing about the case that bites.
    """

    del assets
    return (
        AssetPlacement(
            asset_role="x0_to_face_x1",
            frames=FrameRange(start, start + 60),
            reason="ownership-probe",
        ),
        AssetPlacement(
            asset_role="face_x1_to_x0",
            frames=FrameRange(start + 60, start + 75),
            reason="ownership-probe",
        ),
    )


def _insert_probe_items(
    media_pool: Any,
    placements: tuple[AssetPlacement, ...],
    asset_items: dict[str, Any],
    assets: dict[str, str],
    track_index: int,
) -> list[Any]:
    created: list[Any] = []
    for placement in placements:
        name = assets[placement.asset_role]
        returned = media_pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": asset_items[name],
                    "startFrame": 0,
                    "endFrame": placement.duration_frames,
                    "trackIndex": track_index,
                    "recordFrame": placement.start_frame,
                }
            ]
        )
        items = list(returned or [])
        if len(items) != 1:
            raise RuntimeError(
                f"AppendToTimeline for {placement.asset_role} returned {len(items)} item(s), "
                "expected 1"
            )
        created.append(items[0])
    return created


def _run_checks(
    project: Any,
    scratch: Any,
    created: list[Any],
    placements: tuple[AssetPlacement, ...],
    assets: dict[str, str],
    track_index: int,
    previous_timeline: Any,
    report: OwnershipProbeReport,
) -> Any:
    """The experiment itself. Returns the duplicate timeline it made, so cleanup can drop it."""

    preview_id = str(scratch.GetUniqueId())
    fingerprint = "sha256:probe"
    records = {
        placement.asset_role: build_record(
            preview_id=preview_id,
            role=placement.asset_role,
            asset=assets[placement.asset_role],
            start=placement.start_frame,
            end=placement.end_frame,
            source_fingerprint=fingerprint,
        )
        for placement in placements
    }
    expectations = OwnershipExpectations(
        preview_id=preview_id, source_fingerprint=fingerprint, assets=assets
    )

    # Criterion 8, set up before anything is written: a marker that was already there.
    sentinel_item = created[0]
    sentinel_ok = bool(
        sentinel_item.AddMarker(0, "Blue", "user marker", "", 1, SENTINEL_CUSTOM_DATA)
    )
    report.check(
        "sentinel_marker_placed",
        sentinel_ok,
        "a stand-in user marker occupies local frame 0 of the x1 item"
        if sentinel_ok
        else "AddMarker could not place the stand-in user marker; criterion 8 is untested",
    )

    # 1. tag both items.
    tagged = True
    for item, placement in zip(created, placements, strict=True):
        result = tag_item(item, records[placement.asset_role])
        tagged = tagged and result.ok
        report.check(
            f"tag_{placement.asset_role}",
            result.ok,
            f"marker at local frame {result.marker_frame}"
            if result.ok
            else "; ".join(result.problems),
        )
    if sentinel_ok:
        report.check(
            "daz_avoided_the_user_frame",
            all(
                marker.frame != 0
                for marker in read_markers(sentinel_item)
                if claims_ownership(marker.custom_data)
            ),
            "DAZ did not take local frame 0, which was already occupied",
        )
        report.check(
            "user_marker_preserved",
            any(
                marker.custom_data == SENTINEL_CUSTOM_DATA and marker.frame == 0
                for marker in read_markers(sentinel_item)
            ),
            "the pre-existing user marker is still at local frame 0, unchanged",
        )

    # 2/3. immediate re-read of the whole track, through the real classifier.
    #
    # The track deliberately also holds the untagged control twin, so the expected shape is
    # "exactly the tagged items are owned, and the control is not" — not "everything is
    # owned". Getting this accounting wrong is how a probe talks itself into a false PASS.
    tagged_ids = {str(item.GetUniqueId()) for item in created}

    def _split(items: Any) -> tuple[list[Any], list[Any]]:
        verdicts = [classify_item(item, expectations) for item in items]
        return (
            [v for v in verdicts if v.item.unique_id in tagged_ids],
            [v for v in verdicts if v.item.unique_id not in tagged_ids],
        )

    before_tagged, before_others = _split(snapshot_owned_track(scratch, track_index))
    report.check(
        "immediate_readback",
        len(before_tagged) == len(created)
        and all(v.state == OWNED for v in before_tagged)
        and all(v.state == UNOWNED for v in before_others),
        f"{sum(v.state == OWNED for v in before_tagged)}/{len(created)} tagged item(s) owned, "
        f"{len(before_others)} untagged item(s) unowned",
    )
    report.check(
        "record_round_trip",
        bool(before_tagged)
        and all(
            v.record is not None
            and serialize(v.record) == serialize(records[v.record.role])
            for v in before_tagged
        ),
        "every record came back byte-identical to the one written",
    )

    # 6. switch away, switch back, re-read.
    other = previous_timeline if previous_timeline is not None else scratch
    switched = bool(project.SetCurrentTimeline(other)) and bool(
        project.SetCurrentTimeline(scratch)
    )
    if not switched:
        report.check("survives_timeline_switch", False, "could not switch timelines")
    else:
        after_tagged, after_others = _split(
            snapshot_owned_track(find_timeline(project, str(scratch.GetName())), track_index)
        )
        report.check(
            "survives_timeline_switch",
            len(after_tagged) == len(created)
            and all(v.state == OWNED for v in after_tagged)
            and all(v.state == UNOWNED for v in after_others),
            f"after switching to {str(other.GetName())!r} and back, "
            f"{sum(v.state == OWNED for v in after_tagged)}/{len(created)} still owned and "
            f"the untagged twin is still unowned",
        )
        report.check(
            "readback_is_stable",
            [v.to_dict() for v in after_tagged + after_others]
            == [v.to_dict() for v in before_tagged + before_others],
            "the classification is identical before and after the switch",
        )

    # 1 (asset isolation). The markers must live on the instance, not on the shared asset.
    # If they were on the MediaPoolItem, every other instance of FACE_X1 in the project would
    # now be "owned" — including the ones in DAZ_OUTPUT_MVP.
    x1_name = assets["x0_to_face_x1"]
    contaminated: list[str] = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None or str(timeline.GetName()) == str(scratch.GetName()):
            continue
        for track in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
            for item in timeline.GetItemListInTrack("video", track) or []:
                if str(item.GetName()) != x1_name:
                    continue
                if any(claims_ownership(m.custom_data) for m in read_markers(item)):
                    contaminated.append(
                        f"{timeline.GetName()} V{track} @{item.GetStart()}"
                    )
    report.check(
        "asset_isolation",
        not contaminated,
        "no other instance of the shared asset acquired ownership metadata"
        if not contaminated
        else f"OTHER INSTANCES CARRY DAZ MARKERS: {contaminated}",
    )

    # 9. what does DuplicateTimeline do with them? Recorded, then judged conservatively.
    duplicate = scratch.DuplicateTimeline(scratch_timeline_name())
    if duplicate is None:
        report.observations.append(
            "DuplicateTimeline returned None; duplication semantics remain unknown"
        )
        return None
    report.duplicate_name = str(duplicate.GetName())
    report.duplicate_unique_id = str(duplicate.GetUniqueId())
    copied = snapshot_owned_track(duplicate, track_index)
    carries = sum(
        1 for item in copied if any(claims_ownership(m.custom_data) for m in item.markers)
    )
    report.observations.append(
        f"DuplicateTimeline copied ownership markers onto {carries}/{len(copied)} item(s) of "
        f"{report.duplicate_name!r}"
    )
    # The copy's own identity, which is what a `clean`/`rebuild` targeting it would expect.
    copy_expectations = OwnershipExpectations(
        preview_id=str(duplicate.GetUniqueId()),
        source_fingerprint=fingerprint,
        assets=assets,
    )
    copy_verdicts = [classify_item(item, copy_expectations) for item in copied]
    states = sorted({v.state for v in copy_verdicts})
    report.observations.append(
        f"classified against its own identity, the duplicate's items are {states} — the "
        "records still name the source preview, so they are not automatically cleanable"
    )
    report.check(
        "duplicate_is_not_silently_owned",
        all(v.state != OWNED for v in copy_verdicts),
        "not one item of the hand-duplicated timeline classifies as owned against its own "
        "identity",
    )
    # The property that matters is that a destructive command *stops*. An untagged item on
    # the duplicate reads as `unowned`, which is not a blocker — but it is also never
    # deleted, so it cannot make the duplicate look cleanable. What must hold is that every
    # copied record blocks.
    report.check(
        "duplicate_blocks_destructive_work",
        carries == 0 or any(v.state in (STALE, AMBIGUOUS) for v in copy_verdicts),
        f"copied ownership reads as {states}, which fails destructive commands closed"
        if carries
        else "nothing copied, so there is no ownership to mistake for this timeline's",
    )
    return duplicate


def run_ownership_probe(
    resolve: Any,
    project: Any,
    config: Config,
    target: ApplyPreviewTarget,
    *,
    confirmed: bool,
) -> OwnershipProbeReport:
    """Prove (or disprove) TimelineItem markers as DAZ's ownership primitive, live."""

    report = OwnershipProbeReport(
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
    )
    if not confirmed:
        raise OwnershipProbeRefused(
            f"this probe creates and deletes a scratch timeline and requires {CONFIRM_FLAG}"
        )

    snapshot = snapshot_project(resolve, project, config)
    report.preflight_failures = apply_preview_preflight_failures(snapshot, target)
    if report.preflight_failures:
        raise OwnershipProbeRefused(
            "ownership probe preflight failed, nothing was created:\n"
            + "\n".join(f"  - {failure}" for failure in report.preflight_failures)
        )

    source_snapshot = snapshot.timeline(target.source_timeline)
    assert source_snapshot is not None  # guaranteed by preflight

    assets = dict(target.assets)
    asset_items = find_asset_items(project, config)
    missing = [name for name in target.asset_names if name not in asset_items]
    if missing:
        raise OwnershipProbeRefused(
            f"asset(s) {missing} could not be resolved in the Media Pool; nothing was created"
        )

    media_pool = project.GetMediaPool()
    previous_timeline = project.GetCurrentTimeline()
    (
        report.previous_current_timeline,
        report.previous_current_timeline_unique_id,
    ) = _timeline_identity(previous_timeline)
    before = _protected_signatures(project, config, target.protected_timelines)

    source = find_timeline(project, target.source_timeline)
    assert source is not None  # guaranteed by preflight

    scratch: Any = None
    duplicate: Any = None
    placements = _placements(source_snapshot.start_frame, assets)
    try:
        scratch = source.DuplicateTimeline(scratch_timeline_name())
        if scratch is None:
            raise RuntimeError("DuplicateTimeline returned None")
        report.wrote = True
        report.scratch_name = str(scratch.GetName())
        report.scratch_unique_id = str(scratch.GetUniqueId())

        # Reuses the executor's own preparation, so the probe tests the code path that will
        # actually run rather than a lookalike.
        prepare_target_track(scratch, config.zoom_video_track)
        if not project.SetCurrentTimeline(scratch):
            raise RuntimeError("SetCurrentTimeline(scratch) failed")

        created = _insert_probe_items(
            media_pool, placements, asset_items, assets, config.zoom_video_track
        )
        report.check(
            "items_created",
            len(created) == len(placements),
            f"{len(created)} generator item(s) inserted on V{config.zoom_video_track}",
        )
        # The negative control: one more instance of the same asset, never tagged.
        control = _insert_probe_items(
            media_pool,
            (
                AssetPlacement(
                    asset_role="x0_to_face_x1",
                    frames=FrameRange(
                        placements[-1].end_frame, placements[-1].end_frame + 60
                    ),
                    reason="ownership-probe-control",
                ),
            ),
            asset_items,
            assets,
            config.zoom_video_track,
        )
        control_verdict = classify_item(snapshot_owned_item(control[0]))
        report.check(
            "untagged_twin_is_unowned",
            control_verdict.state == UNOWNED,
            f"an identical, untagged {assets['x0_to_face_x1']!r} classifies as "
            f"{control_verdict.state}",
        )

        duplicate = _run_checks(
            project,
            scratch,
            created,
            placements,
            assets,
            config.zoom_video_track,
            previous_timeline,
            report,
        )
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
    finally:
        _cleanup(project, media_pool, previous_timeline, [duplicate, scratch], report)
        report.audit_checked = tuple(sorted(before))
        try:
            after = _protected_signatures(project, config, target.protected_timelines)
        except Exception as exc:
            report.cleanup_failures += (
                f"the post-run audit could not be read: {exc!r}",
            )
        else:
            differences: list[str] = []
            for key in sorted(set(before) | set(after)):
                differences.extend(signature_differences(key, before.get(key), after.get(key)))
            report.audit_differences = tuple(differences)

    return report


def _cleanup(
    project: Any,
    media_pool: Any,
    previous_timeline: Any,
    scratches: list[Any],
    report: OwnershipProbeReport,
) -> None:
    """Restore the user's timeline, then delete every scratch this run created — only those."""

    outcome = restore_previous_timeline(
        project,
        previous_timeline,
        expected_name=report.previous_current_timeline,
        expected_unique_id=report.previous_current_timeline_unique_id,
    )
    report.restored_current_timeline = outcome.name
    report.restored_current_timeline_unique_id = outcome.unique_id
    report.current_timeline_restored = outcome.restored
    report.cleanup_failures += tuple(
        f"could not restore the previously active timeline: {failure}"
        for failure in outcome.failures
    )
    if not outcome.restored:
        report.notes.append(
            "the previously active timeline could not be provably restored, so the scratch "
            "timelines were deliberately NOT deleted: one of them may still be the active "
            "timeline. No other timeline was touched. Delete them manually after inspection."
        )
        report.scratch_deleted = False
        report.scratch_absent_after_cleanup = False
        return

    names = [str(t.GetName()) for t in scratches if t is not None]
    stray = [name for name in names if not name.startswith(SCRATCH_PREFIX)]
    if stray:  # pragma: no cover - both names are built by this module
        report.cleanup_failures += (
            f"refusing to delete timeline(s) {stray}: not scratch names",
        )
        report.scratch_deleted = False
        return
    targets = [t for t in scratches if t is not None]
    if not targets:
        report.scratch_deleted = None
        report.scratch_absent_after_cleanup = True
        return
    try:
        report.scratch_deleted = bool(media_pool.DeleteTimelines(targets))
    except Exception as exc:
        report.scratch_deleted = False
        report.cleanup_failures += (f"DeleteTimelines raised {exc!r}",)
    try:
        remaining = [name for name in names if find_timeline(project, name) is not None]
    except Exception as exc:
        report.scratch_absent_after_cleanup = False
        report.cleanup_failures += (
            f"could not confirm the scratch timelines were deleted: {exc!r}",
        )
        return
    report.scratch_absent_after_cleanup = not remaining
    if remaining:
        report.cleanup_failures += (
            f"SCRATCH TIMELINE(S) LEFT BEHIND: {remaining}. No other timeline was touched.",
        )


__all__ = [
    "CONFIRM_FLAG",
    "SCRATCH_PREFIX",
    "OwnershipProbeRefused",
    "OwnershipProbeReport",
    "run_ownership_probe",
    "scratch_timeline_name",
]
