# Decision log

## D001 — External Python core

**Status:** accepted for scaffold

Use a Python application/package as the core rather than beginning with a Workflow Integration UI.

Reasons: Resolve scripting affinity, cross-platform potential, simple CLI validation, unit-testability, and clean separation from UI.

## D002 — Integer frame timebase

**Status:** accepted

All internal timeline positions and ranges use integer frames. Milliseconds/seconds are converted at boundaries.

## D003 — Planner cannot call Resolve

**Status:** accepted

All speech-to-zoom decisions belong to pure domain code. Resolve is an adapter/executor only.

## D004 — Speech activity first, full transcription optional

**Status:** accepted for MVP unless Phase 1 reveals a superior supported Resolve-native path

The first requirement is detecting when a single-speaker voice-only track is active. Do not pay the cost/complexity of full speech-to-text unless text is required or it materially improves segmentation.

## D005 — Installed Blackmagic docs are canonical

**Status:** accepted

The coding agent must inspect the Developer/Scripting documentation installed with the user's exact Resolve build before implementing methods. Third-party API mirrors/examples may be clues, not authority.

## D006 — No real writes before capability + dry-run phases

**Status:** accepted

Timeline mutation is deferred until insertion, ownership, collision, cleanup, and asset behavior are understood on a throwaway/duplicate timeline.

---

Decisions below were made during Phase 1 and are backed by observations against
DaVinci Resolve Studio 21.0.4.5. See `.agent/HANDOFF.md` for the raw evidence.

## D007 — User zoom assets are opaque referenced assets, never reconstructed

**Status:** accepted — now evidence-backed

The user's `FACE_X1` / `FACE_X0_SMOOTH` are Media Pool items of `Type: Generator`. Their
behaviour lives entirely inside a Fusion composition (a keyframed `Transform` driven by
Bezier splines); `TimelineItem.GetProperty()` on a placed instance returns *all default*
transform values, so the effect is invisible to the transform API.

DAZ therefore references assets and never inspects, rebuilds, or edits their contents.
This also means the tool stays agnostic to how a user builds a zoom (Transform, Fusion,
OFX, plugins), which is required for it to work across editing styles.

## D008 — Zoom instances are recognised by name, not by asset link

`TimelineItem.GetMediaPoolItem()` returns `None` for generator instances, so Resolve
provides no link from a placed zoom clip back to its Media Pool asset. Recognition is by
`TimelineItem.GetName()` matched against the configured role→name mapping.

For write phases, `TimelineItem.GetUniqueId()` is stable per instance and is the ownership
key; `SetName` and `AddMarker`/`UpdateMarkerCustomData` are documented additional tagging
options to evaluate then.

## D009 — Track enable/lock state is only trusted for the current timeline

`GetIsTrackEnabled` and `GetIsTrackLocked` return `False` for every track of any timeline
that is not the current one. The snapshot models this as `None` (unknown) rather than
recording a false negative. Any future logic that depends on enable state must either
operate on the current timeline or treat the value as unknown.

## D010 — Speech detection uses an external provider; Resolve transcription is not readable

Supersedes the open question in D004. The installed API exposes transcription only as
write actions (`TranscribeAudio`, `CreateSubtitlesFromAudio`, `ClearTranscription`) with
**no getter for transcript text or timestamps**. The sole indirect read path — creating a
subtitle track and reading `TimelineItem.GetName()` on subtitle items — requires mutating
the timeline, depends on Studio/AI packages, and yields caption sentences rather than
speech onsets.

The MVP needs voice-activity / silence segmentation on a voice-only track, not
speech-to-text. `SpeechProvider` implementations stay external. A `resolve_transcript`
provider is explicitly **not** viable and should not be attempted.

## D011 — The voice track is configuration, not discovery

In a realistic project every audio track is named `Audio N` and carries the same source
clip, differing only in `GetSourceAudioChannelMapping()` channel indices. Resolve offers
no metadata identifying a voice track. `voice_audio_track` is therefore a required
1-based index in config, with track-name matching as an optional convenience.

## D012 — The tool's output is one dedicated video track

