from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Config:
    """User configuration. Every Resolve-side name is configurable per edit style."""

    # Optional guards: when set, commands refuse to run against a different project/timeline.
    project: str | None = None
    timeline: str | None = None

    # 1-based Resolve audio track holding only the creator's voice. Resolve exposes no
    # reliable way to detect it (see .agent/HANDOFF.md), so it must be configured.
    voice_audio_track: int = 1

    # 1-based video track that receives the zoom adjustment clips.
    zoom_video_track: int = 3

    asset_bin: str = "DAVINCI_AUTO_ZOOM"

    # Semantic role -> Media Pool clip name.
    assets: dict[str, str] = field(
        default_factory=lambda: {"facecam_x1": "FACE_X1", "reset_x0": "FACE_X0_SMOOTH"}
    )

    @classmethod
    def load(cls, path: Path | None) -> Config:
        if path is None:
            return cls()
        if not path.is_file():
            raise FileNotFoundError(f"config file not found: {path}")
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        resolve = data.get("resolve", {})
        defaults = cls()
        assets = dict(defaults.assets)
        assets.update(data.get("assets", {}))
        return cls(
            project=resolve.get("project") or None,
            timeline=resolve.get("timeline") or None,
            voice_audio_track=int(resolve.get("voice_audio_track", defaults.voice_audio_track)),
            zoom_video_track=int(resolve.get("zoom_video_track", defaults.zoom_video_track)),
            asset_bin=str(resolve.get("asset_bin", defaults.asset_bin)),
            assets={str(k): str(v) for k, v in assets.items()},
        )
