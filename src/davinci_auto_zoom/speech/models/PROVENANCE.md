# Vendored Silero VAD model

`silero_vad.onnx` is **not** written by this project. It is redistributed verbatim from the
official Silero VAD repository.

| Fact | Value |
| --- | --- |
| Upstream project | [snakers4/silero-vad](https://github.com/snakers4/silero-vad) |
| Version | `v6.2.1` (released 2026-02-24) |
| Source path | `src/silero_vad/data/silero_vad.onnx` |
| Exact URL | `https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx` |
| Size | 2 327 524 bytes |
| SHA-256 | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` |
| ONNX opset | 16 |
| License | MIT — full text in `LICENSE-silero-vad.txt` |
| Copyright | © 2020-present Silero Team |

## Why the file is vendored rather than downloaded

- The upstream PyPI package `silero-vad` hard-depends on `torch` and `torchaudio`
  (~2 GB installed). This project only needs the ONNX graph, which `onnxruntime` runs on
  CPU in a few megabytes of dependencies.
- A vendored file pins the model by construction. There is no floating `master` reference,
  no first-run network call, no cache directory to manage, and no "works on my machine,
  fails on the build agent because it is offline" failure mode on Windows/Linux/macOS.
- The MIT license explicitly permits redistribution, provided the copyright notice and
  permission notice travel with the file — which is what `LICENSE-silero-vad.txt` is for.

The checksum above is asserted at load time (`speech/silero.py`), so a corrupted or swapped
model is a loud error rather than silently different speech segments.

## Inference contract (measured against onnxruntime 1.28.0)

| Tensor | Direction | Shape | dtype |
| --- | --- | --- | --- |
| `input` | in | `(batch, 64 + 512)` — 64 context samples followed by one 512-sample window | float32 |
| `state` | in | `(2, batch, 128)` — carried between windows, zeroed at the start | float32 |
| `sr` | in | scalar | int64 |
| `output` | out | `(batch, 1)` speech probability | float32 |
| `stateN` | out | `(2, batch, 128)` | float32 |

At 16 kHz the window is fixed at 512 samples (32 ms) and the context at 64 samples; the
exported graph accepts no other window size.

## Updating

Changing the model is a behaviour change, not a chore. Replace the file, update the version
and SHA-256 here and in `speech/silero.py`, re-run the live `speech-probe` on real material,
and record the before/after segment counts in `.agent/HANDOFF.md`.
