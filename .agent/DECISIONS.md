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

1. candidate hard cuts are those in
   `[base_reset - cut_snap_lookback, base_reset + cut_snap_window]` that fall after the start
   of the x1 this reset ends;
2. a candidate is usable when `cut + reset_x0_frames <= next_zoom_start` — the full reset
   animation must still fit;
3. the usable candidate **nearest** to `base_reset` wins, ranked by `abs(cut - base_reset)`,
   with the later cut preferred on an exact tie;
4. with no usable candidate, the reset stays at `base_reset`;
5. if even `base_reset` leaves no room, **no reset is placed at all** and the x1 is held
   across the pause. Every one of these outcomes is written to the decision trace.

Rules 1 and 3 were revised in Phase 6; see D034.

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

---

Decisions below were made during Phase 5 (safe MVP executor + persistent auto-preview
timeline). They are backed by the pure test suite and by the fake-Resolve executor tests
**only**; the status of the live confirmation run is recorded in `.agent/HANDOFF.md`.

## D029 — A plan is re-validated against a fresh snapshot, totally, before any write

**Status:** accepted

Phase 4 recorded a `PlanSource` and validated nothing (its own note said so). Phase 5 adds
the missing half in `domain/plan_validation.py`:

1. the executor re-snapshots the source **immediately before writing**;
2. it rebuilds a `PlanSource` from that fresh snapshot through
   `build_plan_source` — the *same* constructor the planning run used, so a difference can
   only come from Resolve, never from two call sites filling a field differently;
3. it compares the two field by field: project, timeline name, timeline unique id,
   start/end frame, frame rate, voice audio track, cut-reference video track, zoom video
   track, role→asset-name mapping, per-role transition frames, every planner setting, the
   resolved asset identities, and the structural fingerprint (D030).

Any single difference is a **refusal**: zero insertions, zero preview, and it happens before
the source timeline is even duplicated, so a stale plan costs the user nothing to discover.
There is deliberately no "close enough" tier — the executor's whole safety argument is that
it applies what the planner decided to the material the planner read.

A plan carrying no `PlanSource` at all is refused for the same reason.

Frame rates are compared as canonical strings (`timeline_frame_rate`), so Resolve reporting
`60.0` in one run and `60` in another can never look like a rate change, and `59.94` is
always the exact `60000/1001`.

## D030 — The plan carries a structural fingerprint of the source, not just its name

**Status:** accepted

Names, unique ids and durations are not enough. A user can re-cut V1, slip a clip on the
voice track or trim a sentence and keep the timeline's name, its unique id **and** its total
length; the plan then describes an edit that no longer exists.

`domain/fingerprint.py` therefore hashes the observable structure the planner actually read,
and nothing else:

* the analysed frame range and the exact frame rate;
* every item on the configured **voice audio track** — stable id when Resolve provides one,
  name, start, end, duration;
* every item on the configured **cut-reference video track**, the same way.

Serialization is canonical (JSON, sorted keys, no insignificant whitespace, UTF-8) and the
digest is SHA-256, printed as `sha256:<hex>`. Items are sorted before hashing, so the order
Resolve happens to return them in is not part of the identity.

Deliberately excluded, with reasons:

* **`GetIsTrackEnabled`** — Resolve reports it falsely for any timeline that is not the
  current one (D009). Hashing it would make the fingerprint depend on which timeline the
  user has open;
* **tracks the planner never reads** (V2, A2/A3, subtitles) — a change there cannot change
  the plan, and a check that fails for irrelevant reasons teaches people to bypass it.

**Honest limit, stated once and not softened:** this proves the *structure the scripting API
exposes*. A Fairlight change (level, EQ, a fade, a plugin) that alters what the voice sounds
like without moving a clip is invisible to it, and so is an OFX/Fusion change on a video
clip. Two equal fingerprints mean "the structure the planner looked at is unchanged", not
"the project is unchanged".

## D031 — The preview timeline is the transaction boundary, and success keeps it

**Status:** accepted

`apply-preview` never touches an existing timeline. It duplicates the configured source into
`DAZ_AUTO_PREVIEW_<timestamp>_<short id>` and does everything there, which makes the whole
run a single transaction with an obvious undo: delete the timeline this run created.

* **On failure** — any refused guard, any insertion that does not match, any exception —
  `try/finally` restores the timeline the user had open, deletes *that* preview, verifies it
  is gone, and audits the protected timelines. Rollback is whole-preview rather than
  item-by-item: removing a timeline this run created is a smaller and far more verifiable
  action than un-editing one.
* **On success the preview is kept.** This is the deliberate difference from the Phase 2/3
  probes, which delete everything: the preview *is* the deliverable, the thing a human opens
  and watches. The previously active timeline is still restored, so the user's session is
  where they left it, and the report prints the preview's exact name and unique id.

An older `DAZ_AUTO_PREVIEW_*` is never reused, overwritten or deleted — cleanup only ever
touches the object this run created, and only after checking its name carries the prefix.

`SaveProject()` is never called. Creating the timeline through the API is enough; forcing a
save of the user's project is not this tool's decision.

## D032 — MVP collision policy: a dedicated, verified-empty target track, or nothing

**Status:** accepted

Phase 2 only ever inserted onto empty space, so Resolve's behaviour when a clip collides with
an existing one is still unknown. Phase 5 does **not** find out. It does not need to:

* if the configured `zoom_video_track` does not exist, video tracks are appended (and the
  count re-checked after each `AddTrack`) until the index exists;
* if it exists and is **empty**, it is used;
* if it holds **any** `TimelineItem` — someone's titles, or a previous DAZ run's zooms — the
  run is refused before a single insertion.

No overlapping `Append` is ever issued "to see what happens", nothing is overwritten, shifted
or deleted, and no attempt is made to recognise DAZ's own earlier output. Ownership markers,
idempotence, `clean` and `rebuild` are Phase 6; until they exist, refusing is the only answer
that cannot damage an edit.

Nothing else on the preview is modified either: V1/V2 keep their clips, names and order, no
track is enabled, disabled or deleted, no gap is created, and the audio is left exactly as
duplicated.

Insertions are sequential (one `clipInfo` per `AppendToTimeline`), not batched: a failure
then names the placement that caused it instead of leaving 28 of them to be attributed. Each
one is verified immediately (exactly one returned item, expected name, start, end, duration,
track, non-zero Fusion comp count), and the finished track is compared with the whole plan
afterwards — same count, same order, no extras, no gaps in the mapping, no overlaps, not one
frame of drift. Only then is the run declared successful.

## D033 — Restoring the active timeline and auditing the protected ones are *inside* the transaction

**Status:** accepted (Phase 5 review, before the live run)

As first delivered, the executor closed like this:

```python
with suppress(Exception):
    project.SetCurrentTimeline(previous_timeline)
report.restored_current_timeline = str(current.GetName()) if current else None
```

…and `keep = True` was decided at the end of the `try`, with the protected-timeline audit
running *after* the preview had already been kept. Three things were wrong with that.

**1. Restoration was best-effort, and success did not depend on it.** The exception was
suppressed, the return value ignored, and the report simply recorded whatever
`GetCurrentTimeline()` happened to say — which, if the restore silently failed, is the
preview. A run could print `RESULT: PASS` having left the user staring at a timeline DAZ
created. Restoration is now proven, not attempted: name **and** unique id are captured before
any mutation, `SetCurrentTimeline` must not raise and must not report failure,
`GetCurrentTimeline()` is re-read, and the identity must match. Each of those three checks
catches a failure the others do not — a build that returns `False` while switching anyway, a
build that returns `True` without switching, and an outright raise. Anything short of all
three sets `current_timeline_restored = False` and fails the run.

**2. A preview could be deleted while it was still the active timeline.** The old cleanup went
straight to `DeleteTimelines([preview])` on any failure. But the preview is made current so
`AppendToTimeline` can reach it, so "the restore failed" and "the preview is still active" are
the *same* situation. Deleting the timeline Resolve currently has open, on a path that already
knows the API is misbehaving, is exactly the blind cleanup this tool must not do. The preview
is now kept in that case, named in the report, and the reason stated. Safety beats tidy
cleanup; a leftover `DAZ_AUTO_PREVIEW_*` is an inconvenience, and no other timeline is touched.

**3. The audit could not veto anything.** Running it after the keep decision made it a report,
not a guard — the run could hand over a "successful" preview while `DAZ_OUTPUT_MVP` had moved
under it. The order is now restore → audit → decide, so **keeping the preview is the last
decision of the transaction**. An audit difference deletes this run's preview like any other
failure (when deletion is safe), and DAZ never attempts to "repair" a protected timeline.

