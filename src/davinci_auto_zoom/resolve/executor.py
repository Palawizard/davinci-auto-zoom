"""Phase 5: the first code that turns a plan into real clips, on a preview timeline.

    validated plan  ->  duplicate of the source timeline  ->  empty dedicated zoom track
      ->  one AppendToTimeline per placement  ->  verified 1:1 against the plan

The executor makes **no editorial decision**. It receives an `AssetPlacement` and inserts
exactly it: role, start frame, end frame, duration, target track. Bursts, the silence gate,
cut snapping and the x1/x0 durations belong to the planner and are never recomputed here
(D028). If this module ever needs to re-derive one of them, the architecture is wrong.

Safety model, in order:

1. fail-closed preflight, then a **fresh** re-derivation of `PlanSource` compared field by
   field with the one the plan carries, structural fingerprint included (D030). A single
   mismatch aborts before the source timeline is even duplicated;
2. the assets are re-resolved and their recorded ids re-checked, so a role cannot silently
   point at a different Media Pool item than the one that was planned against;
3. nothing is ever written to an existing user timeline. Every clip lands on a
   `DAZ_AUTO_PREVIEW_*` duplicate created by this run (D031);
4. the target zoom track must be **empty**. Missing tracks are appended until the configured
   index exists; a track holding any item at all is a refusal. DAZ never overwrites, shifts
   or deletes a clip, and it deliberately does not rely on Resolve's collision behaviour
   (D032);
5. every insertion is verified immediately, and the finished track is compared with the whole
   plan afterwards. Any difference fails the run;
6. the preview timeline is the transaction boundary. On failure `try/finally` restores the
   timeline the user had open and deletes the preview this run created — only that one, never
   an older `DAZ_AUTO_PREVIEW_*` (D031);
7. restoring the user's active timeline is **part of the transaction**, not a courtesy after
   it. `SetCurrentTimeline` is called, its return value is checked, and `GetCurrentTimeline()`
   is re-read and matched on unique id (name as fallback). Anything short of that proof fails
   the run — and, because the preview may then still *be* the active timeline, the preview is
   deliberately kept rather than deleted. Safety beats tidy cleanup (D033);
8. the post-run audit of the protected timelines and assets is the **last** thing that can
   veto the run. Keeping the preview is therefore the final decision of the transaction, taken
   only once the insertions, the whole-track verification, the restoration *and* the audit have
   all passed. An audit difference deletes the preview this run created, like any other
   failure.

**on success the preview is kept**, on purpose: it is the artefact a human inspects.

`SaveProject()` is never called. Creating the timeline through the API is enough, and forcing
a save of the user's project is not this tool's decision to make.

Every Resolve method used here is documented in the Developer/Scripting README installed with
the running build (verified for Studio 21.0.4.5): `Timeline.DuplicateTimeline`,
`Timeline.AddTrack`, `Timeline.GetTrackCount`, `Timeline.GetItemListInTrack`,
`Project.SetCurrentTimeline`, `MediaPool.AppendToTimeline([{clipInfo}])`,
`MediaPool.DeleteTimelines`, plus read-only getters.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.apply import (
    ExpectedItem,
    TrackPreparation,
    clip_info_for,
    expected_items,
    insertion_differences,
    placement_differences,
    plan_target_track,
)
from davinci_auto_zoom.domain.plan_validation import (
    build_plan_source,
    timeline_frame_rate,
    validate_plan_source,
)
from davinci_auto_zoom.domain.planner import PlanSource, ZoomPlan
from davinci_auto_zoom.domain.probe import (
    ApplyPreviewTarget,
    apply_preview_preflight_failures,
    asset_signature,
    signature_differences,
    structural_signature,
)
from davinci_auto_zoom.resolve.session import (
    find_asset_items,
    find_timeline,
    snapshot_assets,
    snapshot_project,
    snapshot_timeline,
)

#: Every timeline this command creates carries it. Cleanup refuses to delete anything else,
#: and a preview from an earlier run is never reused, overwritten or removed.
PREVIEW_PREFIX = "DAZ_AUTO_PREVIEW_"

#: Opt-in token. Distinct from both probe flags: this is the one command that intentionally
#: leaves something behind, so it cannot be started by muscle memory for the others.
CONFIRM_FLAG = "--confirm-create-preview-timeline"


class ApplyPreviewRefused(RuntimeError):
    """Raised before any mutation when the executor's guards are not satisfied."""


