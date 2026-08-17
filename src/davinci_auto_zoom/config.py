from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from davinci_auto_zoom.domain.planner import (
    PLANNER_SETTING_KEYS,
    AssetTiming,
    PlannerSettings,
)
from davinci_auto_zoom.domain.transitions import (
    BY_ROLE,
    REQUIRED_ROLES,
    ROLE_FACE_X1_TO_FACE_X2,
    ROLE_FACE_X1_TO_X0,
    ROLE_FACE_X2_TO_FACE_X3,
    ROLE_FACE_X2_TO_X0,
    ROLE_FACE_X3_TO_X0,
    ROLE_X0_TO_FACE_X1,
    ROLES,
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

    # Transition role -> Media Pool clip name. The keys are the roles of
    # `domain/transitions.py`, so the config *is* the state graph's asset table (D045). The
    # defaults are this user's bin: the promotion clips are named after the state they land in
    # (`FACE_X2` performs face_x1 -> face_x2), and the reset clips after the move itself.
    assets: dict[str, str] = field(
        default_factory=lambda: {
            ROLE_X0_TO_FACE_X1: "FACE_X1",
            ROLE_FACE_X1_TO_FACE_X2: "FACE_X2",
            ROLE_FACE_X2_TO_FACE_X3: "FACE_X3",
            ROLE_FACE_X1_TO_X0: "X1_TO_X0",
            ROLE_FACE_X2_TO_X0: "X2_TO_X0",
            ROLE_FACE_X3_TO_X0: "X3_TO_X0",
        }
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

    def asset_names(self, *, reset: bool) -> tuple[str, ...]:
        """Configured clip names for the reset transitions, or for the zoom-in ones.

        Reporting and reference comparison need "every asset that puts the frame on a facecam"
        and "every asset that brings it back", not one hard-coded name each — that assumption
        is exactly what Phase 8 removed.
        """

        return tuple(
            sorted(
                name
                for role, name in self.assets.items()
                if name and BY_ROLE[role].is_reset is reset
            )
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
        speech = data.get("speech", {})
        defaults = cls()
        asset_data = dict(data.get("assets", {}))
        timing_data = asset_data.pop("transition_frames", {})
        # A file that names any transition asset replaces the default table wholesale rather
        # than merging into it: merging would silently keep a default `FACE_X2` for a user who
        # deliberately configured only the two required roles, and then plan promotions they
        # have no asset for.
        assets = {str(k): str(v) for k, v in (asset_data or defaults.assets).items()}
        unknown_assets = sorted(set(assets) - set(ROLES))
        if unknown_assets:
            raise ValueError(
                f"unknown transition role(s) in [assets]: {', '.join(unknown_assets)}. "
                f"Supported: {', '.join(ROLES)}"
            )
        timing = _asset_timing(timing_data)
        if timing is not None:
            named = set(assets)
            timed = set(timing.to_dict())
            if named != timed:
                raise ValueError(
                    "[assets] and [assets.transition_frames] must describe the same "
                    f"transition roles. Only named: {sorted(named - timed) or 'none'}; "
                    f"only timed: {sorted(timed - named) or 'none'}. A role without a clip "
                    "name cannot be placed, and a clip name without an animation length "
                    "cannot be planned."
                )

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
            assets=assets,
            asset_timing=timing,
            planner=_planner_settings(data.get("planner", {})),
            speech_provider=provider,
            vad=_vad_settings(speech.get("vad", {})),
        )


def _asset_timing(data: dict[str, Any]) -> AssetTiming | None:
    """Per-role animation lengths, in frames.

    Frames, not milliseconds, on purpose: these describe keyframed animations authored on a
    frame grid inside the user's own Generator assets, so a millisecond value would only be
    converted straight back — and would round differently per project frame rate.

    The key set is also the list of transitions this project can actually perform, so a user
    with no `FACE_X3` asset simply omits both its lines and the planner never promotes that
    far. Only `x0_to_face_x1` and `face_x1_to_x0` are mandatory; `AssetTiming` enforces that.
    """

    if not data:
        return None
    unknown = sorted(set(data) - set(ROLES))
    if unknown:
        raise ValueError(
            f"unknown transition role(s) in [assets.transition_frames]: "
            f"{', '.join(unknown)}. Supported: {', '.join(ROLES)}"
        )
    missing = [role for role in REQUIRED_ROLES if role not in data]
    if missing:
        raise ValueError(
            f"[assets.transition_frames] is missing {', '.join(missing)}. Without these "
            "there is no zoom at all; the x2/x3 roles are optional."
        )
    return AssetTiming({str(role): int(frames) for role, frames in data.items()})


def _planner_settings(data: dict[str, Any]) -> PlannerSettings:
    """Editorial timing. Unknown keys are an error, exactly as in [speech.vad]."""

    defaults = PlannerSettings()
    unknown = sorted(set(data) - set(PLANNER_SETTING_KEYS))
    if unknown:
        raise ValueError(
            f"unknown key(s) in [planner]: {', '.join(unknown)}. Supported: "
            f"{', '.join(sorted(PLANNER_SETTING_KEYS))}"
        )
    return PlannerSettings(
        **{key: int(data.get(key, getattr(defaults, key))) for key in PLANNER_SETTING_KEYS}
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
