"""Capability matrix for the installed Resolve build.

Two independent sources are combined and never conflated:

* the installed Blackmagic README (authoritative for what is *supported*),
* the live Python object surface (authoritative for what is *present at runtime*).

Write-capable methods are reported only. This phase never calls them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from davinci_auto_zoom.resolve.docs import DocumentedMethod


class Status(StrEnum):
    #: documented, present at runtime and exercised read-only during discovery
    CONFIRMED = "confirmed"
    #: documented and present at runtime, but calling it would mutate the project
    DOCUMENTED_WRITE_GATED = "documented-but-not-runtime-verified"
    #: documented but absent from the live object
    DOCUMENTED_ONLY = "documented"
    #: present at runtime but absent from the installed documentation
    UNDOCUMENTED = "likely"
    #: neither documented nor present
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class CapabilityQuestion:
    """One thing davinci-auto-zoom needs to know it can do."""

    key: str
    owner: str
    method: str
    purpose: str
    mutates: bool = False


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    key: str
    owner: str
    method: str
    purpose: str
    mutates: bool
    status: Status
    signature: str | None
    note: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = str(self.status)
        return data


# Ordered by the question it answers for the project, not by API class.
QUESTIONS: tuple[CapabilityQuestion, ...] = (
    # --- environment -------------------------------------------------------------
    CapabilityQuestion("resolve.version", "Resolve", "GetVersionString", "Exact Resolve build"),
    CapabilityQuestion("resolve.product", "Resolve", "GetProductName", "Studio vs free"),
    CapabilityQuestion("project.current", "ProjectManager", "GetCurrentProject", "Active project"),
    # --- timeline reading --------------------------------------------------------
    CapabilityQuestion("timeline.list", "Project", "GetTimelineByIndex", "Enumerate timelines"),
    CapabilityQuestion("timeline.current", "Project", "GetCurrentTimeline", "Active timeline"),
    CapabilityQuestion("timeline.frame_rate", "Timeline", "GetSetting", "Timeline frame rate"),
    CapabilityQuestion("timeline.start_frame", "Timeline", "GetStartFrame", "Timeline start frame"),
    CapabilityQuestion("timeline.end_frame", "Timeline", "GetEndFrame", "Timeline end frame"),
    CapabilityQuestion("timeline.start_tc", "Timeline", "GetStartTimecode", "Start timecode"),
    CapabilityQuestion("track.count", "Timeline", "GetTrackCount", "Track counts per type"),
    CapabilityQuestion("track.name", "Timeline", "GetTrackName", "Track names"),
    CapabilityQuestion("track.sub_type", "Timeline", "GetTrackSubType", "Audio track format"),
    CapabilityQuestion("track.enabled", "Timeline", "GetIsTrackEnabled", "Track enable state"),
    CapabilityQuestion("track.locked", "Timeline", "GetIsTrackLocked", "Track lock state"),
    CapabilityQuestion("item.list", "Timeline", "GetItemListInTrack", "Clips per track"),
    CapabilityQuestion("item.start", "TimelineItem", "GetStart", "Record-in frame"),
    CapabilityQuestion("item.end", "TimelineItem", "GetEnd", "Record-out frame"),
    CapabilityQuestion("item.duration", "TimelineItem", "GetDuration", "Instance duration"),
    CapabilityQuestion("item.source_in", "TimelineItem", "GetSourceStartFrame", "Source in point"),
    CapabilityQuestion("item.track", "TimelineItem", "GetTrackTypeAndIndex", "Owning track"),
    CapabilityQuestion("item.media_pool", "TimelineItem", "GetMediaPoolItem", "Instance->asset"),
    CapabilityQuestion("item.unique_id", "TimelineItem", "GetUniqueId", "Stable instance id"),
    CapabilityQuestion("item.fusion_count", "TimelineItem", "GetFusionCompCount", "Fusion comps"),
    CapabilityQuestion("item.fusion_names", "TimelineItem", "GetFusionCompNameList", "Comp names"),
    CapabilityQuestion("item.properties", "TimelineItem", "GetProperty", "Transform values"),
    # --- media pool --------------------------------------------------------------
    CapabilityQuestion("pool.root", "MediaPool", "GetRootFolder", "Media Pool root bin"),
    CapabilityQuestion("bin.subfolders", "Folder", "GetSubFolderList", "Recursive bin walk"),
    CapabilityQuestion("bin.clips", "Folder", "GetClipList", "Bin contents"),
    CapabilityQuestion("asset.name", "MediaPoolItem", "GetName", "Asset name"),
    CapabilityQuestion("asset.properties", "MediaPoolItem", "GetClipProperty", "Asset type"),
    CapabilityQuestion("asset.media_id", "MediaPoolItem", "GetMediaId", "Stable asset id"),
    CapabilityQuestion("asset.unique_id", "MediaPoolItem", "GetUniqueId", "Stable asset id"),
    # --- write paths, reported only ----------------------------------------------
    CapabilityQuestion(
        "write.append_asset",
        "MediaPool",
        "AppendToTimeline",
        "Place an existing Media Pool asset at a record frame/track",
        mutates=True,
    ),
    CapabilityQuestion(
        "write.insert_generator",
        "Timeline",
        "InsertGeneratorIntoTimeline",
        "Insert a generator (e.g. Adjustment Clip) at the playhead",
        mutates=True,
    ),
    CapabilityQuestion(
        "write.insert_fusion_generator",
        "Timeline",
        "InsertFusionGeneratorIntoTimeline",
        "Insert a Fusion generator at the playhead",
        mutates=True,
    ),
    CapabilityQuestion(
        "write.import_fusion_comp",
        "TimelineItem",
        "ImportFusionComp",
        "Apply an exported Fusion composition to an item (effect reuse fallback)",
        mutates=True,
    ),
    CapabilityQuestion(
        "write.export_fusion_comp",
        "TimelineItem",
        "ExportFusionComp",
        "Export an item's Fusion composition to disk (writes a file, not the project)",
        mutates=True,
    ),
    CapabilityQuestion(
        "write.delete_items", "Timeline", "DeleteClips", "Remove tool-owned items", mutates=True
    ),
    CapabilityQuestion(
        "write.duplicate_timeline",
        "Timeline",
        "DuplicateTimeline",
        "Create a throwaway timeline for Phase 2 experiments",
        mutates=True,
    ),
    CapabilityQuestion(
        "write.add_track", "Timeline", "AddTrack", "Create the zoom video track", mutates=True
    ),
    CapabilityQuestion(
        "write.item_marker", "TimelineItem", "AddMarker", "Ownership tagging", mutates=True
    ),
    CapabilityQuestion(
        "write.item_name", "TimelineItem", "SetName", "Ownership tagging", mutates=True
    ),
    CapabilityQuestion(
        "write.render", "Project", "SetRenderSettings", "Audio-only render for VAD", mutates=True
    ),
    CapabilityQuestion(
        "write.add_render_job", "Project", "AddRenderJob", "Audio-only render for VAD", mutates=True
    ),
    # --- transcription -----------------------------------------------------------
    CapabilityQuestion(
        "speech.transcribe",
        "MediaPoolItem",
        "TranscribeAudio",
        "Generate a transcript (no documented way to read it back)",
        mutates=True,
    ),
    CapabilityQuestion(
        "speech.subtitles",
        "Timeline",
        "CreateSubtitlesFromAudio",
        "Create a subtitle track whose items expose text via TimelineItem.GetName",
        mutates=True,
    ),
    CapabilityQuestion(
        "speech.read_transcript",
        "MediaPoolItem",
        "GetTranscription",
        "Read transcript text/timestamps directly (expected absent)",
    ),
    CapabilityQuestion(
        "speech.export_subtitles",
        "Timeline",
        "Export",
        "Export a subtitle/SRT file once a subtitle track exists",
        mutates=True,
    ),
)


def evaluate(
    documented: dict[str, dict[str, DocumentedMethod]],
    runtime_methods: dict[str, frozenset[str]],
    confirmed_keys: frozenset[str] = frozenset(),
) -> tuple[CapabilityResult, ...]:
    """Combine documentation and runtime evidence into a capability matrix.

    ``runtime_methods`` maps an API class name to the attribute names observed on a live
    object of that class. ``confirmed_keys`` lists capabilities actually exercised
    read-only during discovery.
    """

    results: list[CapabilityResult] = []
    for question in QUESTIONS:
        doc = documented.get(question.owner, {}).get(question.method)
        present = question.method in runtime_methods.get(question.owner, frozenset())

        if doc and present:
            if question.key in confirmed_keys:
                status, note = Status.CONFIRMED, "exercised read-only"
            elif question.mutates:
                status, note = Status.DOCUMENTED_WRITE_GATED, "not called: would mutate"
            else:
                status, note = Status.CONFIRMED, "documented and present at runtime"
        elif doc and not present:
            status, note = Status.DOCUMENTED_ONLY, "documented but absent from the live object"
        elif present and not doc:
            status = Status.UNDOCUMENTED
            note = "present at runtime but undocumented; do not rely on it"
        else:
            status, note = Status.UNSUPPORTED, "neither documented nor present"

        results.append(
            CapabilityResult(
                key=question.key,
                owner=question.owner,
                method=question.method,
                purpose=question.purpose,
                mutates=question.mutates,
                status=status,
                signature=doc.signature if doc else None,
                note=note,
            )
        )
    return tuple(results)


def summarize(results: tuple[CapabilityResult, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts[str(result.status)] = counts.get(str(result.status), 0) + 1
    return counts
