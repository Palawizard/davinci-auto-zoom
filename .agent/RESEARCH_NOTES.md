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

**Phase 6 resolved this, and both readings were wrong.** Measuring the *previous* cut as well
as the next one showed the edit cuts right *before* the end of speech, not after: 8 of the 14
bursts have a hard cut 4-7 frames before `burst.end`, and the human edit's reset sits exactly
on it in every one of those cases. The forward-only measurement above could not see them.

### Distance from a burst end to the *previous* hard cut, on `DAZ_INPUT`

```
 -7 -151 -228   -4  -30 -139   -4 -148   -4   -6 -114   -4   -5   -7
```

Eight values in `[-7, -4]`, then a gap to -30 and -114. The signal/noise separation is about
4x, which is what makes a 7-frame (120 ms) lookback safe rather than a tuned constant. Full
table, including the manual-reference column, in
`.agent/reports/phase-06-cut-offset-diagnostic.txt`; the rule itself is D034.

The lesson is methodological: a one-sided measurement answered "how far to the next cut?"
confidently and hid the fact that the interesting cut was on the other side. When a detector
boundary is compared against an editorial one, measure both directions before concluding.

---

## Phase 5 — why a fingerprint, and what it can honestly prove

The Phase 4 `PlanSource` recorded the project name, the timeline name, its unique id, the
frame range, the frame rate, three track indices, the asset mapping and the planner settings.
Every one of those can stay identical while the plan silently rots:

| The user does | name | unique id | range | duration | plan still correct? |
| --- | --- | --- | --- | --- | --- |
| re-cuts V1 (same total length) | same | same | same | same | **no** — snap targets moved |
| slips a clip on A1 | same | same | same | same | **no** — speech moved |
| trims a sentence and closes the gap | same | same | same | same | **no** |
| adds an overlay on V2 | same | same | same | same | yes |
| toggles a track's enable state | same | same | same | same | yes |

The first three must fail; the last two must not. That is exactly the line the fingerprint
draws: hash the two tracks the planner actually reads (voice audio, cut reference) plus the
range and rate, and nothing else (D030).

What it cannot see, stated plainly rather than buried: anything that changes the *sound* or
the *look* without moving a clip. A Fairlight level or EQ change on A1 alters what the VAD
would hear and leaves the fingerprint identical; the same is true of an OFX change on a V1
clip. The scripting API offers no handle on either, so a fingerprint that claimed to cover
them would be a lie. The honest statement is "the observable structure is unchanged".

Sorting the items before hashing matters more than it looks: `GetItemListInTrack` gives no
documented ordering guarantee, so hashing the raw sequence would produce spurious mismatches
that teach a user to skip the check — the worst possible outcome for a safety mechanism.

## Phase 5 — why the MVP refuses a populated target track instead of learning to overlap

It was tempting to spend the phase characterising Resolve's collision behaviour, the way
Phase 2 characterised `endFrame`. It would have been the wrong spend:

- to find out, one has to *cause* a collision on a real timeline, and the only honest way to
  read the result is to look at what happened to the clip that was already there;
- the MVP does not need the answer. It writes to a dedicated zoom track, and a dedicated
  zoom track that already has clips on it is either not dedicated or holds a previous run's
  output — and replacing a previous run's output requires ownership, which does not exist
  yet;
- an answer obtained now would have to be re-established anyway once ownership decides
  *which* clips may be replaced.

So the rule is: empty track or refuse (D032). The unknown is recorded, not papered over.

## Phase 5 — why a successful preview is kept

Every earlier write-capable path deleted everything it made, and that was right: they were
probes, and a probe that leaves debris in a user's project is a bad probe. `apply-preview` is
the first path whose *output is the point*. Deleting a correct preview in the `finally` block
would make the command unable to deliver the only thing it exists to produce.

The asymmetry is therefore deliberate and narrow: kept **only** when every insertion and the
whole-track comparison passed; deleted in every other case; and in both cases the timeline
the user had open is restored, so success does not hijack their session (D031).

## Phase 5 — what the live run actually taught us

The executor was written entirely against a fake and then run against Resolve Studio 21.0.4.5
for the first time on 2026-08-16. It passed on the first attempt, which is a result worth
recording honestly in both directions.