Consequently `ApplyReport.succeeded` was rewritten as one flat conjunction of every property
the phase promises — preflight, fresh plan source, faithful preview, usable track, every
insertion, whole-track verification, proven restoration, zero audit differences, no cleanup
failure, preview intentionally kept. If a property matters it is in that expression; it is not
left in `notes` where nothing depends on it. `tests/test_executor.py` walks the truth table
one property at a time.

A fourth, smaller point found while testing the above: the cleanup runs inside a `finally`, so
any read it performs (`find_timeline`, `GetCurrentTimeline`, the audit itself) can raise and
replace the real failure with a cleanup traceback. Those reads are now caught and recorded as
cleanup failures instead.

**Live confirmation (2026-08-16, Studio 21.0.4.5):** the run reported
`restored: DAZ_OUTPUT_MVP (was DAZ_OUTPUT_MVP) proven=True`, and an independent post-run audit
confirmed the active timeline matched the pre-run one by unique id
(`81df1b0c-a91d-4d67-88cc-ed6dddeddb95`), with both protected timelines byte-identical.

## D034 — The reset snaps to the *nearest* hard cut, in an asymmetric window

**Status:** accepted — Phase 6, supersedes rules 1 and 3 of D027

**Confirmed by measurement on `DAZ_INPUT`, not by taste.** The Phase 5 preview placed all 14
resets directly at the burst end: the forward-only search never fired once. Human review then
reported the x0 landing a few frames *after* a video cut it should have started on. The
diagnostic (`.agent/reports/phase-06-cut-offset-diagnostic.txt`) measured every burst end
against every V1 hard cut and found the cause: **in this material the relevant cut is always
slightly before the detected speech end, never after.** Offsets: -4, -4, -4, -4, -5, -6, -7,
-7 frames on 8 of the 14 bursts. The nearest forward cut in any burst was +35 frames, well
outside the 21-frame window. A forward-only search was structurally blind to the whole case.

Two things changed.

**1. The window is asymmetric.** `[base_reset - cut_snap_lookback, base_reset + cut_snap_window]`,
with a new `[planner].cut_snap_lookback_ms`. It belongs to the planner, not to `[speech.vad]`:
the VAD answers *where is speech, technically* and its `speech_pad_ms` deliberately pads each
segment so no phoneme is clipped. That padding is correct and stays in the detector — the
compensation for it is an editing decision. **No VAD parameter was touched to fix this.**

The lookback is a **snap mechanism only**. It never produces `base_reset - lookback`: with no
hard cut in the window the reset stays exactly at `base_reset`. It also cannot reach back past
the start of the x1 it terminates.

**2. The nearest candidate wins, not the last.** Ranked by `abs(cut - base_reset)`, ties broken
towards the later cut so the bias stays conservative — "after the speech" when the evidence is
symmetric. The old "last cut in the window" rule was a guess about a flurry of cuts after a
sentence; nothing in the measured material supports it, and it actively picks the wrong cut
when several are in range.

**Default: `cut_snap_lookback_ms = 120`** = 7 frames at 60 fps. The suggested 100 ms baseline
is 6 frames and would have missed two of the eight cases (both at -7), so it is measurably too
small. 120 ms is the smallest round value covering every observed offset, with a 4x margin to
the nearest non-matching cut (30 frames) and 16x to the first clearly unrelated one (114).

**Corroboration, not the target.** In all 8 bursts with a cut inside the lookback, the human
edit's `FACE_X0_SMOOTH` starts on *exactly* that cut; in the 4 bursts with no such cut, the
manual reset is near the burst end and *not* on a cut. The value was chosen from the cut/burst
offsets alone — `DAZ_OUTPUT_MVP` was never optimised against (D021 stands).

Because this setting changes every reset, it is part of `PlannerSettings`, its JSON, and the
`PlanSource` identity: a plan built with a different lookback cannot be applied under the
current config.

**Live result (2026-08-17):** 14 resets → 6 direct, 8 `reset_cut_snap_backward`, 0 forward.
Median reset offset against the human edit moved from 5 frames to **0**.

## D035 — Ownership is a marker on the TimelineItem instance, and nothing else is evidence

**Status:** adopted, Phase 7. Proven live on Studio 21.0.4.5
(`.agent/reports/phase-07-ownership-probe-report.txt`, 14/14 checks).

Two `FACE_X1` TimelineItems can share a name, a track, a frame range, a duration, a Fusion
comp and a Media Pool asset, and one can be DAZ's while the other was dragged there by the
user. Nothing observable about the clip separates them. So the only proof of ownership is a
marker on the **TimelineItem instance** whose `customData` carries a DAZ record.

Never sufficient, individually or together: clip name, track index, position, duration, Fusion
graph, Media Pool asset name. They may *corroborate* a marker (D037) but they can never create
ownership. **An old clip with no DAZ metadata stays unowned even if it is byte-identical to
one DAZ would have made.**

`TimelineItem.AddMarker(frameId, color, name, note, duration, customData)`, `GetMarkers()`,
`GetMarkerByCustomData()` and `DeleteMarkerByCustomData()` are all documented and appear in
neither the deprecated nor the unsupported section of the README installed with the running
build. `customData` is documented as "not exposed via UI and useful for scripting developer to
attach any user specific data".

Measured live, on real **Generator** items (which expose no Media Pool item and no source
frames, so they were the case most likely to behave differently):

- markers attach to the instance, not to the shared asset — no other `FACE_X1` anywhere in the
  project acquired metadata (`asset_isolation`);
- the record round-trips byte-identically through `customData`;
- it survives switching to another timeline and back (`survives_timeline_switch`), and the
  classification is identical before and after;
- an identical, untagged twin inserted on the same track classifies as `unowned`.

The record is versioned (`schema`), namespaced (`davinci-auto-zoom`) and carries the preview
identity, the semantic role, the configured asset, the planned frames, the source fingerprint
and a deterministic placement id. Format and parser live in `domain/ownership.py`; the live
side is `resolve/ownership.py`.

**100% or nothing.** `apply-preview` now claims every item it creates and re-reads the whole
track through the classifier a later `clean-preview` will use. If one item cannot be claimed,
the entire preview is rolled back. A partially owned preview is not a lesser success — it is a
preview no destructive command could ever safely touch, so it must not be allowed to exist.

## D036 — DAZ never overwrites a marker it did not write, and fails closed instead

**Status:** adopted, Phase 7. Proven live (`user_marker_preserved`).

Local frame 0 is *preferred*, never assumed. Before tagging, the item's existing markers are
read and DAZ takes the first local frame nothing occupies — a marker's occupied span includes
its duration, so a five-frame user marker at frame 0 pushes DAZ to frame 5 rather than landing
inside it. The live probe places a stand-in user marker at frame 0 first and requires it back
untouched; DAZ took frame 1.

If an item has **no** free local frame — genuinely possible on a 15-frame reset — that item
fails closed. It is never tagged by taking somebody else's frame, and the failure rolls the
whole preview back rather than producing a partially owned one.

DAZ also never updates or deletes a marker. Cleanup removes whole TimelineItems, which takes
their markers with them; markers are never edited in place.

## D037 — Four ownership states, and ambiguity means zero deletions

**Status:** adopted, Phase 7.

`unowned` is the *safe* answer, not the fallback. But "I cannot claim this" and "I cannot even
classify this" are different, and collapsing them would let a corrupt record be quietly
deleted around. So:

| state | meaning | destructive commands |
| --- | --- | --- |
| `owned` | well-formed record matching every expectation | deletable |
| `unowned` | nothing claims to be DAZ's | never touched, never blocks |
| `stale` | well-formed record naming a different preview or a different source fingerprint | never deleted, blocks the run |
| `ambiguous` | a marker claims to be DAZ's and contradicts itself | never deleted, blocks the run |

A marker becomes `ambiguous` on: unparseable JSON, wrong namespace, unknown schema, a missing
or wrongly-typed field, an unexpected field, an invalid role name, a backwards frame range, a
`placement_id` that does not match its own fields, a role that is not configured, a role whose
configured asset differs from the record's, a clip renamed away from its record, or two DAZ
markers making different claims. Two *identical* DAZ markers are `owned` with a warning: they
agree, so identity is not in doubt.

**One ambiguous or stale item on a track stops the entire run before its first delete.** A
half-cleaned track whose ownership was never fully classifiable is worse than an uncleaned one.

Sanity checks are defence in depth and can only make a verdict stricter, never looser. An item
that *moved* since it was tagged stays `owned` and carries a diagnostic saying so — position is
not evidence either way (D035), and refusing there would be an ownership rule smuggled back in
through geometry.

## D038 — A hand-duplicated preview is `stale`, and is never destructively cleanable

**Status:** adopted, Phase 7. Behaviour measured live, not assumed.

Measured: `Timeline.DuplicateTimeline` **copies the ownership markers** onto the duplicate's
items. The duplicate gets a new timeline `unique_id`; its markers still name the *source*
preview.

