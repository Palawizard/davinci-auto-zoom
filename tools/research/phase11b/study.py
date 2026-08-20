"""Phase 11b driver: development measurements, the blind prediction table, and the unblinding.

    PYTHONPATH=src:. python -m tools.research.phase11b.study dev \
        --reference .../copy.json --audio .../audio.json
    PYTHONPATH=src:. python -m tools.research.phase11b.study blind \
        --reference .../copy.json --audio .../audio.json --out .../predictions.json
    PYTHONPATH=src:. python -m tools.research.phase11b.study unblind \
        --reference .../copy.json --audio .../audio.json --labels .../timeline1.json

`--reference` is always the blind timeline (`Timeline 1 copy`). `--labels` is the reference
timeline (`Timeline 1`) and is only ever read in the `unblind` stage, after the checkpoint
commit exists.

Only frames, roles and categories are printed. The transcript never enters this module: the
semantic judgements arrive pre-frozen from `annotations.py`.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.dynamics import (
    EnergyEnvelope,
    EnergyPoint,
    EnergySettings,
    voice_valleys,
)
from davinci_auto_zoom.domain.models import FrameRange
from davinci_auto_zoom.domain.transitions import BY_ROLE, STATE_X0
from tools.research.phase11a.ablation import Evaluation, evaluate, table
from tools.research.phase11a.manual import ManualPlacement, reconstruct
from tools.research.phase11a.structure import Clip, ContentIsland, content_islands
from tools.research.phase11b.annotations import BLIND_SHORT_3, DEVELOPMENT
from tools.research.phase11b.evaluate import frame_deltas, recall_by_category
from tools.research.phase11b.policy import (
    REENTRY_GAP_FRAMES,
    BlindPredictions,
    CutPrediction,
    SemanticAnnotation,
    baseline_policy,
    combined_policy,
    fixed_gap_reentry,
    semantic_policy,
)
from tools.research.phase11b.simulate import SimTrace, simulate_island

#: Frozen promotion thresholds. Copied from `PlannerSettings` and NOT retuned (task §37).
PROMOTION = {
    "min_drop_db": 20.0,
    "recovery_within_db": 6.0,
    "min_valley_ms": 30,
    "max_valley_ms": 650,
}
#: Islands 0 and 1 develop the rules, island 2 is the blind test, and everything from island 3
#: on is out of scope for the whole phase (task §3).
DEVELOPMENT_ISLANDS = (0, 1)
BLIND_ISLAND = 2

CANDIDATES = ("P0", "P1", "P2", "P3", "BASELINE")


def _policy(name: str, annotations: Sequence[SemanticAnnotation]) -> Any:
    if name == "P0":
        return semantic_policy(annotations)
    if name == "P1":
        return combined_policy(annotations, rhythm=False, loop=True)
    if name == "P2":
        return combined_policy(annotations, rhythm=True, loop=False)
    if name == "P3":
        return combined_policy(annotations, rhythm=True, loop=True)
    if name == "BASELINE":
        return baseline_policy()
    raise ValueError(f"unknown candidate {name!r}")


@dataclass(frozen=True, slots=True)
class ManualRhythm:
    """The same counters `simulate.RhythmContext` carries, but read off the manual track."""

    state: str
    held: int
    at_x3: int | None
    cycle: int | None
    cuts_in_cycle: int


class Dataset:
    """Islands, manual edit and voice recoveries, all read from ONE timeline export."""

    def __init__(self, reference: dict[str, Any], audio: dict[str, Any]) -> None:
        self.reference = reference
        self.islands = content_islands(
            [Clip(s, e) for island in reference["islands"] for s, e in island["clips"]],
            min_gap_frames=30,
        )
        self.manual = reconstruct(
            [ManualPlacement(**p) for p in reference["placements"]],
            self.islands,
            transition_frames=15,
        )
        envelope = EnergyEnvelope(
            tuple(EnergyPoint(frame, db) for frame, db in audio["envelope"]), EnergySettings()
        )
        self.speech_ranges = [tuple(r) for r in audio["speech_ranges"]]
        self.recoveries: dict[int, tuple[int, ...]] = {}
        for island in self.islands:
            valleys = voice_valleys(
                envelope,
                FrameRange(island.start, island.end),
                burst_index=island.index,
                min_drop_db=PROMOTION["min_drop_db"],
                recovery_within_db=PROMOTION["recovery_within_db"],
                min_valley_ms=int(PROMOTION["min_valley_ms"]),
                max_valley_ms=int(PROMOTION["max_valley_ms"]),
            )
            self.recoveries[island.index] = tuple(
                v.recovery_frame for v in valleys if v.qualified and v.recovery_frame is not None
            )

    def island(self, index: int) -> ContentIsland:
        return self.islands[index]

    def manual_state_at(self, island: ContentIsland, frame: int) -> str:
        """The creator's own state just before `frame`. Only valid where labels exist."""

        state = STATE_X0
        for placement in self.manual.placements:
            if not island.contains(placement.start):
                continue
            if placement.start >= frame:
                break
            state = BY_ROLE[placement.role].to_state
        return state

    def manual_rhythm(self, island: ContentIsland, frame: int) -> ManualRhythm:
        """The rhythm counters at `frame`, read off the creator's own track.

        Only usable where the labels exist — on the blind Short the same quantities come from
        `simulate.RhythmContext` instead, which is the whole point of the simulator.
        """

        state = STATE_X0
        state_since = island.start
        cycle: int | None = None
        x3: int | None = None
        for placement in self.manual.placements:
            if not island.contains(placement.start) or placement.start >= frame:
                continue
            transition = BY_ROLE[placement.role]
            state = transition.to_state
            state_since = placement.start
            if transition.is_reset:
                cycle, x3 = None, None
            elif placement.role == "x0_to_face_x1":
                cycle, x3 = placement.start, None
            elif transition.to_state == "face_x3":
                x3 = placement.start
        return ManualRhythm(
            state=state,
            held=frame - state_since,
            at_x3=None if x3 is None else frame - x3,
            cycle=None if cycle is None else frame - cycle,
            cuts_in_cycle=(
                0 if cycle is None else sum(1 for c in island.hard_cuts if cycle <= c <= frame)
            ),
        )

    def manual_reset_cuts(self, island: ContentIsland) -> tuple[int, ...]:
        """Hard cuts carrying a manual reset EXACTLY on them (Phase 11a: the window is 0)."""

        starts = {r.start for r in self.manual.resets if island.contains(r.start)}
        return tuple(cut for cut in island.hard_cuts if cut in starts)

    def off_cut_resets(self, island: ContentIsland) -> tuple[int, ...]:
        return tuple(
            sorted(
                r.start
                for r in self.manual.resets
                if island.contains(r.start) and r.start not in set(island.hard_cuts)
            )
        )