def preview_timeline_name(now: datetime | None = None, token: str | None = None) -> str:
    """`DAZ_AUTO_PREVIEW_<timestamp>_<short id>` — unique to one run, never guessed."""

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{PREVIEW_PREFIX}{stamp}_{token or uuid.uuid4().hex[:8]}"


@dataclass
class InsertionRecord:
    """What one placement asked for and what Resolve actually produced."""

    index: int
    role: str
    asset_name: str
    clip_info: dict[str, Any]
    expected: dict[str, Any]
    returned_items: int | None = None
    ok: bool = False
    problems: tuple[str, ...] = ()
    error: str | None = None
    name: str | None = None
    unique_id: str | None = None
    track_index: int | None = None
    start: int | None = None
    end: int | None = None
    duration: int | None = None
    fusion_comp_count: int | None = None


@dataclass
class ApplyReport:
    """Everything the run observed. `to_dict()` is the `--json` payload."""

    resolve_version: str = ""
    product_name: str = ""
    project_name: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    preflight_failures: tuple[str, ...] = ()
    source_mismatches: tuple[str, ...] = ()
    plan_fingerprint: str | None = None
    current_fingerprint: str | None = None
    validated_fields: tuple[str, ...] = ()

    planned_placements: int = 0
    nothing_to_apply: bool = False

    track: dict[str, Any] | None = None
    tracks_added: int = 0

    wrote: bool = False
    preview_name: str | None = None
    preview_unique_id: str | None = None
    preview_matched_source: bool | None = None
    preview_kept: bool = False
    preview_deleted: bool | None = None
    preview_absent_after_rollback: bool | None = None

    previous_current_timeline: str | None = None
    previous_current_timeline_unique_id: str | None = None
    restored_current_timeline: str | None = None
    restored_current_timeline_unique_id: str | None = None
    #: Tri-state on purpose: None means "the transaction never got as far as restoring".
    #: Only True is a proof, and only True can contribute to `succeeded`.
    current_timeline_restored: bool | None = None

    insertions: list[InsertionRecord] = field(default_factory=list)
    verification_differences: tuple[str, ...] = ()
    verified: bool | None = None

    audit_checked: tuple[str, ...] = ()
    audit_differences: tuple[str, ...] = ()
    #: Cleanup problems that are failures in their own right — a timeline that could not be
    #: restored, an audit that could not be read. Never silently swallowed.
    cleanup_failures: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def clean(self) -> bool:
        """True when the protected timelines are untouched and no preview leaked."""

        return not self.audit_differences and self.preview_absent_after_rollback is not False

    @property
    def succeeded(self) -> bool:
        """Every property Phase 5 promises, and nothing weaker.

        Written as one flat conjunction on purpose: a reader must be able to see the whole
        definition of "this run is safe to trust" without following it through helpers. If a
        property matters, it belongs here — not in `notes`.
        """

        if self.nothing_to_apply:
            # A plan with no placements is a legitimate, successful outcome: there was
            # nothing to apply, so no preview was created and nothing was touched. The
            # active timeline was never changed, so there is nothing to have restored.
            return (
                self.error is None
                and not self.preflight_failures
                and not self.source_mismatches
                and not self.audit_differences
                and not self.cleanup_failures
            )
        return (
            self.error is None
            and not self.preflight_failures
            and not self.source_mismatches
            and self.wrote
            and self.preview_matched_source is True
            and self.track is not None
            and bool(self.track["usable"])
            and bool(self.insertions)
            and all(record.ok for record in self.insertions)
            and self.verified is True
            and not self.verification_differences
            and self.current_timeline_restored is True
            and not self.audit_differences
            and not self.cleanup_failures
            and self.preview_kept
            and self.clean
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["clean"] = self.clean
        data["succeeded"] = self.succeeded
        return data

    def to_text(self) -> str:
        lines = [
            "davinci-auto-zoom apply-preview (Phase 5)",
            f"  product   : {self.product_name} {self.resolve_version}",
            f"  project   : {self.project_name}",
            f"  placements: {self.planned_placements}",
        ]
        if self.preflight_failures:
            lines.append("  PREFLIGHT REFUSED:")
            lines.extend(f"    - {failure}" for failure in self.preflight_failures)
        if self.source_mismatches:
            lines.append("  PLAN SOURCE REJECTED (nothing was created):")
            lines.extend(f"    - {mismatch}" for mismatch in self.source_mismatches)
        else:
            lines.append(
                f"  source ok : {len(self.validated_fields)} field(s) re-verified against a "
                f"fresh snapshot; fingerprint {self.current_fingerprint or '-'}"
            )
        if self.nothing_to_apply:
            lines.append("  nothing to apply: the plan contains no placement, so no preview "
                         "timeline was created")
        if self.track:
            lines.append(
                f"  track     : V{self.track['target_index']} "
                f"(video tracks {self.track['existing_video_tracks']}, "
                f"added {self.tracks_added}, empty={self.track['usable']})"
            )
            for blocker in self.track["blockers"]:
                lines.append(f"    - {blocker}")
        lines.append(f"  preview   : {self.preview_name or '-'} (wrote={self.wrote})")
        for record in self.insertions:
            status = "ok  " if record.ok else "FAIL"
            lines.append(
                f"  [{status}] {record.index:>3} {record.role:11} {record.asset_name:16} "
                f"rec={record.clip_info['recordFrame']} "
                f"end={record.clip_info['endFrame']:>4} -> start={record.start} "
                f"end={record.end} dur={record.duration} V{record.track_index} "
                f"comps={record.fusion_comp_count}"
            )
            if record.error:
                lines.append(f"           error: {record.error}")
            for problem in record.problems:
                lines.append(f"           {problem}")
        if self.verified is not None:
            lines.append(f"  verified  : {self.verified}")
        if self.verification_differences:
            lines.append("  PLAN vs TIMELINE DIFFERENCES:")
            lines.extend(f"    - {item}" for item in self.verification_differences)
        lines.extend(
            [
                f"  restored  : {self.restored_current_timeline} "
                f"(was {self.previous_current_timeline}) proven={self.current_timeline_restored}",
                f"  cleanup   : preview_kept={self.preview_kept} "
                f"preview_deleted={self.preview_deleted} "
                f"absent={self.preview_absent_after_rollback}",
                f"  audit     : {', '.join(self.audit_checked) or '-'}",
            ]
        )
        if self.cleanup_failures:
            lines.append("  CLEANUP FAILURES:")
            lines.extend(f"    - {failure}" for failure in self.cleanup_failures)
        if self.audit_differences:
            lines.append("  AUDIT DIFFERENCES:")
            lines.extend(f"    - {difference}" for difference in self.audit_differences)
        else:
            lines.append("  audit differences: none")
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        lines.extend(f"  note: {note}" for note in self.notes)
        if self.succeeded and self.preview_kept:
            lines.extend(
                [
                    "",
                    f"  PREVIEW READY FOR HUMAN VISUAL REVIEW: {self.preview_name}",
                    f"  unique id: {self.preview_unique_id}",
                    "  Open it in Resolve and watch the zooms. Structural and timing "
                    "correctness is proven; how it *looks* is not.",
                ]
            )
        lines.append(f"  RESULT: {'PASS' if self.succeeded else 'FAIL'}")
        return "\n".join(lines)


def _item_facts(item: Any) -> dict[str, Any]:
    track = item.GetTrackTypeAndIndex() or [None, None]
    return {
        "name": str(item.GetName()),
        "unique_id": item.GetUniqueId(),
        "track_index": int(track[1]) if track[1] is not None else None,
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
        "duration": int(item.GetDuration()),
        "fusion_comp_count": int(item.GetFusionCompCount() or 0),
    }


def _protected_signatures(
    project: Any, config: Config, names: tuple[str, ...]
) -> dict[str, Any]:
    signatures: dict[str, Any] = {}
    for name in names:
        timeline = find_timeline(project, name)
        if timeline is not None:
            signatures[name] = structural_signature(
                snapshot_timeline(timeline, is_current=False)
            )
    _, assets = snapshot_assets(project, config)
    signatures["assets"] = asset_signature(assets)
    return signatures


def _strip_item_ids(signature: dict[str, Any]) -> dict[str, Any]:
    return {
        **signature,
        "tracks": [
            {
                **track,
                "items": [
                    {k: v for k, v in item.items() if k != "unique_id"}
                    for item in track["items"]
                ],
            }
            for track in signature["tracks"]
        ],
    }


def _prepare_target_track(
    preview: Any, target_index: int, report: ApplyReport
) -> None:
    """Append empty video tracks until the configured index exists, and prove it is empty.

    Only ever *adds* tracks. V1/V2 keep their clips, their names and their order; nothing is
    moved, re-enabled or deleted, so the preview stays a faithful copy of the source plus the
    empty tracks DAZ needs to reach its own.
    """

    while int(preview.GetTrackCount("video") or 0) < target_index:
        before = int(preview.GetTrackCount("video") or 0)
        if not preview.AddTrack("video"):
            raise RuntimeError(
                f"AddTrack('video') failed while growing the preview from V{before} "
                f"towards V{target_index}"
            )
        after = int(preview.GetTrackCount("video") or 0)
        if after != before + 1:
            raise RuntimeError(
                f"AddTrack('video') reported success but the video track count went "
                f"{before} -> {after}"
            )
        report.tracks_added += 1

    count = int(preview.GetTrackCount("video") or 0)
    if count < target_index:  # pragma: no cover - the loop above cannot exit early
        raise RuntimeError(f"preview has {count} video track(s), needs V{target_index}")
    existing = preview.GetItemListInTrack("video", target_index) or []
    if existing:
        raise RuntimeError(
            f"preview target track V{target_index} is not empty ({len(existing)} item(s)) "
            "after preparation; refusing to insert"
        )


def _insert(
    media_pool: Any,
    index: int,
    placement: Any,
    expected: ExpectedItem,
    media_pool_item: Any,
    track_index: int,
) -> InsertionRecord:
    """One placement, one `AppendToTimeline` call, checked immediately.

    Sequential rather than batched on purpose: with one clipInfo per call, a failure names
    the placement that caused it instead of leaving 28 insertions to be attributed.
    """

    clip_info = clip_info_for(placement, media_pool_item, track_index)
    record = InsertionRecord(
        index=index,
        role=placement.asset_role,
        asset_name=expected.name,
        # The MediaPoolItem proxy must never leak into the report.
        clip_info={k: (expected.name if k == "mediaPoolItem" else v) for k, v in clip_info.items()},
        expected=expected.to_dict(),
    )

    try:
        returned = media_pool.AppendToTimeline([clip_info])
    except Exception as exc:  # the Blackmagic wrapper raises bare exceptions
        record.error = f"AppendToTimeline raised {exc!r}"
        return record

    items = list(returned or [])
    record.returned_items = len(items)
    if len(items) != 1:
        record.error = f"expected exactly 1 returned TimelineItem, got {len(items)}"
        return record

    facts = _item_facts(items[0])
    for key, value in facts.items():
        setattr(record, key, value)
    record.problems = insertion_differences(expected, facts)
    record.ok = not record.problems
    if not record.ok:
        record.error = "the created item does not match the placement"
    return record


def _verify_target_track(
    preview: Any, expected: tuple[ExpectedItem, ...], track_index: int, report: ApplyReport
) -> None:
    """Re-read the finished track and compare it with the whole plan, as data."""

    snapshot = snapshot_timeline(preview, is_current=True)
    track = snapshot.track("video", track_index)
    actual = track.items if track is not None else ()
    report.verification_differences = placement_differences(expected, actual)
    report.verified = not report.verification_differences


def _timeline_identity(timeline: Any) -> tuple[str | None, str | None]:
    """`(name, unique_id)` for a live timeline. The id is optional, the name is not."""

    if timeline is None:
        return None, None
    unique_id: Any = None
    with suppress(Exception):  # GetUniqueId is documented, but identity must not hinge on it
        unique_id = timeline.GetUniqueId()
    return str(timeline.GetName()), str(unique_id) if unique_id is not None else None


def _restore_previous_timeline(
    project: Any, previous_timeline: Any, report: ApplyReport
) -> bool:
    """Put the user's timeline back, and *prove* it. Returns False unless proven.

    Fail-closed by design: restoring what the user had open is part of the transaction's
    success, not a best-effort courtesy (D033). Three things must hold — the call must not
    raise, it must not report failure, and the re-read must land on the same timeline —
    because each of them has a plausible failure mode that the others would not catch.
    """

    if previous_timeline is None:
        # No timeline was open before the run, so there is nothing to restore. Whatever is
        # current now is recorded, not asserted.
        name, unique_id = _timeline_identity(project.GetCurrentTimeline())
        report.restored_current_timeline = name
        report.restored_current_timeline_unique_id = unique_id
        report.current_timeline_restored = True
        return True

    failures: list[str] = []
    try:
        returned = project.SetCurrentTimeline(previous_timeline)
    except Exception as exc:
        failures.append(
            f"SetCurrentTimeline({report.previous_current_timeline!r}) raised {exc!r}"
        )
    else:
        if not returned:
            failures.append(
                f"SetCurrentTimeline({report.previous_current_timeline!r}) returned "
                f"{returned!r}"
            )

    try:
        name, unique_id = _timeline_identity(project.GetCurrentTimeline())
    except Exception as exc:
        # Runs inside a `finally`; an unreadable Resolve must fail the run, not crash it.
        failures.append(f"GetCurrentTimeline() could not be re-read: {exc!r}")
        name, unique_id = None, None
    report.restored_current_timeline = name
    report.restored_current_timeline_unique_id = unique_id

    want_id = report.previous_current_timeline_unique_id
    if name != report.previous_current_timeline:
        failures.append(
            f"the active timeline is {name!r}, expected {report.previous_current_timeline!r}"
        )
    elif want_id is not None and unique_id is not None and unique_id != want_id:
        failures.append(
            f"the active timeline is named {name!r} but its unique id is {unique_id!r}, "
            f"expected {want_id!r}"
        )

    report.current_timeline_restored = not failures
    if failures:
        report.cleanup_failures += tuple(
            f"could not restore the previously active timeline: {failure}"
            for failure in failures
        )
    return not failures


def _dispose_preview(
    project: Any,
    media_pool: Any,
    preview: Any,
    report: ApplyReport,
    *,
    keep: bool,
    restored: bool,
) -> None:
    """Keep or delete the preview this run made — never anything else.

    Deletion is whole-transaction: the preview is dropped entirely rather than picking
    individual clips back out of it, because removing a timeline this run created is a
    smaller and far more verifiable action than un-editing one.
    """

    if preview is None:
        return
    name = report.preview_name or ""
    if keep:
        report.preview_kept = True
        return
    if not restored:
        # The preview may still *be* the active timeline. Deleting the timeline Resolve has
        # open is exactly the kind of blind cleanup this tool must not do.
        report.preview_deleted = False
        report.notes.append(
            f"PREVIEW TIMELINE DELIBERATELY NOT DELETED: {name!r}. This run failed, but the "
            "previously active timeline could not be provably restored, so this preview may "
            "still be the active timeline and deleting it was not safe. No other timeline "
            "was touched. Inspect Resolve, then delete it manually."
        )
        return
    if not name.startswith(PREVIEW_PREFIX):
        # Belt and braces: never hand a timeline this run did not create to DeleteTimelines.
        report.notes.append(f"refusing to delete timeline {name!r}: not a preview name")
        report.preview_deleted = False
        return
    try:
        report.preview_deleted = bool(media_pool.DeleteTimelines([preview]))
    except Exception as exc:
        report.preview_deleted = False
        report.notes.append(f"DeleteTimelines raised {exc!r}")
    try:
        report.preview_absent_after_rollback = find_timeline(project, name) is None
    except Exception as exc:
        # This runs inside the caller's `finally`. An exception here would replace the real
        # failure with a cleanup traceback, which is the one thing the report must never do.
        report.preview_absent_after_rollback = False
        report.cleanup_failures += (
            f"could not confirm whether the preview {name!r} was deleted: {exc!r}",
        )
    if not report.preview_absent_after_rollback:
        report.notes.append(
            f"FAILED PREVIEW TIMELINE LEFT BEHIND: {name!r}. It was not deleted; no other "
            "timeline was touched. Delete it manually after inspection."
        )


def _finish_transaction(
    project: Any,
    media_pool: Any,
    config: Config,
    target: ApplyPreviewTarget,
    previous_timeline: Any,
    preview: Any,
    report: ApplyReport,
    before: dict[str, Any],
    *,
    verified: bool,
) -> None:
    """Close the transaction: restore, audit, and only then decide the preview's fate.

    Order matters and is the whole point. Keeping the preview is the *last* decision, taken
    once every other obligation has been discharged, so a run can never announce a keepable
    preview while the user's timeline is still missing or a protected timeline has moved.
    """

    restored = _restore_previous_timeline(project, previous_timeline, report)

    report.audit_checked = tuple(sorted(before))
    try:
        after = _protected_signatures(project, config, target.protected_timelines)
    except Exception as exc:
        report.cleanup_failures += (
            f"the post-run audit of the protected timelines could not be read: {exc!r}",
        )
    else:
        differences: list[str] = []
        for key in sorted(set(before) | set(after)):
            differences.extend(signature_differences(key, before.get(key), after.get(key)))
        report.audit_differences = tuple(differences)

    keep = (
        verified
        and restored
        and not report.audit_differences
        and not report.cleanup_failures
    )
    _dispose_preview(
        project, media_pool, preview, report, keep=keep, restored=restored
    )

    if report.error is None and (report.cleanup_failures or report.audit_differences):
        # The insertions were fine, so nothing above set an error. The run still failed, and
        # it must say so rather than reporting a clean cleanup.
        report.error = "; ".join(report.cleanup_failures + report.audit_differences)


def apply_preview(
    resolve: Any,
    project: Any,
    config: Config,
    target: ApplyPreviewTarget,
    plan: ZoomPlan,
    *,
    confirmed: bool,
    now: datetime | None = None,
) -> ApplyReport:
    """Apply `plan` to a new preview timeline. Refuses unless every guard passes."""

    report = ApplyReport(
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
        target=asdict(target),
        planned_placements=len(plan.placements),
    )

    if not confirmed:
        raise ApplyPreviewRefused(
            f"this command creates a new timeline in your project and requires "
            f"{CONFIRM_FLAG} to run"
        )

    snapshot = snapshot_project(resolve, project, config)
    report.preflight_failures = apply_preview_preflight_failures(snapshot, target)
    if report.preflight_failures:
        raise ApplyPreviewRefused(
            "apply preflight failed, nothing was created:\n"
            + "\n".join(f"  - {failure}" for failure in report.preflight_failures)
        )

    source_snapshot = snapshot.timeline(target.source_timeline)
    assert source_snapshot is not None  # guaranteed by preflight
    assert config.asset_timing is not None  # guaranteed by the CLI before planning

    # Fresh re-derivation of everything the plan claims about its source, then a total
    # comparison. This happens *before* any mutation, so a stale plan costs nothing.
    current_source: PlanSource = build_plan_source(
        project=snapshot.project_name,
        timeline=source_snapshot,
        frame_rate=timeline_frame_rate(source_snapshot),
        voice_audio_track=config.voice_audio_track,
        cut_reference_video_track=config.cut_reference_video_track,
        zoom_video_track=config.zoom_video_track,
        assets=config.assets,
        asset_transition_frames=config.asset_timing.to_dict(),
        planner_settings=config.planner,
        found_assets=snapshot.assets,
    )
    report.plan_fingerprint = plan.source.structural_fingerprint if plan.source else None
    report.current_fingerprint = current_source.structural_fingerprint
    report.validated_fields = tuple(sorted(current_source.to_dict()))
    report.source_mismatches = validate_plan_source(plan.source, current_source)
    if report.source_mismatches:
        raise ApplyPreviewRefused(
            "the plan no longer matches the project, nothing was created:\n"
            + "\n".join(f"  - {mismatch}" for mismatch in report.source_mismatches)
        )

    if not plan.placements:
        # Nothing to apply is a result, not a failure — and it is emphatically not a reason
        # to leave an empty preview timeline lying around in the user's project.
        report.nothing_to_apply = True
        report.notes.append(
            "the plan contains no placement, so no preview timeline was created"
        )
        return report

    if not plan.valid:
        raise ApplyPreviewRefused(
            "the plan is internally invalid and must not be applied:\n"
            + "\n".join(f"  - {problem}" for problem in plan.overlaps)
        )

    # The target track is checked on the source first: a preview that could never receive the
    # zooms should not be created at all.
    preparation: TrackPreparation = plan_target_track(
        source_snapshot, config.zoom_video_track
    )
    report.track = preparation.to_dict()
    if not preparation.usable:
        raise ApplyPreviewRefused(
            "the configured zoom track cannot be used, nothing was created:\n"
            + "\n".join(f"  - {blocker}" for blocker in preparation.blockers)
        )

    asset_items = find_asset_items(project, config)
    missing = [name for name in target.asset_names if name not in asset_items]
    if missing:
        raise ApplyPreviewRefused(
            f"asset(s) {missing} are in the snapshot but no Media Pool item could be "
            "resolved for them; nothing was created"
        )

    expected = expected_items(plan, dict(target.assets), config.zoom_video_track)

    media_pool = project.GetMediaPool()
    # Captured before any mutation, and by identity rather than by name alone: the cleanup
    # has to be able to *prove* it put this exact timeline back.
    previous_timeline = project.GetCurrentTimeline()
    (
        report.previous_current_timeline,
        report.previous_current_timeline_unique_id,
    ) = _timeline_identity(previous_timeline)
    before = _protected_signatures(project, config, target.protected_timelines)

    source = find_timeline(project, target.source_timeline)
    assert source is not None  # guaranteed by preflight
    preview: Any = None
    preview_name = preview_timeline_name(now)
    #: "the insertions and the whole-track verification passed". Not "the run succeeded":
    #: restoration and the protected audit still get a veto, in `_finish_transaction`.
    verified = False

    try:
        preview = source.DuplicateTimeline(preview_name)
        if preview is None:
            raise RuntimeError(f"DuplicateTimeline({preview_name!r}) returned None")
        report.wrote = True
        report.preview_name = str(preview.GetName())
        report.preview_unique_id = preview.GetUniqueId()
        if report.preview_unique_id == source_snapshot.unique_id:
            raise RuntimeError("the duplicated timeline is not a distinct object")

        preview_before = structural_signature(snapshot_timeline(preview, is_current=False))
        source_signature = dict(before[target.source_timeline])
        for key in ("name", "unique_id"):
            preview_before.pop(key)
            source_signature.pop(key)
        # Item unique ids are necessarily new in a duplicate; compare everything else.
        report.preview_matched_source = _strip_item_ids(preview_before) == _strip_item_ids(
            source_signature
        )
        if not report.preview_matched_source:
            raise RuntimeError(
                "the preview timeline is not a faithful copy of the source; refusing to "
                "insert zooms into material that does not match the plan"
            )

        _prepare_target_track(preview, config.zoom_video_track, report)

        # AppendToTimeline is documented to act on "the current timeline", so the preview
        # must be made current. Restored in the finally block below.
        if not project.SetCurrentTimeline(preview):
            raise RuntimeError("SetCurrentTimeline(preview) failed")

        for index, (placement, want) in enumerate(zip(plan.placements, expected, strict=True)):
            record = _insert(
                media_pool,
                index,
                placement,
                want,
                asset_items[want.name],
                config.zoom_video_track,
            )
            report.insertions.append(record)
            if not record.ok:
                raise RuntimeError(
                    f"placement {index} ({record.role} at {want.start}) failed: "
                    f"{record.error}; " + "; ".join(record.problems)
                )

        _verify_target_track(preview, expected, config.zoom_video_track, report)
        if not report.verified:
            raise RuntimeError(
                "the finished track does not match the plan: "
                + "; ".join(report.verification_differences)
            )
        verified = True
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        verified = False
    finally:
        _finish_transaction(
            project,
            media_pool,
            config,
            target,
            previous_timeline,
            preview,
            report,
            before,
            verified=verified,
        )

    return report


__all__ = [
    "CONFIRM_FLAG",
    "PREVIEW_PREFIX",
    "ApplyPreviewRefused",
    "ApplyReport",
    "InsertionRecord",
    "apply_preview",
    "preview_timeline_name",
]
