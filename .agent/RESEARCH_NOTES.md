# Research notes — initial supervisor research

Date: 2026-08-15

## Blackmagic Design

Official Blackmagic DaVinci Resolve 20/20.2 material confirms:

- Resolve contains audio transcription / a transcription engine, including Studio-only AI workflows.
- Blackmagic's feature guides refer scripting developers to `Help > Documentation > Developer` for the detailed scripting API.
- Resolve 20.2 added additional scripting API support (for example setting timeline/media-pool clip names and subtitle options in render jobs), illustrating that exact API capability is version-sensitive.

The public feature material does **not** establish a supported scripting method for retrieving transcription text. Phase 1 inspected the installed README and the live API and confirmed there is none (see below).

## Coding-agent workflow

Current official Codex guidance recommends durable repo instructions through `AGENTS.md`, explicit context/constraints/validation, and maintaining plans/documentation for longer tasks. Claude Code similarly supports project instructions and structured, explicit agentic tasks.

This repo keeps those files local/gitignored at the user's request.


## Phase 1 findings — installed documentation (2026-08-16)

Source of truth: `/opt/resolve/Developer/Scripting/README.txt`, "Last Updated: 24 Jul 2026",
shipped with **DaVinci Resolve Studio 21.0.4.5** on Linux. Also present:
`CHANGELOG.txt` (API changes per version, latest entry "21.0 Beta") and 11 official
Python/Lua examples under `Examples/`.

The repository now parses this README directly (`resolve/docs.py`) rather than restating
it, so the capability matrix cannot drift from the installed build. 354 methods across 13
classes are parsed. Notes:

- Section titles are underlined with `---`; class blocks are not. That distinction is what
  separates prose headings from API classes.
- Everything from "Deprecated Resolve API Functions" onward is excluded on purpose.
- A few long signatures (`AddMarker`) wrap across two lines and need a tolerant regex.

### Transcription — negative result

The README documents transcription only as actions: `Folder.TranscribeAudio`,
`MediaPoolItem.TranscribeAudio`, `ClearTranscription`, `PerformAudioClassification`, and
`Timeline.CreateSubtitlesFromAudio`. There is **no** getter for transcript text or
timestamps, and `dir()` on live `Resolve`, `Project`, `MediaPool`, `Folder`,
`MediaPoolItem`, `Timeline` and `TimelineItem` objects confirms none exists at runtime
either.

