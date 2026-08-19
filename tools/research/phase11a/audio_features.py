"""Run the shipped speech pipeline on the rendered voice and cache its objective facts.

Nothing is retuned: Silero's own defaults and the product's 30/10/30 ms energy settings, the
exact configuration the Phase 10 baseline uses. The output is the `(frame, dB)` envelope and
the speech ranges, both already on absolute timeline frames — numbers only, no audio, no text.

    python -m tools.research.phase11a.audio_features \
        --audio /var/tmp/daz-phase11a/data/audio/daz_voice.wav \
        --start-frame 216000 --frame-rate 60 --out /var/tmp/daz-phase11a/data/audio.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from davinci_auto_zoom.domain.timebase import Timebase
from davinci_auto_zoom.speech.pipeline import analyze_audio_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--start-frame", required=True, type=int)
    parser.add_argument("--frame-rate", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    timebase = Timebase.from_timeline(args.frame_rate, args.start_frame)
    result = analyze_audio_file(args.audio, args.audio.parent / "voice-16k.wav", timebase)

    payload = {
        "start_frame": args.start_frame,
        "frame_rate": str(args.frame_rate),
        "sample_count": result.audio.sample_count,
        "vad_settings": asdict(result.analysis.settings),
        "energy_settings": result.energy.settings.to_dict(),
        "speech_ranges": [[s.frames.start, s.frames.end] for s in result.segments],
        "envelope": [[p.frame, round(p.db, 3)] for p in result.energy.points],
    }
    args.out.write_text(json.dumps(payload), encoding="utf-8")

    print(f"speech segments  {len(result.segments)}")
    print(f"envelope points  {len(result.energy.points)}")
    print(f"wrote            {args.out}")


if __name__ == "__main__":
    main()