That is not a defect, and it defines the semantics. Classified against its own identity, every
copied item comes back `stale`, so a `clean-preview` or `rebuild-preview` aimed at the copy
refuses before deleting anything. Confirmed live: the probe's duplicate classified as
`['stale', 'unowned']` and both destructive-work checks passed.

The conservative rule: a timeline holding records whose `preview_id` is not this timeline's is
never automatically cleaned. Markers on a duplicate are **not** rewritten to adopt the copy —
DAZ does not decide that a copy of the user's is now its own.

## D039 — Legacy previews are inert: never migrated, never retro-tagged, never deleted

**Status:** adopted, Phase 7.

`DAZ_AUTO_PREVIEW_20260816_211026_c676d5af` (Phase 5) and
`DAZ_AUTO_PREVIEW_20260817_132005_77443d7c` (Phase 6, the visually validated one) were made
before DAZ tagged its output. Their 28 items each carry **zero markers of any kind**, verified
by the post-Phase-7 audit.

They are therefore `unowned`, which is correct and permanent. There is no migration path and
there will not be one: retro-tagging would mean deciding from name, track and frames that a
clip is DAZ's, which is exactly what D035 forbids — and it would be indistinguishable from
adopting a user's clips.

A track holding items where **not one** carries ownership metadata is refused as
`legacy preview: no verifiable DAZ ownership metadata`. Verified live against both.

## D040 — `clean` tolerates a foreign clip; `rebuild` fails closed on one

**Status:** adopted, Phase 7. Both halves proven live.

A selective clean can safely leave a clip DAZ did not create: it removes proven-owned items
one identity at a time and touches nothing else. Proven live on a disposable timeline — 3
tagged DAZ items removed, 1 untagged `FACE_X1` on the same track surviving with the *same
unique id*, the same frames, and every other track item-for-item identical (13/13 checks).

`rebuild-preview` refuses instead. After cleaning it would insert fresh placements next to the
surviving clip, and DAZ still does not rely on Resolve's collision behaviour (D032). The
refusal happens **before** any deletion, so a refused rebuild costs nothing.

    clean-preview   -> preserves unowned, reports them
    rebuild-preview -> refuses while any unowned item is on the target track

## D041 — A `DAZ_RECOVERY_*` duplicate exists before the first delete, and any failure keeps it

**Status:** adopted, Phase 7.

`apply-preview` can use "delete the thing we made" as its transaction boundary (D031). The
destructive commands cannot: they modify a timeline that already exists. So before the first
`DeleteClips`, a `DAZ_RECOVERY_<timestamp>_<id>` duplicate of the target is created; if the
duplicate cannot be made, nothing is deleted at all.

Deleting the recovery is the **last** action of the transaction, taken only once the delete,
any re-insertion, the active-timeline restore *and* the protected audit have all passed. A run
can therefore never announce success while the user's timeline is missing or a protected
timeline has moved — in those cases the copy is still there.

On any failure after a mutation the recovery is kept, its exact name reported, and **no
automatic restoration is attempted**. Putting the copy back is the user's decision, made with
the copy in front of them. No other timeline is created or deleted either way.

`DeleteClips` is always called with an explicit `ripple=False` rather than relying on the
documented default: these clips sit on a dedicated track above the user's edit, and a ripple
delete would shift everything after them. No video track is ever deleted. `SaveProject()` is
never called.

## D042 — `DeleteClips` only acts on the current timeline (undocumented; measured)

**Status:** measured on Studio 21.0.4.5, Phase 7. Found live, fixed, regression-tested.

`Timeline.DeleteClips([timelineItems], Bool)` is documented with no mention of the current
timeline. In practice it behaves like `MediaPool.AppendToTimeline`: called on a timeline that
is not current it **returns False and deletes nothing**.

Found by the first live `clean-preview`, which returned `DeleteClips returned False`, removed
0 of 28 items, kept its recovery and restored the active timeline — the safety model worked
exactly as designed, and turned an undocumented API restriction into a clean refusal instead
of a partial mutation.

Measured directly afterwards on a disposable copy:

```
recovery NOT current -> DeleteClips([1], False) -> False,  V3 still 28 items
recovery made current -> DeleteClips([1], False) -> True,   V3 27 items
recovery made current -> DeleteClips([27], False) -> True,  V3 0 items
```

The fix makes the preview current after the recovery exists and before the delete, verifies
via `GetCurrentTimeline().GetUniqueId()` that it actually became current, and restores the
user's timeline in the transaction's `finally` as before. `tests/fake_resolve.py` reproduces
the behaviour, so `test_the_preview_is_made_current_before_the_delete` fails against the old
code.

**Generalised lesson:** the installed README documents signatures, not preconditions. Two
write-capable calls now share the "must be current" precondition; assume any third one does
too until measured.

## D043 — Idempotence is the same desired state, not the same Resolve objects

**Status:** adopted, Phase 7. Proven live.

A `placement_id` is `sha256(canonical JSON of {version, source_fingerprint, role, start, end,
asset})`, truncated to 128 bits. **No clock, no randomness, no counter, and deliberately not
the preview's identity** — a placement belongs to a plan, not to whichever preview holds it.

Two rebuilds are structurally idempotent when they produce the same placement count, the same
roles, the same start/end frames, the same durations, the same placement ids, and no
duplicates. Newly created `TimelineItem.GetUniqueId()` values necessarily differ after a
rebuild and are explicitly **not** part of the comparison.

Proven live: rebuild #1 and rebuild #2 of
`DAZ_AUTO_PREVIEW_20260817_163637_e8787ece` produced byte-identical `(role, start, end,
placement_id)` for all 28 placements, 28 unique placement ids, and 28 items on the track after
each — no accumulation.

Because the fingerprint is hashed in, re-cut source material changes every placement id, and
`rebuild-preview` refuses on the fingerprint mismatch before deleting anything.

## D044 — The domain models **states and transitions**, not a zoom level and its opposite

**Status:** adopted, Phase 8.

Phases 4-7 encoded one editorial idea directly in the type system: there was a `facecam_x1`
role and a `reset_x0` role, and planning meant alternating them. That model cannot express
"the creator kept talking, so go tighter", and every attempt to bolt x2/x3 onto it puts
special cases in the planner *and* in the executor.

So the model is inverted. `domain/transitions.py` holds:

* the **visual states** — `x0`, `face_x1`, `face_x2`, `face_x3`;
* the **allowed transitions** between them, as a closed table;
* the mapping transition -> configurable role.

The planner reasons in states and emits transitions. The executor never learns what a state
is: it resolves a role to a clip name, appends that clip at the frames the plan gives it,
verifies, and tags. **No editorial logic exists below the planner**, which is the property
that makes adding gameplay states later a change to one table rather than to five modules.

A move that is not in the table raises `ForbiddenTransition`. It is never a silent no-op:
asking for `x0 -> face_x2` is a bug in the caller, and a bug that plans nothing is worse than
one that stops.

## D045 — The facecam ladder is climbed one rung at a time and never descended

**Status:** adopted, Phase 8. Confirmed against `DAZ_OUTPUT_MVP2`.

The allowed transitions are exactly:

    x0      -> face_x1        face_x1 -> x0
    face_x1 -> face_x2        face_x2 -> x0
    face_x2 -> face_x3        face_x3 -> x0

There is deliberately no `x0 -> face_x2`, no `face_x2 -> face_x1`, and no `face_x3 ->
face_x2`. A zoom that steps back out to a wider facecam level mid-sentence reads as a mistake;
dropping all the way to x0 reads as the end of a thought. The exit from any level is to x0 and
nowhere else.

This is not only taste. All 14 manual cycles in `DAZ_OUTPUT_MVP2` obey it: none skips a level,
none descends, and every manual reset asset matches the level it came down from.

**Consequences for the assets.** A promotion clip (`FACE_X1`, `FACE_X2`, `FACE_X3`) animates
into its destination state over its `transition_frames` and then *holds* it for as long as the
instance lasts — D014 generalised from x1 to every rung. Its placement therefore runs to
whatever comes next, another promotion or the reset. A reset clip (`X1_TO_X0`, `X2_TO_X0`,
`X3_TO_X0`) does its whole job in `transition_frames`, so its placement is exactly that long.
Native Media Pool lengths remain irrelevant to both (D025).

**Consequences for the config.** `[assets]` and `[assets.transition_frames]` are both keyed by
transition role and must list the same roles. Only `x0_to_face_x1` and `face_x1_to_x0` are
required; a user who has not built an x2 or x3 asset omits those roles and the planner simply
never makes that move. A file that names any asset replaces the default table wholesale rather
than merging into it — merging would silently reinstate a default `FACE_X2` for someone who
deliberately configured two roles, and then plan promotions they cannot perform.

