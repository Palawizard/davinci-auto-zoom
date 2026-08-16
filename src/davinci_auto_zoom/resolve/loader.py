from __future__ import annotations

import importlib
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from davinci_auto_zoom.resolve.docs import scripting_root_candidates


@dataclass(frozen=True, slots=True)
class ResolveModuleLoadResult:
    module: ModuleType | None
    attempted_module_paths: tuple[str, ...]
    error: str | None = None


def _native_library_candidates() -> tuple[Path, ...]:
    """Documented `RESOLVE_SCRIPT_LIB` locations (Developer/Scripting/README.txt).

    Blackmagic's `DaVinciResolveScript.py` looks these up itself, but knowing them lets us
    tell the user exactly what is missing instead of surfacing a bare ImportError.
    """

    candidates: list[Path] = []
    explicit = os.getenv("RESOLVE_SCRIPT_LIB")
    if explicit:
        candidates.append(Path(explicit))

    system = platform.system()
    if system == "Linux":
        candidates += [
            Path("/opt/resolve/libs/Fusion/fusionscript.so"),
            Path("/home/resolve/libs/Fusion/fusionscript.so"),
        ]
    elif system == "Darwin":
        candidates.append(
            Path(
                "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion"
                "/fusionscript.so"
            )
        )
    elif system == "Windows":
        candidates.append(
            Path(r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll")
        )
    return tuple(dict.fromkeys(candidates))


def _candidate_module_paths() -> tuple[Path, ...]:
    return tuple(root / "Modules" for root in scripting_root_candidates())


def existing_native_libraries() -> tuple[str, ...]:
    return tuple(str(path) for path in _native_library_candidates() if path.exists())


def load_resolve_script_module() -> ResolveModuleLoadResult:
    """Import Blackmagic's shipped scripting module without vendoring it."""

    attempted: list[str] = []

    try:
        return ResolveModuleLoadResult(importlib.import_module("DaVinciResolveScript"), ())
    except ModuleNotFoundError:
        pass
    except Exception as exc:  # native fusionscript library failed to load
        return ResolveModuleLoadResult(None, (), f"direct import failed: {exc!r}")

    last_error: str | None = None
    for path in _candidate_module_paths():
        attempted.append(str(path))
        if not path.is_dir():
            continue
        path_string = str(path)
        if path_string not in sys.path:
            sys.path.insert(0, path_string)
        try:
            return ResolveModuleLoadResult(
                importlib.import_module("DaVinciResolveScript"), tuple(attempted)
            )
        except Exception as exc:
            last_error = f"{path}: {exc!r}"

    if last_error is not None:
        error = (
            f"DaVinciResolveScript was found but could not be imported ({last_error}). "
            "The native fusionscript library is usually the cause; set RESOLVE_SCRIPT_LIB."
        )
    else:
        error = (
            "DaVinciResolveScript could not be found. Set RESOLVE_SCRIPT_API to the installed "
            "Developer/Scripting directory (see its README.txt)."
        )
    return ResolveModuleLoadResult(None, tuple(attempted), error)
