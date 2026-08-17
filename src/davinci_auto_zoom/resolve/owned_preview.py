"""Phase 7: the two commands that are allowed to destroy something, and their guard rails.

    clean-preview    remove exactly the items DAZ can prove it created, and nothing else
    rebuild-preview  clean, then re-apply a freshly computed plan onto the same preview

Both are opt-in, both target one explicitly named `DAZ_AUTO_PREVIEW_*` timeline, and both are
built around one asymmetry that is the whole design:

    **deleting the wrong clip is unrecoverable; refusing to delete is merely annoying.**

So every ambiguity resolves towards doing nothing. The refusal list is long on purpose.

## What they refuse, and why

* a **protected timeline** (`DAZ_INPUT`, the reference edit) or anything that is not a
  `DAZ_AUTO_PREVIEW_*` name — these commands cannot be pointed at the user's work at all;
* a **legacy preview** — one whose target track holds items but where not a single item
  carries DAZ ownership metadata. The Phase 5 and Phase 6 previews are exactly this, and they
  are never migrated, never retro-tagged and never deleted (D039). Their items look identical
  to owned ones and that is precisely why they must not be treated as owned;
* **ambiguous or stale ownership anywhere on the track** — a marker that claims to be DAZ's
  and contradicts itself, or one naming a different preview (typically a hand-duplicated
  timeline). One such item stops the entire run *before its first delete*. A half-cleaned
  track whose ownership was never fully classifiable is the worst possible outcome (D037);
* for `rebuild-preview` only, **any unowned item on the target track**. Clean can safely leave
  a foreign clip alone; rebuild would then insert fresh placements next to it, and DAZ still
  refuses to depend on Resolve's collision behaviour (D032, D040).

## Recovery

Unlike `apply-preview`, these commands modify a timeline that already exists, so the
transaction boundary cannot be "delete the thing we made". Before the first `DeleteClips`, a
`DAZ_RECOVERY_*` duplicate of the target is created. On complete success it is deleted and its
disappearance confirmed; on **any** failure after a mutation it is deliberately kept and its
exact name reported. No improvised restoration is ever attempted — putting the copy back is
the user's decision, made with the copy in front of them (D041).

Every Resolve method used here is documented and non-deprecated in the Developer/Scripting
README installed with the running build (verified for Studio 21.0.4.5):
`Timeline.DeleteClips([timelineItems], Bool)` — second argument is the ripple flag and is
always passed explicitly as `False` — `Timeline.DuplicateTimeline`,
`Timeline.GetItemListInTrack`, `MediaPool.DeleteTimelines`, `MediaPool.AppendToTimeline`,
`Project.SetCurrentTimeline`, `TimelineItem.GetMarkers` / `AddMarker`.

`SaveProject()` is never called. No video track is ever deleted.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.apply import ExpectedItem, expected_items
from davinci_auto_zoom.domain.ownership import (
    ItemOwnership,
    OwnershipExpectations,
    TrackOwnership,
    classify_track,
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
    signature_differences,
)
from davinci_auto_zoom.resolve.executor import (
    PREVIEW_PREFIX,
    InsertionRecord,
    _claim,
    _insert,
    _protected_signatures,
    _timeline_identity,
    prepare_target_track,
    restore_previous_timeline,
    verify_ownership,
    verify_target_track,
)
from davinci_auto_zoom.resolve.ownership import snapshot_owned_track
from davinci_auto_zoom.resolve.session import (
    find_asset_items,
    find_timeline,
    snapshot_project,
)

#: The pre-operation copy. Never reused between runs, never deleted unless the run fully
#: succeeded, and never confused with a preview: a different prefix so the preview cleanup
#: rules and this one cannot overlap.
RECOVERY_PREFIX = "DAZ_RECOVERY_"

#: Two distinct tokens on purpose. `clean` and `rebuild` destroy different amounts of work,
#: and one flag that authorised both would make the smaller one a gateway to the larger.
CLEAN_CONFIRM_FLAG = "--confirm-clean-owned-preview"
REBUILD_CONFIRM_FLAG = "--confirm-rebuild-owned-preview"

CLEAN = "clean"
REBUILD = "rebuild"

LEGACY_REFUSAL = (
    "legacy preview: no verifiable DAZ ownership metadata. This timeline was created before "
    "DAZ tagged what it makes, so nothing on it can be proven to be DAZ's. It is left exactly "
    "as it is — it is never migrated, retro-tagged or deleted. Create a fresh preview with "
    "apply-preview instead."
)


class OwnedPreviewRefused(RuntimeError):
    """Raised before any mutation when the guards are not satisfied."""


def recovery_timeline_name(now: datetime | None = None, token: str | None = None) -> str:
    """`DAZ_RECOVERY_<timestamp>_<short id>` — unique to one run, never guessed."""

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{RECOVERY_PREFIX}{stamp}_{token or uuid.uuid4().hex[:8]}"


@dataclass
class DeleteCall:
    """One `DeleteClips` invocation, recorded argument for argument."""

    items: int
    ripple: bool
    returned: Any = None
    error: str | None = None


@dataclass
class OwnedPreviewReport:
    """Everything the run observed. `to_dict()` is the `--json` payload."""

    operation: str = CLEAN
    resolve_version: str = ""
    product_name: str = ""
    project_name: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    preview_name: str | None = None
    preview_unique_id: str | None = None

    preflight_failures: tuple[str, ...] = ()
    #: Why the run stopped before touching anything. Non-empty means zero mutations.
    refusals: tuple[str, ...] = ()
    legacy: bool = False

    # --- rebuild only: the plan must still describe the project ------------------------
    source_mismatches: tuple[str, ...] = ()
    plan_fingerprint: str | None = None
    current_fingerprint: str | None = None
    validated_fields: tuple[str, ...] = ()
    planned_placements: int = 0

    # --- what was on the track before -------------------------------------------------
    ownership_before: dict[str, Any] | None = None
    owned_before: int = 0
    unowned_before: int = 0
    ambiguous_before: int = 0
    stale_before: int = 0

    # --- recovery ----------------------------------------------------------------------
    recovery_name: str | None = None
    recovery_unique_id: str | None = None
    recovery_created: bool = False
    recovery_deleted: bool | None = None
    recovery_absent_after_success: bool | None = None
    recovery_retained: bool = False

    # --- the destructive step -----------------------------------------------------------
    delete_calls: list[DeleteCall] = field(default_factory=list)
    deleted_items: int = 0
    preserved_unowned: int = 0
    clean_differences: tuple[str, ...] = ()
    #: Tri-state: None means the run never got as far as verifying the deletion.
    cleaned: bool | None = None

    # --- rebuild only: the re-insertion --------------------------------------------------
    ownership_preview_id: str | None = None
    ownership_fingerprint: str | None = None
    insertions: list[InsertionRecord] = field(default_factory=list)
    verification_differences: tuple[str, ...] = ()
    verified: bool | None = None
    items_created: int = 0
    items_owned: int = 0
    items_unowned: int = 0
    items_ambiguous: int = 0
    items_stale: int = 0
    ownership_differences: tuple[str, ...] = ()
    ownership_verified: bool | None = None
    #: The structural identity of the result. Two rebuilds that agree here are idempotent.
    placement_ids: tuple[str, ...] = ()

    previous_current_timeline: str | None = None
    previous_current_timeline_unique_id: str | None = None
    restored_current_timeline: str | None = None
    restored_current_timeline_unique_id: str | None = None
    current_timeline_restored: bool | None = None

    audit_checked: tuple[str, ...] = ()
    audit_differences: tuple[str, ...] = ()
    cleanup_failures: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def mutated(self) -> bool:
        """Did this run write anything at all? Drives whether a recovery must be kept."""

        return self.recovery_created or bool(self.delete_calls) or bool(self.insertions)

    @property
    def succeeded(self) -> bool:
        """One flat conjunction, so "this run is safe to trust" is readable in one place."""

        common = (
            self.error is None
            and not self.preflight_failures
            and not self.refusals
            and not self.source_mismatches
            and self.recovery_created
            and self.cleaned is True
            and not self.clean_differences
            and self.current_timeline_restored is True
            and not self.audit_differences
            and not self.cleanup_failures
            and self.recovery_absent_after_success is True
            and not self.recovery_retained
        )
        if self.operation == CLEAN:
            return common
        return (
            common
            and self.verified is True
            and not self.verification_differences
            and self.ownership_verified is True
            and not self.ownership_differences
            and bool(self.insertions)
            and self.items_owned == self.items_created == len(self.insertions)
            and all(record.owned for record in self.insertions)
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mutated"] = self.mutated
        data["succeeded"] = self.succeeded
        return data

    def to_text(self) -> str:
        lines = [
            f"davinci-auto-zoom {self.operation}-preview (Phase 7)",
            f"  product   : {self.product_name} {self.resolve_version}",
            f"  project   : {self.project_name}",
            f"  preview   : {self.preview_name or '-'} ({self.preview_unique_id})",
        ]
        if self.preflight_failures:
            lines.append("  PREFLIGHT REFUSED:")
            lines.extend(f"    - {failure}" for failure in self.preflight_failures)
        if self.refusals:
            lines.append("  REFUSED (nothing was deleted, nothing was created):")
            lines.extend(f"    - {refusal}" for refusal in self.refusals)
        if self.source_mismatches:
            lines.append("  PLAN SOURCE REJECTED (nothing was deleted):")
            lines.extend(f"    - {mismatch}" for mismatch in self.source_mismatches)
        lines.append(
            f"  before    : owned {self.owned_before}, unowned {self.unowned_before}, "
            f"stale {self.stale_before}, ambiguous {self.ambiguous_before}"
        )
        lines.append(
            f"  recovery  : {self.recovery_name or '-'} created={self.recovery_created} "
            f"deleted={self.recovery_deleted} absent={self.recovery_absent_after_success}"
        )
        for call in self.delete_calls:
            lines.append(
                f"  DeleteClips([{call.items} item(s)], ripple={call.ripple}) -> "
                f"{call.returned!r}{f' error={call.error}' if call.error else ''}"
            )
        lines.append(
            f"  removed   : {self.deleted_items} owned item(s); "
            f"{self.preserved_unowned} unowned item(s) left untouched"
        )
        if self.clean_differences:
            lines.append("  CLEAN DIFFERENCES:")
            lines.extend(f"    - {item}" for item in self.clean_differences)
        else:
            lines.append(f"  cleaned   : {self.cleaned}")
        if self.operation == REBUILD:
            lines.append(f"  plan      : {self.planned_placements} placement(s)")
            for record in self.insertions:
                status = "ok  " if record.ok else "FAIL"
                lines.append(
                    f"  [{status}] {record.index:>3} {record.role:11} "
                    f"{record.asset_name:16} start={record.start} end={record.end} "
                    f"owned={record.owned} pid={record.placement_id}"
                )
                for problem in record.problems + record.ownership_problems:
                    lines.append(f"           {problem}")
            lines.extend(
                [
                    f"  verified  : {self.verified}",
                    f"  ownership : created {self.items_created}, owned {self.items_owned}, "
                    f"unowned {self.items_unowned}, stale {self.items_stale}, "
                    f"ambiguous {self.items_ambiguous}, verification "
                    f"{self.ownership_verified}",
                ]
            )
            for item in self.verification_differences + self.ownership_differences:
                lines.append(f"    - {item}")
        lines.extend(
            [
                f"  restored  : {self.restored_current_timeline} "
                f"(was {self.previous_current_timeline}) "
                f"proven={self.current_timeline_restored}",
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
        if self.recovery_retained:
            lines.extend(
                [
                    "",
                    f"  RECOVERY TIMELINE KEPT: {self.recovery_name}",
                    "  It is an exact duplicate of the target preview as it was BEFORE this "
                    "run touched anything. Nothing else was created or deleted. Inspect both, "
                    "then decide yourself what to keep; this tool will not undo the run for "
                    "you.",
                ]
            )
        if self.succeeded and self.operation == REBUILD:
            lines.extend(
                [
                    "",
                    f"  REBUILT: {self.preview_name}",
                    f"  {self.items_owned} owned item(s), placement ids "
                    + ", ".join(self.placement_ids[:3])
                    + (" ..." if len(self.placement_ids) > 3 else ""),
                ]
            )
        lines.append(f"  RESULT: {'PASS' if self.succeeded else 'FAIL'}")
        return "\n".join(lines)


def _identity(item: ItemOwnership) -> tuple[Any, ...]:
    """How an item is recognised across a re-read.

    `unique_id` when Resolve gives one — it is the strongest handle available for "the same
    object" — with name and frames as the fallback and as corroboration.
    """

    return (item.item.unique_id, item.item.name, item.item.start, item.item.end)


def _name_refusals(preview_name: str, target: ApplyPreviewTarget) -> tuple[str, ...]:
    """Refusals that need no knowledge of the track's contents.

    Evaluated **first**, and separately, so a timeline this command may not touch is not even
    inspected. The name is never evidence of ownership — but it is perfectly good evidence
    that something is *not* a DAZ preview, and that asymmetry is the whole point.
    """

    refusals: list[str] = []
    if preview_name in target.protected_timelines:
        refusals.append(
            f"{preview_name!r} is a protected timeline for this run (source or reference). "
            "These commands never touch the material you work in."
        )
    if not preview_name.startswith(PREVIEW_PREFIX):
        refusals.append(
            f"{preview_name!r} is not a DAZ preview: the name does not start with "
            f"{PREVIEW_PREFIX!r}. Only timelines DAZ created can be cleaned or rebuilt."
        )
    return tuple(refusals)


def _ownership_refusals(ownership: TrackOwnership, operation: str) -> tuple[str, ...]:
    """Every reason the track's contents stop the run. Empty means cleared to destroy."""

    refusals: list[str] = []
    blockers = ownership.deletion_blockers()
    if blockers:
        refusals.extend(blockers)
        refusals.append(
            "ownership on this track is not fully classifiable, so NOTHING was deleted. A "
            "partially cleaned track is worse than an uncleaned one."
        )
    elif ownership.items and not ownership.owned:
        refusals.append(LEGACY_REFUSAL)

    if operation == REBUILD and ownership.unowned:
        refusals.append(
            f"the target track holds {len(ownership.unowned)} item(s) DAZ did not create: "
            + "; ".join(item.item.label for item in ownership.unowned)
            + ". clean-preview can safely leave a foreign clip alone, but a rebuild would "
            "then insert fresh placements alongside it, and DAZ does not rely on Resolve's "
            "collision behaviour. Move or remove the clip yourself, or use clean-preview."
        )
    return tuple(refusals)


