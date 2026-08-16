"""Read-only adapter between live Resolve proxy objects and plain snapshot data.

Every function here is strictly read-only. Raw Resolve objects never leave this module.
"""

from __future__ import annotations

from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.snapshot import (
    AssetSnapshot,
    ProjectSnapshot,
    TimelineItemSnapshot,
    TimelineSnapshot,
    TrackSnapshot,
)
from davinci_auto_zoom.resolve.loader import load_resolve_script_module

TRACK_TYPES = ("video", "audio", "subtitle")


class ResolveUnavailableError(RuntimeError):
    """Resolve could not be reached. The message explains what the user should check."""


def connect() -> Any:
    """Return the live Resolve API object, or raise with an actionable message."""

    load = load_resolve_script_module()
    if load.module is None:
        attempted = ", ".join(load.attempted_module_paths) or "none"
        raise ResolveUnavailableError(
            f"{load.error or 'DaVinciResolveScript could not be imported'}.\n"
            f"Searched module paths: {attempted}.\n"
            "Set RESOLVE_SCRIPT_API and RESOLVE_SCRIPT_LIB as described in the installed "
            "Developer/Scripting/README.txt, and make sure DaVinci Resolve is running."
        )
    try:
        resolve = load.module.scriptapp("Resolve")
    except Exception as exc:  # the Blackmagic wrapper raises bare exceptions
        raise ResolveUnavailableError(f"scriptapp('Resolve') failed: {exc!r}") from exc
    if resolve is None:
        raise ResolveUnavailableError(
            "Resolve scripting returned no application object. Open DaVinci Resolve and enable "
            "External scripting in Preferences > System > General."
        )
    return resolve


def current_project(resolve: Any) -> Any:
    manager = resolve.GetProjectManager()
    if manager is None:
        raise ResolveUnavailableError("Resolve returned no ProjectManager.")
    project = manager.GetCurrentProject()
    if project is None:
        raise ResolveUnavailableError("No project is currently open in Resolve.")
    return project


def runtime_method_names(resolve: Any, project: Any) -> dict[str, frozenset[str]]:
    """Attribute names present on one live object per API class.

    Presence is evidence of a runtime surface only; the installed docs decide support.
    """

    media_pool = project.GetMediaPool()
    root = media_pool.GetRootFolder() if media_pool else None
    clips = (root.GetClipList() if root else None) or []
    timeline = project.GetCurrentTimeline() or (
        project.GetTimelineByIndex(1) if project.GetTimelineCount() else None
    )
    item = None
    if timeline is not None:
        for index in range(1, timeline.GetTrackCount("video") + 1):
            items = timeline.GetItemListInTrack("video", index) or []
            if items:
                item = items[0]
                break

    objects: dict[str, Any] = {
        "Resolve": resolve,
        "ProjectManager": resolve.GetProjectManager(),
        "Project": project,
        "MediaPool": media_pool,
        "Folder": root,
        "MediaPoolItem": clips[0] if clips else None,
        "Timeline": timeline,
        "TimelineItem": item,
    }
    return {
        name: frozenset(a for a in dir(obj) if not a.startswith("_"))
        for name, obj in objects.items()
        if obj is not None
    }


def _snapshot_item(item: Any) -> TimelineItemSnapshot:
    media_pool_item = item.GetMediaPoolItem()
    return TimelineItemSnapshot(
        name=str(item.GetName()),
        start=int(item.GetStart()),
        end=int(item.GetEnd()),
        unique_id=item.GetUniqueId(),
        media_pool_item_name=str(media_pool_item.GetName()) if media_pool_item else None,
        source_start_frame=item.GetSourceStartFrame(),
        fusion_comp_count=int(item.GetFusionCompCount() or 0),
    )


