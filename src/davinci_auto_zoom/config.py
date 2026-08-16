from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.planner import (
    ROLE_FACECAM_X1,
    ROLE_RESET_X0,
    AssetTiming,
    PlannerSettings,
)
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

    # 1-based video track whose hard cuts the planner may snap a reset to. Deliberately a
    # separate setting from zoom_video_track: the cuts live on the edited footage track, the
    # zooms land on a dedicated track that has no cuts of its own.
    cut_reference_video_track: int = 1

    asset_bin: str = "DAVINCI_AUTO_ZOOM"

    # Semantic role -> Media Pool clip name.
    assets: dict[str, str] = field(
        default_factory=lambda: {"facecam_x1": "FACE_X1", "reset_x0": "FACE_X0_SMOOTH"}
    )

    # How long each asset's own animation takes, in frames. This is **user metadata about
    # user-built assets**: DAZ treats the assets as opaque (D007) and never inspects the
    # Fusion graph to guess it. There is deliberately no default — 15/15 is this project's
    # setup, not a property of the software — so an unconfigured planner refuses to run
    # rather than silently planning against someone else's numbers.
    asset_timing: AssetTiming | None = None

    # Editorial timing. Distinct from `vad`, which is technical detection tuning.
    planner: PlannerSettings = field(default_factory=PlannerSettings)

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
        asset_data = dict(data.get("assets", {}))
        timing_data = asset_data.pop("transition_frames", {})
        assets = dict(defaults.assets)
        assets.update(asset_data)

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
            cut_reference_video_track=int(
                resolve.get(
                    "cut_reference_video_track", defaults.cut_reference_video_track
                )
            ),
            asset_bin=str(resolve.get("asset_bin", defaults.asset_bin)),
            assets={str(k): str(v) for k, v in assets.items()},
            asset_timing=_asset_timing(timing_data),
            planner=_planner_settings(data.get("planner", {})),
            speech_provider=provider,
            vad=_vad_settings(speech.get("vad", {})),
        )


def _asset_timing(data: dict[str, Any]) -> AssetTiming | None:
    """Per-role animation lengths, in frames.

    Frames, not milliseconds, on purpose: these describe keyframed animations authored on a
    frame grid inside the user's own Generator assets, so a millisecond value would only be
    converted straight back — and would round differently per project frame rate.
    """

    if not data:
        return None
    known = {ROLE_FACECAM_X1, ROLE_RESET_X0}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(
            f"unknown role(s) in [assets.transition_frames]: {', '.join(unknown)}. "
            f"Supported: {', '.join(sorted(known))}"
        )
    missing = sorted(known - set(data))
    if missing:
        raise ValueError(
            f"[assets.transition_frames] is missing {', '.join(missing)}. Every zoom role "
            "needs the number of frames its own animation takes to finish."
        )
    return AssetTiming(
        facecam_x1_transition_frames=int(data[ROLE_FACECAM_X1]),
        reset_x0_transition_frames=int(data[ROLE_RESET_X0]),
    )


def _planner_settings(data: dict[str, Any]) -> PlannerSettings:
    """Editorial timing. Unknown keys are an error, exactly as in [speech.vad]."""

    defaults = PlannerSettings()
    known = {
        "reset_after_silence_ms",
        "zoom_lead_in_ms",
        "zoom_lead_out_ms",
        "cut_snap_window_ms",
    }
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(
            f"unknown key(s) in [planner]: {', '.join(unknown)}. Supported: "
            f"{', '.join(sorted(known))}"
        )
    return PlannerSettings(
        reset_after_silence_ms=int(
            data.get("reset_after_silence_ms", defaults.reset_after_silence_ms)
        ),
        zoom_lead_in_ms=int(data.get("zoom_lead_in_ms", defaults.zoom_lead_in_ms)),
        zoom_lead_out_ms=int(data.get("zoom_lead_out_ms", defaults.zoom_lead_out_ms)),
        cut_snap_window_ms=int(
            data.get("cut_snap_window_ms", defaults.cut_snap_window_ms)
        ),
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