def _delete_owned(
    preview: Any, ownership: TrackOwnership, report: OwnedPreviewReport
) -> None:
    """Remove exactly the proven-owned items, in one non-ripple call.

    `ripple=False` is passed explicitly rather than relying on the documented default. These
    clips sit on a dedicated zoom track above the user's edit; a ripple delete would shift
    every clip after them and silently destroy the timing of the whole preview.

    **The target timeline must already be the current one.** `DeleteClips` shares the
    undocumented restriction measured on `AppendToTimeline`: called on a timeline that is not
    current it returns False and deletes nothing (D042). The caller makes the preview current
    and checks that it did; doing it here would hide a state change inside a helper whose job
    is one API call.
    """

    owned = [verdict for verdict in ownership.owned]
    if not owned:
        report.cleaned = True
        return

    live = preview.GetItemListInTrack("video", ownership.track_index) or []
    by_identity = {
        (item.GetUniqueId(), str(item.GetName()), int(item.GetStart()), int(item.GetEnd())): item
        for item in live
    }
    targets: list[Any] = []
    for verdict in owned:
        handle = by_identity.get(_identity(verdict))
        if handle is None:
            raise RuntimeError(
                f"the owned item {verdict.item.label} could not be re-found on the timeline "
                "between classification and deletion; refusing to delete anything"
            )
        targets.append(handle)

    call = DeleteCall(items=len(targets), ripple=False)
    report.delete_calls.append(call)
    try:
        call.returned = preview.DeleteClips(targets, False)
    except Exception as exc:
        call.error = repr(exc)
        raise RuntimeError(f"DeleteClips raised {exc!r}") from exc
    if not call.returned:
        raise RuntimeError(f"DeleteClips returned {call.returned!r}")