def run(dataset: Dataset, island_index: int, annotations: Sequence[SemanticAnnotation]) -> dict[
    str, SimTrace
]:
    island = dataset.island(island_index)
    recoveries = dataset.recoveries[island_index]
    return {
        name: simulate_island(
            island,
            recoveries,
            reset_policy=_policy(name, annotations),
            reentry_policy=fixed_gap_reentry(),
        )
        for name in CANDIDATES
    }


def _score(
    name: str, island: ContentIsland, trace: SimTrace, actual: Sequence[int]
) -> Evaluation:
    return evaluate(name, island.hard_cuts, trace.reset_frames, actual)


# ------------------------------------------------------------------------------ dev stage
def dev_report(dataset: Dataset) -> str:
    lines: list[str] = ["DEVELOPMENT SET — Shorts 1 and 2 of `Timeline 1 copy`", ""]
    annotations = {a.frame: a for a in DEVELOPMENT}

    lines.append("islands detected (island 3 and anything after it are OUT OF SCOPE):")
    for island in dataset.islands:
        role = (
            "development"
            if island.index in DEVELOPMENT_ISLANDS
            else ("BLIND TEST" if island.index == BLIND_ISLAND else "EXCLUDED")
        )
        lines.append(
            f"  island {island.index}  [{island.start},{island.end})  {len(island.clips):>2} clips"
            f"  {len(island.hard_cuts):>2} hard cuts   {role}"
        )
    lines.append("")

    lines.append(
        f"{'cut':>7} {'isl':>3} {'k':>3} {'manual state':<12} {'held':>5} {'atX3':>5} "
        f"{'cycle':>6} {'cuts':>5} {'reset':>6}  semantic"
    )
    header_len = len(lines[-1])
    lines.append("-" * header_len)
    counts = {"SEMANTIC_RESET": 0, "RHYTHM_REFRESH_RESET": 0, "LOOP_RESET": 0}
    for index in DEVELOPMENT_ISLANDS:
        island = dataset.island(index)
        reset_cuts = set(dataset.manual_reset_cuts(island))
        for position, cut in enumerate(island.hard_cuts):
            rhythm = dataset.manual_rhythm(island, cut)
            annotation = annotations[cut]
            is_reset = cut in reset_cuts
            if is_reset:
                if cut == island.hard_cuts[-1]:
                    kind = "LOOP_RESET"
                elif annotation.predicts_reset:
                    kind = "SEMANTIC_RESET"
                else:
                    kind = "RHYTHM_REFRESH_RESET"
                counts[kind] += 1
            lines.append(
                f"{cut:>7} {index:>3} {position:>3} {rhythm.state:<12} "
                f"{rhythm.held:>5} {str(rhythm.at_x3):>5} {str(rhythm.cycle):>6} "
                f"{rhythm.cuts_in_cycle:>5} {('yes' if is_reset else '-'):>6}  "
                f"{annotation.semantic_class}/{annotation.subtype}"
            )
    lines += ["", "manual reset taxonomy on the development set:"]
    for kind, count in counts.items():
        lines.append(f"  {kind:<24s} {count}")
    lines.append(f"  {'off-cut resets':<24s} " + str(
        sum(len(dataset.off_cut_resets(dataset.island(i))) for i in DEVELOPMENT_ISLANDS)
    ))
    return "\n".join(lines)


