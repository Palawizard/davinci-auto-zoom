from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from davinci_auto_zoom.resolve.capabilities import CapabilityResult, evaluate, summarize
from davinci_auto_zoom.resolve.docs import load_documented_api
from davinci_auto_zoom.resolve.loader import existing_native_libraries, load_resolve_script_module
from davinci_auto_zoom.resolve.session import (
    ResolveUnavailableError,
    connect,
    current_project,
    runtime_method_names,
)

# Capabilities exercised read-only by `snapshot`/`assets`/`compare` during Phase 1.
CONFIRMED_BY_DISCOVERY = frozenset(
    {
        "resolve.version",
        "resolve.product",
        "project.current",
        "timeline.list",
        "timeline.current",
        "timeline.frame_rate",
        "timeline.start_frame",
        "timeline.end_frame",
        "timeline.start_tc",
        "track.count",
        "track.name",
        "track.sub_type",
        "track.enabled",
        "track.locked",
        "item.list",
        "item.start",
        "item.end",
        "item.duration",
        "item.source_in",
        "item.track",
        "item.media_pool",
        "item.unique_id",
        "item.fusion_count",
        "item.fusion_names",
        "item.properties",
        "pool.root",
        "bin.subfolders",
        "bin.clips",
        "asset.name",
        "asset.properties",
        "asset.media_id",
        "asset.unique_id",
    }
)


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    connected: bool
    module_loaded: bool
    resolve_version: str | None
    product_name: str | None
    project_name: str | None
    timeline_name: str | None
    attempted_module_paths: tuple[str, ...]
    native_libraries: tuple[str, ...]
    developer_docs_path: str | None
    documented_classes: tuple[str, ...]
    documented_method_count: int
    capabilities: tuple[CapabilityResult, ...]
    notes: tuple[str, ...]

    @property
    def status_counts(self) -> dict[str, int]:
        return summarize(self.capabilities)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = [c.to_dict() for c in self.capabilities]
        data["status_counts"] = self.status_counts
        return data

    def to_text(self) -> str:
        lines = [
            "davinci-auto-zoom doctor",
            f"  module loaded : {self.module_loaded}",
            f"  connected     : {self.connected}",
            f"  product       : {self.product_name or '-'}",
            f"  resolve       : {self.resolve_version or '-'}",
            f"  project       : {self.project_name or '-'}",
            f"  timeline      : {self.timeline_name or '-'}",
            f"  developer docs: {self.developer_docs_path or 'NOT FOUND'}",
            f"  documented    : {self.documented_method_count} methods "
            f"across {len(self.documented_classes)} classes",
        ]
        if self.native_libraries:
            lines.append("  native libs   :")
            lines.extend(f"    - {item}" for item in self.native_libraries)
        if self.attempted_module_paths:
            lines.append("  module paths  :")
            lines.extend(f"    - {item}" for item in self.attempted_module_paths)
        if self.capabilities:
            lines.append("  capabilities  :")
            counts = self.status_counts
            lines.extend(f"    {status:38} {count}" for status, count in sorted(counts.items()))
        if self.notes:
            lines.append("  notes:")
            lines.extend(f"    - {item}" for item in self.notes)
        return "\n".join(lines)


def probe_resolve() -> CapabilityReport:
    docs_path, documented = load_documented_api()
    documented_count = sum(len(methods) for methods in documented.values())
    notes: list[str] = []
    if docs_path is None:
        notes.append(
            "Blackmagic Developer/Scripting README.txt was not found; capability statuses "
            "cannot be backed by documentation. Set RESOLVE_SCRIPT_API."
        )

    load = load_resolve_script_module()
    module_paths = load.attempted_module_paths
    libraries = existing_native_libraries()
    docs = str(docs_path) if docs_path else None
    classes = tuple(sorted(documented))

    try:
        resolve = connect()
        project = current_project(resolve)
    except ResolveUnavailableError as exc:
        notes.append(str(exc))
        return CapabilityReport(
            connected=False,
            module_loaded=load.module is not None,
            resolve_version=None,
            product_name=None,
            project_name=None,
            timeline_name=None,
            attempted_module_paths=module_paths,
            native_libraries=libraries,
            developer_docs_path=docs,
            documented_classes=classes,
            documented_method_count=documented_count,
            capabilities=evaluate(documented, {}),
            notes=tuple(notes),
        )

    runtime = runtime_method_names(resolve, project)
    timeline = project.GetCurrentTimeline()

    return CapabilityReport(
        connected=True,
        module_loaded=True,
        resolve_version=str(resolve.GetVersionString()),
        product_name=str(resolve.GetProductName()),
        project_name=str(project.GetName()),
        timeline_name=str(timeline.GetName()) if timeline else None,
        attempted_module_paths=module_paths,
        native_libraries=libraries,
        developer_docs_path=docs,
        documented_classes=classes,
        documented_method_count=documented_count,
        capabilities=evaluate(documented, runtime, CONFIRMED_BY_DISCOVERY),
        notes=tuple(notes),
    )
