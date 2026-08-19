"""Confusion metrics for a candidate reset rule, reported per class rather than as accuracy.

A false reset in the middle of a sentence and a missed reset at a real boundary are different
editorial defects, so precision and recall are always reported separately and a family is
never ranked by accuracy alone.

The universe is the hard cuts of the labelled range. Cuts the creator marked as loop resets
can be excluded (evaluation A, semantics only) or folded back in as a deterministic override
(evaluation B) — the two numbers answer different questions and are never merged.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Confusion:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int

    @property
    def precision(self) -> float:
        predicted = self.true_positives + self.false_positives
        return self.true_positives / predicted if predicted else 0.0

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def accuracy(self) -> float:
        total = (
            self.true_positives
            + self.false_positives
            + self.false_negatives
            + self.true_negatives
        )
        return (self.true_positives + self.true_negatives) / total if total else 0.0


@dataclass(frozen=True, slots=True)
class Evaluation:
    name: str
    confusion: Confusion
    #: The exact cuts that went wrong, so every error can be explained one by one.
    false_positive_frames: tuple[int, ...]
    false_negative_frames: tuple[int, ...]


def evaluate(
    name: str,
    universe: Iterable[int],
    predicted: Iterable[int],
    actual: Iterable[int],
) -> Evaluation:
    """Score `predicted` against `actual` over `universe`, which is the set of hard cuts.

    A prediction outside the universe is a bug in the caller, not a silently ignored cut, so
    it raises.
    """

    cuts = set(universe)
    predicted_set = set(predicted)
    actual_set = set(actual)
    stray = (predicted_set | actual_set) - cuts
    if stray:
        raise ValueError(f"frames outside the evaluated universe: {sorted(stray)}")

    tp = predicted_set & actual_set
    fp = predicted_set - actual_set
    fn = actual_set - predicted_set
    tn = cuts - predicted_set - actual_set
    return Evaluation(
        name=name,
        confusion=Confusion(len(tp), len(fp), len(fn), len(tn)),
        false_positive_frames=tuple(sorted(fp)),
        false_negative_frames=tuple(sorted(fn)),
    )


def table(evaluations: list[Evaluation]) -> str:
    """Fixed-width report block. Ordered as given — never re-sorted by score."""

    header = (
        f"{'family':<52s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'TN':>4s} "
        f"{'prec':>6s} {'rec':>6s} {'F1':>6s}"
    )
    lines = [header, "-" * len(header)]
    for evaluation in evaluations:
        c = evaluation.confusion
        lines.append(
            f"{evaluation.name:<52s} {c.true_positives:>4d} {c.false_positives:>4d} "
            f"{c.false_negatives:>4d} {c.true_negatives:>4d} "
            f"{c.precision:>6.3f} {c.recall:>6.3f} {c.f1:>6.3f}"
        )
    return "\n".join(lines)


__all__ = ["Confusion", "Evaluation", "evaluate", "table"]
