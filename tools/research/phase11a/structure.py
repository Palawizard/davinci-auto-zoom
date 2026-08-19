"""Content islands and hard cuts, read off the reference video track.

One timeline can carry several Shorts separated by empty timeline space. That space is not
a silence, not an editorial decision and not a training example — it is the absence of
content. So the unit of study is the **ContentIsland**: a maximal run of clips on the cut
reference track with no meaningful gap inside it. Every Short is analysed on its own, and no
context (words, audio, state) ever crosses an island boundary.

Nothing here assumes how many islands exist. The `bluescreen 2` reference happens to have
two inside the labelled range; the model accepts N.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Clip:
    """One item on the cut reference track. Half-open `[start, end)`, absolute frames."""

    start: int
    end: int
    name: str = ""

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"clip end must be > start ({self.start}, {self.end})")


@dataclass(frozen=True, slots=True)
class ContentIsland:
    """One continuous stretch of edited content — in practice, one Short."""

    index: int
    start: int
    end: int
    clips: tuple[Clip, ...]
    hard_cuts: tuple[int, ...]

    def contains(self, frame: int) -> bool:
        return self.start <= frame < self.end

    @property
    def last_hard_cut(self) -> int | None:
        """The last hard cut inside the island, which is where a loop reset would sit."""

        return self.hard_cuts[-1] if self.hard_cuts else None


def content_islands(clips: list[Clip], *, min_gap_frames: int) -> tuple[ContentIsland, ...]:
    """Group clips into islands, splitting wherever the empty space reaches `min_gap_frames`.

    `min_gap_frames` must be stated by the caller: what counts as "a different Short" is a
    property of the edit, not of this function. A gap smaller than it stays inside the island
    but never becomes a hard cut — only an exact `A.end == B.start` boundary does.
    """

    if min_gap_frames <= 0:
        raise ValueError("min_gap_frames must be > 0")
    if not clips:
        return ()

    ordered = sorted(clips, key=lambda c: (c.start, c.end))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.start < previous.end:
            raise ValueError(
                f"clips overlap on the reference track: [{previous.start},{previous.end}) "
                f"and [{current.start},{current.end})"
            )

    groups: list[list[Clip]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.start - previous.end >= min_gap_frames:
            groups.append([current])
        else:
            groups[-1].append(current)

    islands: list[ContentIsland] = []
    for index, group in enumerate(groups):
        cuts = tuple(
            current.start
            for previous, current in zip(group, group[1:], strict=False)
            if current.start == previous.end
        )
        islands.append(
            ContentIsland(
                index=index,
                start=group[0].start,
                end=group[-1].end,
                clips=tuple(group),
                hard_cuts=cuts,
            )
        )
    return tuple(islands)


def island_of(islands: tuple[ContentIsland, ...], frame: int) -> ContentIsland | None:
    """The island containing `frame`, or None if it falls in the empty space between two."""

    for island in islands:
        if island.contains(frame):
            return island
    return None


def clip_at(island: ContentIsland, frame: int) -> Clip | None:
    for clip in island.clips:
        if clip.start <= frame < clip.end:
            return clip
    return None


__all__ = [
    "Clip",
    "ContentIsland",
    "clip_at",
    "content_islands",
    "island_of",
]
