"""Phase 11c driver: every table in `.agent/reports/phase-11c-semantic-rhythm-study.txt`.

    PYTHONPATH=src:. python -m tools.research.phase11c.study \
        --reference .../reference.json --audio .../audio.json [--transcript .../transcript.json]

`--reference` is the export of `Timeline 1` restricted to islands 0-2 (the fourth island is
outside the labelled range and is never read). `--transcript` is optional: without it the word
anchor section is skipped and everything else still runs.

Only frames, categories and counters are printed. No sentence the creator spoke appears here
or in any file this module writes.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.transitions import (
    BY_ROLE,
    ROLE_X0_TO_FACE_X1,
    STATE_FACE_X1,
    STATE_X0,
    promotion_from,
)
from tools.research.phase11a.ablation import evaluate
from tools.research.phase11a.structure import ContentIsland
from tools.research.phase11a.words import AlignedWord, word_from_asr, words_in_island
from tools.research.phase11b.annotations import BLIND_SHORT_3, DEVELOPMENT
from tools.research.phase11b.policy import REENTRY_GAP_FRAMES, fixed_gap_reentry, semantic_policy
from tools.research.phase11b.simulate import (
    TRANSITION_FRAMES,
    ZOOM_CUT_SNAP_FRAMES,
    simulate_island,
)
from tools.research.phase11b.study import Dataset
from tools.research.phase11c import headroom, semantics_v2
from tools.research.phase11c.creator_feedback import (
    CREATOR_ANSWERS,
    RHYTHM_REFRESH_RESET,
    VISUAL_PRESENTATION_RESET,
    reclassified,
)
from tools.research.phase11c.taxonomy import ResetRow, counts, taxonomy

#: The rhythm gate this phase proposes, and the only number in it. See section 4.
MIN_HEADROOM_GAIN = 1


def choose_threshold(positives: Sequence[int], negatives: Sequence[int]) -> int | None:
    """The smallest N for which `gain >= N` fires on some positive and on NO negative.

    This is the whole "training" step of the numeric rhythm gate, and it is deliberately this
    small: a leave-one-Short-out run that re-derives an integer cannot overfit much, and if the
    integer moves between folds that is itself the finding.
    """

    for candidate in range(-2, 5):
        if all(value < candidate for value in negatives) and any(
            value >= candidate for value in positives
        ):
            return candidate
    return None


def _v1_boundaries() -> frozenset[int]:
    return frozenset(a.frame for a in (*DEVELOPMENT, *BLIND_SHORT_3) if a.predicts_reset)


def _load(path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


# ---------------------------------------------------------------------------- 1. taxonomy
def taxonomy_section(dataset: Dataset) -> tuple[str, tuple[ResetRow, ...]]:
    rows = taxonomy(
        dataset.manual.resets,
        dataset.islands,
        boundary_cuts=_v1_boundaries(),
        creator_answers=CREATOR_ANSWERS,
    )
    lines = ["1. CREATOR-GROUNDED TAXONOMY — all three Shorts", ""]
    for frame, (old, new) in sorted(reclassified().items()):
        lines.append(f"   D071 reclassified {frame}: {old} -> {new}")
    lines += [
        "",
        f"{'frame':>7} {'isl':>3} {'from':<8} {'cut':>4} {'dcut':>5} {'nextX1':>7} "
        f"{'anchor':>7} {'e-a':>4}  {'reason':<26} source",
    ]
    for row in rows:
        entry_delta = "-" if row.entry_to_anchor is None else str(row.entry_to_anchor)
        lines.append(
            f"{row.frame:>7} {row.island_index:>3} {row.from_state:<8} "
            f"{'yes' if row.on_cut else 'no':>4} {row.nearest_cut_delta:>5} "
            f"{row.next_x1 if row.next_x1 is not None else '-':>7} "
            f"{row.anchor_cut if row.anchor_cut is not None else '-':>7} {entry_delta:>4}  "
            f"{row.reason:<26} {row.source}"
        )
    lines += ["", f"   totals   {counts(rows)}"]
    for island in dataset.islands:
        island_rows = [r for r in rows if r.island_index == island.index]
        lines.append(f"   island {island.index} {counts(island_rows)}")
    return "\n".join(lines), rows


# ---------------------------------------------------------------------------- 2. semantics
def semantics_section(dataset: Dataset, rows: Sequence[ResetRow]) -> str:
    v1 = _v1_boundaries()
    v2 = semantics_v2.boundary_frames(v1)
    v2x = semantics_v2.boundary_frames(v1, extended=True)
    cuts = [cut for island in dataset.islands for cut in island.hard_cuts]
    last_cuts = {island.last_hard_cut for island in dataset.islands if island.last_hard_cut}
    anchored = {r.anchor_cut: r for r in rows if r.anchor_cut is not None}
    unreachable = [r.frame for r in rows if r.anchor_cut is None]

    lines = [
        "2. SEMANTIC RUBRIC V2 — concession and contrast",
        "",
        "   every concessive / adversative / reformulation marker in the three Shorts:",
        f"{'frame':>7} {'isl':>3} {'cut':>7} {'d':>4} {'initial':>8} {'reset':>7}  note",
    ]
    for marker in semantics_v2.MARKER_OCCURRENCES:
        lines.append(
            f"{marker.frame:>7} {marker.island_index:>3} {marker.cut:>7} "
            f"{marker.frames_after_cut:>4} {'yes' if marker.clause_initial else 'no':>8} "
            f"{marker.reset if marker.reset is not None else '-':>7}  {marker.note}"
        )
    lines += ["", f"   {semantics_v2.marker_summary()}", ""]
    lines += [
        "   decision universe: a cut is positive when a manual reset is ANCHORED to it, which",
        "   includes the resets placed early so that their FACE_X1 lands on the cut.",
        f"   {len(cuts)} hard cuts, {len(anchored)} anchored resets, "
        f"{len(unreachable)} reset(s) with no anchoring cut at all: {unreachable}",
        "",
        f"{'scope':<12} {'candidate':<38} {'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3} "
        f"{'prec':>6} {'rec':>6} {'F1':>6}",
    ]
    strict = sorted(anchored)
    addressable = sorted(
        frame for frame, row in anchored.items() if row.reason != VISUAL_PRESENTATION_RESET
    )
    variants = (("V1 frozen", v1), ("V2 +concession", v2), ("V2X +reformulation", v2x))
    for name, boundaries in variants:
        for loop in (False, True):
            fires = {c for c in cuts if c in boundaries} | (last_cuts if loop else set())
            predicted = sorted(fires)
            label = f"{name}{' + loop' if loop else ''}"
            for scope, actual in (("strict", strict), ("addressable", addressable)):
                result = evaluate(label, cuts, predicted, actual)
                confusion = result.confusion
                lines.append(
                    f"{scope:<12} {label:<38} {confusion.true_positives:>3} "
                    f"{confusion.false_positives:>3} {confusion.false_negatives:>3} "
                    f"{confusion.true_negatives:>3} {confusion.precision:>6.3f} "
                    f"{confusion.recall:>6.3f} {confusion.f1:>6.3f}"
                )
    predicted = sorted({c for c in cuts if c in v2} | last_cuts)
    result = evaluate("V2 + loop", cuts, predicted, strict)
    lines += [
        "",
        f"   V2 + loop false positives: {result.false_positive_frames}",
        f"   V2 + loop false negatives: {result.false_negative_frames}",
        "   ALL THREE SHORTS ARE DEVELOPMENT DATA NOW. These numbers are a fit, not a score.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- 3. rhythm
def _horizon(island: ContentIsland, frame: int, boundaries: frozenset[int]) -> int:
    later = [cut for cut in island.hard_cuts if cut > frame and cut in boundaries]
    return later[0] if later else island.end


def _features(
    dataset: Dataset, island: ContentIsland, frame: int, boundaries: frozenset[int]
) -> headroom.ZoomHeadroomFeatures | None:
    rhythm = dataset.manual_rhythm(island, frame)
    if rhythm.state == STATE_X0:
        return None
    return headroom.features(
        frame,
        rhythm.state,
        recoveries=dataset.recoveries[island.index],
        horizon=_horizon(island, frame, boundaries),
        speech_ranges=dataset.speech_ranges,
        time_since_last_reset=None,
        time_since_last_x1=rhythm.cycle,
        time_in_current_state=rhythm.held,
        reentry_gap=REENTRY_GAP_FRAMES,
    )


def _controls(dataset: Dataset, rows: Sequence[ResetRow], boundaries: frozenset[int]) -> list[
    tuple[ContentIsland, int]
]:
    """No-reset hard cuts where a reset was legal and the discourse was still running."""

    reset_frames = {row.frame for row in rows}
    found: list[tuple[ContentIsland, int]] = []
    for island in dataset.islands:
        for cut in island.hard_cuts:
            if cut in reset_frames or cut in boundaries:
                continue
            if dataset.manual_rhythm(island, cut).state == STATE_X0:
                continue
            found.append((island, cut))
    return found


def rhythm_section(dataset: Dataset, rows: Sequence[ResetRow]) -> str:
    boundaries = semantics_v2.boundary_frames(_v1_boundaries())
    lines = [
        "3. RHYTHM RESETS AGAINST NO-RESET CONTROLS — prospective ladder headroom",
        "   ORACLE STATE: the current state is read off the creator's own zoom track.",
        "",
        f"{'frame':>7} {'isl':>3} {'kind':<8} {'state':<8} {'held':>5} {'cycle':>5} "
        f"{'horizon':>8} {'span':>5} {'recov':>5} {'pKEEP':>5} {'pRESET':>6} {'gain':>5} "
        f"{'satK':>5} {'satR':>5}",
    ]

    def row_line(island: ContentIsland, frame: int, kind: str) -> str:
        measured = _features(dataset, island, frame, boundaries)
        assert measured is not None
        return (
            f"{frame:>7} {island.index:>3} {kind:<8} {measured.current_state:<8} "
            f"{measured.time_in_current_state:>5} "
            f"{measured.time_since_last_x1 if measured.time_since_last_x1 is not None else -1:>5} "
            f"{measured.horizon:>8} {measured.future_discourse_duration:>5} "
            f"{measured.future_qualifying_recoveries:>5} "
            f"{measured.promotions_available_without_reset:>5} "
            f"{measured.promotions_available_after_reset:>6} {measured.headroom_gain:>5} "
            f"{measured.keep.frames_saturated_at_x3:>5} {measured.reset.frames_saturated_at_x3:>5}"
        )

    by_index = {island.index: island for island in dataset.islands}
    rhythm_rows = [row for row in rows if row.reason == RHYTHM_REFRESH_RESET]
    for row in rhythm_rows:
        lines.append(row_line(by_index[row.island_index], row.frame, "RHYTHM"))
    controls = _controls(dataset, rows, boundaries)
    for island, cut in controls:
        lines.append(row_line(island, cut, "CONTROL"))

    positives = [
        (row.island_index, row.frame, _features(dataset, by_index[row.island_index], row.frame,
                                                boundaries))
        for row in rhythm_rows
    ]
    negatives = [(island.index, cut, _features(dataset, island, cut, boundaries))
                 for island, cut in controls]
    fired = [
        (f, m) for _, f, m in positives if m is not None and m.headroom_gain >= MIN_HEADROOM_GAIN
    ]
    false_fire = [
        (f, m) for _, f, m in negatives if m is not None and m.headroom_gain >= MIN_HEADROOM_GAIN
    ]
    lines += [
        "",
        f"   gate `headroom_gain >= {MIN_HEADROOM_GAIN}`: "
        f"{len(fired)}/{len(positives)} rhythm resets, {len(false_fire)}/{len(negatives)} controls",
        f"   fired at {[f for f, _ in fired]}; missed "
        f"{[f for _, f, m in positives if m is not None and m.headroom_gain < MIN_HEADROOM_GAIN]}",
        "",
        "   LEAVE-ONE-SHORT-OUT on the threshold N of `headroom_gain >= N`:",
    ]
    for held in sorted(by_index):
        train_neg = [m.headroom_gain for i, _, m in negatives if i != held and m is not None]
        train_pos = [m.headroom_gain for i, _, m in positives if i != held and m is not None]
        chosen = choose_threshold(train_pos, train_neg)
        test_pos = [m.headroom_gain for i, _, m in positives if i == held and m is not None]
        test_neg = [m.headroom_gain for i, _, m in negatives if i == held and m is not None]
        if chosen is None:
            lines.append(f"   held-out Short {held}: no threshold separates the training Shorts")
            continue
        lines.append(
            f"   train on the other two -> N={chosen};  held-out Short {held}: "
            f"TP {sum(1 for g in test_pos if g >= chosen)}/{len(test_pos)}  "
            f"FP {sum(1 for g in test_neg if g >= chosen)}/{len(test_neg)}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------- 4. gate windows
def window_section(dataset: Dataset, rows: Sequence[ResetRow]) -> str:
    boundaries = semantics_v2.boundary_frames(_v1_boundaries())
    lines = [
        "4. WHERE THE GATE IS OPEN AT ALL — every frame of every island, ORACLE STATE",
        "",
        f"{'isl':>3} {'window':>17} {'len':>5} {'nextReset':>10} {'d(end)':>7}  reason",
    ]
    total = followed = 0
    for island in dataset.islands:
        open_frames = [
            frame
            for frame in range(island.start, island.end - 1)
            if (m := _features(dataset, island, frame, boundaries)) is not None
            and m.headroom_gain >= MIN_HEADROOM_GAIN
        ]
        runs: list[list[int]] = []
        for frame in open_frames:
            if runs and frame == runs[-1][1] + 1:
                runs[-1][1] = frame
            else:
                runs.append([frame, frame])
        for start, end in runs:
            total += 1
            following = next(
                (r for r in rows if r.island_index == island.index and r.frame >= start), None
            )
            followed += following is not None
            lines.append(
                f"{island.index:>3} [{start},{end}]{'':>3} {end - start + 1:>5} "
                f"{following.frame if following else -1:>10} "
                f"{(following.frame - end) if following else -1:>7}  "
                f"{following.reason if following else 'NONE'}"
            )
    lines += [
        "",
        f"   {followed}/{total} open windows are followed by a manual reset.",
        "   The window END is an artefact: the oracle state changes at the reset, so the gate",
        "   necessarily closes there. Only the window START and the count carry information.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- 5. simulation
@dataclass(frozen=True, slots=True)
class CycleReplay:
    """One creator cycle replayed by the promotion engine with the boundaries handed to it."""

    island_index: int
    entry: int
    end: int
    manual: tuple[int, ...]
    simulated: tuple[int, ...]
    recoveries_available: int


def _snap(anchor: int, cuts: Sequence[int]) -> int:
    candidates = [c for c in cuts if abs(c - anchor) <= ZOOM_CUT_SNAP_FRAMES]
    return min(candidates, key=lambda c: (abs(c - anchor), -c)) if candidates else anchor


def oracle_cycles(dataset: Dataset) -> list[CycleReplay]:
    """Replay each manual cycle with its own boundaries: does the audio engine agree inside?"""

    replays: list[CycleReplay] = []
    for island in dataset.islands:
        placements = [p for p in dataset.manual.placements if island.contains(p.start)]
        recoveries = dataset.recoveries[island.index]
        for index, placement in enumerate(placements):
            if placement.role != ROLE_X0_TO_FACE_X1:
                continue
            rest = placements[index + 1 :]
            end = next((q.start for q in rest if BY_ROLE[q.role].is_reset), island.end)
            manual = tuple(q.start for q in rest if q.start < end and not BY_ROLE[q.role].is_reset)
            state, last = STATE_FACE_X1, placement.start
            simulated: list[int] = []
            for recovery in recoveries:
                if recovery < placement.start or recovery >= end:
                    continue
                if recovery - last < TRANSITION_FRAMES:
                    continue
                step = promotion_from(state)
                if step is None:
                    break
                frame = _snap(recovery, island.hard_cuts)
                if frame - last < TRANSITION_FRAMES:
                    frame = recovery
                simulated.append(frame)
                state, last = step.to_state, frame
            replays.append(
                CycleReplay(
                    island_index=island.index,
                    entry=placement.start,
                    end=end,
                    manual=manual,
                    simulated=tuple(simulated),
                    recoveries_available=sum(1 for r in recoveries if placement.start <= r < end),
                )
            )
    return replays


def simulation_section(dataset: Dataset) -> str:
    replays = oracle_cycles(dataset)
    same = [r for r in replays if len(r.manual) == len(r.simulated)]
    over = [r for r in replays if len(r.simulated) > len(r.manual)]
    under = [r for r in replays if len(r.simulated) < len(r.manual)]
    starved = [r for r in replays if r.manual and r.recoveries_available == 0]
    deltas = sorted(
        abs(s - m) for r in same for m, s in zip(r.manual, r.simulated, strict=True)
    )
    lines = [
        "5. STATE RECONSTRUCTION — where the Phase 11b simulator actually loses",
        "",
        "5.1 ORACLE CYCLES: the creator's own resets and entries are handed to the engine, so",
        "    the ONLY thing left to get wrong is which recovery becomes which promotion.",
        f"    cycles                                  {len(replays)}",
        f"    manual promotions                       {sum(len(r.manual) for r in replays)}",
        f"    simulated promotions                    {sum(len(r.simulated) for r in replays)}",
        f"    cycles with the same promotion COUNT    {len(same)}",
        f"    cycles the engine over-promotes         {len(over)}",
        f"    cycles the engine under-promotes        {len(under)}",
        f"    cycles where the creator promoted with NO qualifying recovery at all: {len(starved)}",
    ]
    if deltas:
        lines.append(
            f"    frame error on the {len(deltas)} count-matched promotions: "
            f"median {statistics.median(deltas):.1f}  p90 "
            f"{deltas[int(0.9 * (len(deltas) - 1))]}  max {deltas[-1]}"
        )
    lines += [
        "",
        "5.2 LABEL-FREE: the Phase 11b simulator run with the P0 policy, state compared against",
        "    the creator's own at every hard cut.",
    ]
    annotations = [*DEVELOPMENT, *BLIND_SHORT_3]
    exact = coarse = total = 0
    mismatches: list[str] = []
    for island in dataset.islands:
        trace = simulate_island(
            island,
            dataset.recoveries[island.index],
            reset_policy=semantic_policy(
                [a for a in annotations if a.island_index == island.index]
            ),
            reentry_policy=fixed_gap_reentry(),
        )
        for context in trace.contexts:
            manual_state = dataset.manual_state_at(island, context.frame)
            total += 1
            exact += context.state == manual_state
            coarse += (context.state == STATE_X0) == (manual_state == STATE_X0)
            if context.state != manual_state:
                mismatches.append(
                    f"    island {island.index} cut {context.frame}  manual {manual_state:<8} "
                    f"simulated {context.state}"
                )
    lines += [
        f"    exact state agreement                   {exact}/{total}",
        f"    coarse (face versus X0)                 {coarse}/{total}",
        "",
        *mismatches,
        "",
        "    ROOT CAUSE: 5.1 shows the promotion engine already disagrees with the creator when",
        "    the cycle boundaries are correct. Fixing the reset history cannot repair the state.",
        "    No promotion threshold was changed (task section 14).",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- 6. anchors
def _words(transcript: dict[str, Any]) -> list[AlignedWord]:
    timebase = Timebase.from_timeline("60", 216000)
    return [
        word_from_asr(word["word"], word.get("start"), word.get("end"), timebase)
        for segment in transcript["whisperx"]["segments"]
        for word in segment["words"]
    ]


def _nearest(candidates: Sequence[int], frame: int) -> int | None:
    if not candidates:
        return None
    return min(candidates, key=lambda c: (abs(c - frame), c)) - frame


def anchor_section(
    dataset: Dataset, rows: Sequence[ResetRow], transcript: dict[str, Any] | None
) -> str:
    lines = ["6. PLACEMENT — what the off-cut resets and the re-entries are anchored to", ""]
    starts: dict[int, list[int]] = {}
    if transcript is not None:
        words = _words(transcript)
        for island in dataset.islands:
            starts[island.index] = sorted(
                w.start_frame for w in words_in_island(words, island) if w.start_frame is not None
            )
        chance: list[int] = []
        for island in dataset.islands:
            island_starts = starts[island.index]
            chance += [
                min(abs(s - f) for s in island_starts) for f in range(island.start, island.end)
            ]
        chance.sort()
        lines += [
            "   CHANCE BASELINE — |distance to the nearest word start| over every frame of the",
            f"   three islands: median {statistics.median(chance):.1f}  "
            f"mean {statistics.mean(chance):.2f}  p90 {chance[int(0.9 * len(chance))]}",
            "   Any anchor claim has to beat THIS, and the word-boundary ones below do not.",
            "",
        ]
    lines.append(
        f"{'frame':>7} {'reason':<26} {'cut':>6} {'wordStart':>10} {'recovery':>9}"
    )
    for row in rows:
        if row.on_cut:
            continue
        island_starts = starts.get(row.island_index, [])
        lines.append(
            f"{row.frame:>7} {row.reason:<26} {-row.nearest_cut_delta:>6} "
            f"{_nearest(island_starts, row.frame) if island_starts else '-':>10} "
            f"{_nearest(dataset.recoveries[row.island_index], row.frame):>9}"
        )

    lines += ["", "   RE-ENTRY: |predicted FACE_X1 - actual FACE_X1| over every entry"]
    candidates: dict[str, list[int]] = {}
    gaps_by_reason: dict[str, list[int]] = {}
    for row in rows:
        if row.next_x1 is None:
            continue
        island = dataset.islands[row.island_index]
        gaps_by_reason.setdefault(row.reason, []).append(row.next_x1 - row.frame)
        options: dict[str, int | None] = {
            "reset + 36 (frozen baseline)": row.frame + REENTRY_GAP_FRAMES,
            "first hard cut after reset+15": next(
                (c for c in island.hard_cuts if c >= row.frame + TRANSITION_FRAMES), None
            ),
            "first recovery after reset+15": next(
                (
                    r
                    for r in dataset.recoveries[row.island_index]
                    if r >= row.frame + TRANSITION_FRAMES
                ),
                None,
            ),
        }
        island_starts = starts.get(row.island_index, [])
        if island_starts:
            options["first word start after reset+30"] = next(
                (s for s in island_starts if s >= row.frame + 30), None
            )
        for name, value in options.items():
            if value is not None:
                candidates.setdefault(name, []).append(abs(value - row.next_x1))
    for name, errors in candidates.items():
        ordered = sorted(errors)
        lines.append(
            f"   {name:<32} n={len(ordered):>2} median {statistics.median(ordered):>5.1f} "
            f"p90 {ordered[int(0.9 * (len(ordered) - 1))]:>3} max {ordered[-1]:>3} "
            f"exact {sum(1 for e in ordered if e == 0)}"
        )
    lines += ["", "   anchor gap (reset -> next FACE_X1) by reset reason:"]
    for reason, gaps in sorted(gaps_by_reason.items()):
        lines.append(
            f"   {reason:<26} n={len(gaps):>2} median {statistics.median(gaps):>5.1f} "
            f"min {min(gaps):>3} max {max(gaps):>3}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------- driver
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--transcript", type=Path, default=None)
    args = parser.parse_args()

    dataset = Dataset(_load(args.reference), _load(args.audio))
    transcript = _load(args.transcript) if args.transcript is not None else None

    taxonomy_text, rows = taxonomy_section(dataset)
    print(taxonomy_text)
    print()
    print(semantics_section(dataset, rows))
    print()
    print(rhythm_section(dataset, rows))
    print()
    print(window_section(dataset, rows))
    print()
    print(simulation_section(dataset))
    print()
    print(anchor_section(dataset, rows, transcript))


if __name__ == "__main__":
    main()