**No live-vs-fake divergence was found.** `DuplicateTimeline`, `AddTrack` + `GetTrackCount`,
sequential `AppendToTimeline`, `SetCurrentTimeline` and the `TimelineItem` getters
(`GetStart` / `GetEnd` / `GetDuration` / `GetTrackTypeAndIndex` / `GetFusionCompCount`) all
behaved as modelled. 28 insertions landed frame-exact, in order, on V3. The `endFrame`
semantics established in Phase 2 (D013) held: `endFrame` exclusive, equal to the duration,
`recordFrame` absolute.

**The one thing only a live run could prove: the Fusion composition survives insertion.**
Every created `FACE_X1` / `FACE_X0_SMOOTH` instance reported `GetFusionCompCount() == 1` and
`GetFusionCompNameList() == ['Composition 1']`. The whole product rests on that — the zoom
behaviour lives in the comp, not in the clip — and until this run it had only ever been
asserted against a fake that was written to say so.

**What the run does *not* prove.** It confirms structure, timing and identity. It says nothing
about whether the zooms look right: no frame was rendered, no pixel inspected. That is why the
preview is kept rather than deleted, and why the report ends by naming it for human review
instead of claiming the edit is good.

Worth noting for later phases: the counts (15 segments → 14 bursts → 28 placements) reproduced
the Phase 4 baseline exactly, which is what made it safe to apply. A materially different plan
from the same input would have been a reason to stop and diagnose, not to write.

## Phase 7 — TimelineItem markers as ownership (Studio 21.0.4.5)

Measured by `probe-ownership` on a scratch duplicated from `DAZ_INPUT`, with real
`FACE_X1` / `FACE_X0_SMOOTH` **Generator** items. Full output:
`.agent/reports/phase-07-ownership-probe-report.txt`.

### What the installed README says

`TimelineItem` carries the same marker block as `Timeline`, `MediaPoolItem` and `Folder`:

```
AddMarker(frameId, color, name, note, duration, customData)  --> Bool
GetMarkers()                                                 --> {markers...}
GetMarkerByCustomData(customData)                            --> {markers...}
UpdateMarkerCustomData(frameId, customData)                  --> Bool
GetMarkerCustomData(frameId)                                 --> string
DeleteMarkerAtFrame(frameNum) / DeleteMarkerByCustomData(customData)
```

None of them appear in the Deprecated or Unsupported sections. `customData` is documented as
"not exposed via UI and is useful for scripting developer to attach any user specific data to
markers" — which is precisely the intended use.

`GetMarkers()` returns `{frameId -> {...}}` with **float** keys (`96.0`) and float durations,
and the frame is **item-local** ("clip offset 96"), not a timeline frame.

### What is actually true, measured

| question | answer |
| --- | --- |
| do markers work on Generator items? | **yes** |
| instance-scoped or asset-scoped? | **instance.** No other `FACE_X1` in the project acquired metadata |
| does `customData` round-trip? | **yes**, byte-identical (~230 chars of JSON) |
| does it survive a timeline switch + re-read? | **yes**, classification identical before and after |
| can two markers share a local frame? | **no** — `AddMarker` returns False on an occupied frame |
| does `DuplicateTimeline` copy them? | **yes** — 2/2 tagged items kept their records |
| does the duplicate look owned? | **no** — the copied records still name the source preview, so the copy classifies `stale` (D038) |

The occupied-frame behaviour is why local frame 0 is preferred but never assumed: a user
marker there makes `AddMarker(0, ...)` fail outright. DAZ reads the existing markers, picks the
first free local frame (counting a marker's duration as occupied), and fails the item closed if
there is none — relevant for a 15-frame reset.

### `DeleteClips` only acts on the current timeline

**Undocumented.** The README gives `Timeline.DeleteClips([timelineItems], Bool)` with no
precondition. Measured on a disposable duplicate:

```
timeline NOT current  -> DeleteClips([1], False)  -> False   V3 still 28 items
timeline made current -> DeleteClips([1], False)  -> True    V3 27 items
timeline made current -> DeleteClips([27], False) -> True    V3 0 items
```

Same restriction as `MediaPool.AppendToTimeline`, which the README *does* describe as acting on
"the current timeline". Found by the first live `clean-preview` failing safely (D042).

Batching 27 items in one call worked. No upper bound was probed.

### Not measured

- maximum `customData` length, or maximum markers per item;
- whether `UpdateMarkerCustomData` / `DeleteMarkerByCustomData` behave on Generator items — DAZ
  never calls them (D036), so they were deliberately left untested;
- whether marker metadata survives a project close/reopen. Asset `unique_id`s do (Phase 6), and
  timeline `unique_id`s appear to, but the markers themselves were only tested within one
  session and across a timeline switch.