# ------------------------------------------------------------------------------ blind stage
def blind_predictions(dataset: Dataset) -> BlindPredictions:
    island = dataset.island(BLIND_ISLAND)
    traces = run(dataset, BLIND_ISLAND, BLIND_SHORT_3)
    annotations = {a.frame: a for a in BLIND_SHORT_3}
    rows: list[CutPrediction] = []
    for position, cut in enumerate(island.hard_cuts):
        annotation = annotations[cut]
        rows.append(
            CutPrediction(
                island_index=island.index,
                cut_index=position,
                frame=cut,
                semantic_class=annotation.semantic_class,
                semantic_subtype=annotation.subtype,
                confidence=annotation.confidence,
                state={
                    name: (context.state if (context := trace.context_at(cut)) else "?")
                    for name, trace in traces.items()
                },
                predicted={
                    name: trace.reset_reasons.get(cut, "") for name, trace in traces.items()
                },
            )
        )
    return BlindPredictions(
        timeline=str(dataset.reference["timeline"]),
        island_index=island.index,
        island_start=island.start,
        island_end=island.end,
        hard_cuts=island.hard_cuts,
        rows=tuple(rows),
        reset_frames={name: trace.reset_frames for name, trace in traces.items()},
        entry_frames={name: trace.entry_frames for name, trace in traces.items()},
    )