def _verify_clean(
    preview: Any,
    ownership: TrackOwnership,
    expectations: OwnershipExpectations,
    report: OwnedPreviewReport,
) -> None:
    """Re-read the track and prove exactly the owned items went and nothing else did."""

    after = classify_track(
        ownership.track_index,
        snapshot_owned_track(preview, ownership.track_index),
        expectations,
    )
    differences: list[str] = []

    survivors = {_identity(item) for item in after.items}
    for verdict in ownership.owned:
        if _identity(verdict) in survivors:
            differences.append(f"owned item {verdict.item.label} is still on the track")
    for verdict in ownership.unowned:
        if _identity(verdict) not in survivors:
            differences.append(
                f"UNOWNED item {verdict.item.label} disappeared — it was not DAZ's and must "
                "not have been touched"
            )
    if after.owned:
        differences.append(
            f"{len(after.owned)} owned item(s) remain on the track after the delete"
        )

    report.deleted_items = len(ownership.owned) - sum(
        1 for verdict in ownership.owned if _identity(verdict) in survivors
    )
    report.preserved_unowned = len(after.unowned)
    report.clean_differences = tuple(differences)
    report.cleaned = not differences


def _reinsert(
    project: Any,
    media_pool: Any,
    preview: Any,
    plan: ZoomPlan,
    expected: tuple[ExpectedItem, ...],
    asset_items: dict[str, Any],
    target: ApplyPreviewTarget,
    track_index: int,
    report: OwnedPreviewReport,
) -> None:
    """Apply the fresh plan onto the now-empty track, tagging as we go.

    The planner is the only source of timing here, exactly as in `apply-preview`: nothing in
    this module recomputes a frame, and the placements are inserted verbatim (D028).
    """

    report.ownership_preview_id = str(preview.GetUniqueId())
    assert report.ownership_fingerprint is not None  # set by the source validation

    if not project.SetCurrentTimeline(preview):
        raise RuntimeError("SetCurrentTimeline(preview) failed before re-insertion")

    for index, (placement, want) in enumerate(zip(plan.placements, expected, strict=True)):
        record, created = _insert(
            media_pool, index, placement, want, asset_items[want.name], track_index
        )
        report.insertions.append(record)
        if not record.ok:
            raise RuntimeError(
                f"placement {index} ({record.role} at {want.start}) failed: "
                f"{record.error}; " + "; ".join(record.problems)
            )
        _claim(
            created,
            record,
            want,
            preview_id=report.ownership_preview_id,
            source_fingerprint=report.ownership_fingerprint,
        )
        if not record.owned:
            raise RuntimeError(
                f"placement {index} ({record.role} at {want.start}) was created but could "
                "not be claimed as DAZ's: " + "; ".join(record.ownership_problems)
            )

    report.verification_differences = verify_target_track(preview, expected, track_index)
    report.verified = not report.verification_differences
    if not report.verified:
        raise RuntimeError(
            "the rebuilt track does not match the plan: "
            + "; ".join(report.verification_differences)
        )

    placement_ids = {r.placement_id for r in report.insertions if r.placement_id is not None}
    ownership = verify_ownership(
        preview,
        track_index,
        OwnershipExpectations(
            preview_id=report.ownership_preview_id,
            source_fingerprint=report.ownership_fingerprint,
            assets=dict(target.assets),
        ),
        placement_ids,
    )
    report.items_created = ownership.created
    report.items_owned = ownership.owned
    report.items_unowned = ownership.unowned
    report.items_ambiguous = ownership.ambiguous
    report.items_stale = ownership.stale
    report.ownership_differences = ownership.differences
    report.ownership_verified = ownership.verified
    report.placement_ids = tuple(
        r.placement_id for r in report.insertions if r.placement_id is not None
    )
    if not report.ownership_verified:
        raise RuntimeError(
            "the rebuilt track is not fully owned by DAZ: "
            + "; ".join(report.ownership_differences)
        )


