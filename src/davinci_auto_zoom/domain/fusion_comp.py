"""Structural comparison of two exported Fusion compositions (`.comp` files).

`TimelineItem.GetFusionCompCount() == 1` proves only that *a* composition exists. To claim
that a newly inserted instance carries the *user's* effect, two exported comps are compared
here as plain text: no Resolve object is touched and nothing is ever reconstructed.

Exported comps are Lua-like tables. Rather than implementing a Lua parser, this module
extracts the facts that decide the question — which tools exist, of which class, how they
are wired, and where the keyframes are — and classifies every remaining textual difference
as either duration-dependent, volatile, or unexplained. Unexplained differences are
reported rather than ignored, so a wrong conclusion cannot hide behind a filter.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass
from typing import Any

# A tool declaration sits at indent level 2 inside `Tools = { ... }`:  "\t\tName = Class {"
_TOOL = re.compile(r"^\t\t(\w+) = (\w+) \{", re.MULTILINE)
# A keyframe inside a spline's KeyFrames block: "\t\t\t\t[15] = { 1.5, LH = { ... } }"
_KEYFRAME = re.compile(r"^\t+\[(-?[\d.]+)\] = \{(.*?)\},?$", re.MULTILINE)
_LINK = re.compile(r'SourceOp = "([^"]+)"')
_MEDIA_NAME = re.compile(r'MEDIA_NAME = "([^"]*)"')

#: Keys whose value legitimately changes with the instance's length. A difference here is
#: expected when the same asset is placed at two different durations.
DURATION_DEPENDENT_KEYS = frozenset(
    {
        "RenderRange",
        "GlobalRange",
        "GlobalOut",
        "GlobalEnd",
        "ClipTimeEnd",
        "ClipTimeStart",
        "TrimIn",
        "TrimOut",
        "Length",
        "MEDIA_MARK_IN",
        "MEDIA_MARK_OUT",
        "MEDIA_NUM_FRAMES",
        "MEDIA_START_FRAME",
        "CurrentTime",
        # Observed on Studio 21.0.4.5: the Loader carries `ExtentSet = true` exactly when
        # the clip's extent was set explicitly rather than left at the media's natural
        # length. Instances requested at the asset's native duration omit it; every
        # trimmed or extended instance has it. It records how the length was established,
        # not what the effect does.
        "ExtentSet",
    }
)

#: Keys that carry per-instance/per-session identifiers rather than behaviour.
VOLATILE_KEYS = frozenset(
    {
        "CurrentID",
        "Version",
        "MEDIA_ID",
        "MEDIA_AUDIO_TRACK_NAME",
        "MEDIA_AUDIO_TRACK_ID",
        '["ResolveCaches:"]',
    }
)


@dataclass(frozen=True, slots=True)
class CompFingerprint:
    """The behaviour-carrying structure of one exported composition."""

    tools: tuple[tuple[str, str], ...]  # (tool name, tool class), source order
    links: tuple[tuple[str, str], ...]  # (tool name, upstream tool name)
    keyframes: tuple[tuple[str, tuple[tuple[float, str], ...]], ...]  # spline -> keyframes
    media_name: str | None

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.tools)

    @property
    def keyframe_times(self) -> tuple[tuple[str, tuple[float, ...]], ...]:
        return tuple((name, tuple(t for t, _ in keys)) for name, keys in self.keyframes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _line_key(line: str) -> str:
    stripped = line.strip()
    key, _, _ = stripped.partition(" = ")
    return key.strip()


def parse_comp(text: str) -> CompFingerprint:
    """Extract the structural fingerprint of an exported composition."""

    tools = tuple((match.group(1), match.group(2)) for match in _TOOL.finditer(text))

    # Split the file into per-tool chunks so keyframes and links can be attributed.
    starts = [(match.start(), match.group(1)) for match in _TOOL.finditer(text)]
    bounds = [
        (name, start, starts[i + 1][0] if i + 1 < len(starts) else len(text))
        for i, (start, name) in enumerate(starts)
    ]

    links: list[tuple[str, str]] = []
    keyframes: list[tuple[str, tuple[tuple[float, str], ...]]] = []
    for name, start, end in bounds:
        chunk = text[start:end]
        links.extend((name, upstream) for upstream in _LINK.findall(chunk))
        keys = tuple(
            (float(time), payload.strip()) for time, payload in _KEYFRAME.findall(chunk)
        )
        if keys:
            keyframes.append((name, keys))

    media = _MEDIA_NAME.search(text)
    return CompFingerprint(
        tools=tools,
        links=tuple(links),
        keyframes=tuple(keyframes),
        media_name=media.group(1) if media else None,
    )


@dataclass(frozen=True, slots=True)
class CompComparison:
    """Why two exported comps are (or are not) the same user effect."""

    verdict: str  # "identical" | "equivalent-modulo-duration" | "different" | "unparsable"
    tools_equal: bool
    links_equal: bool
    keyframes_equal: bool
    keyframe_times_equal: bool
    media_names: tuple[str | None, str | None]
    duration_dependent_differences: tuple[str, ...]
    unexplained_differences: tuple[str, ...]
    reference: CompFingerprint
    candidate: CompFingerprint

    @property
    def carries_user_effect(self) -> bool:
        """True when the candidate has the reference's tools, wiring and keyframe values."""
        return self.tools_equal and self.links_equal and self.keyframes_equal

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["carries_user_effect"] = self.carries_user_effect
        return data


