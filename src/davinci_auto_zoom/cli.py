from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.compare import added_generator_items, compare_timelines
from davinci_auto_zoom.domain.probe import WriteProbeTarget
from davinci_auto_zoom.domain.snapshot import ProjectSnapshot
from davinci_auto_zoom.resolve.capability_probe import probe_resolve
from davinci_auto_zoom.resolve.session import (
    ResolveUnavailableError,
    connect,
    current_project,
    snapshot_project,
)
from davinci_auto_zoom.resolve.write_probe import (
    CONFIRM_FLAG,
    WriteProbeRefused,
    run_write_probe,
)

EXIT_OK = 0
EXIT_RESOLVE_UNAVAILABLE = 2
EXIT_BAD_REQUEST = 3
EXIT_PROBE_FAILED = 4


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="davinci-auto-zoom",
        description="Speech-driven zoom automation for DaVinci Resolve. "
        "Every command in this phase is strictly read-only.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON.")
        sub.add_argument("--config", type=Path, default=None, help="Path to config.toml.")
        return sub

    add("doctor", "Environment, connectivity and documented-vs-runtime capability matrix.")
    add("snapshot", "Read-only normalized snapshot of the open project and its timelines.")
    add("assets", "Read-only discovery of the configured asset bin and zoom assets.")

    compare = add("compare", "Read-only structural diff between two timelines.")
    compare.add_argument("input_timeline", help="Timeline without the automated zooms.")
    compare.add_argument("reference_timeline", help="Human reference timeline.")

    probe = add(
        "probe-write",
        "DEVELOPMENT ONLY. Phase 2 spike: WRITES to a scratch timeline it creates and "
        "deletes. Requires an explicit confirmation flag and exact expected names.",
    )
    probe.add_argument(
        CONFIRM_FLAG,
        action="store_true",
        dest="confirmed",
        help="Required. Without it the probe refuses to run and nothing is modified.",
    )
    # Required and explicit on purpose: a write-capable command must never inherit a
    # partially-filled config and guess what it is allowed to touch.
    probe.add_argument("--project", required=True, help="Exact expected project name.")
    probe.add_argument(
        "--source-timeline", required=True, help="Timeline to duplicate. Never modified."
    )
    probe.add_argument(
        "--reference-timeline",
        required=True,
        help="Timeline holding correct manual instances. Read-only reference.",
    )
    return parser


def _emit(payload: dict[str, Any], text: str, as_json: bool) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True) if as_json else text)


def _snapshot(config: Config) -> ProjectSnapshot:
    resolve = connect()
    project = current_project(resolve)
    return snapshot_project(resolve, project, config)


def _snapshot_text(snapshot: ProjectSnapshot) -> str:
    lines = [
        f"{snapshot.product_name} {snapshot.resolve_version}",
        f"project  : {snapshot.project_name} @ {snapshot.frame_rate} fps",
        f"current  : {snapshot.current_timeline or '-'}",
    ]
    for timeline in snapshot.timelines:
        lines.append(
            f"\ntimeline {timeline.name} "
            f"[{timeline.start_frame}, {timeline.end_frame}) tc={timeline.start_timecode}"
            f"{' (current)' if timeline.is_current else ''}"
        )
        for track in timeline.tracks:
            enabled = "?" if track.enabled is None else ("on" if track.enabled else "off")
            lines.append(
                f"  {track.track_type[0].upper()}{track.index} {track.name!r:14} "
                f"{track.sub_type or '-':8} enabled={enabled:3} items={len(track.items)}"
            )
        cuts = timeline.edit_boundaries()
        lines.append(f"  edit boundaries on V1: {len(cuts)}")
    return "\n".join(lines) + _assets_text(snapshot)