## D046 — Promotions are earned by sustained speech; only resets snap to cuts

**Status: SUPERSEDED by D049 and D052 (Phase 8c).** Kept for the history and for the
measurement in its last paragraph, which still holds. Its rule does not: promotions are no
longer earned by elapsed talking time, and every facecam transition may now snap to a cut. The
four thresholds named below no longer exist.

Original text, Phase 8, measured on `DAZ_OUTPUT_MVP2`:

Once a burst is zoomed, the level climbs on elapsed talking time and nothing else:
`promote_to_face_x2_after_ms` into the burst, then `promote_to_face_x3_after_ms`. Each
promotion also requires that enough burst still remains
(`min_remaining_after_face_x2_ms` / `..._x3_ms`), so a burst that stops a heartbeat after
crossing a threshold does not flash a tighter level nobody can read.

**Promotions are never cut-snapped.** The measurement: of the 7 manual promotion frames in
`DAZ_OUTPUT_MVP2`, exactly 1 lands on a hard cut — and there are 20 cuts in a 3555-frame
range, so one hit is what chance predicts. The same measurement on the same timeline puts 8 of
14 manual *resets* exactly on a cut. Snapping stays where the evidence is; the reset rule
(D034) is untouched and its live output is frame-identical to Phases 6 and 7.

The reset asset now depends on the level reached, which introduces one ordering problem: the
"does the reset still fit before the next zoom" check runs before the planner knows which
level the burst will reach. It budgets for the **longest** configured reset animation. Budgeting
high is safe — the reset actually placed is never longer, so it always fits too.

## D047 — The x2/x3 thresholds are calibrated on measured data, and the x3 pair is weak

**Status: SUPERSEDED by D049 and D051 (Phase 8c).** The four thresholds it calibrates were
removed; the x3 pair it flagged as resting on n=1 is exactly what the voice-dynamics model
replaced. The "known divergence that no simple rule can fix" paragraph is still true and now
has a measured cause — see D049 and
`.agent/reports/phase-08c-voice-dynamics-analysis.txt`.

Original text, Phase 8:

    promote_to_face_x2_after_ms    = 1000   (60 frames at 60 fps)
    min_remaining_after_face_x2_ms =  350   (21 frames)
    promote_to_face_x3_after_ms    = 1800   (108 frames)
    min_remaining_after_face_x3_ms =  500   (30 frames)

**x2 is well grounded.** On `DAZ_OUTPUT_MVP2` the cycles the editor left at x1 span at most 75
frames, and the shortest they promoted spans 87. The two populations do not overlap, and there
is an 11-frame gap between them. A rule of the form "promote at +T, if at least R remains"
fires exactly when `span >= T + R`, so any value in (75, 87] reproduces all fourteen human
decisions. 1000 + 350 ms = 81 frames sits in the middle of that gap. This is a gap in the data,
not a curve fit.

**x3 rests on a single observation** and is the weakest thing in the phase. There is exactly
one `FACE_X3` in the reference timeline. 1800 + 500 ms = 138 frames is exactly that cycle's
span, and the resulting promotion frame lands 1 frame from the human's. Calibration on n=1 is
recorded as calibration on n=1.

Explicitly not done: no ML, no automated threshold search, no per-burst fitting to reproduce
`MVP2`. `DAZ_OUTPUT_MVP2` is a strong heuristic reference, never ground truth.

**Known divergence that no simple rule can fix.** The longest manual cycle in the timeline (204
frames) was deliberately kept at x2, while the 138-frame closing cycle went to x3. No monotonic
duration rule produces both. The live plan matches the human's peak level on 11 of 14 cycles;
of the 3 misses, 2 are burst-extent disagreements inherited from Phase 6 rather than promotion
errors. See `.agent/reports/phase-08-mvp2-analysis.txt`.

## D048 — Gameplay states are absent, not stubbed

**Status:** adopted, Phase 8.

`domain/transitions.py` contains no gameplay state and no placeholder for one. Adding empty
rungs now would be scaffolding for a design nobody has measured: there is no reference edit for
gameplay zooms, no asset family in the bin, and no evidence about what triggers them. The graph
is a closed table precisely so that adding them later is one honest change with its own
measurement behind it, rather than a half-built abstraction that constrains that measurement in
advance.

`state_after("x0_to_gameplay")` raises. It does not return `None` and it does not plan nothing.

## D049 — Facecam levels are earned by intra-burst voice dynamics, not by elapsed time

**Status:** adopted, Phase 8c. **Supersedes D046 and D047.**

Phase 8 promoted the framing on how long the burst had been running:
`promote_to_face_x2_after_ms` into it, then `promote_to_face_x3_after_ms`, each gated by a
`min_remaining_after_*` term. Those four keys **no longer exist**. A config that still contains
one is rejected with an error naming its replacement — not ignored with a warning, because a
silently ignored key leaves a user believing they still tune the edit.

The rule is now:

    burst start                            -> x0 -> face_x1
    1st qualifying voice recovery inside it -> face_x1 -> face_x2
    2nd qualifying voice recovery           -> face_x2 -> face_x3
    any further recovery                    -> ignored, face_x3 holds
    burst end                               -> current state -> x0

A **qualifying recovery** is the moment the voice comes back after a real dip: the short-time
energy fell at least `promotion_min_drop_db` below the cycle's own voice level for between
`promotion_min_valley_ms` and `promotion_max_valley_ms`, then returned to within
`promotion_recovery_within_db` of it. The transition is anchored on the *recovery*, never on
the floor of the dip and never on its start.

**Why the change.** The duration rule predicted the level from the length of a burst, which is
why D047 had to record a divergence it could not fix: a 204-frame cycle the editor kept at x2
while a 138-frame one went to x3. No monotonic function of duration produces both. Measurement
on `DAZ_OUTPUT_MVP2` (`.agent/reports/phase-08c-voice-dynamics-analysis.txt`) shows what the
editor was actually reacting to: **all seven manual promotions sit within 8 frames of a detected
voice recovery, six of them within 3, median +1.**

**What the new rule is honest about.** It scores 9 of 14 peak levels against the human, where
the duration rule scored 11 — and every one of the 5 misses is a cycle whose *burst extent*
disagrees with the human's. On the 9 cycles where the burst matches, the level matches 9 times
out of 9; on the 5 where it does not, 0 of 5. The correlation is total, which converts a bag of
unexplained divergences into one named, already-planned problem (Phase 8b). A higher score with
no mechanism was the worse deal.

Also recorded, because it constrains any successor: **no local feature of a single valley
separates the 7 used from the 56 unused.** Depth and duration overlap completely. The
separation comes from the chain — two rungs, taken by the first two usable cues — not from a
better classifier.

## D050 — The energy envelope is a second reader of the SAME normalized PCM

**Status:** adopted, Phase 8c.

Phase 3 renders the voice track through a scratch timeline, normalizes it to 16 kHz mono PCM
with ffmpeg and decodes it once. Phase 8c's loudness envelope is computed from **that same
in-memory array**, in `speech/energy.py`, immediately after the VAD run.

No second render, no second ffmpeg pass, no second decode. A `plan-probe` still costs exactly
one render of A1. Two independent extractions of the same audio could disagree by a resampling
rounding — and a speech segment and an energy valley that disagree about where a frame is would
be undebuggable.

The layering rule from D021 is unchanged and now covers three layers rather than two:

    speech/   PCM -> speech probabilities, PCM -> dBFS envelope     objective facts
    domain/   envelope -> valleys -> promotion cues                 editorial reading
    planner   cues + bursts + cuts -> AssetPlacements               the edit

`speech/` never decides that a dip deserves a level, and `domain/` never imports numpy,
onnxruntime or a file path. `EnergyEnvelope` crosses that boundary as plain
`(frame, dB)` pairs.

## D051 — Voice-dynamics thresholds are relative, few, and part of the plan's identity

**Status:** adopted, Phase 8c. Calibrated on `DAZ_OUTPUT_MVP2`.

    [speech.energy]  window_ms = 30   hop_ms = 10   smoothing_ms = 30
    [planner]        promotion_min_drop_db        = 20
                     promotion_recovery_within_db =  6
                     promotion_min_valley_ms      = 30
                     promotion_max_valley_ms      = 650
                     promotion_min_hold_ms        = 400

**Every editorial threshold is a dB difference against the cycle's own voice level** (its
75th-percentile envelope point), never an absolute amplitude. Multiplying the whole waveform by
any constant shifts every point equally and cancels out of every comparison, so a change of
microphone gain or a compressor cannot change the edit. Tested at ±12 dB in the unit tests and
at gains ×0.25 to ×4 on the full 14-cycle live plan: identical anchors, all four times.

