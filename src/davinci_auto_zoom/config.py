from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.vad import VadSettings

#: Providers this build can actually construct. Listed here so a typo in config is a clear
#: error instead of a mysterious failure much later, next to a live Resolve session.
SPEECH_PROVIDERS = ("silero_vad",)


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

    speech_provider: str = "silero_vad"

    # Technical VAD tuning only. Editorial timing (how long a silence must last before the
    # tool zooms back out, zoom lead-in/out) belongs to [planner] and is Phase 4's business;
    # mixing the two under one name was the ambiguity this split removes.
    vad: VadSettings = field(default_factory=VadSettings)

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
        speech = data.get("speech", {})
        defaults = cls()
        assets = dict(defaults.assets)
        assets.update(data.get("assets", {}))

        voice_audio_track = int(
            resolve.get("voice_audio_track", defaults.voice_audio_track)
        )
        if voice_audio_track < 1:
            raise ValueError(
                f"[resolve].voice_audio_track must be a 1-based track index, got "
                f"{voice_audio_track}. A1 is 1."
            )
        provider = str(speech.get("provider", defaults.speech_provider))
        if provider not in SPEECH_PROVIDERS:
            raise ValueError(
                f"[speech].provider {provider!r} is not available; "
                f"supported: {', '.join(SPEECH_PROVIDERS)}"
            )
        return cls(
            project=resolve.get("project") or None,
            timeline=resolve.get("timeline") or None,
            voice_audio_track=voice_audio_track,
            zoom_video_track=int(resolve.get("zoom_video_track", defaults.zoom_video_track)),
            asset_bin=str(resolve.get("asset_bin", defaults.asset_bin)),
            assets={str(k): str(v) for k, v in assets.items()},
            speech_provider=provider,
            vad=_vad_settings(speech.get("vad", {})),
        )


def _vad_settings(data: dict[str, Any]) -> VadSettings:
    """Build technical VAD settings, rejecting unknown keys.

    Silence about a misspelled tuning key would be indistinguishable from the model simply
    behaving differently, which is exactly the sort of thing that wastes an afternoon.
    """

    defaults = VadSettings()
    known = {"threshold", "min_speech_ms", "min_silence_ms", "speech_pad_ms", "neg_threshold"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(
            f"unknown key(s) in [speech.vad]: {', '.join(unknown)}. Supported: "
            f"{', '.join(sorted(known))}"
        )
    neg = data.get("neg_threshold", defaults.neg_threshold)
    return VadSettings(
        threshold=float(data.get("threshold", defaults.threshold)),
        min_speech_ms=int(data.get("min_speech_ms", defaults.min_speech_ms)),
        min_silence_ms=int(data.get("min_silence_ms", defaults.min_silence_ms)),
        speech_pad_ms=int(data.get("speech_pad_ms", defaults.speech_pad_ms)),
        neg_threshold=None if neg is None else float(neg),
    )
