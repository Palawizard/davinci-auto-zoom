# Research notes — measured behaviour and its origins

Date: 2026-08-15, extended each phase.

## How to read this file

It is in two parts, and the split is load-bearing (D068):

**ACTIVE EVIDENCE USED BY THE PRODUCT** — everything from "Blackmagic Design" down to and
including "Phase 8c". Resolve API discoveries, asset reuse, the render and VAD path, the
planner, cut snapping, voice dynamics and ownership. These findings are why the shipping
facecam behaviour is what it is; changing the code without reading them is how a measured
decision gets silently reverted.

**ARCHIVED RESEARCH / NOT USED BY THE PRODUCT** — the "Phase 9a" and "Phase 9b" sections at the
end. Gameplay automation was researched and deliberately not shipped; davinci-auto-zoom is
facecam-only. Those measurements are kept because they explain *why* the feature does not
exist, and because two of them (the `DeleteTrack` renaming behaviour, `ExportFusionComp` as a
read-only measuring instrument) are durable facts about Resolve regardless of what they were
measured for. **Nothing in that part describes code that still exists.**

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

---

## Phase 8 — the multi-level asset family, measured

### The bin changed between Phase 7 and Phase 8

`FACE_X0_SMOOTH` **no longer exists**. Everything above that names it describes the Phase 5-7
bin and is kept as history, not as current fact. `DAVINCI_AUTO_ZOOM` now holds six Generators:

```
FACE_X1    132 frames native   4274cc06-b251-480b-95be-edbf44ca4ebe
FACE_X2    132 frames native   217606f0-c2d2-48ba-9f55-c89ba33f38ed
FACE_X3    132 frames native   f1641e08-2df1-416e-9a1c-36e83df9b3b2
X1_TO_X0    42 frames native   c3cf0e7d-c49a-4df0-aee5-e60fb16bb2c7
X2_TO_X0    42 frames native   5ea45470-93fd-48a6-8bbe-9e40d7d60ea7
X3_TO_X0    42 frames native   846a736b-4af1-447e-8c6f-539038452fd6
```

Native lengths are recorded and used by nothing; D025 is unchanged. All six animate in 15
frames (user metadata, not measured by DAZ — D007).

The three `FACE_X*` assets behave like the old `FACE_X1`: animate, then **hold**. So D014
generalises without modification — a promotion instance is as long as the burst needs it to be,
and 15 frames is only the floor. The three `*_TO_X0` assets behave like the old
`FACE_X0_SMOOTH`: the whole return in 15 frames.

Placement lengths, generalised from the Phase 4 note above:

```
entry / promotion instance : [move, next move or reset)   length >= its own animation
reset instance             : [reset, reset + 15)          exactly the animation, never 42
```

### What `DAZ_OUTPUT_MVP2` says about how a human uses the levels

Full analysis in `.agent/reports/phase-08-mvp2-analysis.txt`. The measurements that are facts
about the material rather than conclusions:

- 35 items on V3, forming **14 zoom cycles**: `FACE_X1` x14, `FACE_X2` x6, `FACE_X3` x1,
  `X1_TO_X0` x8, `X2_TO_X0` x5, `X3_TO_X0` x1;
- every cycle is a run of adjacent clips (gap = 0) terminated by exactly one `*_TO_X0`;
- **no cycle skips a level, descends, or resets with the wrong asset for its level.** The state
  graph D045 defines is the one the editor used, not one imposed on them;
- manual reset instances are 42 frames (the native length), where DAZ places 15. The extra 27
  frames are hold-at-X0 and are visually identical to no clip;
- promotion offsets from the cycle start: 43, 55, 88, 63, 61, 49 frames (x2); 109 (the one x3);
- cycle spans that stayed at x1: 23, 40, 40, 44, 48, 48, 74, 75. Cycle spans that promoted: 87,
  88, 104, 113, 138, 204. **The two sets do not overlap and there is an 11-frame gap.**

### Promotions do not sit on cuts; resets do

