"""Read the manual reference edit out of a live Resolve project. STRICTLY READ-ONLY.

No timeline is created, duplicated, modified or deleted here, and no project setting is
touched: this module only calls getters. The one write-capable step Phase 11a needs is the
audio render, which reuses the already-proven `resolve.voice_render` path unchanged.

Run it directly to print the structural dataset:

    python -m tools.research.phase11a.reference --project "bluescreen 2" \
        --timeline "Timeline 1" --labelled 216000 218870
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from davinci_auto_zoom.resolve.session import connect, find_timeline
from tools.research.phase11a.manual import ManualEdit, ManualPlacement, nearest_cut, reconstruct
from tools.research.phase11a.structure import Clip, ContentIsland, content_islands

#: Clip name -> transition role, for this creator's bin. Same table the product's config has.
DEFAULT_ROLE_BY_ASSET: dict[str, str] = {
    "FACE_X1": "x0_to_face_x1",
    "FACE_X2": "face_x1_to_face_x2",
    "FACE_X3": "face_x2_to_face_x3",
    "X1_TO_X0": "face_x1_to_x0",
    "X2_TO_X0": "face_x2_to_x0",
    "X3_TO_X0": "face_x3_to_x0",
}


@dataclass(frozen=True, slots=True)
class Reference:
    project: str
    timeline: str
    timeline_unique_id: str
    frame_rate: str
    timeline_start: int
    timeline_end: int
    labelled_start: int
    labelled_end: int
    islands: tuple[ContentIsland, ...]
    manual: ManualEdit

    @property
    def hard_cuts(self) -> tuple[int, ...]:
        return tuple(cut for island in self.islands for cut in island.hard_cuts)


def read_reference(
    *,
    project_name: str,
    timeline_name: str,
    labelled_start: int,
    labelled_end: int,
    cut_track: int,
    zoom_track: int,
    transition_frames: int,
    min_gap_frames: int,
    role_by_asset: dict[str, str] | None = None,
) -> Reference:
    """Everything the study needs about the human edit, with nothing outside the labels."""

    roles = role_by_asset or DEFAULT_ROLE_BY_ASSET
    resolve = connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None or str(project.GetName()) != project_name:
        raise RuntimeError(
            f"expected {project_name!r} to be the open project, found "
            f"{project.GetName() if project else None!r}. Phase 11a never switches projects."
        )
    timeline = find_timeline(project, timeline_name)
    if timeline is None:
        raise RuntimeError(f"timeline {timeline_name!r} not found in {project_name!r}")

    clips = [
        Clip(int(item.GetStart()), int(item.GetEnd()), str(item.GetName()))
        for item in (timeline.GetItemListInTrack("video", cut_track) or [])
        # Labels exist only inside the annotated range. A clip outside it is not a negative
        # example, so it never enters the dataset at all.
        if int(item.GetStart()) >= labelled_start and int(item.GetEnd()) <= labelled_end
    ]
    islands = content_islands(clips, min_gap_frames=min_gap_frames)

    placements: list[ManualPlacement] = []
    unknown: list[str] = []
    for item in timeline.GetItemListInTrack("video", zoom_track) or []:
        start, end = int(item.GetStart()), int(item.GetEnd())
        if start < labelled_start or end > labelled_end:
            continue
        role = roles.get(str(item.GetName()))
        if role is None:
            unknown.append(f"{item.GetName()!r} at {start}")
            continue
        placements.append(ManualPlacement(role=role, start=start, end=end))
    manual = reconstruct(placements, islands, transition_frames=transition_frames)
    if unknown:
        manual = ManualEdit(
            placements=manual.placements,
            resets=manual.resets,
            entries=manual.entries,
            problems=manual.problems + tuple(f"unknown asset {u}" for u in unknown),
        )

    return Reference(
        project=str(project.GetName()),
        timeline=str(timeline.GetName()),
        timeline_unique_id=str(timeline.GetUniqueId()),
        frame_rate=str(timeline.GetSetting("timelineFrameRate")),
        timeline_start=int(timeline.GetStartFrame()),
        timeline_end=int(timeline.GetEndFrame()),
        labelled_start=labelled_start,
        labelled_end=labelled_end,
        islands=islands,
        manual=manual,
    )


def to_json(reference: Reference) -> str:
    """Frame numbers and roles only. No clip source paths, no user text — safe to read."""

    payload: dict[str, Any] = {
        "project": reference.project,
        "timeline": reference.timeline,
        "timeline_unique_id": reference.timeline_unique_id,
        "frame_rate": reference.frame_rate,
        "labelled": [reference.labelled_start, reference.labelled_end],
        "islands": [
            {
                "index": island.index,
                "start": island.start,
                "end": island.end,
                "clips": [[c.start, c.end] for c in island.clips],
                "hard_cuts": list(island.hard_cuts),
            }
            for island in reference.islands
        ],
        "placements": [asdict(p) for p in reference.manual.placements],
        "resets": [asdict(r) for r in reference.manual.resets],
        "entries": list(reference.manual.entries),
        "problems": list(reference.manual.problems),
    }
    return json.dumps(payload, indent=2)


def _report(reference: Reference) -> str:
    lines = [
        f"project            {reference.project}",
        f"timeline           {reference.timeline}  id={reference.timeline_unique_id}",
        f"frame rate         {reference.frame_rate}",
        f"timeline range     [{reference.timeline_start},{reference.timeline_end})",
        f"labelled range     [{reference.labelled_start},{reference.labelled_end})",
        "",
    ]
    for island in reference.islands:
        lines.append(
            f"island {island.index}  [{island.start},{island.end})  "
            f"{len(island.clips)} clips  {len(island.hard_cuts)} hard cuts"
        )
    cuts = reference.hard_cuts
    lines += ["", f"hard cuts total    {len(cuts)}", ""]
    lines.append(
        f"{'reset':>8s} {'role':<16s} {'isl':>3s} {'cut':>8s} {'d':>5s} "
        f"{'nextX1':>8s} {'anchor':>7s} {'dwell':>6s}  reason"
    )
    for reset in reference.manual.resets:
        island_cuts = reference.islands[reset.island_index].hard_cuts
        near = nearest_cut(island_cuts, reset.start)
        cut_text = f"{near[0]:>8d} {near[1]:>5d}" if near else f"{'-':>8s} {'-':>5s}"
        lines.append(
            f"{reset.start:>8d} {reset.role:<16s} {reset.island_index:>3d} {cut_text} "
            f"{(reset.next_face_x1_start or 0):>8d} "
            f"{(reset.anchor_gap if reset.anchor_gap is not None else -1):>7d} "
            f"{(reset.pure_x0_dwell if reset.pure_x0_dwell is not None else -1):>6d}  "
            f"{reset.reason}"
        )
    if reference.manual.problems:
        lines += ["", "PROBLEMS:"] + [f"  - {p}" for p in reference.manual.problems]
    else:
        lines += ["", "no state-machine problems: every manual move is a legal transition"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--labelled", nargs=2, type=int, required=True, metavar=("START", "END"))
    parser.add_argument("--cut-track", type=int, default=1)
    parser.add_argument("--zoom-track", type=int, default=2)
    parser.add_argument("--transition-frames", type=int, default=15)
    parser.add_argument("--min-gap-frames", type=int, default=30)
    parser.add_argument("--json", type=str, default=None, help="write the dataset here")
    args = parser.parse_args()

    reference = read_reference(
        project_name=args.project,
        timeline_name=args.timeline,
        labelled_start=args.labelled[0],
        labelled_end=args.labelled[1],
        cut_track=args.cut_track,
        zoom_track=args.zoom_track,
        transition_frames=args.transition_frames,
        min_gap_frames=args.min_gap_frames,
    )
    print(_report(reference))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(to_json(reference))


if __name__ == "__main__":
    main()