The `CHANGELOG.txt` entry for 20.2.1 ("TimelineItem.GetName not returning subtitle text
content") shows the only indirect path: create subtitles, then read subtitle-track items'
names. It requires a write and yields captions, not speech onsets. Ruled out for the MVP
(D010).

### Adjustment/zoom assets

Resolve models the user's zoom clips as Media Pool `Generator` items. The README's
`InsertGeneratorIntoTimeline` / `InsertFusionGeneratorIntoTimeline` insert *by generator
name at the playhead*, which would produce a blank clip rather than the user's configured
one. The documented way to place a specific existing asset at a chosen frame and track is
`MediaPool.AppendToTimeline([{clipInfo}])` with `mediaPoolItem` / `trackIndex` /
`recordFrame`. Whether that preserves the attached Fusion composition is undocumented, and
Phase 2 answered it experimentally (below).


## Phase 2 findings — write behaviour (2026-08-16)

Same build, same docs (re-checked: `README.txt` still "Last Updated: 24 Jul 2026",
`CHANGELOG.txt` still ending at "21.0 Beta"), so Phase 1 conclusions were not invalidated.

### `AppendToTimeline` with a Generator clipInfo — undocumented details now measured

The README documents the *shape* of `{clipInfo}` but not the semantics of its frame fields.
Measured on Studio 21.0.4.5, for both `FACE_X1` and `FACE_X0_SMOOTH`:

- `recordFrame` is an **absolute** timeline frame (216200 in, `GetStart() == 216200` out),
  not an offset from `GetStartFrame()`;
- `endFrame` is **exclusive**: the resulting duration is exactly `endFrame - startFrame`,
  proven by the adjacent pair 132 → 132 frames and 131 → 131 frames;
- `trackIndex` is honoured exactly, confirmed via `GetTrackTypeAndIndex()`;
- a Generator instance may be **shorter or longer than the Media Pool asset's native frame
  count** — 141 frames from a 132-frame asset worked, which independently explains the
  141-frame human instance observed in Phase 1;
- the returned list holds exactly one `TimelineItem` per clipInfo.

### The Fusion composition travels with the instance

`GetFusionCompCount() == 1` was treated as necessary but not sufficient. The stronger test
compares `ExportFusionComp` output of a new instance against that of a known-good *manual*
instance in `DAZ_OUTPUT_MVP`. Exported `.comp` files are Lua-like tables; the meaningful
fields are the tool declarations (`Name = Class {`), `SourceOp` wiring, and `BezierSpline`
`KeyFrames` blocks.

For every duration tested the tool graph, the wiring and the keyframe values *and handles*
were identical, and the only textual differences were length fields (`RenderRange`,
`GlobalRange`, `GlobalOut`, `MEDIA_NUM_FRAMES`, `MEDIA_MARK_OUT`, `Length`, `TrimOut`, …)
plus session-volatile ones (`CurrentID`, `Version`, cache paths, audio track names).

Two traps worth recording:

1. **`ExtentSet = true`** appears on the Loader only when the clip's extent was set
   explicitly rather than left at the asset's natural length. Instances requested at the
   native duration omit it; trimmed/extended ones have it. It is length bookkeeping, not
   effect data.
2. Comparing two comps **line-by-line positionally is wrong**: comps of the same effect can
   differ in line *count* (see `ExtentSet`), which makes every following line look
   different. `difflib.SequenceMatcher` over the lines is the correct tool.

### Keyframes are anchored, not rescaled

In all six instances (132, 131, 87, 141, 10 and 42 frames) the keyframes stayed at absolute
frames **0 and 15**. Resolve does not rescale, stretch or re-time a generator's animation
when the instance length changes: longer instances hold the final value, and an instance
shorter than the animation is truncated mid-move. This is the constraint behind the
planner's minimum zoom duration (D014).

### Changelog workaround: not needed (Phase 2)

The 20.3.2 entry "AppendToTimeline failure when no media pool clip is selected" describes a
bug that is fixed on 21.0.4.5: six insertions succeeded with no Media Pool selection or
current-folder change. The probe keeps a one-shot retry that only fires after a genuine
failure, so the historical workaround never becomes a silent permanent dependency.


## Phase 3 findings — rendering and VAD (2026-08-16)

Same build (Studio 21.0.4.5), same installed docs.

### Silero VAD — upstream check before implementing

Checked against the official repository (`snakers4/silero-vad`) rather than a blog post:

- current release **v6.2.1**, published 2026-02-24, titled "Make ONNX Runtime optional";
- licence **MIT**, so redistribution is allowed with the notice attached;
- the ONNX graphs live in `src/silero_vad/data/`: `silero_vad.onnx` is the opset-16 model
  that accepts both 8 kHz and 16 kHz; `silero_vad_16k_op15.onnx` is 16 kHz only;
- the PyPI package `silero-vad` **hard-depends on `torch>=1.12` and `torchaudio>=0.12`**
  even for the ONNX path (`onnxruntime` is only an extra), which is why the package is not
  used as a dependency (D022).

Inference contract, read off the graph with `onnxruntime` 1.28.0 rather than assumed:

| Tensor | Direction | Shape | dtype |
| --- | --- | --- | --- |
| `input` | in | `(batch, 64 + 512)` — context then window | float32 |
| `state` | in | `(2, batch, 128)` | float32 |
| `sr` | in | scalar | int64 |
| `output` | out | `(batch, 1)` probability | float32 |
| `stateN` | out | `(2, batch, 128)` | float32 |

At 16 kHz the window is fixed at 512 samples (32 ms) with 64 samples of carried context.
Digital silence scores ~0.0017, so the model is not biased towards speech on empty input.

Official defaults (`get_speech_timestamps`): `threshold=0.5`, `min_speech_duration_ms=250`,
`min_silence_duration_ms=100`, `speech_pad_ms=30`, and a negative/exit threshold of
`threshold - 0.15`. Those are the values shipped in `config.example.toml`, unmodified.

Our port drops `max_speech_duration_s` and its silence bookkeeping: nothing wants a speech
segment force-split at an arbitrary length, and it is the most intricate third of the loop.

### Audio-only rendering — what the API actually allows

The Deliver page is the awkward part, because `SetRenderSettings` is write-only.

- `GetRenderFormats()` lists `Wave -> wav`, but **`SetCurrentRenderFormatAndCodec("wav", …)`
  returns `False`** for every codec string tried (`""`, `"lpcm"`, `"LinearPCM"`,
  `"linearPCM"`), and `GetRenderCodecs("wav")` returns `{}`. The wav format cannot be
  selected through that call.
- The built-in **`Audio Only`** render preset works instead. After loading it,
  `GetCurrentRenderFormatAndCodec()` reports `{'format': 'unknown', 'codec': ''}` — the
  audio-only state simply has no video format. Its presence is checked in preflight rather
  than assumed, since presets are installation state.
- Because the produced extension is not knowable in advance, the probe renders into an empty
  directory it owns and takes whatever single file appears. On this project that was a
  17 MB `.wav`.
- `GetQuickExportRenderPresets()` returns only video presets (`H.264 Master`, `HyperDeck`,
  `H.265 Master`, `ProRes 422 HQ`, `Presentations`), so `RenderWithQuickExport` — which
  would have bypassed the render queue entirely — is not usable for audio.
- **`Project.GetRenderCodecs(format)` is not safely read-only**: the current render format
  changed during exploration around calls to it. Not used outside investigation.
- Project settings (`Project.GetSetting()` with no argument, 158 keys) contain **no** Deliver
  render settings, so they are no help for snapshot/restore. Hence the preset round-trip
  (D020).

### Three failures worth recording, because each one cost a run

1. `SetTrackEnable` returns `True` and changes nothing readable (D017) — isolation switched
   to `DeleteTrack` on the scratch, whose effect *is* observable.
2. `StartRendering([job], isInteractiveMode=False)` hung the whole application (D018). The
   positional overload `StartRendering(job)` is used instead. After the hang, reads still
   worked but every setter returned `None` and `GetCurrentPage()` returned `None` — that
   combination is the signature of a modal dialog waiting in the GUI.
3. `ReplaceExistingFilesInPlace` is documented but rejected, and `SetRenderSettings` fails
   the entire dict for one bad key (D019).

### Render Path Inaccessible

`AddRenderJob()` returned an empty string with no explanation. The GUI was showing:

> **Render Path Inaccessible** — Please select a render path from within the media storage.

Resolve only renders into its configured Media Storage. `resolve.GetMediaStorage()
.GetMountedVolumeList()` returned `['/home/palawi/Vidéos', '/home/palawi', '/mnt/nas']`;
`/tmp` is not among them, which is why every attempt to render into a system temp directory
failed — and why the first attempt hung, since the same condition raises a modal dialog.

The probe now creates `DAZ_RENDER_TMP_<uuid8>` inside the first writable mounted volume,
moves the rendered file out into its own private temp directory immediately, and deletes the
directory in cleanup. Verified afterwards: no `DAZ_RENDER_TMP_*` remains on any volume.

### Measured results on `DAZ_INPUT` A1

Timeline `[216000, 219555)` = 3555 frames at 60 fps, 48 kHz.

| Stage | Result |
| --- | --- |
| Resolve render | 5.0 s, 17 065 588 bytes |
| ffmpeg normalize | 0.05 s → 948 000 samples, 16 kHz mono |
| Duration check | expected 59.250 s, got 59.250 s, **delta 0.000 frames** |
| Model load | 0.03 s |
| Inference | 0.14 s for 59 s of audio (~420× real time on CPU) |
| Segments | 15, 1441 frames of speech = 24.02 s = **40.5%** |
| Durations | min 30 / median 93 / max 250 frames |
| Gaps | min 17 / median 114 / max 222 frames |

Threshold stability, re-running only the post-processing on the same probabilities:

| threshold | segments | speech ratio | matched | boundary shift min/med/max (samples) |
| --- | --- | --- | --- | --- |
| 0.40 | 15 | 40.7% | 15 | 0 / 0 / 2048 |
| 0.50 | 15 | 40.1% | 15 | 0 / 0 / 0 |
| 0.60 | 15 | 39.8% | 15 | 0 / 0 / 512 |

Same segment count and near-identical boundaries across the range — the default sits on a
plateau, not a cliff. 0.5 stays, unchanged from Silero's recommendation.

Reproducibility: running `speech-file` on the retained normalized WAV, with no Resolve
session involved, reproduced all 15 segments identically.

---

## Phase 4 — what the keyframe evidence actually means for the planner

The Phase 2 measurement (keyframes at absolute frames **0 and 15** in every instance, whatever
its length) was correct, but it was being read as "an instance must be at least as long as the
asset". It does not say that. It says:

- the asset's **animation** is 15 frames long, for both `FACE_X1` and `FACE_X0_SMOOTH`;
- an instance longer than 15 frames **holds** the final value for the remainder;
- an instance shorter than 15 frames is truncated mid-move;
- the asset's **native Media Pool length** (132 / 42 frames) is unrelated to either, and is a
  planner input nowhere.

Hence the two rules the planner really enforces (D025):

```
facecam_x1 instance : [x1_start, x0_start)     length >= 15, otherwise the cycle is dropped
reset_x0 instance   : [reset, reset + 15)      exactly the animation, never 42
```

### Distance from a burst end to the next hard cut, on `DAZ_INPUT`

Measured after the live Phase 4 run, for the 14 editorial bursts against the 20 hard cuts on
V1 (frames):

```
+35 +199 +122 +54 +382 +273 +97 +53 +180 +205 +97 +289 +360 +352
```

Minimum 35 frames (583 ms), median ~190. The MVP `cut_snap_window_ms = 350` is 21 frames at
60 fps, so **not one reset snapped**. Two readings are possible — the window is too small for
this edit, or this edit simply does not cut right after speech — and distinguishing them needs
more than one timeline. Deliberately not tuned: the human reference is taste, not a target.