Justification per number is in `.agent/reports/phase-08c-voice-dynamics-analysis.txt`. The
shape of it matters more than the values: 20 dB sits in the **middle of a plateau** (19-21 dB
score identically), the recovery threshold is deliberately insensitive (3-12 dB all identical),
`promotion_max_valley_ms` is a guard that never fires on this material, and
`promotion_min_hold_ms = 400` is the only genuinely new editorial minimum — measured, because
no manual promotion is held for fewer than 27 frames (450 ms), and 500 ms would have rejected
the real `FACE_X3`.

Because they change the plan, `[speech.energy]` is recorded in `PlanSource.energy_settings` and
compared field-by-field by `plan_source_mismatches`, exactly like `[planner]`. A preview built
with one drop threshold is not the same desired state as one built with another, and
`rebuild-preview` must refuse a plan whose settings have moved (D029).

## D052 — Every facecam transition may snap to a cut; a cut never creates one

**Status:** adopted, Phase 8c. Extends D034, supersedes D046's "promotions are never
cut-snapped".

Every transition now has a **raw audio anchor**: burst start for the entry, a recovery cue for
a promotion, burst end for the reset. Each may move onto a hard cut inside a window around that
anchor, through one shared helper (`_snap_to_cut`), with the nearest candidate winning and the
later cut preferred on an exact tie.

The windows are **per class, because they were measured per class**:

    reset       [-120 ms, +350 ms]   asymmetric (D034, Phase 6, unchanged)
    entry       [-120 ms, +120 ms]   symmetric
    promotion   [-120 ms, +120 ms]   symmetric

The zoom-in window is not the reset's window reused. On `DAZ_OUTPUT_MVP2` four manual x1 starts
sit exactly on a hard cut, and in all four the detected burst start is 0-2 frames away, while
the nearest *non*-matching cut to any burst start is 61 frames away — 120 ms = 7 frames catches
every real one with an 8x margin. It is symmetric because a zoom-in anchor has no systematic
bias, unlike a VAD end, which `speech_pad_ms` puts a few frames late by construction.

Snapping an entry slightly **before** the first word is allowed and correct: the cut just
before someone starts talking is often the edit point a human would use.

Three properties hold for every class, and are tested:

1. **A cut never creates a transition.** Snapping only moves a transition the audio already
   decided on. Promotions confirm this empirically — only 1 of the 14 cues in the live plan
   found a cut within its window at all.
2. **No cut, no movement.** With nothing in the window the transition stays exactly on its raw
   anchor. The lookback never pulls anything earlier on its own.
3. **A nearer but invalid cut gives way to the next valid candidate**, never to nothing.
   Validity is the chain: transitions stay ordered, the previous animation finishes, the new
   one fits, nothing overlaps, nothing leaves the timeline.

## D053 — `GAMEPLAY` is one state, reachable from all four framings and leaving to only two

Phase 9a, superseding D048's "absent, not stubbed" now that the evidence D048 demanded exists.
`DAZ_OUTPUT_MVP3` is a human edit that uses gameplay zooms, the asset family exists in the bin
with measured animation lengths, and the moves below were **observed before they were added**.

    X0       -> GAMEPLAY     x0_to_gameplay
    FACE_X1  -> GAMEPLAY     face_x1_to_gameplay
    FACE_X2  -> GAMEPLAY     face_x2_to_gameplay
    FACE_X3  -> GAMEPLAY     face_x3_to_gameplay
    GAMEPLAY -> X0           gameplay_to_x0
    GAMEPLAY -> FACE_X1      gameplay_to_face_x1

Six moves, and the graph stays closed: `GAMEPLAY -> FACE_X2`, `GAMEPLAY -> FACE_X3` and
`GAMEPLAY -> GAMEPLAY` raise like any other move outside the table.

**Why the way out is narrower than the way in.** A facecam level is earned by what the voice
does *inside* a burst (D049). Coming off the game, no burst has started yet, so nothing has
been earned — landing at `FACE_X2` would assert a level the audio has not justified. Landing
at `FACE_X1` (the creator is talking again) or at `X0` (the shot ended) are the only two things
the picture can honestly say at that instant.

The manual edit agrees, and lopsidedly: 9 of its 10 exits are `gameplay_to_face_x1`. Five of
the six entries appear; `face_x3_to_gameplay` has no instance anywhere in MVP3 and therefore
rests on the user's statement rather than on evidence — recorded here so nobody later reads it
as measured.

`GAMEPLAY` is deliberately **not** a rung of `FACECAM_LADDER`. It is not tighter or wider than
a facecam level, it is a different subject, so `Transition.is_promotion` was redefined
positively (both ends in the ladder) and "leave face_x2 for the game" cannot be counted as a
promotion by `top_state_counts`.

## D054 — The gameplay roles are optional capabilities, and that is a tested guarantee

`REQUIRED_ROLES` is unchanged: `x0_to_face_x1` and `face_x1_to_x0`. A project that configures
no gameplay asset must keep producing exactly the plan it produced before `STATE_GAMEPLAY`
existed — not a similar one.

Proven live, not asserted: `plan-probe` on `DAZ_INPUT` before and after this phase produces
**42 placements identical in `(role, start, end, reason, cut, burst)`**, the same 14 bursts,
the same 15 segments, the same 63 valleys, the same 126-line decision trace bar one line, and
the same fingerprint `sha256:a1d107e1…`. The only differences anywhere are the two places that
*should* change when a config declares more assets: `role_counts` now lists the six gameplay
roles at zero, and `PlanSource` records the six extra configured assets.

`tests/test_planner.py::test_configuring_gameplay_assets_does_not_change_the_facecam_plan`
keeps this true without Resolve.

## D055 — `vision.py` measures the picture; it never interprets it

The `speech/` boundary (D021, D050) applied to video. `vision.py` answers "how much did the
picture change here" and stops. No object detection, no OCR, no game-specific detector, no
VLM, no learned classifier — reading the envelope as an editorial cue is `domain/gameplay.py`.

Implementation constraints that follow from being a measurement and not a feature:

- **no new dependency.** ffmpeg and numpy are already required; a mean absolute frame
  difference is one numpy expression. OpenCV, scipy and torch were not added and are not
  needed;
- frames are decoded small (64x36) and slow (10/s) on purpose — far finer than any editorial
  decision this feeds, and a minute of video decodes in well under a second;
- **frames are mean-subtracted before differencing.** A uniform brightness change — a fade, an
  exposure shift, a flashbang — moves every pixel equally and would otherwise read as maximal
  motion while nothing moved. What survives is structural change. It is not contrast-invariant
  and does not claim to be;
- `motion_from_frames` is split from `motion_envelope` so the metric is testable against frames
  a test builds by hand, with no video file and no ffmpeg process involved.

## D056 — Silence duration does not predict gameplay here, and no rule pretends it does

The Phase 9a brief's prior hypothesis was that the length of the creator's silence would be the
main signal. Measured on `DAZ_OUTPUT_MVP3`, over all 15 would-be-X0 windows:

    gameplay windows: 46, 47, 53, 91, 103, 128, 160, 183, 211, 222 frames
    X0 windows      : 55, 97, 126, 154, 421 frames

The shortest gap that became gameplay (46) is **shorter** than the shortest that stayed X0
(55), and the **longest silence in the timeline (421 frames, 7.0 s) stayed X0**. The ranges do
not merely overlap, they nest. There is no threshold with useful behaviour.

Neither does anything else measured. Secondary-audio activity, mean level, dynamic range and
onset count all have fully nested class ranges; so do mean motion, p90 motion, active fraction
and hard-cut density. Every single-threshold rule scores 10 or 11 of 15 against a majority
baseline of 10.

So the candidate rule uses **no signal**: a would-be-X0 window becomes gameplay unless it is
shorter than 500 ms or is the timeline's tail. It scores 11/15 with zero fitted parameters.
`long_silence_ms` is left in the settings at 100 s — effectively disabled, kept as a documented
knob rather than deleted, because one timeline cannot rule the idea out for other material.

This decision records a **negative result as the phase's main finding**. Fitting a threshold to
reach 11 here would be fitting one lucky negative on n=15.

## D057 — "Gameplay or X0" and "exactly where" are separate problems, reported separately

`decide_gameplay` answers them in that order and never merges them, and a `False` decision
carries no anchors at all, so no report can show a proposed frame for a declined window.

The reason is empirical: on MVP3 the rule gets **problem A right 11 times of 15** and is
**biased 62 frames early on problem B**, with 7 of 10 entries opening the game before the
editor did, by up to 170 frames. A single accuracy number would have hidden that completely.

Split out, the three sub-problems have very different status:

    exit frame     SOLVED    — anchored on the creator's next burst start, 6 of 10 within one
                               frame, 8 of 10 within 20. The editor places exits on the VOICE,
                               not on the cut list: the nearest hard cut is 87-152 frames away
                               in seven of nine cases.
    whether        WEAK      — 11/15, and all four errors are false positives sharing a
                               LOCATION (gap 1, and the consecutive 10/11/12) rather than a
                               feature. Nothing measured sees that stretch.
    entry frame    UNSOLVED  — no cluster, no signal, systematically early.