`DAZ_OUTPUT_MVP` is exactly `DAZ_INPUT` plus one extra video track carrying the zoom
clips; every other track is identical. This confirms the intended write surface: DAZ
creates/populates a single configured zoom video track and never touches existing tracks.

---

Decisions below were made during Phase 2 and are backed by a reproducible write probe run
against DaVinci Resolve Studio 21.0.4.5. Raw evidence:
`.agent/reports/phase-02-write-probe-report.txt`.

## D013 — Asset reuse is `MediaPool.AppendToTimeline([{clipInfo}])`, and it preserves the effect

**Status:** accepted — proven experimentally

Placing a new instance of a user's zoom asset is done with a single documented call:

```python
media_pool.AppendToTimeline([{
    "mediaPoolItem": <the Media Pool Generator item>,
    "startFrame": 0,
    "endFrame": <duration>,
    "trackIndex": <1-based video track>,
    "recordFrame": <absolute timeline frame>,
}])
```

Observed semantics, all verified on 21.0.4.5 for both `FACE_X1` and `FACE_X0_SMOOTH`:

| Question | Answer |
| --- | --- |
| Does it accept Generator MediaPoolItems? | **Yes**, one `TimelineItem` returned per clipInfo. |
| `recordFrame` | **Absolute timeline frame**, not relative to `GetStartFrame()`. Requested 216200 → `GetStart() == 216200`. |
| `trackIndex` | Honoured exactly; `GetTrackTypeAndIndex()` returns the requested video track. |
| `startFrame` / `endFrame` | `startFrame` inclusive, **`endFrame` exclusive**: `duration == endFrame - startFrame`. Requesting `endFrame = 132` yields 132 frames, `131` yields 131. |
| Asset name | Preserved: `TimelineItem.GetName()` is the Media Pool clip name. |
| Fusion composition | **Preserved.** `GetFusionCompCount() == 1`, named `Composition 1`, and the exported comp matches the user's manual instance. |
| Duration vs native length | Any duration works. 132 (native), 131, 87, 10 and **141** frames all produced exactly the requested duration, so an instance may be shorter *or longer* than the asset's native frame count. |
| Media Pool selection workaround | **Not needed** on 21.0.4.5 (the 20.3.2 changelog fix holds). The probe keeps a one-shot retry that only triggers after a real failure. |

No fallback was needed. `ExportFusionComp`/`ImportFusionComp` and
`InsertGeneratorIntoTimeline` remain unused; do not add them without new evidence.

This supersedes the "asset insertion is unknown" status in D006/D007: DAZ references the
user's asset and never rebuilds it, and that is now a demonstrated capability rather than
an intention.

## D014 — Instance keyframes are anchored to the clip head and are never rescaled

**Status:** accepted — proven experimentally