def _finish(
    project: Any,
    media_pool: Any,
    config: Config,
    target: ApplyPreviewTarget,
    previous_timeline: Any,
    recovery: Any,
    report: OwnedPreviewReport,
    before: dict[str, Any],
    *,
    operation_ok: bool,
) -> None:
    """Restore, audit, and only then decide the recovery's fate — in that order.

    Deleting the recovery is the **last** action of the transaction, taken once every other
    obligation has been discharged. A run can therefore never announce success while the
    user's timeline is missing, a protected timeline has moved, or the audit could not even be
    read — because in all of those cases the recovery is still there.
    """

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

    if report.error is None and (report.cleanup_failures or report.audit_differences):
        report.error = "; ".join(report.cleanup_failures + report.audit_differences)

    keep_recovery = not (
        operation_ok
        and outcome.restored
        and not report.audit_differences
        and not report.cleanup_failures
    )
    _dispose_recovery(project, media_pool, recovery, report, keep=keep_recovery)


def _dispose_recovery(
    project: Any,
    media_pool: Any,
    recovery: Any,
    report: OwnedPreviewReport,
    *,
    keep: bool,
) -> None:
    if recovery is None:
        return
    name = report.recovery_name or ""
    if keep:
        report.recovery_retained = True
        report.recovery_deleted = False
        report.notes.append(
            f"RECOVERY TIMELINE RETAINED: {name!r}. This run did not fully succeed, so the "
            "pre-operation copy of the target preview is deliberately kept. No other timeline "
            "was created or deleted, and no automatic restoration was attempted."
        )
        return
    if not name.startswith(RECOVERY_PREFIX):  # pragma: no cover - built by this module
        report.cleanup_failures += (
            f"refusing to delete timeline {name!r}: not a recovery name",
        )
        report.recovery_retained = True
        return
    try:
        report.recovery_deleted = bool(media_pool.DeleteTimelines([recovery]))
    except Exception as exc:
        report.recovery_deleted = False
        report.cleanup_failures += (f"DeleteTimelines raised {exc!r}",)
    try:
        report.recovery_absent_after_success = find_timeline(project, name) is None
    except Exception as exc:
        report.recovery_absent_after_success = False
        report.cleanup_failures += (
            f"could not confirm whether the recovery {name!r} was deleted: {exc!r}",
        )
    if not report.recovery_absent_after_success:
        report.recovery_retained = True
        report.notes.append(
            f"RECOVERY TIMELINE LEFT BEHIND: {name!r}. The run itself succeeded but the copy "
            "could not be removed. It is harmless; delete it manually."
        )