## D058 — The gameplay decision sees neutral data only

`GameplayWindowFeatures` carries a window, two feature bundles and a frame rate. No Resolve
object, no reference timeline, no burst index, no timeline name. A policy that cannot see
*which* window it is looking at cannot special-case one, which is the structural version of
"do not overfit to MVP3" rather than a promise not to.

Everything the features contain is **relative**: audio levels are dB against the timeline's own
p75, motion is a ratio of the timeline's own median. Changing the mix gain or the exposure of
the whole delivery cannot move a decision — the rule D051 already established for the voice.

## D059 — Thresholds and named conditions, never a weighted score

With one reference timeline, a `gameplay_score = 0.437*silence + 0.281*audio + …` that
reproduces MVP3 would be a curve fit wearing a lab coat. `GameplayPolicySettings` is a flat set
of readable thresholds, `GameplayDecision` carries the reason tokens that fired, and the dry
run prints them per window so a decision can be understood without reading the code.

The ablation is run by switching signals off in the **same** `decide_gameplay`, never by a
second implementation, so the families cannot diverge from the shipped rule.

Result, and it decided the recommendation:

    A silence only            5/15
    B silence + audio         8/15    false+ 1, 11
    C silence + video         8/15    false+ 10, 11, 12
    D silence + audio + video 8/15    false+ 1, 10, 11, 12
    candidate (no signal)    11/15

B, C and D all land **below** the majority baseline of 10, and D inherits every false positive
of B *and* of C while fixing one false negative — the signature of two signals that are wrong
in different places, not two that combine. **D was not selected because it has more features.**
Neither secondary audio nor video motion belongs in a runtime planner on this evidence; both
stay as measurement, in `gameplay-study`.

## D060 — Gameplay snap windows are carried from the facecam ones on sufferance

The facecam windows (D052) were measured for the facecam and are not assumed to transfer.
`GameplayPolicySettings` gives entry and exit their own settings, defaulted to
`[-120, +120] ms` because that is what the measurement says is *harmless* here, not what it
says is right:

- **entry**: snapping fired on 4 of 10 and helped none. In every case the cut it found was 4-6
  frames from the raw anchor while the manual entry was 53-170 frames away. Only 3 of 10 manual
  entries are on a cut at all; the other seven are 23-132 frames from the nearest one;
- **exit**: snapping fired once, on gap 3, and made it **worse** — the raw anchor was 38 frames
  from the manual exit and the snap moved it one frame further onto a cut. That is the only
  measured instance of gameplay cut-snapping doing harm, and it argues for a narrower exit
  window, not a wider one.

## D061 — Phase 9a is a dry run; nothing it produces reaches a timeline

`gameplay-study` never calls `AppendToTimeline`, creates no preview, and the production planner
does not import `domain/gameplay.py`. `GameplayEpisodeProposal` stops at naming the two roles a
move would need — no `AssetPlacement`, no asset name, no frame count.

The mutating surface is exactly `plan-probe`'s, through the same `render_voice_track`: scratch
duplicates, one render job at a time, captured and restored Deliver state, post-run audit. It
renders more than once (voice, secondary audio, video), which is why it is metered by the same
opt-in flag and no other.

Turning a proposal into a plan is Phase 9b, and on this evidence it should not be attempted
before a second reference edit exists — the entry anchor is unsolved and the four "whether"
errors are unexplained.

## D062 — Gameplay assets are hold clips animating in 15 frames, measured not assumed

Read-only, via `TimelineItem.ExportFusionComp` on the manual instances in `DAZ_OUTPUT_MVP3` and
the existing `domain/fusion_comp.py` parser. A one-off measurement: the runtime still never
opens a Fusion graph (D007) and receives these as configuration.

    asset             Media Pool length   keyframes   instance durations observed in MVP3
    X0_TO_GAMEPLAY           45            0, 15      45, 49, 58, 68, 93, 137
    X1_TO_GAMEPLAY           45            0, 15      64, 112, 124
    X2_TO_GAMEPLAY           45            0, 15      96
    X3_TO_GAMEPLAY           45            none — no manual instance exists
    GAMEPLAY_TO_X0           45            0, 15      39
    GAMEPLAY_TO_X1           45            0, 15      23, 40, 43, 48, 48, 49, 61, 63, 88

Every measured gameplay asset animates in **15 frames**, exactly like the six facecam ones,
despite being 45 frames long in the Media Pool. The 45 is a property of the asset file and is
irrelevant to planning, the same way `X1_TO_X0`'s 42 always was (D014).

Both families are **hold** clips, not fixed-length ones: the shortest `*_TO_GAMEPLAY` is 45
frames and the shortest `GAMEPLAY_TO_X1` is 23, both far past 15, and the editor varies them
continuously. So both follow the `FACE_X*` shape (D014/D045), not the `X*_TO_X0` one.

`face_x3_to_gameplay = 15` was **inferred from the other five** by Phase 9a, because
`X3_TO_GAMEPLAY` has no manual instance to export a comp from. **Phase 9b closed this: the
creator confirmed explicitly that `X3_TO_GAMEPLAY` animates fully in 15 frames.** The value is
no longer flagged as unconfirmed in `config.example.toml`, in the handoff or in the plan.
Phase 9b also measured what the eleven exportable roles animate *to*, and all four entries
into gameplay converge on one state, so the endpoint `X3_TO_GAMEPLAY` has to reach is known
even though its own comp was never read (D064).

## D063 — `DeleteTrack` renumbers *and renames* survivors, so it cannot isolate a complement

Measured on Studio 21.0.4.5 while building the Phase 9a secondary-audio render. Deleting A1 to
leave A2+A3 shifts them to indices 1 and 2 **and renames them** `Audio 1`, `Audio 2`. In this
project all three audio tracks carry the same 21 items from the same source, so after the
deletion nothing distinguishes them and the post-condition check in `_isolate_voice_track`
cannot prove which track survived.

It correctly **refused to render** rather than guess, which is the guard working as designed.

The fix is not to weaken the check. `_silence_audio_tracks` empties the unwanted tracks with
`DeleteClips` instead of removing them: the dropped tracks end at zero items while every kept
track keeps its index, its name and its exact item count. That is strictly stronger evidence
than the delete path had, and positional rather than name-based.

`_isolate_voice_track` is untouched and still runs for the single-voice-track case — the Phase
3 path stays exactly the code proven across five phases. Two paths, one comment explaining why,
rather than one path that is worse for both.

Consequence for the analysis: **which of A2/A3 carries the game and which carries other people
is not claimed anywhere.** The names are positional and prove nothing.

## D064 — The GAMEPLAY state is a centred 1.25x push-in, and the ROI is derived from it

Measured in Phase 9b, read-only, with `TimelineItem.ExportFusionComp` on one instance of each
of the eleven roles present in `DAZ_OUTPUT_MVP3` and a parse of the exported `.comp` text. The
runtime still never opens a Fusion graph (D007); this is a one-off measurement.

Every one of the eleven is the same graph — `Loader -> Transform -> Saver`, `Size` on a
`BezierSpline`, `Center` on a `PolyPath`, keyframes at `[0]` and `[15]`. **There is no Crop, no
Mask, no Merge and no second Transform anywhere.** The whole state model is two numbers:

    state       Transform Size    Centre offset (Fusion, y up)
    X0                 1.00       (0.00, 0.00)
    GAMEPLAY           1.25       (0.00, 0.00)
    FACE_X1            1.50       (0.25, 0.25)
    FACE_X2            2.00       (0.50, 0.50)
    FACE_X3            2.50       (0.75, 0.75)

All four `*_TO_GAMEPLAY` roles converge on the same final state and both `GAMEPLAY_TO_*` roles
leave from it, which is what makes `GAMEPLAY` one state rather than a family (D053, confirmed).

A Fusion `Transform` maps output `u` to source `(u - C)/size + 0.5` with `C = 0.5 + offset`, so
the visible region follows from the two numbers. `domain/visual_episodes.Roi.from_transform`
performs exactly that arithmetic and `GAMEPLAY_ROI` is its result, not a typed-in rectangle:

    GameplayTargetROI = x [0.10, 0.90], y [0.10, 0.90]   — the central 80%, area 0.64

The derivation is checked against the facecam ladder, which is why it can be trusted:
`FACE_X1 -> [0, 0.667]`, `FACE_X2 -> [0, 0.5]`, `FACE_X3 -> [0, 0.4]`, a nested family anchored
in the corner where the facecam inset actually sits (`tests/test_visual_episodes.py`).