def snapshot_timeline(timeline: Any, *, is_current: bool) -> TimelineSnapshot:
    tracks: list[TrackSnapshot] = []
    for track_type in TRACK_TYPES:
        for index in range(1, int(timeline.GetTrackCount(track_type) or 0) + 1):
            items = timeline.GetItemListInTrack(track_type, index) or []
            tracks.append(
                TrackSnapshot(
                    track_type=track_type,
                    index=index,
                    name=str(timeline.GetTrackName(track_type, index)),
                    sub_type=str(timeline.GetTrackSubType(track_type, index) or ""),
                    # Resolve reports False for every track of a non-current timeline,
                    # so the value is only meaningful for the current one.
                    enabled=bool(timeline.GetIsTrackEnabled(track_type, index))
                    if is_current
                    else None,
                    locked=bool(timeline.GetIsTrackLocked(track_type, index))
                    if is_current
                    else None,
                    items=tuple(_snapshot_item(item) for item in items),
                )
            )

    return TimelineSnapshot(
        name=str(timeline.GetName()),
        unique_id=timeline.GetUniqueId(),
        frame_rate=float(timeline.GetSetting("timelineFrameRate")),
        start_frame=int(timeline.GetStartFrame()),
        end_frame=int(timeline.GetEndFrame()),
        start_timecode=str(timeline.GetStartTimecode()),
        is_current=is_current,
        tracks=tuple(tracks),
    )


def _walk_bins(folder: Any, path: str = "") -> list[tuple[str, Any]]:
    here = f"{path}/{folder.GetName()}" if path else str(folder.GetName())
    found = [(here, folder)]
    for child in folder.GetSubFolderList() or []:
        found.extend(_walk_bins(child, here))
    return found


def snapshot_assets(project: Any, config: Config) -> tuple[bool, tuple[AssetSnapshot, ...]]:
    """Find the configured asset bin recursively and describe every clip inside it."""

    media_pool = project.GetMediaPool()
    if media_pool is None:
        return False, ()

    roles = {name: role for role, name in config.assets.items()}
    found = False
    assets: list[AssetSnapshot] = []
    for bin_path, folder in _walk_bins(media_pool.GetRootFolder()):
        if folder.GetName() != config.asset_bin:
            continue
        found = True
        for clip in folder.GetClipList() or []:
            properties = clip.GetClipProperty() or {}
            frames = str(properties.get("Frames", ""))
            name = str(clip.GetName())
            assets.append(
                AssetSnapshot(
                    name=name,
                    bin_path=bin_path,
                    clip_type=str(properties.get("Type", "")),
                    frames=int(frames) if frames.isdigit() else None,
                    media_id=clip.GetMediaId(),
                    unique_id=clip.GetUniqueId(),
                    role=roles.get(name),
                )
            )
    return found, tuple(assets)


def snapshot_project(resolve: Any, project: Any, config: Config) -> ProjectSnapshot:
    current = project.GetCurrentTimeline()
    current_name = str(current.GetName()) if current else None

    timelines = tuple(
        snapshot_timeline(
            project.GetTimelineByIndex(index),
            is_current=str(project.GetTimelineByIndex(index).GetName()) == current_name,
        )
        for index in range(1, int(project.GetTimelineCount() or 0) + 1)
    )

    bin_found, assets = snapshot_assets(project, config)

    warnings: list[str] = []
    if not bin_found:
        warnings.append(
            f"Media Pool bin {config.asset_bin!r} was not found; configure [resolve].asset_bin."
        )
    for role, name in config.assets.items():
        matches = [a for a in assets if a.name == name]
        if not matches:
            warnings.append(f"No asset named {name!r} for role {role!r} in {config.asset_bin!r}.")
        elif len(matches) > 1:
            warnings.append(
                f"Asset name {name!r} (role {role!r}) is ambiguous: {len(matches)} matches."
            )
    if config.project and config.project != str(project.GetName()):
        warnings.append(
            f"Configured project {config.project!r} != open project {project.GetName()!r}."
        )
    if any(t.name == config.timeline for t in timelines) is False and config.timeline:
        warnings.append(f"Configured timeline {config.timeline!r} was not found in the project.")

    return ProjectSnapshot(
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
        frame_rate=float(project.GetSetting("timelineFrameRate")),
        current_timeline=current_name,
        timelines=timelines,
        asset_bin=config.asset_bin,
        asset_bin_found=bin_found,
        assets=assets,
        warnings=tuple(warnings),
    )