Same timeline, same measurement, opposite answers — which is why the two rules differ (D046):

```
manual promotion frames on a hard cut :  1 of 7    (20 cuts in 3555 frames; 1 is chance)
manual reset frames on a hard cut     :  8 of 14
```

Widening the promotion tolerance to +-8 frames adds no further hits.

### The burst count question is settled

The planner finds 14 editorial bursts on `DAZ_INPUT`; `DAZ_OUTPUT_MVP2` has 14 manual cycles,
aligned 1:1 in order. The "x1 over-triggering, 14 planned vs 12 manual" carried since Phase 4
was measured against `DAZ_OUTPUT_MVP`, a looser earlier edit. **Not a defect.** Nothing was
tuned to reach this; the burst logic is byte-identical to Phase 6.

### Not measured

- whether the x2/x3 thresholds transfer to other material. They come from one timeline, and the
  x3 pair from a single clip on it;
- whether a short promotion instance (the shortest DAZ places here is 26 frames) reads as a move
  or as a glitch. That is a viewing question and the Phase 8 preview has not been watched;
- what triggers a gameplay zoom. No reference edit, no asset family, no evidence (D048).

## Phase 8c — what the voice actually does inside a burst (2026-08-18)

Measured on `DAZ_INPUT` A1 against `DAZ_OUTPUT_MVP2`, from one `plan-probe` render. Full
report: `.agent/reports/phase-08c-voice-dynamics-analysis.txt`.

**The finding that made the phase possible.** All seven manual promotions in the reference edit
sit within 8 frames of a detected voice recovery — the moment the short-time energy climbs back
to the speaker's own level after a dip. Six of the seven are within 3 frames, median +1. The
editor is not counting seconds; they are cutting on a breath.

**The finding that constrains every successor.** Speech contains a dip roughly every second.
The 14 cycles contain **63** valleys; the human used 7. Depth and low-span duration do not
separate them at all:

    used     (40 ms, 23.0 dB) … (270 ms, 118 dB)
    unused   (40 ms, 22.6 dB), (50 ms, 23.2 dB), (30 ms, 22.1 dB), …

There is no threshold pair that keeps all 7 and rejects the other 56. What makes the model work
is that the ladder has exactly two rungs and takes the first two *usable* cues; the animation
and hold constraints remove most of the rest, and 17 of the 63 are simply "already at x3".
Anyone tempted to add a fifth threshold should read this paragraph first.

**Relative in dB is not a nicety, it is the whole design.** Re-running the complete 14-cycle
plan on the same audio scaled by ×0.25, ×0.5, ×2 and ×4 produces byte-identical anchors. An
absolute RMS gate would have made the edit a property of the microphone preamp.

**Envelope shape matters less than expected, but not nothing.** Window 20-50 ms and smoothing
20-50 ms all tell the same story; the anchor sets agree on 8-12 of 14 cycles across that range.
30/10/30 ms was chosen because it is central, not because it is uniquely good.

**One real bug, worth remembering.** `numpy.convolve(..., mode="same")` zero-pads, so smoothing
a dBFS curve that way averages the first and last points against an implicit 0 dB — inventing a
loud burst at each end of every render, which is precisely where real bursts start. Edge padding
fixes it. It cost one confusing calibration round.

**Cut alignment, measured per transition class.** Manual x1 starts: 4 of 14 exactly on a hard
cut, with the detected burst start 0-2 frames away, and the nearest non-matching cut 61 frames
away. Manual promotions: 1 of 7 on a cut (chance), offsets otherwise 27-89 frames. Manual
resets: 8 of 14, unchanged since Phase 6. So the entry window is worth having and small
(±120 ms, 8x margin), the promotion window will almost never fire — 1 of 14 cues in the live
plan — and the reset window stays exactly as D034 left it.

---

# ARCHIVED RESEARCH — NOT USED BY THE PRODUCT

