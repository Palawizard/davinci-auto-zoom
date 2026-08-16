"""Phase 2 integration spike: prove that a user's zoom asset can be re-instanced.

**This is the only write-capable code in the project, and it is not the production
executor.** It answers one question experimentally and reproducibly:

    configured user asset -> new TimelineItem on a scratch timeline
    -> chosen track -> known frame placement -> known duration semantics
    -> user's Fusion effect preserved

Safety model, in order:

1. fail-closed preflight — a single mismatch aborts *before* any mutating call;
2. structural snapshots of the timelines that must not change;
3. every mutation happens on a scratch timeline created by this run and named uniquely;
4. `try/finally` restores the previously current timeline and deletes only the scratch
   timeline this run created;
5. a post-run audit re-reads the protected timelines and fails on any difference.

Every Resolve method called here is documented in the Developer/Scripting README installed
with the running build (verified for Studio 21.0.4.5):
`Timeline.DuplicateTimeline`, `Timeline.AddTrack`, `Project.SetCurrentTimeline`,
`MediaPool.AppendToTimeline([{clipInfo}])`, `MediaPool.DeleteTimelines`,
`TimelineItem.ExportFusionComp`, plus read-only getters.
"""

from __future__ import annotations

import tempfile
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.fusion_comp import CompComparison, compare_comps
from davinci_auto_zoom.domain.probe import (
    WriteProbeTarget,
    asset_signature,
    preflight_failures,
    signature_differences,
    structural_signature,
)
from davinci_auto_zoom.resolve.session import (
    find_asset_folder,
    find_asset_items,
    find_timeline,
    snapshot_assets,
    snapshot_project,
    snapshot_timeline,
)

SCRATCH_PREFIX = "DAZ_SCRATCH_"

#: Opt-in token. A write-capable run is impossible without passing this explicitly.
CONFIRM_FLAG = "--confirm-resolve-write-test"


class WriteProbeRefused(RuntimeError):
    """Raised before any mutation when the probe's guards are not satisfied."""


@dataclass(frozen=True, slots=True)
class Experiment:
    """One planned insertion. `start_frame`/`end_frame` are passed to Resolve as given.

    Measured on Studio 21.0.4.5: `endFrame` is **exclusive**, so the resulting duration is
    `endFrame - startFrame`. `expected_duration` states what the run asserts, and the
    `endframe_semantics` experiment exists precisely to keep re-proving that.
    """

    label: str
    role: str
    asset_name: str
    record_frame: int
    start_frame: int
    end_frame: int
    expected_duration: int
    expectation: str
    #: False for experiments that only characterise semantics; those never fail the run.
    required: bool = True

    @property
    def exclusive_duration(self) -> int:
        return self.end_frame - self.start_frame


@dataclass
class InsertionResult:
    """What Resolve actually did, recorded whether or not it matched expectations."""

    label: str
    role: str
    asset_name: str
    clip_info: dict[str, Any]
    expectation: str
    requested_record_frame: int
    requested_duration: int
    required: bool = True
    returned_items: int | None = None
    ok: bool = False
    error: str | None = None
    used_media_pool_selection_workaround: bool = False
    name: str | None = None
    unique_id: str | None = None
    track_type: str | None = None
    track_index: int | None = None
    start: int | None = None
    end: int | None = None
    duration: int | None = None
    fusion_comp_count: int | None = None
    fusion_comp_names: tuple[str, ...] = ()
    comp_exported: bool = False
    comp_comparison: dict[str, Any] | None = None

    @property
    def record_frame_is_absolute(self) -> bool | None:
        if self.start is None:
            return None
        return self.start == self.requested_record_frame

    @property
    def duration_as_requested(self) -> bool | None:
        """Did Resolve produce exactly the duration this experiment asked for?"""
        if self.duration is None:
            return None
        return self.duration == self.requested_duration

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["record_frame_is_absolute"] = self.record_frame_is_absolute
        data["duration_as_requested"] = self.duration_as_requested
        return data


