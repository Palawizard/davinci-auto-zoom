"""The Phase 11a analysis: assemble the cut dataset, measure it, and run the ablation.

Inputs are the three local caches produced by the other scripts (`reference.json`,
`audio.json`, `transcript.json`). Only `transcript.json` contains the creator's words, and
nothing derived from it that reaches stdout carries a sentence — the printed report names cuts
by island, index and absolute frame, exactly as the task requires.

    PYTHONPATH=src:. python -m tools.research.phase11a.study \
        --reference .../reference.json --audio .../audio.json \
        --transcript .../transcript.json --semantic .../semantic.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.dynamics import EnergyPoint
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.domain.transitions import STATE_X0
from tools.research.phase11a.ablation import Evaluation, evaluate, table
from tools.research.phase11a.acoustic import cut_audio
from tools.research.phase11a.dataset import (
    AMBIGUOUS,
    CutResearchExample,
    label_from_resets,
)
from tools.research.phase11a.lexical import find_markers, punctuation_evidence
from tools.research.phase11a.manual import (
    RESET_REASON_SHORT_LOOP,
    ManualPlacement,
    ManualReset,
    nearest_cut,
    reconstruct,
)
from tools.research.phase11a.structure import Clip, ContentIsland, clip_at, content_islands
from tools.research.phase11a.words import (
    ALIGNED,
    AlignedWord,
    alignment_ratios,
    cut_context,
    word_from_asr,
    words_in_island,
)

#: Words of context kept each side of a cut. A study parameter, not a future constant.
CONTEXT_WORDS = 8
#: Half-width of the acoustic window around a cut: 0.5 s at 60 fps.
AUDIO_WINDOW_FRAMES = 30
ROLE_ENTRY = "x0_to_face_x1"
#: Depth a dip must reach to count as a valley. Borrowed unchanged from the product's
#: validated `promotion_min_drop_db`, so the acoustic family is not tuned to this dataset.
VALLEY_DROP_DB = 20.0


@dataclass(frozen=True, slots=True)
class Inputs:
    reference: dict[str, Any]
    audio: dict[str, Any]
    transcript: dict[str, Any]
    semantic: dict[str, str]


def load(paths: argparse.Namespace) -> Inputs:
    semantic: dict[str, str] = {}
    if paths.semantic is not None and Path(paths.semantic).exists():
        semantic = json.loads(Path(paths.semantic).read_text(encoding="utf-8"))
    return Inputs(
        reference=json.loads(Path(paths.reference).read_text(encoding="utf-8")),
        audio=json.loads(Path(paths.audio).read_text(encoding="utf-8")),
        transcript=json.loads(Path(paths.transcript).read_text(encoding="utf-8")),
        semantic=semantic,
    )


def rebuild(inputs: Inputs) -> tuple[tuple[ContentIsland, ...], tuple[ManualReset, ...], Timebase]:
    reference = inputs.reference
    clips = [
        Clip(start, end)
        for island in reference["islands"]
        for start, end in island["clips"]
    ]
    islands = content_islands(clips, min_gap_frames=30)
    manual = reconstruct(
        [ManualPlacement(**placement) for placement in reference["placements"]],
        islands,
        transition_frames=15,
    )
    timebase = Timebase.from_timeline(reference["frame_rate"], reference["labelled"][0])
    return islands, manual.resets, timebase


def whisperx_words(inputs: Inputs, timebase: Timebase) -> list[AlignedWord]:
    return [
        word_from_asr(word["word"], word.get("start"), word.get("end"), timebase)
        for segment in inputs.transcript["whisperx"]["segments"]
        for word in segment["words"]
    ]


def native_words(inputs: Inputs, timebase: Timebase) -> list[AlignedWord]:
    return [
        word_from_asr(word["word"], word.get("start"), word.get("end"), timebase)
        for segment in inputs.transcript["faster_whisper_native"]["segments"]
        for word in segment["words"]
    ]


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
        return ordered[index]

    return {
        "min": ordered[0],
        "p50": statistics.median(ordered),
        "p90": at(0.90),
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
    }


def compare_timings(
    aligned: list[AlignedWord], native: list[AlignedWord]
) -> tuple[dict[str, dict[str, float]], int]:
    """Pair the two word streams by their token sequence, and report the timing deltas.

    `difflib.SequenceMatcher` rather than a greedy scan: the two passes tokenise slightly
    differently (elisions, hyphens, numbers), and a greedy walk desynchronises permanently at
    the first difference. Only tokens inside an `equal` block are compared, so a token is
    never paired with a neighbour it is not.
    """

    starts: list[float] = []
    ends: list[float] = []
    left = [w for w in aligned if w.is_aligned and w.normalized_text]
    right = [w for w in native if w.is_aligned and w.normalized_text]
    matcher = SequenceMatcher(
        None, [w.normalized_text for w in left], [w.normalized_text for w in right], autojunk=False
    )
    matched = 0
    for tag, i1, i2, j1, _ in matcher.get_opcodes():
        if tag != "equal":
            continue
        for offset in range(i2 - i1):
            word, other = left[i1 + offset], right[j1 + offset]
            matched += 1
            assert word.start_seconds is not None and other.start_seconds is not None
            assert word.end_seconds is not None and other.end_seconds is not None
            starts.append(abs(word.start_seconds - other.start_seconds) * 1000.0)
            ends.append(abs(word.end_seconds - other.end_seconds) * 1000.0)
    return {"start_ms": _percentiles(starts), "end_ms": _percentiles(ends)}, matched


def word_shape(words: list[AlignedWord], timebase: Timebase) -> dict[str, Any]:
    """Durations, gaps and the degenerate cases §14A asks to count."""

    durations: list[float] = []
    gaps: list[float] = []
    zero_or_negative = 0
    overlaps = 0
    previous: AlignedWord | None = None
    for word in words:
        if not word.is_aligned:
            continue
        assert word.start_seconds is not None and word.end_seconds is not None
        duration = (word.end_seconds - word.start_seconds) * 1000.0
        if duration <= 0:
            zero_or_negative += 1
        durations.append(duration)
        if previous is not None and previous.end_seconds is not None:
            gap = (word.start_seconds - previous.end_seconds) * 1000.0
            if gap < 0:
                overlaps += 1
            gaps.append(gap)
        previous = word
    return {
        "duration_ms": _percentiles(durations),
        "gap_ms": _percentiles(gaps),
        "zero_or_negative_durations": zero_or_negative,
        "overlaps": overlaps,
        "frame_ms": 1000.0 / float(timebase.frame_rate),
    }


def build_examples(
    islands: tuple[ContentIsland, ...],
    resets: tuple[ManualReset, ...],
    words: list[AlignedWord],
    envelope: list[EnergyPoint],
    speech_ranges: list[tuple[int, int]],
    semantic: dict[str, str],
) -> list[CutResearchExample]:
    """One record per hard cut, with every context strictly scoped to its own island."""

    examples: list[CutResearchExample] = []
    for island in islands:
        scoped = words_in_island(words, island)
        for cut_index, cut in enumerate(island.hard_cuts):
            reset = label_from_resets(cut, resets, window_frames=0)
            context = cut_context(scoped, cut, count=CONTEXT_WORDS)
            previous_clip = clip_at(island, cut - 1)
            next_clip = clip_at(island, cut)
            assert previous_clip is not None and next_clip is not None
            examples.append(
                CutResearchExample(
                    frame=cut,
                    content_island=island.index,
                    cut_index=cut_index,
                    previous_clip=previous_clip,
                    next_clip=next_clip,
                    is_last_hard_cut_of_island=cut == island.last_hard_cut,
                    manual_reset_near_cut=reset is not None,
                    reset_role=reset.role if reset else None,
                    reset_frame=reset.start if reset else None,
                    reset_delta=reset.start - cut if reset else None,
                    is_loop_reset=bool(reset and reset.reason == RESET_REASON_SHORT_LOOP),
                    context=context,
                    markers_before=find_markers(context.before, side="before"),
                    markers_after=find_markers(context.after, side="after"),
                    punctuation=punctuation_evidence(context.before, context.after),
                    audio=cut_audio(
                        cut,
                        envelope,
                        speech_ranges,
                        window_frames=AUDIO_WINDOW_FRAMES,
                        bounds=(island.start, island.end),
                    ),
                    semantic_label=semantic.get(str(cut), AMBIGUOUS),
                )
            )
    return examples


def ablation(
    examples: list[CutResearchExample], states: dict[int, str] | None = None
) -> list[Evaluation]:
    """Families A-G. The universe is every labelled hard cut; nothing outside it is scored."""

    universe = [e.frame for e in examples]
    actual_all = [e.frame for e in examples if e.manual_reset_near_cut]
    semantic_only = [e.frame for e in examples if e.manual_reset_near_cut and not e.is_loop_reset]
    loop_cuts = [e.frame for e in examples if e.is_last_hard_cut_of_island]
    non_loop = [e for e in examples if not e.is_loop_reset]

    def score(name: str, predicted: list[int], *, semantics_only: bool) -> Evaluation:
        if semantics_only:
            # Evaluation A of §20: the loop resets are removed from BOTH sides, so a rule
            # cannot borrow credit from a decision the creator handed us as a rule.
            keep = [e.frame for e in non_loop]
            return evaluate(
                name,
                keep,
                [f for f in predicted if f in set(keep)],
                [f for f in semantic_only if f in set(keep)],
            )
        return evaluate(name, universe, predicted, actual_all)

    a = score("A  cut only (STRUCTURAL)", universe, semantics_only=True)
    # The VAD finds no pause at any labelled cut (the creator removed them all), so the
    # acoustic family has to be built on the *envelope* instead: a dip at least as deep as
    # the product's own `promotion_min_drop_db`, which is the one relative dB threshold this
    # project has already validated. Nothing is retuned to make the number look better.
    b = score(
        f"B  cut + energy valley >= {VALLEY_DROP_DB:.0f} dB (ACOUSTIC)",
        [
            e.frame
            for e in examples
            if e.audio.dip_db is not None and e.audio.dip_db >= VALLEY_DROP_DB
        ],
        semantics_only=True,
    )
    c = score(
        "C  cut + ASR sentence boundary (ASR PUNCTUATION)",
        [e.frame for e in examples if e.punctuation.boundary],
        semantics_only=True,
    )
    d = score(
        "D  cut + discourse marker in position (LEXICAL)",
        [e.frame for e in examples if e.has_boundary_marker],
        semantics_only=True,
    )
    e = score(
        "E  cut + lexical/punctuation + acoustic (LEX+PUNCT+ACOUSTIC)",
        [
            example.frame
            for example in examples
            if (example.has_boundary_marker or example.punctuation.boundary)
            and example.audio.dip_db is not None
            and example.audio.dip_db >= VALLEY_DROP_DB
        ],
        semantics_only=True,
    )
    f = score(
        "F  agent semantic continuity (AGENT SEMANTIC)",
        [e.frame for e in examples if e.semantic_predicts_reset],
        semantics_only=True,
    )
    g = evaluate(
        "G  F + deterministic loop override (AGENT SEMANTIC + USER RULE)",
        universe,
        sorted({e.frame for e in examples if e.semantic_predicts_reset} | set(loop_cuts)),
        actual_all,
    )
    families = [a, b, c, d, e, f, g]
    if states is not None:
        # NOT one of the requested families: an extra reading of F, kept separate. It asks
        # what F would score if it were only consulted where a reset is legal at all.
        families.append(
            score(
                "H  F, only where a face state is held (F + STRUCTURAL gate)",
                [
                    e.frame
                    for e in examples
                    if e.semantic_predicts_reset and states.get(e.frame, STATE_X0) != STATE_X0
                ],
                semantics_only=True,
            )
        )
    return families


def _dwell_block(resets: tuple[ManualReset, ...], frame_ms: float) -> str:
    semantic = [r for r in resets if r.reason != RESET_REASON_SHORT_LOOP]
    anchors = [r.anchor_gap for r in semantic if r.anchor_gap is not None]
    dwells = [r.pure_x0_dwell for r in semantic if r.pure_x0_dwell is not None]
    lines = [
        f"{'measure':<16s} {'min':>6s} {'median':>7s} {'max':>6s}   "
        f"{'min ms':>7s} {'med ms':>7s} {'max ms':>7s}"
    ]
    for name, values in (("anchor_gap", anchors), ("pure_x0_dwell", dwells)):
        if not values:
            continue
        low, mid, high = min(values), statistics.median(values), max(values)
        lines.append(
            f"{name:<16s} {low:>6d} {mid:>7.1f} {high:>6d}   "
            f"{low * frame_ms:>7.0f} {mid * frame_ms:>7.0f} {high * frame_ms:>7.0f}"
        )
    return "\n".join(lines)


def state_before_cut(inputs: Inputs, islands: tuple[ContentIsland, ...]) -> dict[int, str]:
    """The visual state the reference was in as each cut arrives.

    A reset is only *available* from a face state. Scoring a reset predictor on a cut where
    the picture was already at X0 asks it to make a move the graph forbids, so the state has
    to be part of the reading even though it is not part of any candidate rule.
    """

    placements = sorted(inputs.reference["placements"], key=lambda p: int(p["start"]))
    states: dict[int, str] = {}
    for island in islands:
        for cut in island.hard_cuts:
            state = STATE_X0
            for placement in placements:
                start = int(placement["start"])
                if not island.contains(start) or start >= cut:
                    continue
                state = str(placement["role"]).split("_to_", 1)[1]
            states[cut] = state
    return states


def transitions_at_cuts(inputs: Inputs, islands: tuple[ContentIsland, ...]) -> str:
    """What each cut actually carries — not only whether it carries a reset.

    Framing the question as "reset or nothing" hides the reference's strongest regularity:
    almost every cut receives *some* transition. A sentence boundary that got a promotion
    instead of a reset is not a counterexample to the semantic hypothesis, it is a cut where
    the state machine was somewhere else.
    """

    by_start = {p["start"]: p["role"] for p in inputs.reference["placements"]}
    cuts = [cut for island in islands for cut in island.hard_cuts]
    counts: dict[str, int] = {}
    for cut in cuts:
        counts[by_start.get(cut, "(nothing)")] = counts.get(by_start.get(cut, "(nothing)"), 0) + 1

    entries = [p["start"] for p in inputs.reference["placements"] if p["role"] == ROLE_ENTRY]
    promotions = [
        p["start"]
        for p in inputs.reference["placements"]
        if p["role"] in {"face_x1_to_face_x2", "face_x2_to_face_x3"}
    ]
    lines = [f"{role:<24s} {count:>3d}" for role, count in sorted(counts.items())]
    lines.append(f"{'TOTAL cuts':<24s} {len(cuts):>3d}")
    lines.append("")
    on_cut = sum(1 for start in entries if start in set(cuts))
    lines.append(f"entries on a cut         {on_cut} / {len(entries)}")
    lines.append(
        f"promotions on a cut      {sum(1 for s in promotions if s in set(cuts))} / "
        f"{len(promotions)}"
    )
    return "\n".join(lines)


def acoustic_plausibility(words: list[AlignedWord], envelope: list[EnergyPoint]) -> str:
    """Sanity-check the word boundaries against loudness. NOT a claim about linguistic truth.

    If the alignment is roughly right, frames the aligner calls "inside a word" must be
    louder than frames it calls "between words". A small or inverted difference would mean
    the boundaries are decorative. That is all this measures.
    """

    by_frame = {point.frame: point.db for point in envelope}
    inside: list[float] = []
    between: list[float] = []
    previous: AlignedWord | None = None
    for word in words:
        if word.start_frame is None or word.end_frame is None:
            continue
        inside.extend(by_frame[f] for f in range(word.start_frame, word.end_frame) if f in by_frame)
        if previous is not None and previous.end_frame is not None:
            between.extend(
                by_frame[f] for f in range(previous.end_frame, word.start_frame) if f in by_frame
            )
        previous = word
    if not inside or not between:
        return "not enough envelope coverage to check"
    loud, quiet = statistics.median(inside), statistics.median(between)
    return (
        f"median dB inside a word    {loud:7.2f}  ({len(inside)} frames)\n"
        f"median dB between words    {quiet:7.2f}  ({len(between)} frames)\n"
        f"separation                 {loud - quiet:7.2f} dB"
    )


def split_word_overhang(examples: list[CutResearchExample]) -> str:
    """How far a straddling word reaches past its cut, in frames.

    A few frames each side reads as alignment jitter or a genuinely tight splice. A consistent
    one-word overhang would instead mean the transcript sits systematically late against the
    picture, which would be a timing problem rather than an editorial finding.
    """

    overhangs = [
        example.context.split_word.end_frame - example.frame
        for example in examples
        if example.context.split_word is not None
        and example.context.split_word.end_frame is not None
    ]
    heads = [
        example.frame - example.context.split_word.start_frame
        for example in examples
        if example.context.split_word is not None
        and example.context.split_word.start_frame is not None
    ]
    if not overhangs:
        return "no cut falls inside an aligned word"
    stats_tail = _percentiles([float(v) for v in overhangs])
    stats_head = _percentiles([float(v) for v in heads])
    return (
        f"cuts inside an aligned word   {len(overhangs)} / {len(examples)}\n"
        f"frames of the word AFTER  cut  min {stats_tail['min']:.0f}  "
        f"p50 {stats_tail['p50']:.1f}  p90 {stats_tail['p90']:.0f}  max {stats_tail['max']:.0f}\n"
        f"frames of the word BEFORE cut  min {stats_head['min']:.0f}  "
        f"p50 {stats_head['p50']:.1f}  p90 {stats_head['p90']:.0f}  max {stats_head['max']:.0f}"
    )


def report(inputs: Inputs) -> str:
    islands, resets, timebase = rebuild(inputs)
    aligned = whisperx_words(inputs, timebase)
    native = native_words(inputs, timebase)
    envelope = [EnergyPoint(frame, db) for frame, db in inputs.audio["envelope"]]
    speech = [(a, b) for a, b in inputs.audio["speech_ranges"]]
    examples = build_examples(islands, resets, aligned, envelope, speech, inputs.semantic)
    frame_ms = 1000.0 / float(timebase.frame_rate)

    deltas, matched = compare_timings(aligned, native)
    shape = word_shape(aligned, timebase)
    ratios = alignment_ratios(aligned)

    out: list[str] = []
    out.append("=== DATASET (STRUCTURAL) ===")
    for island in islands:
        out.append(
            f"island {island.index}  [{island.start},{island.end})  "
            f"{len(island.clips)} clips  {len(island.hard_cuts)} hard cuts  "
            f"last cut {island.last_hard_cut}"
        )
    positives = [e for e in examples if e.manual_reset_near_cut]
    out += [
        f"hard cuts                {len(examples)}",
        f"reset-positive cuts      {len(positives)}",
        f"  of which loop resets   {sum(1 for e in positives if e.is_loop_reset)}",
        f"no-reset cuts            {len(examples) - len(positives)}",
        f"resets not on any cut    {len(resets) - len(positives)}",
        "",
        "=== WORD TIMING QUALITY ===",
        f"whisperx words           {len(aligned)}",
        f"native words             {len(native)}",
        f"aligned ratio            {ratios[ALIGNED]:.4f}",
        f"unaligned ratio          {1 - ratios[ALIGNED]:.4f}",
        f"zero/negative durations  {shape['zero_or_negative_durations']}",
        f"negative inter-word gaps {shape['overlaps']}",
        f"one frame                {frame_ms:.2f} ms",
        f"matched token pairs      {matched}",
    ]
    for key in ("start_ms", "end_ms"):
        stats = deltas[key]
        if stats:
            out.append(
                f"|whisperx-native| {key:<9s} p50 {stats['p50']:7.1f} ms  "
                f"p90 {stats['p90']:7.1f} ms  max {stats['max']:8.1f} ms   "
                f"(p50 {stats['p50'] / frame_ms:.2f} fr, p90 {stats['p90'] / frame_ms:.2f} fr)"
            )
    for key in ("duration_ms", "gap_ms"):
        stats = shape[key]
        if stats:
            out.append(
                f"word {key:<12s}      p50 {stats['p50']:7.1f} ms  "
                f"p90 {stats['p90']:7.1f} ms  max {stats['max']:8.1f} ms"
            )

    out += ["", "=== MANUAL RESET BEHAVIOUR ==="]
    all_cuts = tuple(cut for island in islands for cut in island.hard_cuts)
    for reset in resets:
        near = nearest_cut(all_cuts, reset.start)
        out.append(
            f"reset {reset.start}  {reset.role:<16s} island {reset.island_index}  "
            f"nearest cut {near[0] if near else '-'} delta {near[1] if near else '-':>4}  "
            f"{reset.reason}"
        )
    out += ["", "=== X0 DWELL ===", _dwell_block(resets, frame_ms)]
    out += [
        "",
        "=== WHAT EACH CUT CARRIES (STRUCTURAL) ===",
        transitions_at_cuts(inputs, islands),
        "",
        "=== ASR BEHAVIOUR AT EDIT BOUNDARIES ===",
        split_word_overhang(examples),
        "",
        "=== ACOUSTIC PLAUSIBILITY OF THE WORD BOUNDARIES ===",
        acoustic_plausibility(aligned, envelope),
    ]

    states_for_table = state_before_cut(inputs, islands)
    out += ["", "=== CUTS (no transcript text, by design) ==="]
    out.append(
        f"{'isl':>3s} {'idx':>3s} {'frame':>7s} {'reset':>5s} {'loop':>4s} "
        f"{'state':<8s} {'mark':>4s} {'punct':>5s} {'pause':>5s} {'dip dB':>6s} {'disc':>6s} "
        f"{'split':>5s}  semantic"
    )
    for example in examples:
        out.append(
            f"{example.content_island:>3d} {example.cut_index:>3d} {example.frame:>7d} "
            f"{'Y' if example.manual_reset_near_cut else '.':>5s} "
            f"{'Y' if example.is_loop_reset else '.':>4s} "
            f"{states_for_table.get(example.frame, '?'):<8s} "
            f"{'Y' if example.has_boundary_marker else '.':>4s} "
            f"{'Y' if example.punctuation.boundary else '.':>5s} "
            f"{example.audio.pause_frames:>5d} "
            f"{(example.audio.dip_db if example.audio.dip_db is not None else 0.0):>6.1f} "
            f"{(example.audio.discontinuity_db or 0.0):>6.1f} "
            f"{'Y' if example.cut_splits_a_word else '.':>5s}  {example.semantic_label}"
        )

    states = state_before_cut(inputs, islands)
    out += ["", "=== ABLATION A-G (+H, a separate reading) ===",
            table(ablation(examples, states))]
    out += ["", "=== ERRORS PER FAMILY ==="]
    for evaluation in ablation(examples, states):
        out.append(
            f"{evaluation.name}\n"
            f"    false positives {list(evaluation.false_positive_frames)}\n"
            f"    false negatives {list(evaluation.false_negative_frames)}"
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--semantic", default=None)
    args = parser.parse_args()
    print(report(load(args)))


if __name__ == "__main__":
    main()