**Consequence, and it is a design fact rather than a detail: the gameplay zoom privileges no
region.** It is symmetric and small. It cannot magnify a corner HUD element — it crops the
outer 10%, which is exactly where this delivery's HUD lives. Any future rule of the form "the
interesting thing is inside the zoom's ROI" is therefore nearly content-free for GAMEPLAY,
though it would be meaningful for the facecam states, whose ROI is a corner.

## D065 — The picture is read spatially by the same measurement, and the runtime stays local

`vision.activity_from_frames` keeps the per-cell values of the identical mean-subtracted
absolute frame difference that `motion_from_frames` averages away, on a coarser grid (32x18 at
10 samples/s). One decode of an already-rendered file, no second notion of "motion", and no new
dependency: still ffmpeg and numpy (D055 extended, not replaced).

Reading those cells as an editorial cue stays entirely in `domain/visual_episodes.py`, which is
pure — no numpy, no ffmpeg, no file, no clock — exactly as `domain/dynamics.py` is for the
voice (D050). The boundary is the same one: `vision.py` says *how much this part of the picture
changed*; only the domain says *whether that deserves a zoom*.

**No VLM, no cloud API, no object detector and no game-specific model is a dependency of DAZ,
and none was contacted during Phase 9b.** The agent's own vision was used to *inspect* frames
during the study, which is research, and the report tags every conclusion with whether it came
from a measurement, from that inspection, or from the creator. A rule DAZ cannot recompute
locally cannot ship.

## D066 — Motion topology fails exactly as motion amount did, and the reason is nameable

Phase 9b measured nine spatial features per window — activity inside and outside the ROI, their
ratio, active-cell fraction and its peak, bounding-box area, concentration, region count,
persistence and novelty — on the same 15 would-be-X0 windows.

    every class range nests; every best single threshold scores 10 or 11 of 15
    against a majority baseline of 10 (`always gameplay`)

and, more decisively than any threshold table, **each hard negative has a manual-gameplay twin
within about one standard deviation** in the full nine-dimensional space: gap 8 (GAMEPLAY) and
gap 11 (X0) are 0.92 apart, gap 5 and gap 10 are 1.19 apart, gap 13 and gap 14 are 1.87 apart.
The classes are not merely hard to threshold; they contain near-identical pairs.

The ROI feature is inert for the reason D064 predicted: `roi_ratio` spans 0.84-1.23 across both
classes, because a centred 64%-area region samples the same picture as its complement.

**Why they fail is specific**: in a first-person game, every mouse movement translates the whole
image, so a frame difference measures the player's camera rather than the game's events. Gaps 11
and 12 are visually empty corridors and score `active cells 64-66%, bbox 0.99-1.00` — the same
as gap 10, where an NPC really does charge the camera. Separating the two needs camera-motion
compensation or a notion of an object, neither of which exists in this pipeline.

**Retired by this decision**: Phase 9a's hypothesis that gaps 10/11/12 were mainly a section or
recency phenomenon because they are consecutive. The creator's explanations supersede it — gap 1
"nothing new or interesting is visible", gap 10 "something happens but zooming adds nothing",
gaps 11 and 12 "the friend is talking about something that is not on screen". Those are four
statements about the *picture*, not about the timeline's structure.

**No candidate rule is proposed by Phase 9b.** A tuned 12/15 over features whose classes contain
twins would be fitting noise on n=15, and the honest output is the sharper question instead.

## D067 — Secondary audio is context and creator silence is a prior; neither may trigger GAMEPLAY

Stated by the creator, and now structural in the code rather than a note in a report. In
`decide_gameplay`, when `use_zoom_utility` is on, the visual verdict is the only thing that can
say *yes*; `candidate_silence_ms` and `require_secondary_audio` are AND-gates that can only ever
remove a candidate. Four tests hold the line, including
`test_secondary_audio_alone_can_never_trigger_a_gameplay_move` and
`test_a_long_creator_silence_alone_can_never_trigger_a_gameplay_move`.

This is the one durable thing Phase 9b's negative result leaves behind: even without a working
visual reader, the *shape* of the eventual rule is known —

    would-be-X0 window  ->  candidate (creator quiet, long enough)
                        ->  is there a visual episode a zoom would improve?
                        ->  yes: GAMEPLAY,  no: X0

`gameplay_by_default` stays the shipped Phase 9a candidate (11/15, zero fitted parameters) and
`use_zoom_utility` defaults to **off**, because on the reference material the visual branch
scores 7/15. Nothing about the production planner changed: it still places no gameplay clip.

## D068 — Gameplay automation is retired from the current product scope

**Superseding note applying to D053-D067.** Those decisions are historically accurate and are
not rewritten. Where any of them speaks in the present tense about a gameplay state, a gameplay
role, a gameplay config key or a future gameplay policy, read it as **archived research**: none
of it exists in the active tree any more, and no part of the runtime anticipates its return.

**What happened.** Phases 9a and 9b were research spikes, dry-run only, and both produced
negative results — deliberately, and reported as such:

- 9a measured creator silence, secondary-audio activity and visual motion amount. Every class
  range nested; the ablation put silence+audio, silence+video and all three at 8/15, *below* a
  majority baseline of 10, while a rule using no signal at all scored 11/15 with zero fitted
  parameters (D056, D059);
- 9b measured the zoom's own geometry (D064) and nine spatial features of the picture. Same
  outcome, with the reason now nameable: a frame difference in a first-person game measures the
  player's camera, not the game's events, and every hard negative has a manual-gameplay twin
  within about one standard deviation (D066). Ablation families A-F scored 8, 8, 7, 6, 6, 5.

Both results remain valid as measurements, and their reports stay in `.agent/reports/`.

**The decision, and whose it is.** No robust generic rule for placing a GAMEPLAY zoom was
found, and the creator decided not to pursue the direction for this type of video: gameplay
placement depends on context the available signals cannot see. This is a **product decision**,
not a blocked task waiting for evidence. Phase 9c is therefore CANCELLED, not BLOCKED.

**What the supported product is.** davinci-auto-zoom is **facecam-only**. Four visual states
(`x0`, `face_x1`, `face_x2`, `face_x3`) and six transitions, driven by the creator's voice.

**What was removed from the active tree**, because it existed only for 9a/9b and no facecam
path used it:

    domain/gameplay.py              the whether/where decision and its feature model
    domain/visual_episodes.py       ROI derivation, spatial statistics, zoom_utility
    vision.py                       the motion/activity envelopes
    resolve/gameplay_study.py       the study command's implementation
    CLI gameplay-study              the diagnostic itself
    STATE_GAMEPLAY + 6 transitions  from domain/transitions.py
    6 gameplay roles                from [assets] and [assets.transition_frames]
    render_voice_track's keep_audio_tracks / export_video / render_preset arguments,
      _silence_audio_tracks, the report's media_kind / kept_audio_tracks fields, and
      voice_render_preflight_failures' required_preset — the secondary-audio and video
      render paths, which only 9a/9b ever called
    tests/test_gameplay.py, tests/test_vision.py, tests/test_visual_episodes.py

Git keeps the history; the working tree does not keep the complexity. **No speculative
extension hook is left behind** — a hook is a claim about the future, and this project has
evidence for the opposite claim.

**What was NOT removed**, and must not be: any safety mechanism. Source fingerprinting, plan
validation, preview ownership, marker metadata, stale/ambiguous refusal, the recovery copy,
transactional cleanup, active-timeline restoration, Deliver restoration, render-queue
preservation and the pre/post protected audits are product features, not legacy.

**Consequences a reader should know:**

- a config still naming a gameplay role is a **clear error naming the retirement** (a
  `RETIRED_ROLES` table in `domain/transitions.py`), never a silently ignored key and never a
  migration layer;
- the plan's `PlanSource` identity loses its six gameplay asset entries, so a plan computed
  before this change is no longer byte-identical in metadata. The `structural_fingerprint`
  covers the voice track, the cut-reference track, the range and the frame rate (D030) and is
  **unchanged**; the EDITORIAL plan is unchanged, and that was verified live;
- **existing facecam previews stay fully ownable.** Every marker on a real preview names a
  facecam role, because 9a/9b placed nothing at all. A hypothetical marker naming a retired
  role classifies as `ambiguous` and blocks deletion — fail-closed, as designed. Both are
  tested;
- gameplay may be revisited one day as **new research**. If it is, it starts from a second
  reference edit, not from a hook someone left in the code.

## D069

**Continuous-talking facecam is a new RESEARCH profile. The X1/X2/X3 dynamics are frozen, and
only the reset / re-entry policy is under study.**

Phase 11a began research on a second video type: the creator's `bluescreen 2` Shorts, where he
talks almost continuously and cuts most pauses out. It is still a facecam, it uses **exactly**
the four states and six transitions of `domain/transitions.py`, and the reference edit was
replayed through that graph with **zero state-machine problems** — every manual move is legal.
So the new format needs no new state, no new role, and no new asset.