@dataclass
class ProbeReport:
    resolve_version: str = ""
    product_name: str = ""
    project_name: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    preflight_failures: tuple[str, ...] = ()
    wrote: bool = False
    scratch_name: str | None = None
    scratch_unique_id: str | None = None
    scratch_matched_source: bool | None = None
    scratch_track_index: int | None = None
    previous_current_timeline: str | None = None
    restored_current_timeline: str | None = None
    scratch_deleted: bool | None = None
    scratch_absent_after_cleanup: bool | None = None
    temp_files_removed: bool = False
    insertions: list[InsertionResult] = field(default_factory=list)
    audit_differences: tuple[str, ...] = ()
    audit_checked: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def clean(self) -> bool:
        """True when the originals are untouched and the scratch timeline is gone."""
        return (
            not self.audit_differences
            and self.scratch_absent_after_cleanup is not False
            and self.error is None
        )

    @property
    def succeeded(self) -> bool:
        return (
            self.clean
            and bool(self.insertions)
            and all(result.ok for result in self.insertions if result.required)
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["insertions"] = [result.to_dict() for result in self.insertions]
        data["clean"] = self.clean
        data["succeeded"] = self.succeeded
        return data

    def to_text(self) -> str:
        lines = [
            "davinci-auto-zoom write probe (Phase 2 spike)",
            f"  product   : {self.product_name} {self.resolve_version}",
            f"  project   : {self.project_name}",
            f"  scratch   : {self.scratch_name or '-'}"
            f" (track V{self.scratch_track_index or '-'})",
            f"  wrote     : {self.wrote}",
        ]
        if self.preflight_failures:
            lines.append("  PREFLIGHT REFUSED:")
            lines.extend(f"    - {failure}" for failure in self.preflight_failures)
        for result in self.insertions:
            status = "ok  " if result.ok else ("FAIL" if result.required else "info")
            lines.append(
                f"  [{status}] {result.label:24} req rec={result.requested_record_frame} "
                f"dur={result.requested_duration:4} -> "
                f"start={result.start} end={result.end} dur={result.duration} "
                f"V{result.track_index} comps={result.fusion_comp_count}"
            )
            if result.error:
                lines.append(f"           error: {result.error}")
            if result.comp_comparison:
                lines.append(
                    f"           fusion: {result.comp_comparison['verdict']} "
                    f"(carries_user_effect="
                    f"{result.comp_comparison['carries_user_effect']}, "
                    f"keyframe_times_equal="
                    f"{result.comp_comparison['keyframe_times_equal']})"
                )
        lines.extend(
            [
                f"  cleanup   : restored={self.restored_current_timeline} "
                f"deleted={self.scratch_deleted} absent={self.scratch_absent_after_cleanup}",
                f"  audit     : {', '.join(self.audit_checked) or '-'}",
            ]
        )
        if self.audit_differences:
            lines.append("  AUDIT DIFFERENCES:")
            lines.extend(f"    - {difference}" for difference in self.audit_differences)
        else:
            lines.append("  audit differences: none")
        if self.error:
            lines.append(f"  ERROR: {self.error}")
        lines.extend(f"  note: {note}" for note in self.notes)
        lines.append(f"  RESULT: {'PASS' if self.succeeded else 'FAIL'}")
        return "\n".join(lines)


def scratch_timeline_name(now: datetime | None = None, token: str | None = None) -> str:
    """Unique name for a timeline created by *this* run. Never reused, never guessed."""

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{SCRATCH_PREFIX}{stamp}_{token or uuid.uuid4().hex[:8]}"


def plan_experiments(
    target: WriteProbeTarget,
    native_frames: dict[str, int],
    *,
    first_record_frame: int,
    spacing: int,
) -> tuple[Experiment, ...]:
    """The insertion experiments, ordered so each isolates a single unknown.

    Native duration comes first: mixing "does the effect survive" with "can the duration be
    chosen" in one call would make a failure impossible to attribute.
    """

    roles = dict(target.assets)
    x1 = roles.get("facecam_x1", "")
    x0 = roles.get("reset_x0", "")
    native_x1 = native_frames.get(x1, 0)
    native_x0 = native_frames.get(x0, 0)

    planned = [
        ("x1_native", x1, "facecam_x1", native_x1, True,
         f"native length of {x1}: {native_x1} frames"),
        ("x1_endframe_semantics", x1, "facecam_x1", native_x1 - 1, False,
         "one frame shorter than native; paired with x1_native this shows whether "
         "endFrame is inclusive or exclusive"),
        ("x1_short_87", x1, "facecam_x1", 87, True,
         "87 frames, a duration observed in the human reference timeline"),
        ("x1_extended_141", x1, "facecam_x1", 141, True,
         f"141 frames, longer than the {native_x1}-frame Media Pool asset"),
        ("x1_shorter_than_animation", x1, "facecam_x1", 10, False,
         "10 frames, shorter than the asset's own 15-frame keyframed move; shows what "
         "happens to an animation that cannot complete"),
        ("x0_native", x0, "reset_x0", native_x0, True,
         f"native length of {x0}: {native_x0} frames"),
    ]
    return tuple(
        Experiment(
            label=label,
            role=role,
            asset_name=asset,
            record_frame=first_record_frame + index * spacing,
            start_frame=0,
            # endFrame is exclusive on the verified build, so it equals the duration.
            end_frame=duration,
            expected_duration=duration,
            expectation=expectation,
            required=required,
        )
        for index, (label, asset, role, duration, required, expectation) in enumerate(
            planned
        )
    )


def _item_facts(item: Any) -> dict[str, Any]:
    track = item.GetTrackTypeAndIndex() or [None, None]
    return {
        "name": str(item.GetName()),
        "unique_id": item.GetUniqueId(),
        "track_type": track[0],
        "track_index": int(track[1]) if track[1] is not None else None,
        "start": int(item.GetStart()),
        "end": int(item.GetEnd()),
        "duration": int(item.GetDuration()),
        "fusion_comp_count": int(item.GetFusionCompCount() or 0),
        "fusion_comp_names": tuple(item.GetFusionCompNameList() or ()),
    }


def _reference_comps(
    project: Any, target: WriteProbeTarget, directory: Path, report: ProbeReport
) -> dict[str, str]:
    """Export one known-good manual instance per role from the reference timeline.

    Read-only with respect to the project: `ExportFusionComp` writes a file on disk and
    does not modify the timeline.
    """

    reference = find_timeline(project, target.reference_timeline)
    if reference is None:
        return {}

    wanted = {name: role for role, name in target.assets}
    exported: dict[str, str] = {}
    for index in range(1, int(reference.GetTrackCount("video") or 0) + 1):
        for item in reference.GetItemListInTrack("video", index) or []:
            name = str(item.GetName())
            if name not in wanted or name in exported:
                continue
            path = directory / f"reference_{name}.comp"
            if item.ExportFusionComp(str(path), 1) and path.is_file():
                exported[name] = path.read_text(encoding="utf-8", errors="replace")
                report.notes.append(
                    f"reference comp for {name!r} exported from "
                    f"{target.reference_timeline} item [{item.GetStart()},{item.GetEnd()}) "
                    f"({item.GetDuration()} frames)"
                )
    missing = sorted(set(wanted) - set(exported))
    if missing:
        report.notes.append(
            f"no manual reference instance found for: {', '.join(missing)}; "
            "Fusion comparison will be skipped for those roles"
        )
    return exported


def _append_one(
    media_pool: Any, clip_info: dict[str, Any]
) -> tuple[list[Any], str | None]:
    try:
        returned = media_pool.AppendToTimeline([clip_info])
    except Exception as exc:  # the Blackmagic wrapper raises bare exceptions
        return [], f"AppendToTimeline raised {exc!r}"
    if not returned:
        return [], f"AppendToTimeline returned {returned!r}"
    return list(returned), None


def _run_experiment(
    experiment: Experiment,
    *,
    media_pool: Any,
    asset_items: dict[str, Any],
    track_index: int,
    directory: Path,
    reference_comps: dict[str, str],
    asset_folder: Any,
    report: ProbeReport,
) -> InsertionResult:
    clip_info: dict[str, Any] = {
        "mediaPoolItem": asset_items[experiment.asset_name],
        "startFrame": experiment.start_frame,
        "endFrame": experiment.end_frame,
        "trackIndex": track_index,
        "recordFrame": experiment.record_frame,
    }
    result = InsertionResult(
        label=experiment.label,
        role=experiment.role,
        asset_name=experiment.asset_name,
        # The MediaPoolItem proxy must never leak into the report.
        clip_info={
            key: (experiment.asset_name if key == "mediaPoolItem" else value)
            for key, value in clip_info.items()
        },
        expectation=experiment.expectation,
        requested_record_frame=experiment.record_frame,
        requested_duration=experiment.expected_duration,
        required=experiment.required,
    )

    items, error = _append_one(media_pool, clip_info)
    if error is not None and asset_folder is not None:
        # Documented-changelog workaround (20.3.2: "AppendToTimeline failure when no media
        # pool clip is selected"). Applied only after the plain call actually failed, so it
        # never becomes a silent permanent dependency.
        report.notes.append(
            f"{experiment.label}: plain AppendToTimeline failed ({error}); "
            "retrying once with the asset bin/clip selected"
        )
        previous_folder = media_pool.GetCurrentFolder()
        try:
            media_pool.SetCurrentFolder(asset_folder)
            media_pool.SetSelectedClip(asset_items[experiment.asset_name])
            items, error = _append_one(media_pool, clip_info)
            result.used_media_pool_selection_workaround = not error
        finally:
            if previous_folder is not None:
                media_pool.SetCurrentFolder(previous_folder)

    result.returned_items = len(items)
    if error is not None:
        result.error = error
        return result
    if len(items) != 1:
        result.error = f"expected exactly 1 returned TimelineItem, got {len(items)}"
        return result

    item = items[0]
    for key, value in _item_facts(item).items():
        setattr(result, key, value)

    reference_text = reference_comps.get(experiment.asset_name)
    if result.fusion_comp_count and reference_text:
        path = directory / f"{experiment.label}.comp"
        if item.ExportFusionComp(str(path), 1) and path.is_file():
            result.comp_exported = True
            comparison: CompComparison = compare_comps(
                reference_text, path.read_text(encoding="utf-8", errors="replace")
            )
            result.comp_comparison = comparison.to_dict()

    placed_right = (
        result.track_index == track_index
        and result.start == experiment.record_frame
        and result.duration == experiment.expected_duration
        and result.name == experiment.asset_name
        and result.fusion_comp_count == 1
    )
    carries_effect = bool(
        result.comp_comparison and result.comp_comparison["carries_user_effect"]
    )
    result.ok = placed_right and (carries_effect or not reference_text)
    if not result.ok and result.error is None:
        result.error = "placement or Fusion preservation did not match expectations"
    return result


def _protected_signatures(
    project: Any, config: Config, target: WriteProbeTarget
) -> dict[str, Any]:
    signatures: dict[str, Any] = {}
    for name in target.protected_timelines:
        timeline = find_timeline(project, name)
        if timeline is not None:
            signatures[name] = structural_signature(
                snapshot_timeline(timeline, is_current=False)
            )
    _, assets = snapshot_assets(project, config)
    signatures["assets"] = asset_signature(assets)
    return signatures


def run_write_probe(
    resolve: Any,
    project: Any,
    config: Config,
    target: WriteProbeTarget,
    *,
    confirmed: bool,
    first_record_frame: int | None = None,
    spacing: int = 400,
    now: datetime | None = None,
) -> ProbeReport:
    """Run the Phase 2 spike. Refuses to write unless `confirmed` and every guard passes."""

    report = ProbeReport(
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
        target=asdict(target),
    )

    if not confirmed:
        raise WriteProbeRefused(
            f"this probe mutates Resolve and requires {CONFIRM_FLAG} to run"
        )

    snapshot = snapshot_project(resolve, project, config)
    report.preflight_failures = preflight_failures(snapshot, target)
    if report.preflight_failures:
        raise WriteProbeRefused(
            "write preflight failed, nothing was modified:\n"
            + "\n".join(f"  - {failure}" for failure in report.preflight_failures)
        )

    native_frames = {
        asset.name: asset.frames
        for asset in snapshot.assets
        if asset.frames is not None and asset.name in target.asset_names
    }
    asset_items = find_asset_items(project, config)
    asset_folder = find_asset_folder(project, config)
    source = find_timeline(project, target.source_timeline)
    source_snapshot = snapshot.timeline(target.source_timeline)
    assert source is not None and source_snapshot is not None  # guaranteed by preflight

    before = _protected_signatures(project, config, target)

    if first_record_frame is None:
        first_record_frame = source_snapshot.start_frame + 200
    experiments = plan_experiments(
        target, native_frames, first_record_frame=first_record_frame, spacing=spacing
    )

    media_pool = project.GetMediaPool()
    previous_timeline = project.GetCurrentTimeline()
    report.previous_current_timeline = (
        str(previous_timeline.GetName()) if previous_timeline else None
    )

    scratch: Any = None
    scratch_name = scratch_timeline_name(now)

    with tempfile.TemporaryDirectory(prefix="daz-probe-") as raw_directory:
        directory = Path(raw_directory)
        reference_comps = _reference_comps(project, target, directory, report)
        try:
            scratch = source.DuplicateTimeline(scratch_name)
            if scratch is None:
                raise RuntimeError(f"DuplicateTimeline({scratch_name!r}) returned None")
            report.wrote = True
            report.scratch_name = str(scratch.GetName())
            report.scratch_unique_id = scratch.GetUniqueId()
            if report.scratch_unique_id == source_snapshot.unique_id:
                raise RuntimeError("the duplicated timeline is not a distinct object")

            scratch_before = structural_signature(
                snapshot_timeline(scratch, is_current=False)
            )
            source_signature = dict(before[target.source_timeline])
            for key in ("name", "unique_id"):
                scratch_before.pop(key)
                source_signature.pop(key)
            # Item unique ids are necessarily new in a duplicate; compare the rest.
            report.scratch_matched_source = _strip_item_ids(
                scratch_before
            ) == _strip_item_ids(source_signature)
            if not report.scratch_matched_source:
                report.notes.append(
                    "scratch timeline does not structurally match the source before "
                    "modification; insertion results must be read with that in mind"
                )

            if not scratch.AddTrack("video"):
                raise RuntimeError("AddTrack('video') on the scratch timeline failed")
            track_index = int(scratch.GetTrackCount("video"))
            report.scratch_track_index = track_index
            if scratch.GetItemListInTrack("video", track_index):
                raise RuntimeError(f"new scratch track V{track_index} is not empty")

            # AppendToTimeline is documented to act on "the current timeline", so the
            # scratch must be made current. Restored in the finally block below.
            if not project.SetCurrentTimeline(scratch):
                raise RuntimeError("SetCurrentTimeline(scratch) failed")

            for experiment in experiments:
                report.insertions.append(
                    _run_experiment(
                        experiment,
                        media_pool=media_pool,
                        asset_items=asset_items,
                        track_index=track_index,
                        directory=directory,
                        reference_comps=reference_comps,
                        asset_folder=asset_folder,
                        report=report,
                    )
                )
        except Exception as exc:
            report.error = f"{type(exc).__name__}: {exc}"
        finally:
            _cleanup(project, media_pool, previous_timeline, scratch, report)
        report.temp_files_removed = True

    after = _protected_signatures(project, config, target)
    report.audit_checked = tuple(sorted(before))
    differences: list[str] = []
    for key in sorted(set(before) | set(after)):
        differences.extend(
            signature_differences(key, before.get(key), after.get(key))
        )
    report.audit_differences = tuple(differences)
    return report


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


def _cleanup(
    project: Any,
    media_pool: Any,
    previous_timeline: Any,
    scratch: Any,
    report: ProbeReport,
) -> None:
    """Restore the current timeline first, then delete only what this run created."""

    if previous_timeline is not None:
        with suppress(Exception):
            project.SetCurrentTimeline(previous_timeline)
    current = project.GetCurrentTimeline()
    report.restored_current_timeline = str(current.GetName()) if current else None

    if scratch is None:
        return
    name = report.scratch_name or ""
    if not name.startswith(SCRATCH_PREFIX):
        # Belt and braces: never hand a timeline this run did not create to DeleteTimelines.
        report.notes.append(f"refusing to delete timeline {name!r}: not a scratch name")
        report.scratch_deleted = False
        return
    try:
        report.scratch_deleted = bool(media_pool.DeleteTimelines([scratch]))
    except Exception as exc:
        report.scratch_deleted = False
        report.notes.append(f"DeleteTimelines raised {exc!r}")
    report.scratch_absent_after_cleanup = find_timeline(project, name) is None
    if not report.scratch_absent_after_cleanup:
        report.notes.append(
            f"SCRATCH TIMELINE LEFT BEHIND: {name!r}. It was not deleted; no other "
            "timeline was touched. Delete it manually after inspection."
        )


__all__ = [
    "CONFIRM_FLAG",
    "SCRATCH_PREFIX",
    "Experiment",
    "InsertionResult",
    "ProbeReport",
    "WriteProbeRefused",
    "plan_experiments",
    "run_write_probe",
    "scratch_timeline_name",
]
