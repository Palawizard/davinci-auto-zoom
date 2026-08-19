"""Word-level French transcription for the study: WhisperX, plus faster-whisper for comparison.

RESEARCH ONLY. WhisperX, torch and CUDA are **not** dependencies of `davinci-auto-zoom` and
must not become any until this phase has shown they buy something (Phase 11a task, §8). This
script therefore runs from its own isolated venv and is never imported by the package:

    uv venv --python 3.12 /var/tmp/daz-phase11a/wx-venv
    uv pip install --python /var/tmp/daz-phase11a/wx-venv/bin/python whisperx
    PYTHONPATH=src:. /var/tmp/daz-phase11a/wx-venv/bin/python \
        -m tools.research.phase11a.transcribe --audio .../voice-16k.wav --out .../transcript.json

Two passes over the same audio, deliberately:

* **WhisperX** — ASR, then wav2vec2 forced alignment, which is where the word boundaries
  actually come from;
* **faster-whisper native** — the same backend's own `word_timestamps=True`, which comes from
  cross-attention rather than an acoustic aligner.

Neither is ground truth. Their *disagreement* is the evidence about how much a word boundary
can be trusted at 60 fps, and §14 of the task asks for it explicitly.

The output contains the creator's actual words and is written outside the repository. It is
never committed (`AGENTS.md`, "User media").
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "large-v3"
LANGUAGE = "fr"


def _peak_gpu_mb() -> float | None:
    try:
        import torch
    except ImportError:  # pragma: no cover - research script
        return None
    if not torch.cuda.is_available():
        return None
    return float(torch.cuda.max_memory_allocated()) / (1024 * 1024)


def _release_gpu() -> None:
    """Drop everything CUDA is holding before loading the next model.

    Both passes load `large-v3`, and 8 GB does not fit two copies: without this the second
    one dies with "CUDA failed with error out of memory" and the comparison is lost.
    """

    import gc

    gc.collect()
    try:
        import torch
    except ImportError:  # pragma: no cover - research script
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def run_whisperx(
    audio_path: Path, *, model_name: str, device: str, compute_type: str, batch_size: int
) -> dict[str, Any]:
    import whisperx

    started = time.perf_counter()
    audio = whisperx.load_audio(str(audio_path))
    model = whisperx.load_model(
        model_name, device, compute_type=compute_type, language=LANGUAGE
    )
    # No diarization: the voice track is already the creator's, isolated by the render.
    result = model.transcribe(audio, batch_size=batch_size, language=LANGUAGE)
    asr_seconds = time.perf_counter() - started

    started = time.perf_counter()
    align_model, metadata = whisperx.load_align_model(language_code=LANGUAGE, device=device)
    aligned = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    align_seconds = time.perf_counter() - started
    del model, align_model
    _release_gpu()

    return {
        "asr_seconds": asr_seconds,
        "align_seconds": align_seconds,
        # WhisperX does not put the checkpoint name in `metadata`, so it is read from the
        # table it picked it from. Guessing it would defeat the point of recording it.
        # French resolves to a torchaudio bundle, not a HuggingFace checkpoint, so the torch
        # table is consulted first — that is the order `load_align_model` itself uses.
        "align_model": whisperx.alignment.DEFAULT_ALIGN_MODELS_TORCH.get(
            LANGUAGE, whisperx.alignment.DEFAULT_ALIGN_MODELS_HF.get(LANGUAGE, "unknown")
        ),
        "align_dictionary_size": len(metadata.get("dictionary", {})),
        "segments": [
            {
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": segment.get("text", ""),
                "words": [
                    {
                        "word": word.get("word", ""),
                        # WhisperX omits the keys entirely for a word it could not place.
                        # Keeping `None` is the whole point: an invented timestamp would be
                        # indistinguishable from a measured one downstream.
                        "start": word.get("start"),
                        "end": word.get("end"),
                        "score": word.get("score"),
                    }
                    for word in segment.get("words", [])
                ],
            }
            for segment in aligned["segments"]
        ],
    }


def run_faster_whisper(
    audio_path: Path, *, model_name: str, device: str, compute_type: str
) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    started = time.perf_counter()
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path), language=LANGUAGE, word_timestamps=True
    )
    collected = [
        {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "words": [
                {"word": word.word, "start": word.start, "end": word.end}
                for word in (segment.words or [])
            ],
        }
        for segment in segments
    ]
    del model
    _release_gpu()
    return {
        "seconds": time.perf_counter() - started,
        "duration": info.duration,
        "language_probability": info.language_probability,
        "segments": collected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    import importlib.metadata as metadata

    versions = {}
    for package in ("whisperx", "faster-whisper", "ctranslate2", "torch", "transformers"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "absent"

    whisperx_result = run_whisperx(
        args.audio,
        model_name=args.model,
        device=args.device,
        compute_type=args.compute_type,
        batch_size=args.batch_size,
    )
    native_result = run_faster_whisper(
        args.audio, model_name=args.model, device=args.device, compute_type=args.compute_type
    )

    payload = {
        "setup": {
            "versions": versions,
            "model": args.model,
            "language": LANGUAGE,
            "device": args.device,
            "compute_type": args.compute_type,
            "batch_size": args.batch_size,
            "diarization": False,
            "align_model": whisperx_result["align_model"],
            "peak_gpu_mb": _peak_gpu_mb(),
        },
        "whisperx": whisperx_result,
        "faster_whisper_native": native_result,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    words = sum(len(s["words"]) for s in whisperx_result["segments"])
    native_words = sum(len(s["words"]) for s in native_result["segments"])
    print(f"whisperx segments {len(whisperx_result['segments'])}  words {words}")
    print(f"native   segments {len(native_result['segments'])}  words {native_words}")
    print(f"align model       {whisperx_result['align_model']}")
    print(f"peak gpu MB       {payload['setup']['peak_gpu_mb']}")
    print(f"wrote             {args.out}  (LOCAL ONLY, never committed)")


if __name__ == "__main__":
    main()