def compare_comps(reference_text: str, candidate_text: str) -> CompComparison:
    """Compare a known-good user comp against a comp produced by an automated insertion."""

    reference = parse_comp(reference_text)
    candidate = parse_comp(candidate_text)

    duration_differences: list[str] = []
    unexplained: list[str] = []
    reference_lines = reference_text.splitlines()
    candidate_lines = candidate_text.splitlines()
    # A real diff, not a positional zip: two comps of the same effect can differ in line
    # *count* (Resolve emits e.g. `ExtentSet` only sometimes), and index-by-index
    # comparison would then report every following line as a spurious difference.
    matcher = difflib.SequenceMatcher(None, reference_lines, candidate_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        left_lines = reference_lines[i1:i2]
        right_lines = candidate_lines[j1:j2]
        for offset in range(max(len(left_lines), len(right_lines))):
            left = left_lines[offset] if offset < len(left_lines) else ""
            right = right_lines[offset] if offset < len(right_lines) else ""
            key = _line_key(left) or _line_key(right)
            if key in VOLATILE_KEYS:
                continue
            entry = f"{i1 + offset + 1}: {left.strip()!r} != {right.strip()!r}"
            if key in DURATION_DEPENDENT_KEYS:
                duration_differences.append(entry)
            else:
                unexplained.append(entry)

    tools_equal = reference.tools == candidate.tools
    links_equal = reference.links == candidate.links
    keyframes_equal = reference.keyframes == candidate.keyframes

    if not (reference.tools and candidate.tools):
        verdict = "unparsable"
    elif not (tools_equal and links_equal and keyframes_equal):
        verdict = "different"
    elif not unexplained and not duration_differences:
        verdict = "identical"
    elif not unexplained:
        verdict = "equivalent-modulo-duration"
    else:
        verdict = "different"

    return CompComparison(
        verdict=verdict,
        tools_equal=tools_equal,
        links_equal=links_equal,
        keyframes_equal=keyframes_equal,
        keyframe_times_equal=reference.keyframe_times == candidate.keyframe_times,
        media_names=(reference.media_name, candidate.media_name),
        duration_dependent_differences=tuple(duration_differences),
        unexplained_differences=tuple(unexplained),
        reference=reference,
        candidate=candidate,
    )


__all__ = [
    "DURATION_DEPENDENT_KEYS",
    "VOLATILE_KEYS",
    "CompComparison",
    "CompFingerprint",
    "compare_comps",
    "parse_comp",
]