Everything below measured the gameplay question. Gameplay automation is retired (D068) and the
modules described here (`domain/gameplay.py`, `domain/visual_episodes.py`, `vision.py`,
`resolve/gameplay_study.py`) were removed from the active tree in Phase 10. The numbers stay
because they are the evidence for the retirement; the code they refer to lives in Git history.

## Phase 9a — measured Resolve behaviour, and the gameplay material

**`DeleteTrack` renumbers *and renames* the survivors.** Studio 21.0.4.5. Deleting audio track
1 to leave A2+A3 produces tracks named `Audio 1` and `Audio 2` at indices 1 and 2. In
`davinci-auto-zoom-test` all three audio tracks carry the same 21 items from the same source
clip, so after the deletion nothing distinguishes them and `_isolate_voice_track`'s
post-condition genuinely cannot prove which track survived. It refused to render, which is the
guard working. The complement is now isolated by **emptying** unwanted tracks with
`DeleteClips` instead (D063) — the dropped tracks end at zero items while every kept track
keeps its index, name and item count, which is stronger evidence than the delete path had.

**`TimelineItem.ExportFusionComp` is a usable read-only measuring instrument.** Exporting the
comp of a manual instance and parsing it with `domain/fusion_comp.py` answered the asset-timing
question exactly, with no timeline mutation. All twelve asset families in this bin keyframe
`Transform1Size` at t=0 and t=15 — including the six gameplay ones, whose Media Pool length is
45 and whose animation is therefore 15 (D062). `X0_TO_GAMEPLAY` and `GAMEPLAY_TO_X0` carry a
single `Path1Displacement` key, i.e. size only and no path travel, which is what you would
expect of the two moves that do not involve the facecam.

**A video render costs about 18 seconds here** through the built-in `H.264 Master` preset, for
3555 frames at 1080p60, versus 5-7 seconds for an audio-only render. `FormatWidth`/
`FormatHeight` were deliberately *not* passed: `SetRenderSettings` is all-or-nothing (D019), so
an untested key risks the whole call, and ffmpeg downscales for free.

**The gameplay labels are not predicted by anything measured, and the ranges nest.** Over 15
would-be-X0 windows on `DAZ_OUTPUT_MVP3`:

    silence duration       gameplay 46-222 frames    X0  55-421     nested
    secondary-audio active gameplay 0-92%            X0  8-81%      nested
    secondary-audio mean   gameplay -22.5..+4.1 dB   X0  -24.1..-4.5 dB   nested
    visual motion (mean)   gameplay 0.21-2.52x       X0  0.10-1.77x nested
    hard cuts in window    flat across both classes

Two individual cases kill any monotone story on their own: gap 0 is fully gameplay with **0%**
secondary-audio activity, and gap 13 is 62% gameplay with the second-*lowest* motion in the
whole timeline. Meanwhile gap 11 stays X0 at 81% audio activity and 1.55x motion.

**What the motion curve does show cleanly is structure unrelated to the labels**: motion
collapses after frame 218767 (0.21x then 0.10x), which is the outro. A real, legible feature of
the delivery — simply not the one that predicts gameplay.

**Gameplay exits are placed on the voice, not on the cut list.** The nine `gameplay_to_face_x1`
exits sit -1 to +41 frames from the next burst start, six of them within a single frame, while
the nearest hard cut is 87-152 frames away in seven of the nine. This is the sharpest
signal-to-decision link found in the project so far, and it is the mirror image of the facecam
resets, which *do* snap to cuts (8 of 14).

**Gameplay entries are not explained by anything.** Three of ten sit exactly on a cut; the
other seven are 23-132 frames from the nearest one. The delay from the start of the silence
window runs 0 to 166 frames with no cluster. Three entries begin *inside* a detected burst, and
one of those (-77 frames) is exactly the burst-1 overrun Phase 8c already recorded — the two
numbers match to the frame, which is independent confirmation that the divergence is the VAD's
burst end and not the editor's taste.

## Phase 9b — the zoom's own geometry, and why frame differences cannot see a game event

