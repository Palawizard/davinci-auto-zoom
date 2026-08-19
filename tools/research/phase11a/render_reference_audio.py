"""Render the reference timeline's voice track for the study, through the proven safe path.

This adds **nothing** to the Resolve safety model and changes nothing in it: it calls
`resolve.voice_render.render_voice_track` exactly as `plan-probe` does — scratch duplicate,
one render job, Deliver state captured and restored, transactional cleanup, post-run audit —
and then normalises the result to 16 kHz mono PCM with the shipped `speech.audio` helper.

The reference timeline is never modified. The rendered audio is the creator's own voice and
is temporary, local and gitignored; it is never committed (`AGENTS.md`, "User media").

    python -m tools.research.phase11a.render_reference_audio \
        --project "bluescreen 2" --timeline "Timeline 1" --out /tmp/daz-11a --confirm
"""

from __future__ import annotations

import argparse
from pathlib import Path

from davinci_auto_zoom.config import Config
from davinci_auto_zoom.domain.probe import VoiceRenderTarget
from davinci_auto_zoom.domain.timebase import frame_rate_fraction
from davinci_auto_zoom.resolve.session import connect
from davinci_auto_zoom.resolve.voice_render import render_voice_track
from davinci_auto_zoom.speech.audio import normalized_audio
from davinci_auto_zoom.speech.pipeline import verify_duration


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--timeline", required=True)
    parser.add_argument("--voice-track", type=int, default=1)
    parser.add_argument("--render-preset", default="Audio Only")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: this renders from Resolve on a scratch timeline it creates and deletes",
    )
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    resolve = connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    report, rendered = render_voice_track(
        resolve,
        project,
        Config(project=args.project, timeline=args.timeline),
        VoiceRenderTarget(
            project=args.project,
            source_timeline=args.timeline,
            voice_audio_track=args.voice_track,
            render_preset=args.render_preset,
        ),
        out,
        confirmed=args.confirm,
        protected_timelines=(args.timeline,),
    )
    if rendered is None:
        print(report.to_text())
        raise SystemExit("voice render failed; see the report above")

    assert report.timeline_start_frame is not None and report.timeline_end_frame is not None
    normalized = out / "voice-16k.wav"
    audio = normalized_audio(rendered, normalized)
    # Filled in so `report.succeeded` means what it says. The duration check is the proof the
    # render was not trimmed: every downstream frame number depends on sample 0 being the
    # timeline's own first frame.
    report.audio.normalized_path = str(normalized)
    report.audio.sample_count = audio.sample_count
    report.audio.duration_seconds = audio.duration_seconds
    check = verify_duration(
        expected_frames=report.timeline_end_frame - report.timeline_start_frame,
        sample_count=audio.sample_count,
        frame_rate=frame_rate_fraction(report.timeline_frame_rate or "60"),
    )
    report.audio.expected_seconds = check.expected_seconds
    report.audio.delta_frames = check.delta_frames
    report.audio.duration_ok = check.ok
    print(report.to_text())
    print(f"\nnormalised   {normalized}")
    print(f"samples      {audio.sample_count}  ({audio.duration_seconds:.3f} s)")
    print(f"duration     delta {check.delta_frames:+.4f} frames, ok={check.ok}")


if __name__ == "__main__":
    main()
