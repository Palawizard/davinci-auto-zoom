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
