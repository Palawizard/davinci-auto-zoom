"""Rendered program video -> an objective visual-activity envelope. No semantics.

This is the video counterpart of `speech/energy.py` and it keeps the same boundary (D050,
generalised in D055): it answers **"how much did the picture change here"** and nothing else.
It never answers "is this an interesting moment in the game". There is no object detection,
no OCR, no game-specific model and no learned classifier anywhere in this module, on purpose
— reading the envelope as an editorial cue is `domain/gameplay.py`'s job, and only that
module's.

The pipeline is deliberately the cheapest thing that produces a comparable number:

    rendered video -> ffmpeg -> small grayscale raw frames at a low rate -> numpy

`ffmpeg` and `numpy` are already dependencies. OpenCV, scipy and any vision model are not,
and nothing here needs them: a mean absolute frame difference is one numpy expression.

**Why the frames are mean-subtracted before differencing.** A uniform brightness change — a
fade, an exposure shift, a flashbang in the game — moves every pixel by the same amount and
would otherwise read as maximal "motion" while nothing actually moved. Subtracting each
frame's own mean first makes the metric invariant to that, so what survives is *structural*
change. It is not invariant to contrast, and does not claim to be.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from davinci_auto_zoom.domain.models import Frame
from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.speech.audio import FfmpegError, ffmpeg_executable


@dataclass(frozen=True, slots=True)
class VisionSettings:
    """How the picture is sampled before it is measured. Changing these changes the numbers.

    The defaults are small on purpose. 64x36 keeps a 16:9 frame's layout while throwing away
    everything below "a shape moved"; 10 samples per second is far finer than any editorial
    decision this feeds and still decodes a minute of video in well under a second.
    """

    width: int = 64
    height: int = 36
    #: Samples per second of timeline. NOT the timeline's frame rate, and never assumed to be.
    sample_rate: int = 10

    def __post_init__(self) -> None:
        if self.width < 2 or self.height < 2:
            raise ValueError("width and height must be >= 2")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be > 0")


@dataclass(frozen=True, slots=True)
class MotionPoint:
    """How much the picture changed at one absolute timeline frame."""

    frame: Frame
    #: Mean absolute difference of two mean-subtracted grayscale frames, in [0, 1].
    motion: float


@dataclass(frozen=True, slots=True)
class MotionEnvelope:
    """The whole visual-activity curve, on absolute timeline frames."""

    points: tuple[MotionPoint, ...]
    settings: VisionSettings

    def __bool__(self) -> bool:
        return bool(self.points)

    def between(self, start: Frame, end: Frame) -> tuple[float, ...]:
        """Motion values whose frame falls in the half-open range `[start, end)`."""

        return tuple(p.motion for p in self.points if start <= p.frame < end)


def decode_gray_frames(video: Path, settings: VisionSettings) -> np.ndarray:
    """`(n, height, width)` float32 luma in [0, 1], sampled at `settings.sample_rate`.

    One ffmpeg process, raw output, no intermediate files. The `fps` filter is what
    guarantees constant sample spacing, and therefore that sample *i* is at
    `i / sample_rate` seconds: it duplicates and drops frames to hit the requested rate, so a
    variable-frame-rate source cannot silently shift every measurement.

    Nothing here passes `-vsync`. It was removed in ffmpeg 8, the `fps` filter already
    provides the guarantee it used to add, and a flag that makes the command fail on a
    current ffmpeg buys nothing.
    """

    if not video.is_file():
        raise FfmpegError(f"video file not found: {video}")
    executable = ffmpeg_executable()
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(video),
        "-an",
        "-vf", f"fps={settings.sample_rate},scale={settings.width}:{settings.height}",
        "-pix_fmt", "gray",
        "-f", "rawvideo",
        "-",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command, capture_output=True, timeout=1800, check=False
        )
    except OSError as exc:
        raise FfmpegError(f"could not execute ffmpeg: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"ffmpeg timed out decoding {video}") from exc
    if completed.returncode != 0:
        raise FfmpegError(
            f"ffmpeg failed with exit code {completed.returncode} decoding {video}:\n"
            f"{completed.stderr.decode('utf-8', 'replace').strip()}"
        )

    stride = settings.width * settings.height
    raw = completed.stdout
    if len(raw) < stride:
        raise FfmpegError(f"ffmpeg produced no usable frames from {video}")
    count = len(raw) // stride
    # A partial trailing frame would be garbage rather than a short frame; drop it.
    flat = np.frombuffer(raw[: count * stride], dtype=np.uint8)
    return flat.reshape(count, settings.height, settings.width).astype(np.float32) / 255.0


def motion_envelope(
    video: Path,
    timebase: Timebase,
    settings: VisionSettings | None = None,
) -> MotionEnvelope:
    """Visual activity of `video`, stamped on absolute timeline frames. Decode, then measure."""

    settings = settings or VisionSettings()
    return motion_from_frames(decode_gray_frames(video, settings), timebase, settings)


def motion_from_frames(
    frames: np.ndarray,
    timebase: Timebase,
    settings: VisionSettings | None = None,
) -> MotionEnvelope:
    """The measurement itself, on an already-decoded `(n, h, w)` array in [0, 1].

    Split from `motion_envelope` so the metric can be tested against frames a test builds by
    hand — a still, a moving square, a cut, a brightness ramp — with no video file and no
    ffmpeg process anywhere near it.

    Sample *i* is stamped at the **midpoint** between the two frames it compares, for the
    same reason `energy_envelope` stamps at the centre of its window: a change belongs to the
    instant it happened, not to the frame after it.
    """

    settings = settings or VisionSettings()
    if frames.shape[0] < 2:
        return MotionEnvelope((), settings)

    # Mean-subtract each frame, so a uniform brightness shift contributes nothing.
    centred = frames - frames.mean(axis=(1, 2), keepdims=True)
    motion = np.abs(np.diff(centred, axis=0)).mean(axis=(1, 2))

    seconds_per_sample = 1.0 / settings.sample_rate
    points = []
    for index, value in enumerate(motion):
        midpoint_seconds = (index + 0.5) * seconds_per_sample
        sample = int(round(midpoint_seconds * timebase.sample_rate))
        points.append(MotionPoint(timebase.start_frame_of(sample), float(value)))
    return MotionEnvelope(tuple(points), settings)


__all__ = [
    "MotionEnvelope",
    "MotionPoint",
    "VisionSettings",
    "decode_gray_frames",
    "motion_envelope",
    "motion_from_frames",
]