**`ExportFusionComp` answers geometry as cleanly as it answered timing.** One instance of each
of the eleven roles present in `DAZ_OUTPUT_MVP3`, exported read-only and parsed. Every comp is
the same five nodes — `Loader -> Transform -> Saver`, `Size` on a `BezierSpline`, `Center` on a
`PolyPath` — and there is **no Crop, no Mask, no Merge and no second Transform anywhere** in
this asset family. The user's whole zoom vocabulary is two numbers per state:

    state       Size    centre offset (Fusion, y up)     shows (source, y down)
    X0          1.00    (0.00, 0.00)                     everything
    GAMEPLAY    1.25    (0.00, 0.00)                     x[0.10,0.90] y[0.10,0.90]
    FACE_X1     1.50    (0.25, 0.25)                     x[0, 0.667] y[0.333, 1]
    FACE_X2     2.00    (0.50, 0.50)                     x[0, 0.5]   y[0.5, 1]
    FACE_X3     2.50    (0.75, 0.75)                     x[0, 0.4]   y[0.6, 1]

Two things worth writing down about the `.comp` format itself, both learned the hard way:

  * the `PolyPath` points are **offsets from the tool's default centre**, not absolute
    coordinates. Reading them as absolute puts `X0` — a state that must be a no-op — at the
    bottom-left corner of the frame, which is how the mistake announces itself. The facecam
    ladder is the check: read as offsets it produces a nested family of corner rectangles
    landing exactly on the facecam inset, which is where the facecam demonstrably is;
  * Fusion's Y axis points **up** and ffmpeg's decoded rows go **down**, so exactly one flip is
    needed, in one place (`Roi.from_transform`). The GAMEPLAY rectangle is symmetric, so the
    flip is invisible there and would have gone unnoticed without the facecam check.

**The gameplay zoom is a centred 1.25x push-in.** It privileges no region; it crops the outer
10% of the frame — which in this delivery is precisely where the HUD lives (weapon wheel top,
ammo bottom-right, title card top-left, kill feed top-right) — and enlarges the rest a quarter.

**Frame differencing measures the camera, not the game.** The single most useful measured fact
of Phase 9b. On 32x18 cells at 10 samples/s, over the 15 would-be-X0 windows:

    gaps 11, 12 — visually EMPTY corridors, the player walking and looking around
                  active cells 64-66%, bbox 0.99-1.00, concentration 0.34-0.39
    gap 10      — an NPC charging the camera, a real and obvious event
                  active cells 64%,     bbox 0.98,      concentration 0.41

The two are indistinguishable because in a first-person game every mouse movement translates
the entire image. Any spatial statistic built on an uncompensated frame difference is therefore
a statistic about the player's hand. This is not a threshold problem and no threshold fixes it;
the fix is to estimate and subtract the global translation first, which is the next experiment
(pure numpy over the same grid, no new dependency).

**Nested ranges, again, and twins.** All nine spatial features have fully nested class ranges
and best-threshold scores of 10 or 11 of 15 against a 10/15 baseline — the same numbers Phase
9a got from completely different features. Z-scored over all nine, each hard negative has a
manual-gameplay window within ~1 sigma: gap 8 <-> gap 11 at 0.92, gap 5 <-> gap 10 at 1.19,
gap 13 <-> gap 14 at 1.87.

**Costs, for planning future experiments.** Decoding the already-rendered program video a
second time on a 32x18 grid costs 3.5 s per minute of timeline; the pure spatial statistics
over 592 samples cost 0.11 s. The Resolve renders the study depends on cost 22 s for the same
minute. Analysis is not the expensive part.

**What the agent's own eyes added that no number did.** Looking at extracted frames of all 15
windows identified the pair that states the target concept: gap 9 (aiming at a small, distant
enemy — magnification genuinely helps) versus gap 10 (an NPC filling the frame — magnification
only crops). The distinguishing property is the *apparent size of a subject*, and nothing in
this pipeline can name a subject. That inspection is research only; DAZ's runtime remains
ffmpeg + numpy + Silero (D065).