def run_owned_preview(
    resolve: Any,
    project: Any,
    config: Config,
    target: ApplyPreviewTarget,
    preview_name: str,
    *,
    operation: str,
    confirmed: bool,
    plan: ZoomPlan | None = None,
    now: datetime | None = None,
) -> OwnedPreviewReport:
    """`clean-preview` and `rebuild-preview`. A rebuild is a clean that then re-applies `plan`."""

    report = OwnedPreviewReport(
        operation=operation,
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
        target=asdict(target),
        preview_name=preview_name,
        planned_placements=len(plan.placements) if plan else 0,
    )
    flag = CLEAN_CONFIRM_FLAG if operation == CLEAN else REBUILD_CONFIRM_FLAG
    if not confirmed:
        raise OwnedPreviewRefused(
            f"this command deletes clips from an existing timeline and requires {flag} to run"
        )

    snapshot = snapshot_project(resolve, project, config)
    report.preflight_failures = apply_preview_preflight_failures(snapshot, target)
    if report.preflight_failures:
        raise OwnedPreviewRefused(
            f"{operation} preflight failed, nothing was touched:\n"
            + "\n".join(f"  - {failure}" for failure in report.preflight_failures)
        )

    source_snapshot = snapshot.timeline(target.source_timeline)
    assert source_snapshot is not None  # guaranteed by preflight
    assert config.asset_timing is not None  # guaranteed by the CLI

    # `rebuild` needs the plan to still describe the project; `clean` deliberately does not —
    # removing what DAZ made does not depend on the source material having stood still.
    fingerprint: str | None = None
    if operation == REBUILD:
        if plan is None:  # pragma: no cover - the CLI always supplies one
            raise OwnedPreviewRefused("rebuild-preview requires a freshly computed plan")
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
            raise OwnedPreviewRefused(
                "the plan no longer matches the project, nothing was touched:\n"
                + "\n".join(f"  - {mismatch}" for mismatch in report.source_mismatches)
            )
        if not plan.valid:
            raise OwnedPreviewRefused(
                "the plan is internally invalid and must not be applied:\n"
                + "\n".join(f"  - {problem}" for problem in plan.overlaps)
            )
        fingerprint = current_source.structural_fingerprint
        report.ownership_fingerprint = fingerprint

    # Refused on the name alone, before the timeline is even read. A protected timeline is
    # not inspected, let alone classified.
    report.refusals = _name_refusals(preview_name, target)
    if report.refusals:
        raise OwnedPreviewRefused(
            f"{operation}-preview refused, nothing was deleted and nothing was created:\n"
            + "\n".join(f"  - {refusal}" for refusal in report.refusals)
        )

    preview = find_timeline(project, preview_name)
    if preview is None:
        raise OwnedPreviewRefused(
            f"timeline {preview_name!r} was not found in {snapshot.project_name!r}"
        )
    report.preview_unique_id = str(preview.GetUniqueId())

    # Classified BEFORE anything else happens, and the verdict is what authorises the run.
    expectations = OwnershipExpectations(
        preview_id=report.preview_unique_id,
        source_fingerprint=fingerprint,
        assets=dict(target.assets),
    )
    ownership = classify_track(
        config.zoom_video_track,
        snapshot_owned_track(preview, config.zoom_video_track),
        expectations,
    )
    report.ownership_before = ownership.to_dict()
    report.owned_before = len(ownership.owned)
    report.unowned_before = len(ownership.unowned)
    report.ambiguous_before = len(ownership.ambiguous)
    report.stale_before = len(ownership.stale)

    report.refusals = _ownership_refusals(ownership, operation)
    report.legacy = LEGACY_REFUSAL in report.refusals
    if report.refusals:
        raise OwnedPreviewRefused(
            f"{operation}-preview refused, nothing was deleted and nothing was created:\n"
            + "\n".join(f"  - {refusal}" for refusal in report.refusals)
        )

    asset_items = find_asset_items(project, config)
    expected: tuple[ExpectedItem, ...] = ()
    if operation == REBUILD:
        assert plan is not None
        missing = [name for name in target.asset_names if name not in asset_items]
        if missing:
            raise OwnedPreviewRefused(
                f"asset(s) {missing} could not be resolved in the Media Pool; nothing was "
                "touched"
            )
        expected = expected_items(plan, dict(target.assets), config.zoom_video_track)

    media_pool = project.GetMediaPool()
    previous_timeline = project.GetCurrentTimeline()
    (
        report.previous_current_timeline,
        report.previous_current_timeline_unique_id,
    ) = _timeline_identity(previous_timeline)
    before = _protected_signatures(project, config, target.protected_timelines)

    recovery: Any = None
    operation_ok = False
    try:
        # THE recovery point. Nothing destructive happens before this line.
        recovery_name = recovery_timeline_name(now)
        recovery = preview.DuplicateTimeline(recovery_name)
        if recovery is None:
            raise RuntimeError(
                f"DuplicateTimeline({recovery_name!r}) returned None; refusing to delete "
                "anything without a pre-operation copy"
            )
        report.recovery_created = True
        report.recovery_name = str(recovery.GetName())
        report.recovery_unique_id = str(recovery.GetUniqueId())
        if report.recovery_unique_id == report.preview_unique_id:
            raise RuntimeError("the recovery timeline is not a distinct object")

        # Measured on Studio 21.0.4.5: `DeleteClips` silently no-ops and returns False unless
        # its timeline is the current one, exactly like `AppendToTimeline` (D042). Made
        # current *after* the recovery exists, and restored in `_finish`.
        if not project.SetCurrentTimeline(preview):
            raise RuntimeError(
                f"SetCurrentTimeline({preview_name!r}) failed; DeleteClips only acts on the "
                "current timeline, so nothing was deleted"
            )
        current = project.GetCurrentTimeline()
        if current is None or str(current.GetUniqueId()) != report.preview_unique_id:
            raise RuntimeError(
                "the target preview did not become the current timeline; refusing to call "
                "DeleteClips, which would then silently act on the wrong timeline or not at all"
            )

        _delete_owned(preview, ownership, report)
        _verify_clean(preview, ownership, expectations, report)
        if not report.cleaned:
            raise RuntimeError(
                "the target track is not in the expected state after the delete: "
                + "; ".join(report.clean_differences)
            )

        if operation == REBUILD:
            assert plan is not None
            prepare_target_track(preview, config.zoom_video_track)
            _reinsert(
                project,
                media_pool,
                preview,
                plan,
                expected,
                asset_items,
                target,
                config.zoom_video_track,
                report,
            )
        operation_ok = True
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
        operation_ok = False
    finally:
        _finish(
            project,
            media_pool,
            config,
            target,
            previous_timeline,
            recovery,
            report,
            before,
            operation_ok=operation_ok,
        )

    return report


__all__ = [
    "CLEAN",
    "CLEAN_CONFIRM_FLAG",
    "LEGACY_REFUSAL",
    "REBUILD",
    "REBUILD_CONFIRM_FLAG",
    "RECOVERY_PREFIX",
    "DeleteCall",
    "OwnedPreviewRefused",
    "OwnedPreviewReport",
    "recovery_timeline_name",
    "run_owned_preview",
]