The proof that the *user's* effect (not a generic empty comp) survives insertion is a
structural comparison of exported `.comp` files against a known-good manual instance in
`DAZ_OUTPUT_MVP`: tool names/classes, wiring and keyframe values/handles are identical for
every duration tested; only length fields differ (`RenderRange`, `GlobalRange`,
`MEDIA_NUM_FRAMES`, `MEDIA_MARK_OUT`, …, plus `ExtentSet`, which Resolve adds whenever the
extent is set explicitly rather than left at the asset's natural length).

Critically, the keyframes stay at absolute frames **0 and 15** in *every* instance —
132, 131, 87, 141 and 10 frames alike. Therefore:

- the animation is **not** rescaled to the clip length,
- it is **not** stretched or hold-compressed,
- a longer instance holds the final value after the move completes,
- an instance **shorter than the asset's own animation is truncated mid-move**.

Planner consequence: `min_zoom_ms` must be at least the asset's own animation length
(15 frames = 250 ms at 60 fps for `FACE_X1`), otherwise the zoom never reaches its target.
This is a property of the user's asset, not of DAZ, so it must not be hardcoded; the
comparison in `domain/fusion_comp.py` can read the keyframe extent when it matters.

**Not established by the API:** the rendered pixels. The evidence is structural
(identical Fusion graph and splines). A human visual confirmation on a scratch timeline is
still the only way to certify what the frames look like.

## D015 — Write-capable code is fail-closed; read-only code may warn

**Status:** accepted

Read-only commands (`snapshot`, `assets`, `compare`) keep reporting a project/timeline
mismatch as a warning. Any write-capable entry point must instead refuse to start:
`domain.probe.preflight_failures` returns every violated guard and the caller raises
before the first mutating call. The probe additionally requires an explicit
`--confirm-resolve-write-test` flag and explicit `--project` / `--source-timeline` /
`--reference-timeline` arguments, so a write can never be triggered by an ambient config.

Cleanup is transactional: `try/finally` restores the previously current timeline, then
deletes only a timeline this run created (name must carry the run's `DAZ_SCRATCH_` prefix).
If deletion fails, the probe reports the exact name and deletes nothing else.

## D016 — `TimelineItemSnapshot.probable_kind` is a heuristic label, not a type

**Status:** accepted

"No media pool item and no source frames" is how the observed zoom generators look, but
Resolve documents no guarantee that nothing else shares that shape. The property was
renamed from `kind` to `probable_kind` (`likely-generator`) and is for reporting only.
Ownership and recognition of DAZ's clips rely on the dedicated zoom video track plus the
configured role names (D008), never on this heuristic.

---

Decisions below were made during Phase 3 and are backed by a reproducible speech probe run
against DaVinci Resolve Studio 21.0.4.5.

## D017 — Voice-track isolation deletes tracks on a scratch; `SetTrackEnable` is unusable

**Status:** accepted — the enable API was tried first and measured to be unverifiable

The obvious way to render one audio track in isolation is to disable the others. On
21.0.4.5 that cannot be *confirmed*:

| Call | Result |
| --- | --- |
| `Timeline.SetTrackEnable("audio", 2, False)` | returns `True` |
| `Timeline.GetIsTrackEnabled("audio", 2)` afterwards | still `True` |
| same for video tracks | identical behaviour |
| `GetIsTrackEnabled` on a freshly duplicated, non-current timeline | `True` |

So the getter neither reflects the setter nor follows the "False for non-current timelines"
rule from D009 — it is simply not a trustworthy readback in either direction. Rendering
audio whose isolation cannot be verified is worse than not rendering: the tool would
silently analyse the game mix as if it were the creator's voice, and every downstream
speech segment would be wrong for a reason nothing in the report could reveal.

**`Timeline.DeleteTrack(trackType, trackIndex)` is used instead**, because its effect is
observable: `GetTrackCount("audio")` drops, and the surviving track's name and item count
can be matched against what was captured beforehand. Measured: 3 audio tracks → delete
indices 3 and 2 → count 1, surviving track still `Audio 1` with its 21 items, timeline
frame range unchanged, and `DAZ_INPUT` still reporting 3 audio tracks afterwards.

This is only ever done on a `DAZ_AUDIO_SCRATCH_*` duplicate the run creates and deletes.
Deleting a track on a user timeline is destructive and is never done anywhere.

Tracks are deleted in **descending** index order so that removing one never renumbers
another still queued for removal, and the surviving voice track is re-checked by name — a
voice track at A2 correctly ends up as the single remaining track after A3 and A1 go.

## D018 — `StartRendering` is called with the positional overload

**Status:** accepted — the keyword form hung the application

`project.StartRendering([job_id], isInteractiveMode=False)` — the documented list form with
its documented keyword — hung indefinitely through the Blackmagic C bridge. The application
was left in a state where reads still worked but **every** setter returned `None` and
`Resolve.GetCurrentPage()` returned `None`, which is what an open modal dialog looks like
from the scripting side. Recovery required a human dismissing the dialog in the GUI.

The project therefore uses the plain varargs overload, `StartRendering(jobId)`, which takes
no keyword argument. General rule: prefer the Blackmagic overload with no keyword arguments;
the bridge is a C wrapper, not a Python function.

Two consequences are now built into the probe:

- the poll loop gives up after `STALL_GRACE_SECONDS` (30 s) if the job is neither terminal
  nor rendering, instead of spinning until the overall timeout, and says in its error that
  a modal dialog in the Resolve window will cause exactly this;
- cleanup is in `finally`, so an interrupt still restores the Deliver page, the current
  timeline and the scratch — which is what happened during the incident. Only the temporary
  render preset survived a `SIGKILL`, and loading/deleting it manually restored the Deliver
  page exactly (`mov`/`ProRes422HQ` back), which is independent evidence that the preset
  round-trip in D020 works.

## D019 — `SetRenderSettings` is all-or-nothing, and `ReplaceExistingFilesInPlace` is rejected

**Status:** accepted — measured key by key

`SetRenderSettings({...})` returns `False` for the whole dictionary if a single key is not
accepted, and reports nothing about which one. Bisecting the probe's dict on 21.0.4.5:

| Key | Accepted |
| --- | --- |
| `SelectAllFrames`, `TargetDir`, `CustomName` | ✔ |
| `ExportVideo`, `ExportAudio` | ✔ |
| `AudioBitDepth`, `AudioSampleRate` | ✔ |
| `ExportSubtitle`, `UseFullExtents`, `AddFrameHandles` | ✔ |
| **`ReplaceExistingFilesInPlace`** | **✘ returns `False`** |

It is documented in the installed README's "Looking up Render Settings" section, which is a
reminder that the installed docs describe the API surface, not which parts of it this build
honours. The key is not set; the render target is a fresh empty temporary directory, so
there is never an existing file to replace.

## D020 — Deliver-page state is snapshotted through a temporary render preset

**Status:** accepted

`SetRenderSettings` is write-only: the API exposes no getter for target directory, filename,
export flags or audio settings. The only readable Deliver state is
`GetCurrentRenderFormatAndCodec()` and `GetCurrentRenderMode()`.

Since `AddRenderJob()` renders "based on current render settings", there is no way to queue
a job without changing that state. The state is therefore captured the only documented way
it can be: `SaveAsNewRenderPreset("DAZ_RENDER_RESTORE_<uuid8>")` before, then
`LoadRenderPreset(...)` + `DeleteRenderPreset(...)` after, plus an explicit restore of the
readable format/codec/mode and a verification of both.

Measured round-trip: `mov`/`ProRes422HQ`, mode 1 → load `Audio Only` → restore → back to
`mov`/`ProRes422HQ`, mode 1, with the preset list byte-identical to before.

Anything the probe cannot verify is **reported**, not assumed: `DeliveryState.unrestored`
lists every field that did not come back, the run is marked unclean, and the report says so
in both text and JSON. If `SaveAsNewRenderPreset` fails, the report says only
format/codec/mode can be restored rather than claiming the pipeline is safe.

One read-only-looking call is **not** safe: `Project.GetRenderCodecs(format)` appeared to
change the current render format as a side effect during Phase 3 exploration. It is not used
outside interactive investigation.

## D021 — VAD parameters are technical; silence-before-reset is editorial

**Status:** accepted

The Phase 0 config had a single `[speech]` block mixing the two, and `min_silence_ms = 650`
was the clearest symptom: 650 ms is not a claim about phonetics, it is a claim about when a
zoom should end. The two now live apart:

| Concern | Where | Meaning |
| --- | --- | --- |
| `[speech.vad].min_silence_ms` = 100 | technical | how long the model must stay quiet before an utterance has really ended |
| `[planner].reset_after_silence_ms` = 650 | editorial | how long the creator must be silent before the tool resets to x0 |
| `[speech.vad].speech_pad_ms` = 30 | technical | padding so a segment does not clip the first/last phoneme |
| `[planner].zoom_lead_in_ms` / `zoom_lead_out_ms` | editorial | former `pre_roll_ms` / `post_roll_ms`, which sat in `[speech]` and were indistinguishable from the technical padding |

Consequence, enforced by a test: the VAD does **not** merge a 650 ms pause. It emits two
segments and lets the planner decide whether to bridge them. Deleting information at the
detector stage would make the planner's decision unmakeable.

`silence_threshold_db` was removed: it belonged to an amplitude-gate provider that Phase 3
replaced. The planner keys are documented in `config.example.toml` but deliberately not
parsed by `Config` yet — Phase 4 owns them.

## D022 — Silero VAD is vendored as a checksummed ONNX file, not a dependency or a download

**Status:** accepted

The MVP provider is Silero VAD v6.2.1 (MIT), run through ONNX Runtime on CPU.

Rejected alternatives:

- **the `silero-vad` PyPI package** — it hard-depends on `torch>=1.12` and
  `torchaudio>=0.12`, roughly two gigabytes of installed dependencies, to run a 2.3 MB
  graph. ONNX Runtime plus numpy does the same job.
- **downloading the model at first run** — pinning would still be needed, plus a cache
  directory, a checksum fetch, and an offline failure path on three operating systems. The
  file is 2.3 MB; vendoring removes all of it.

The model ships in `src/davinci_auto_zoom/speech/models/`, alongside the upstream MIT
licence text and a `PROVENANCE.md` recording version, exact source URL, size, SHA-256 and
the measured inference contract. The checksum is asserted at load time, so a modified model
is a loud error rather than quietly different speech segments.

Defaults are Silero's own (`threshold=0.5`, `min_speech=250 ms`, `min_silence=100 ms`,
`speech_pad=30 ms`). They are not tuned to make the output resemble `DAZ_OUTPUT_MVP`.

## D023 — Sample↔frame conversion is exact, and rounds outwards at segment boundaries

**Status:** accepted

`domain/timebase.py` converts with `fractions.Fraction`, never floats. Rounded NTSC rates
reported by Resolve (`23.976`, `29.97`, `59.94`) are mapped to their true `n×1000/1001`
values; using the decimal drifts by a fifth of a frame per hour and grows.

Rounding policy at segment boundaries, chosen and tested:

- **start frame = floor**, **end frame = ceil**, range half-open `[start, end)`.

The frame range therefore always *covers* every audio sample the VAD attributed to speech;
it never trims speech to reach a rounder number. Maximum error is strictly under one frame
at each boundary. A speech region shorter than one frame widens to a single frame rather
than collapsing to an empty range.

Sample 0 of the render is the timeline's own `GetStartFrame()` (216000 on the test project),
which is why the render must not be trimmed or padded at either end — and why the probe
fails when the rendered duration differs from the timeline range by more than two frames.

Measured on the real timeline: 3555 frames expected, 948 000 samples rendered, delta
**0.000 frames**.

## D024 — Renders must target a Media Storage volume, not a system temp directory

**Status:** accepted — diagnosed from the GUI after three failed runs

Resolve refuses any render path outside the locations configured in
`Preferences > System > Media Storage`. From the scripting side this is close to invisible:
`SetRenderSettings({"TargetDir": "/tmp/..."})` returns `True`, and only `AddRenderJob()`
fails — returning an **empty string**, with no error text. In the GUI the same condition
raises a modal *"Render Path Inaccessible — Please select a render path from within the
media storage"*, and that modal is what hung the first run (D018).

`resolve.GetMediaStorage().GetMountedVolumeList()` reports the acceptable roots
(`['/home/palawi/Vidéos', '/home/palawi', '/mnt/nas']` on this machine). The probe creates
`DAZ_RENDER_TMP_<uuid8>` inside the first writable one, **moves the rendered file out into
its own private temp directory immediately**, and deletes the directory in cleanup — so the
user's media storage holds tool files only for the duration of one render, and never a file
the run did not create.

Preflight fails closed when no volume is configured, naming the preference pane to fix.

The system temp directory is still used for everything after the render (the normalized
16 kHz WAV, diagnostics), since only Resolve has this restriction.

An empty-string return from a Blackmagic API is worth treating as a general lesson: it means
"refused", and the reason is usually visible only in the GUI.

---

Decisions below were made during Phase 4 (pure zoom planner). They are backed by the pure
test suite and by a live `plan-probe` run against DaVinci Resolve Studio 21.0.4.5:
`.agent/reports/phase-04-plan-probe-report.txt`.

## D025 — An asset instance's length is not its animation's length

**Status:** accepted — supersedes the way D014 was being read

D014 established that a Generator instance's keyframes stay anchored to frame 0 and are never
rescaled. Phase 4 draws the consequence the earlier notes got wrong:

| Quantity | `FACE_X1` | `FACE_X0_SMOOTH` | Who owns it |
| --- | --- | --- | --- |
| Media Pool native length | 132 frames | **42 frames** | the asset file; the planner never reads it |
| Animation length | 15 frames | **15 frames** | user metadata, configured |
| Instance length in a plan | variable, `>= 15` | exactly 15 | the planner |

`FACE_X1` is an entry animation **followed by a hold**: after 15 frames the zoom has arrived
and simply stays there for as long as the clip lasts. So an x1 placement spans
`[x1_start, x0_start)` — 30, 93, 250 frames, whatever the speech needs — and its only length
rule is `duration >= facecam_x1 animation`, below which the move is truncated mid-way.

`FACE_X0_SMOOTH` completes its whole return in 15 frames. **The 42-frame native duration is
not a constraint and is used nowhere.** Any earlier statement that a reset needs 42 frames of
room is wrong and has been removed from the docs; a reset needs
`[assets.transition_frames].reset_x0` frames, currently 15.

Both numbers are **this user's assets**, not constants of the software. They live in
`[assets.transition_frames]`, in frames (the keyframes were authored on a frame grid), and
have deliberately **no default**: an unconfigured planner refuses to run rather than planning
against someone else's animation lengths. DAZ never opens the Fusion graph to discover them —
that would break D007, and the config value is a one-line answer to a question the user
already knows.

## D026 — `reset_after_silence_ms` is a gate, not a delay

**Status:** accepted — this is the rule the Phase 3 handoff had wrong

The planner is offline: when it looks at a pause, it already knows how long that pause is
going to be. So the parameter answers *"is this silence worth leaving the zoom for?"*, and it
is never added to a speech end:

```
gap = next_speech_start - speech_end
gap <  reset_after_silence  ->  same editorial burst, one continuous FACE_X1 across the pause
gap >= reset_after_silence  ->  a reset may be planned, at the END of the burst
```

`x0_start = speech_end + 650 ms` is explicitly **not** what happens. That reading would push
every reset into the following silence for no reason, and it is what the Phase 3 note about
"the reset delay is much longer than 650 ms" was implicitly assuming.

The VAD keeps emitting two segments for a 650 ms pause (D021); merging them into bursts is
the planner's decision, made where the editorial parameters live.

## D027 — Only real hard cuts are snap targets, on a configurable reference track

**Status:** accepted

`TimelineSnapshot.edit_boundaries()` returns every clip start and end, which includes the
first clip's head, the last one's tail, and both edges of any gap. Snapping a reset onto one
of those would land it where nothing visibly happens.

`TimelineSnapshot.hard_cuts(track)` is the planner's input instead: a frame where one clip
ends **and** another begins on the same track. Entering from black or running out into a gap
is not a cut.

The track is `[resolve].cut_reference_video_track` (V1 here), deliberately different from
`zoom_video_track` (V3): the cuts live on the edited footage, the zooms land on DAZ's own
track, which has no cuts of its own.

Snapping algorithm, for a reset that has already been allowed:

1. candidate hard cuts are those in `[base_reset, base_reset + cut_snap_window]` that are
   before the next zoom;
2. a candidate is usable when `cut + reset_x0_frames <= next_zoom_start` — the full reset
   animation must still fit;
3. the **last** usable candidate wins (the end of the flurry of cuts after a sentence, not
   the first one);
4. with no usable candidate, the reset stays at `base_reset`;
5. if even `base_reset` leaves no room, **no reset is placed at all** and the x1 is held
   across the pause. Every one of these outcomes is written to the decision trace.

## D028 — The plan carries complete placements, not ENTER/RESET events

**Status:** accepted — replaces the Phase 0 `ZoomAction` scaffold

The scaffold emitted `ZoomAction(frame, kind, target)` pairs, which forced whoever applied
them to re-derive every duration. Phase 4 emits `AssetPlacement(role, [start, end), reason,
cut_frame)` instead: exactly the arguments of the proven insertion call (D013), so Phase 5
inserts what the planner decided rather than recomputing it — and so a plan can be checked
for overlaps, sortedness and range containment as data.

`ZoomState` / `ZoomActionKind` / `ZoomAction` were deleted rather than kept alongside. Future
states (x2/x3, gameplay) become further roles and placements.

`PlanSource` records what the plan was built from — project, timeline and its unique id, the
frame range, the exact frame rate, the three track indices, the asset role→name mapping, the
asset transition frames and every planner setting — so a future executor can verify a plan
still applies. Phase 4 records it; validating it is Phase 5's job.
