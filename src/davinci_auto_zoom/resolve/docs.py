"""Locate and parse the Blackmagic Developer/Scripting documentation installed with Resolve.

The installed README is the project's source of truth for API capabilities. Nothing here
touches Resolve itself; parsing is pure and unit-tested.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

# "  MethodName(args)   --> ReturnType   # comment"
# The closing parenthesis is optional: long signatures such as TimelineItem.AddMarker wrap
# their arguments onto the following line in the installed README.
_METHOD = re.compile(
    r"^\s{2,}(?P<name>[A-Z]\w*)\((?P<args>[^)]*?)\)?\s*-->\s*(?P<returns>\S.*?)\s*(?:#(?P<note>.*))?$"
)
# A non-indented single word starting a class block (section titles are underlined with ---).
_HEADING = re.compile(r"^(?P<name>[A-Z]\w*)\s*$")

# Everything from this heading onwards documents API we must not rely on.
_STOP_HEADINGS = ("Deprecated Resolve API Functions", "Unsupported Resolve API Functions")


@dataclass(frozen=True, slots=True)
class DocumentedMethod:
    owner: str
    name: str
    args: str
    returns: str
    note: str

    @property
    def signature(self) -> str:
        return f"{self.owner}.{self.name}({self.args}) --> {self.returns}"


def parse_scripting_readme(text: str) -> dict[str, dict[str, DocumentedMethod]]:
    """Return {class name: {method name: DocumentedMethod}} for the supported API only."""

    api: dict[str, dict[str, DocumentedMethod]] = {}
    lines = text.splitlines()
    owner: str | None = None

    for number, line in enumerate(lines):
        if line.strip() in _STOP_HEADINGS:
            break

        heading = _HEADING.match(line)
        if heading:
            # Section titles are followed by a dashed underline; class blocks are not.
            following = lines[number + 1] if number + 1 < len(lines) else ""
            owner = None if set(following.strip()) == {"-"} else heading.group("name")
            continue

        method = _METHOD.match(line)
        if method and owner:
            entry = DocumentedMethod(
                owner=owner,
                name=method.group("name"),
                args=method.group("args").strip(),
                returns=method.group("returns").strip(),
                note=(method.group("note") or "").strip(),
            )
            # Overloads: keep the first documented form.
            api.setdefault(owner, {}).setdefault(entry.name, entry)

    return api


def scripting_root_candidates() -> tuple[Path, ...]:
    """Documented install locations, environment variable first (README 'Using a script')."""

    candidates: list[Path] = []
    explicit = os.getenv("RESOLVE_SCRIPT_API")
    if explicit:
        candidates.append(Path(explicit))

    system = platform.system()
    if system == "Linux":
        candidates += [
            Path("/opt/resolve/Developer/Scripting"),
            Path("/home/resolve/Developer/Scripting"),
        ]
    elif system == "Darwin":
        candidates.append(
            Path(
                "/Library/Application Support/Blackmagic Design/DaVinci Resolve"
                "/Developer/Scripting"
            )
        )
    elif system == "Windows":
        program_data = os.getenv("PROGRAMDATA", r"C:\ProgramData")
        candidates.append(
            Path(program_data)
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Support"
            / "Developer"
            / "Scripting"
        )

    return tuple(dict.fromkeys(candidates))


def find_scripting_readme() -> Path | None:
    for root in scripting_root_candidates():
        readme = root / "README.txt"
        if readme.is_file():
            return readme
    return None


def load_documented_api() -> tuple[Path | None, dict[str, dict[str, DocumentedMethod]]]:
    readme = find_scripting_readme()
    if readme is None:
        return None, {}
    return readme, parse_scripting_readme(readme.read_text(encoding="utf-8", errors="replace"))
