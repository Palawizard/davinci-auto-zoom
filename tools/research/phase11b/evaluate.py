"""Scoring for the blind trial: strict first, subset only when an exclusion is *proved*.

Three things are kept apart on purpose, because merging them is how a blind test quietly
flatters itself:

    the DECISION   did the candidate want a reset at this cut at all (precision/recall);
    the FRAME      given that it wanted one, did it land where the creator put it;
    the SUBSET     a reset the transcript cannot possibly explain (a purely visual one) may be
                   removed from the target — but only by an explicit, listed frame, never by
                   "the model missed it, so it must have been visual".

`tools.research.phase11a.ablation.evaluate` already computes the confusion over a universe of
hard cuts and is reused unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from tools.research.phase11a.ablation import Evaluation, evaluate

#: Manual reset categories reconstructed after unblinding.
SEMANTIC_RESET = "SEMANTIC_RESET"
RHYTHM_REFRESH_RESET = "RHYTHM_REFRESH_RESET"
LOOP_RESET = "LOOP_RESET"
VISUAL_PRESENTATION_RESET = "VISUAL_PRESENTATION_RESET"
AMBIGUOUS_RESET = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class Delta:
    """One predicted frame against the manual frame it is closest to."""

    predicted: int
    manual: int | None
    delta: int | None

    @property
    def exact(self) -> bool:
        return self.delta == 0


def frame_deltas(predicted: Iterable[int], manual: Sequence[int]) -> tuple[Delta, ...]:
    """Nearest manual frame for every prediction, in prediction order.

    Used for both resets and re-entries. With no manual frames at all the deltas are `None`,
    which is reported as such rather than as a zero.
    """

    ordered = sorted(manual)
    result: list[Delta] = []
    for frame in sorted(predicted):
        if not ordered:
            result.append(Delta(frame, None, None))
            continue
        nearest = min(ordered, key=lambda m: (abs(m - frame), m))
        result.append(Delta(frame, nearest, frame - nearest))
    return tuple(result)


def match_pairs(predicted: Iterable[int], manual: Sequence[int]) -> tuple[Delta, ...]:
    """One-to-one nearest matching, closest pair first, no manual frame used twice.

    This is the "right decision, wrong frame" measurement (task §35). `frame_deltas` lets two
    predictions claim the same manual reset, which flatters a candidate that fires twice around
    one real one; this does not. Predictions left over when the manual resets run out come back
    with `manual=None`, which is the honest reading of "there was nothing there".
    """

    available = sorted(manual)
    pending = sorted(predicted)
    pairs: list[Delta] = []
    while pending and available:
        best = min(
            ((p, m) for p in pending for m in available),
            key=lambda pair: (abs(pair[0] - pair[1]), pair[0], pair[1]),
        )
        pairs.append(Delta(best[0], best[1], best[0] - best[1]))
        pending.remove(best[0])
        available.remove(best[1])
    pairs.extend(Delta(frame, None, None) for frame in pending)
    return tuple(sorted(pairs, key=lambda d: d.predicted))


def recall_by_category(
    categories: Mapping[int, str], predicted: Iterable[int]
) -> dict[str, tuple[int, int]]:
    """`category -> (hit, total)` over the manual resets, so a miss can be attributed.

    A category with no manual example is absent from the result rather than reported as 0/0:
    the blind Short decides which categories exist, not this function.
    """

    hits: dict[str, tuple[int, int]] = {}
    predicted_set = set(predicted)
    for frame, category in sorted(categories.items()):
        hit, total = hits.get(category, (0, 0))
        hits[category] = (hit + (1 if frame in predicted_set else 0), total + 1)
    return hits


def subset_evaluation(
    name: str,
    universe: Iterable[int],
    predicted: Iterable[int],
    actual: Iterable[int],
    *,
    excluded: Iterable[int],
) -> Evaluation:
    """Strict scoring with a listed set of cuts removed from BOTH the universe and the target.

    Every excluded frame must actually be a manual reset in `actual`; excluding a cut that was
    never positive would silently delete a false positive instead of a target, so it raises.
    """

    excluded_set = set(excluded)
    actual_set = set(actual)
    stray = excluded_set - actual_set
    if stray:
        raise ValueError(
            f"cannot exclude {sorted(stray)}: an exclusion must name a manual reset, and these "
            "are not ones"
        )
    return evaluate(
        name,
        (c for c in universe if c not in excluded_set),
        (p for p in predicted if p not in excluded_set),
        (a for a in actual_set if a not in excluded_set),
    )


__all__ = [
    "AMBIGUOUS_RESET",
    "LOOP_RESET",
    "RHYTHM_REFRESH_RESET",
    "SEMANTIC_RESET",
    "VISUAL_PRESENTATION_RESET",
    "Delta",
    "frame_deltas",
    "match_pairs",
    "recall_by_category",
    "subset_evaluation",
]