def blind_report(predictions: BlindPredictions) -> str:
    lines = [
        f"BLIND PREDICTIONS — island {predictions.island_index} "
        f"[{predictions.island_start},{predictions.island_end}) of `{predictions.timeline}`",
        f"hard cuts: {len(predictions.hard_cuts)}",
        "",
        f"{'cut':>7} {'k':>3} {'semantic':<38} {'conf':<7} "
        + " ".join(f"{name:<10}" for name in CANDIDATES),
    ]
    lines.append("-" * len(lines[-1]))
    for row in predictions.rows:
        semantic = f"{row.semantic_class}/{row.semantic_subtype}"
        cells = []
        for name in CANDIDATES:
            reason = row.predicted[name]
            cells.append(f"{(reason.split('_')[0] if reason else '-'):<10}")
        lines.append(
            f"{row.frame:>7} {row.cut_index:>3} {semantic:<38} {row.confidence:<7} "
            + " ".join(cells)
        )
    lines += ["", "simulated state at each cut, per candidate:"]
    lines.append(f"{'cut':>7} " + " ".join(f"{name:<10}" for name in CANDIDATES))
    for row in predictions.rows:
        lines.append(
            f"{row.frame:>7} " + " ".join(f"{row.state[name]:<10}" for name in CANDIDATES)
        )
    lines += ["", "predicted resets:"]
    for name in CANDIDATES:
        frames = predictions.reset_frames[name]
        lines.append(f"  {name:<9} {len(frames):>2}  {list(frames)}")
    lines += ["", f"predicted FACE_X1 re-entries (reset + {REENTRY_GAP_FRAMES} frames):"]
    for name in CANDIDATES:
        frames = predictions.entry_frames[name]
        lines.append(f"  {name:<9} {len(frames):>2}  {list(frames)}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------ unblind stage
def unblind_report(dataset: Dataset, labels: dict[str, Any], categories: dict[int, str]) -> str:
    """Score the frozen predictions against the manual Short 3. Measurement only, no tuning."""

    island = dataset.island(BLIND_ISLAND)
    labelled = Dataset(labels, {"envelope": [], "speech_ranges": []})
    manual_island = next(
        i for i in labelled.islands if i.start == island.start and i.end == island.end
    )
    actual = labelled.manual_reset_cuts(manual_island)
    off_cut = labelled.off_cut_resets(manual_island)
    traces = run(dataset, BLIND_ISLAND, BLIND_SHORT_3)

    lines = [
        f"manual reset cuts   {len(actual)}  {list(actual)}",
        f"off-cut resets      {len(off_cut)}  {list(off_cut)}",
        "",
        table([_score(name, island, traces[name], actual) for name in CANDIDATES]),
        "",
        "category recall (strict, all manual resets on cuts):",
    ]
    for name in CANDIDATES:
        hits = recall_by_category(categories, traces[name].reset_frames)
        rendered = "  ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(hits.items()))
        lines.append(f"  {name:<9} {rendered}")
    lines += ["", "reset frame deltas (predicted -> nearest manual reset):"]
    manual_all = sorted({*actual, *off_cut})
    for name in CANDIDATES:
        deltas = frame_deltas(traces[name].reset_frames, manual_all)
        rendered = ", ".join(f"{d.predicted}{'' if d.delta is None else f'({d.delta:+d})'}"
                             for d in deltas)
        lines.append(f"  {name:<9} {rendered}")
    lines += ["", "re-entry deltas (predicted FACE_X1 -> nearest manual FACE_X1):"]
    manual_entries = sorted(e for e in labelled.manual.entries if manual_island.contains(e))
    for name in CANDIDATES:
        deltas = frame_deltas(traces[name].entry_frames, manual_entries)
        values = [abs(d.delta) for d in deltas if d.delta is not None]
        summary = (
            f"median|d| {statistics.median(values):.1f}  max {max(values)}" if values else "-"
        )
        lines.append(f"  {name:<9} {summary}")
    return "\n".join(lines)


def _load(path: str) -> dict[str, Any]:
    return dict(json.loads(Path(path).read_text(encoding="utf-8")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("dev", "blind", "unblind"))
    parser.add_argument("--reference", required=True, help="export of `Timeline 1 copy`")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--labels", default=None, help="export of `Timeline 1` (unblind only)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    dataset = Dataset(_load(args.reference), _load(args.audio))
    if args.stage == "dev":
        print(dev_report(dataset))
        return
    if args.stage == "blind":
        predictions = blind_predictions(dataset)
        print(blind_report(predictions))
        if args.out:
            Path(args.out).write_text(predictions.to_json(), encoding="utf-8")
        return

    if args.labels is None:
        raise SystemExit("unblind needs --labels, the export of `Timeline 1`")
    labels = _load(args.labels)
    island = dataset.island(BLIND_ISLAND)
    labelled = Dataset(labels, {"envelope": [], "speech_ranges": []})
    manual_island = next(
        i for i in labelled.islands if i.start == island.start and i.end == island.end
    )
    # Every manual reset starts as SEMANTIC unless the loop rule owns it; the report refines
    # this taxonomy by hand once the failures have been read.
    categories = {
        cut: ("LOOP_RESET" if cut == manual_island.hard_cuts[-1] else "SEMANTIC_RESET")
        for cut in labelled.manual_reset_cuts(manual_island)
    }
    print(unblind_report(dataset, labels, categories))


if __name__ == "__main__":
    main()