What does not transfer is one thing only: **when to return to X0, and when to re-enter
FACE_X1.** The shipped rule is a silence gate (`reset_after_silence_ms = 650` between speech
segments). Measured on the reference, Silero finds **two** speech segments in the whole
labelled range — one per Short. The shipped rule would fire at most twice where the creator
made 17 resets. It does not need retuning here; it has no events to fire on.

**In force from this decision:**

- the facecam MVP frozen in Phase 10 is untouched and stays the supported product. Phase 11a
  changed no planner, no threshold, no state, no role, no config key and no CLI command, and
  the live `plan-probe` regression is byte-identical to the Phase 10 baseline on every
  editorial field (same fingerprint `sha256:a1d107e1…d46a5483`, 15 segments, 14 bursts, 63
  valleys, 42 placements, 126 decision lines);
- **X1/X2/X3 are frozen for this research.** The creator states the promotion behaviour is the
  same in this format, and the phase took that as given. No promotion threshold was retuned or
  even examined for tuning. Any X2/X3 difference observed on `bluescreen 2` is a diagnostic,
  not scope;
- research tooling lives in `tools/research/phase11a/` and is **never imported by the
  package**. The dependency points research -> product only. It is held to the same strict
  mypy and has its own tests in `tests/research/`;
- **no profile architecture.** No `VideoProfile`, no `Strategy`, no `ResetPolicy` interface, no
  plugin system, no video-type enum. Phase 11a produced data; architecture waits for a
  validated rule, exactly as D068 taught;
- **WhisperX, torch and CUDA are not dependencies** and none were added. The transcription runs
  from a throwaway venv outside the repository;
- the creator's words are **user media**. Rendered audio, the WhisperX JSON and every per-cut
  transcript context stay local, gitignored and temporary. `.gitignore` now names them.

**The measured result** (full detail in `.agent/reports/phase-11a-continuous-facecam-reset-study.txt`):
**TRANSCRIPT HELPS BUT SEMANTICS REQUIRED.** Word timing is good enough — 360/360 tokens
aligned, boundaries 8.8 dB above the inter-word floor, and a median of 3 frames from the
picture cut at edit boundaries. But the signals DAZ could compute locally today top out at
F1 0.476 (ASR punctuation), lexical discourse markers reach only F1 0.300, and adding prosody
makes both worse. Reading what the sentences mean reaches F1 0.800 — and still cannot separate
three grammatically parallel enumeration items where one is a reset and two are not.

**Two findings that transfer now, and are worth keeping even if the direction is dropped:**

1. **the loop rule is exact.** "The last hard cut of each content island returns to X0" holds
   2/2, with delta 0, and the creator stated it in advance rather than it being fitted. A future
   deterministic override, never a learned class;
2. **gating any reset predictor on "a face state is currently held" is free precision.** It is
   nothing but the transition graph, and it removed four of six false positives (+0.21
   precision). A reset predictor evaluated without the state machine is measuring the wrong
   question.

**What the reference disproved about our own intuitions**, recorded so nobody re-assumes them:

- the "~1 second at X0" intuition is wrong. Median anchor gap 600 ms, median **pure X0 dwell
  350 ms**, spread continuously from 67 to 850 ms with no plateau. The dwell is not a constant
  the creator applies — the re-entry is anchored (to a cut, or to where the voice restarts) and
  the dwell falls out of it. A fixed 1000 ms hold would be about three times too long;
- `et donc` / `du coup` are directionally right and far too sparse to be a rule: **10 of the 13
  semantic resets carry no discourse marker at all**, and the same words appear mid-clause at
  cuts that get no reset;
- the empirical reset-to-cut window is **0 frames**, not the ±120 ms the gaming profile uses.
  15 of 17 resets sit exactly on a cut; the other 2 sit 54 and 64 frames early, placed backwards
  from an entry that lands on the following cut.

**Status: RESEARCH / NOT SHIPPED** until the creator validates a rule. The next step is the
smallest one the evidence justifies — a **blind** semantic annotation of a second Short — and
it is not assigned.


## D070 — The blind trial happened: the boundary rubric transfers, the machinery around it does not, and 36% of the edit is invisible to text

Phase 11a's family F scored F1 0.800 with the manual labels visible while it was annotated, so
it was an upper bound rather than a measurement. D069 named the one experiment that could
falsify it. Phase 11b ran that experiment properly: the semantic rubric, the rhythm rule, the
re-entry rule and four candidates P0-P3 were frozen and **committed** (`46c5e56`) before
`Timeline 1`'s Short 3 zooms were read at all. Full detail in
`.agent/reports/phase-11b-blind-predictions.txt` and
`.agent/reports/phase-11b-blind-validation.txt`.

**Result: SEMANTICS HELP BUT GENERALISATION WEAK.**

**What is now measured rather than assumed:**

- **the discourse-boundary rubric transfers.** Blind, on Short 3, all seven of P0's predictions
  correspond to a real manual reset (7/7) and it found all six semantic resets (6/6). Its
  strict F1 of 0.714 is *higher* than the 0.552 it scores on the development Shorts. The
  rubric is `AGENT_SEMANTIC_REASONING` and is still not computable by DAZ;
- **the loop rule is now 3/3, delta 0 on all three Shorts.** It remains the most reliable
  finding of the whole direction, and it is a deterministic override, never a learned class;
- **"FACE_X3 does not survive a hard cut" is a real rule with zero counterexamples** in three
  Shorts (6/6 and 2/2 on-cut, 8/8 and 4/4 counting X3 periods) — and **no time threshold is
  involved.** The creator's "X3 for N seconds -> reset" hypothesis is not what the edit
  contains. On Short 3 the rule is also entirely REDUNDANT with the semantics: it fires at two
  cuts that are already discourse boundaries;
- **there is no rhythm rule for FACE_X2.** Same-thought resets at FACE_X2 are not separable
  from same-thought non-resets by hold, cycle length or cuts-in-cycle; the distributions
  overlap and they overlap *differently* in each Short. Nothing is claimed;
- **at least 4 of Short 3's 11 resets (36%) are explained by nothing in this study.** Whether
  they are the visual "show the avatar" resets the creator described could NOT be proved: the
  composited picture cannot be observed here, and the source footage is one continuous
  full-body shot whose framing never changes. So no reset was excluded, no
  transcript-addressable subset was scored, and 36% is the measured ceiling on the transcript
  direction as it stands.

**What the blind trial disproved about our own machinery, and this is the important half:**

- **a simulated ladder position is not good enough to gate on.** D069's "gating on a held face
  state is free precision" was measured with the creator's own track. Simulated causally from
  the audio, the state agrees with his only **11/28** of the time (82% for the coarse
  face-versus-X0 gate), and every point P2/P3 lost on Short 3 is traceable to that, not to the
  rhythm hypothesis. This was declared in the checkpoint before unblinding;
- **the reset is not reliably cut-anchored.** 15/17 on-cut in the development Shorts became
  7/11 in Short 3. Two of the four off-cut resets are the pattern Phase 11a described — the
  reset placed backwards so the FACE_X1 entry lands on the cut — and they cost P0 its only two
  false positives. Those are wrong PLACEMENTS, not wrong decisions, and the two numbers must
  never be merged into one score;
- **no label-free anchor reproduces the re-entry.** Neither the next hard cut nor the next
  qualifying voice recovery beats a constant: median error 55 and 33 frames against 14 for
  "reset + 36 frames". The frozen constant then scored a 9.5-frame median error blind, better
  than it did in development. This refines D069's "the entry is anchored and the dwell falls
  out": the entry is anchored to something this study cannot see.

**In force from this decision:**

- the facecam MVP frozen in Phase 10 remains the supported product. Phase 11b changed **no**
  product source at all — `git diff d297e31 -- src/` is empty — and added no state, role,
  config key, CLI command, profile or dependency;
- **nothing from this direction may be built into the planner yet.** No `VideoProfile`, no
  `ResetPolicy` interface, no reset policy, no runtime NLP, no state-simulation fix. Fixing the
  simulator now would mean fitting it to the only blind Short that exists;
- research tooling lives in `tools/research/phase11b/`, is never imported by the package, and
  is held to the same strict mypy with its own tests;
- **the blind predictions of `46c5e56` are immutable.** They were not edited after unblinding
  and must not be. A defect found in them is reported, never repaired.

**Status: RESEARCH / NOT SHIPPED.** The next step is four yes/no answers from the creator about
Short 3's four unexplained resets (219354, 219525, 219784, 220442) — whether each exists to
show the avatar. That measurement costs nothing and decides whether the direction has a
ceiling of ~64% of this edit or a rhythm signal still to find. It is not assigned.