def _assets_text(snapshot: ProjectSnapshot) -> str:
    found = "found" if snapshot.asset_bin_found else "MISSING"
    lines = [f"\nasset bin {snapshot.asset_bin!r}: {found}"]
    for asset in snapshot.assets:
        role = asset.role or "-"
        lines.append(
            f"  {asset.name!r:18} role={role:12} type={asset.clip_type!r:12} "
            f"frames={asset.frames} id={asset.media_id}"
        )
    for warning in snapshot.warnings:
        lines.append(f"  ! {warning}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        config = Config.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}")
        return EXIT_BAD_REQUEST

    if args.command == "probe-write":
        try:
            resolve = connect()
            project = current_project(resolve)
        except ResolveUnavailableError as exc:
            print(f"error: {exc}")
            return EXIT_RESOLVE_UNAVAILABLE
        target = WriteProbeTarget(
            project=args.project,
            source_timeline=args.source_timeline,
            reference_timeline=args.reference_timeline,
            asset_bin=config.asset_bin,
            assets=tuple(sorted(config.assets.items())),
        )
        try:
            probe = run_write_probe(
                resolve, project, config, target, confirmed=args.confirmed
            )
        except WriteProbeRefused as exc:
            print(f"refused: {exc}")
            return EXIT_BAD_REQUEST
        _emit(probe.to_dict(), probe.to_text(), args.as_json)
        return EXIT_OK if probe.succeeded else EXIT_PROBE_FAILED

    if args.command == "doctor":
        report = probe_resolve()
        _emit(report.to_dict(), report.to_text(), args.as_json)
        return EXIT_OK if report.connected else EXIT_RESOLVE_UNAVAILABLE

    try:
        snapshot = _snapshot(config)
    except ResolveUnavailableError as exc:
        print(f"error: {exc}")
        return EXIT_RESOLVE_UNAVAILABLE

    if args.command == "snapshot":
        _emit(snapshot.to_dict(), _snapshot_text(snapshot), args.as_json)
        return EXIT_OK

    if args.command == "assets":
        payload = {
            "asset_bin": snapshot.asset_bin,
            "asset_bin_found": snapshot.asset_bin_found,
            "assets": [asdict(a) for a in snapshot.assets],
            "warnings": list(snapshot.warnings),
        }
        _emit(payload, _assets_text(snapshot).lstrip(), args.as_json)
        return EXIT_OK

    if args.command == "compare":
        left = snapshot.timeline(args.input_timeline)
        right = snapshot.timeline(args.reference_timeline)
        missing = [
            name
            for name, value in ((args.input_timeline, left), (args.reference_timeline, right))
            if value is None
        ]
        if left is None or right is None:
            available = ", ".join(t.name for t in snapshot.timelines)
            print(f"error: timeline(s) not found: {', '.join(missing)}. Available: {available}")
            return EXIT_BAD_REQUEST

        comparison = compare_timelines(left, right)
        added = added_generator_items(comparison)
        text = [
            f"{comparison.input_timeline} -> {comparison.reference_timeline}",
            f"shared tracks        : {', '.join(comparison.shared_tracks) or '-'}",
            f"reference-only tracks: {', '.join(comparison.reference_only_tracks) or '-'}",
            f"input-only tracks    : {', '.join(comparison.input_only_tracks) or '-'}",
            f"cuts on V1           : {comparison.cut_count}",
            f"added video items    : {len(added)} "
            f"(start on a cut: {comparison.starts_on_cut}, end on a cut: {comparison.ends_on_cut})",
            "",
        ]
        # Offsets are None when the source track has no cut at all, so they cannot be
        # formatted with a numeric spec.
        def offset(value: int | None) -> str:
            return "    ?" if value is None else f"{value:+5}"

        for item in added:
            text.append(
                f"  {item.name:16} V{item.track_index} [{item.start},{item.end}) "
                f"dur={item.duration:4} startΔcut={offset(item.start_offset_to_nearest_cut)} "
                f"endΔcut={offset(item.end_offset_to_nearest_cut)} gap={item.gap_from_previous}"
            )
        _emit(comparison.to_dict(), "\n".join(text), args.as_json)
        return EXIT_OK

    raise AssertionError(f"Unhandled command: {args.command}")
