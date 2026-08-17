# Implementation plan — davinci-auto-zoom

This plan is intentionally milestone-driven. Do not skip capability gates simply because an API call seems plausible from memory or third-party examples.

## Phase 0 — Repository scaffold [DONE]

Goal: establish testable architecture before touching Resolve.

Deliverables:

- Python package and CLI
- Resolve module loader bootstrap
- read-only `doctor`
- frame-domain models
- pure basic planner skeleton
- local agent docs and safety rules
- test/lint configuration

Exit criteria:

- tests pass without Resolve installed
- `doctor` fails gracefully when Resolve is unavailable

## Phase 1 — Installed API capability discovery [DONE]

Goal: replace API assumptions with evidence from the user's installed Resolve version.

Verified against **DaVinci Resolve Studio 21.0.4.5**, docs at
`/opt/resolve/Developer/Scripting/README.txt`. Full results in `.agent/HANDOFF.md`.

Delivered:

- documented-vs-runtime capability matrix parsed from the installed README
  (`resolve/docs.py`, `resolve/capabilities.py`)
- read-only `doctor`, `snapshot`, `assets`, `compare` commands
- normalized plain-data snapshot models (`domain/snapshot.py`)
- structural read-only timeline comparison (`domain/compare.py`)
- configuration loader with per-user asset names (`config.py`)

Key evidence that changed the plan:

- the zoom assets are Media Pool `Generator` items whose behaviour lives in a Fusion
  composition; placed instances return `None` from `GetMediaPoolItem()` (D007, D008)
- Resolve exposes **no** transcript getter, so speech detection must be external (D010)
- the voice track is not discoverable and must be configured (D011)
- `GetIsTrackEnabled` is unreliable for non-current timelines (D009)

Hard rule (respected): **no timeline writes**.

Decision gate outcome: the insertion strategy is not yet proven. `MediaPool.AppendToTimeline`
with a `{clipInfo}` dict is the primary documented candidate;
`ExportFusionComp`/`ImportFusionComp` is the documented fallback. Both are write-gated and
are the subject of Phase 2.

## Phase 2 — Prove asset reuse on a throwaway timeline [DONE]

Goal: answer the one question Phase 1 could not answer without a write —
`user asset -> new timeline instance -> preserved effect`.

Delivered as a **spike**, not the executor. Every mutation happened on a uniquely named
`DAZ_SCRATCH_*` timeline created and deleted by the run.

Outcome: **passed**. `MediaPool.AppendToTimeline([{clipInfo}])` places both MVP assets at a
chosen track and absolute frame, at any requested duration, with the user's Fusion
composition intact. No fallback (`ImportFusionComp`, `InsertGeneratorIntoTimeline`) and no
Media Pool selection workaround were needed on 21.0.4.5. Full semantics in D013; keyframe
behaviour in D014; safety model in D015.

Delivered:

- `domain/probe.py` — pure fail-closed preflight and structural audit signatures
- `domain/fusion_comp.py` — pure `.comp` fingerprinting/comparison, the evidence that the
  *user's* effect (not an empty comp) travels with a new instance
- `resolve/write_probe.py` — the only write-capable module; transactional cleanup + audit
- `probe-write` CLI command, gated behind `--confirm-resolve-write-test`
- write-capable fakes with a mutation log, so "guard fails ⇒ zero mutations" is a test

Exit criteria — all met:

- reproducible way to place a user asset with its effect intact ✔
- `{clipInfo}` semantics recorded in `DECISIONS.md` (D013: `recordFrame` absolute,
  `endFrame` exclusive, `trackIndex` honoured) ✔
- originals provably untouched (pre/post structural audit) ✔

Known limits carried into Phase 5: structural proof is not pixel proof; collision behaviour
on a non-empty target track is untested; multi-clipInfo calls are untested.

## Phase 2b — Timeline snapshot + asset registry

Largely delivered by Phases 1-2 (`domain/snapshot.py`, `resolve/session.py`,
`domain/probe.py`). Remaining:

- role-based `AssetRegistry` for the executor, built on `resolve.session.find_asset_items`
  and the now-proven insertion strategy (D013)
- read-only commands keep warning on missing/duplicate assets; write paths already refuse
  (D015), so no further error work is needed before Phase 5

## Phase 3 — Speech activity source [DONE]

Goal: return accurate speech intervals in timeline frames.

Outcome: **passed**. Proven on Studio 21.0.4.5 against `DAZ_INPUT` A1 —

```
DAZ_INPUT A1 -> isolated render -> 16 kHz mono PCM -> Silero VAD
  -> 15 SpeechSegments in absolute frames [216046..219134)
```

with `DAZ_INPUT` and `DAZ_OUTPUT_MVP` structurally unchanged, the scratch timeline deleted,
the temporary render job deleted, the pre-existing render queue preserved, the Deliver page
restored and verified, and the active timeline restored.

Delivered:

- `domain/timebase.py` — exact `Fraction` sample↔frame conversion, NTSC-aware, with a
  tested floor/ceil boundary policy (D023)
- `domain/vad.py` — pure probability→segment post-processing, a port of Silero's own state
  machine, testable without numpy/onnxruntime
- `domain/speech_report.py` — statistics, threshold-stability, editing-reference diagnostics
- `speech/silero.py` — vendored, checksummed ONNX model run on CPU (D022)
- `speech/audio.py` — ffmpeg normalization, with actionable errors
- `speech/pipeline.py` — normalize → analyse → verify duration → map to frames
- `resolve/voice_render.py` — the second write-capable module: scratch duplicate, verifiable
  track isolation, one render job, transactional cleanup, audit
- `speech-file` (no Resolve) and `speech-probe` (opt-in) CLI commands

Key evidence that changed the plan:

- the planned `ffmpeg silencedetect` baseline was **dropped**, not merely deferred. It is an
  amplitude gate; the whole point of the phase is distinguishing voice from other audio, and
  ONNX Runtime + numpy is a far lighter dependency than the "heavyweight ML" the original
  plan feared. There is no PyTorch anywhere.
- `SetTrackEnable` cannot be verified on this build, so isolation deletes tracks on the
  scratch instead (D017)
- `StartRendering`'s keyword overload hangs the application (D018)
- `SetRenderSettings` is all-or-nothing and rejects `ReplaceExistingFilesInPlace` (D019)
- Resolve refuses to render outside Media Storage (D024)
- the config's `min_silence_ms = 650` was an editorial rule wearing a technical name (D021)

A `resolve_transcript` provider stays ruled out: the API has no way to read transcription
data back. Do not revisit without new evidence from a future Resolve release.

Exit criteria — all met:

- deterministic fixture tests ✔ (segmentation tested on synthetic probabilities; the engine
  on generated silence/voiced audio; no audio committed to the repository)
- real sample produces reasonable speech intervals ✔ (15 segments, 40.5% speech ratio,
  stable across thresholds 0.4/0.5/0.6)
- no permanent project state changes while obtaining audio ✔ (audit reports no differences)

Explicitly **not** done here, and deliberately: no zoom placement, no planner, no
`reset_after_silence_ms` logic, no Whisper.

## Phase 4 — Pure zoom planner and cut-aware timing [DONE]

Goal: turn snapshot + speech intervals into deterministic placements without Resolve calls.

Outcome: **passed**, live on `DAZ_INPUT` —

```
A1 -> 15 SpeechSegments -> 14 editorial bursts -> 14 FACE_X1 + 14 FACE_X0 placements
  zero overlaps, 41.0% zoom coverage, zero zoom clips written to Resolve
```

Delivered:

- `domain/planner.py` (rewritten, pure): `PlannerSettings`, `AssetTiming`, `AssetPlacement`,
  `PlanSource`, `ZoomPlan`, `plan_zooms`, exact ms→frame conversion
- `TimelineSnapshot.hard_cuts()` — a real cut needs a clip on both sides (D027)
- `domain/speech_report.compare_plan_to_reference` — qualitative plan-vs-human diagnostics
- `plan-probe` CLI command: snapshot → A1 voice render → Silero VAD → hard cuts → planner →
  report, in text and `--json`, and it never inserts a zoom
- `[planner]`, `[assets.transition_frames]` and `cut_reference_video_track` in the config
- 33 pure planner tests plus the config/snapshot/CLI ones

Rules, as implemented (full statements in D025-D028):

- speech segments are grouped into editorial bursts; `reset_after_silence_ms` is the **gate**
  for that grouping, never a delay added to a speech end (D026)
- `x1 = [burst_start - lead_in, reset)` — one instance for the whole burst, as long as the
  speech needs, minimum its own animation length
- `x0 = [reset, reset + reset_x0_transition_frames)` — 15 frames here, **not** the asset's
  42-frame native length (D025)
- a reset may be snapped forward to the **last** hard cut in the window that still leaves
  room for the whole x0 animation; otherwise it is direct; if even direct leaves no room, no
  reset is placed and the x1 is held (D027)
- an x1 shorter than its animation drops the whole cycle
- every one of those decisions is written to a trace the dry run prints

Key evidence that changed the plan:

- `min_zoom_ms = 450` was deleted, not re-tuned: the only technical minimum is the asset's
  own animation length, and 450 ms was an editorial guess nobody measured
- the scaffold's 80/120 ms lead-in/out defaults are now 0/0 for the same reason
- the asset's native Media Pool duration is **not** a planner constraint (D025)
- cut snapping never fired on `DAZ_INPUT`: the nearest hard cut after a burst end is 35
  frames away at best, against a 21-frame window. Left as is — tuning against one human
  timeline is exactly what this phase was told not to do

Exit criteria — all met:

- all edge cases covered by synthetic tests ✔ (no speech, gate boundaries at ±1 frame, x1 of
  exactly/below the animation length, x0 room of exactly 15/14 frames, cut accepted/rejected/
  earlier-cut fallback, empty-space boundaries, timeline edges, 60 fps and fractional rates)
- same inputs always produce the same plan ✔ (asserted on the full `to_dict()`)
- zero overlaps, sorted placements, nothing outside the range ✔ (a plan property, checked in
  the probe: an invalid plan fails the run)

## Phase 4 (historical intent, kept for context)

Goal: turn snapshot + speech intervals into deterministic actions without Resolve calls.

Inputs are now real, not hypothetical: `SpeechSegment` tuples in absolute frames from
`speech/pipeline.py`, plus `TimelineSnapshot.edit_boundaries()`.

Phase 3 measured, purely as *editing-reference diagnostics* against `DAZ_OUTPUT_MVP` (12
human zoom-ins; **not** ground truth, and not to be fitted):

- every human zoom-in overlapped a detected speech region (12/12), and no zoom-in sat on
  silence — so speech is a plausible trigger signal;
- 3 detected speech regions carry no zoom at all, which confirms the editor deliberately
  leaves speech un-zoomed. A planner that zooms on every segment will over-trigger;
- speech start → zoom start: min/median/max = **-2 / 0 / 129** frames. The zoom lands
  essentially on the speech onset;
- speech end → reset start: min/median/max = **99 / 250 / 427** frames (1.7 / 4.2 / 7.1 s at
  60 fps). This is much longer than the configured `reset_after_silence_ms = 650`, and the
  spread is wide — the reset is clearly not a fixed timeout. Investigate cut snapping and
  minimum-zoom-duration effects before freezing any rule.

Rules as sketched before implementation, with what actually shipped:

- speech start enters `FACECAM_X1` — **kept**, at the burst start
- short pauses are merged according to configured hold threshold — **kept**, and the
  threshold turned out to be a gate rather than a delay (D026)
- sustained silence exits to `NORMAL` — **kept**, at the end of the burst
- reset may snap to an eligible nearby cut boundary — **kept**, but only to a real hard cut
  on a configurable reference track, not to any clip boundary (D027)
- minimum zoom duration prevents flicker — **replaced**. There is no `min_zoom_ms`; the only
  minimum is the x1 asset's own animation length (D025)
- overlapping/adjacent speech intervals are normalized first — **kept**
- output as `ZoomAction`s — **replaced** by complete `AssetPlacement`s (D028)

The note about verifying "reset after the last cut if present and close, otherwise direct"
against concrete examples was followed: those examples are the parametrised cases in
`tests/test_planner.py`.

## Phase 5 — Safe MVP executor + persistent auto-preview timeline [DONE]

Goal: apply a reviewed plan for the first time, on a timeline the run creates itself.

Outcome: **passed, live on Resolve Studio 21.0.4.5** (2026-08-16). 28 placements became 28
frame-exact items on V3 of `DAZ_AUTO_PREVIEW_20260816_211026_c676d5af`, verified 1:1 by an
independent read-back; originals, active timeline, render queue and Deliver state all
unchanged. Details in `HANDOFF.md` and
`.agent/reports/phase-05-apply-preview-report.txt`. The chain runs all the way to real clips —

```
DAZ_INPUT -> voice render -> Silero VAD -> plan -> fresh source validation
  -> DAZ_AUTO_PREVIEW_<timestamp>_<id> (duplicate) -> empty V3
  -> one AppendToTimeline per placement -> verified 1:1 against the plan -> preview KEPT
```

Delivered:

- `domain/fingerprint.py` — canonical, order-independent SHA-256 of the voice track, the
  cut-reference track, the range and the frame rate (D030)
- `domain/plan_validation.py` — one shared `build_plan_source`, plus a total field-by-field
  comparison against a fresh snapshot; any difference refuses (D029)
- `domain/apply.py` — target-track policy (create missing tracks, accept an empty one,
  refuse a populated one), the exact `{clipInfo}` for a placement, per-insertion checks and
  the whole-track expected-vs-actual comparison
- `domain/probe.py` — `ApplyPreviewTarget` and its fail-closed preflight
- `resolve/executor.py` — the executor: duplicate, prepare, insert sequentially, verify,
  restore the user's active timeline, delete the preview on failure, keep it on success
  (D031/D032)
- `apply-preview` CLI command behind **two** flags: `--confirm-create-preview-timeline` for
  the write, and the existing render flag for the temporary voice render
- `PlanSource.asset_identities` and `PlanSource.structural_fingerprint`
- ~85 new tests: fingerprinting, plan-source validation, target-track policy, insertion
  arguments, verification, rollback, "old preview never touched", "empty plan creates
  nothing"

Deliberately **not** done, and still out of scope: apply in place on a user timeline,
ownership markers, idempotence, `clean`, `rebuild`, updating an old plan, x2/x3, gameplay
zooms, UI, packaging. Nor was any editorial parameter tuned — VAD, reset gate, cut snap
window and the 15/15 transition frames are untouched.

Original requirements, and what shipped:

- dedicated output video track — **shipped**, and it must be empty (D032)
- dry-run remains default — **shipped**: `plan-probe` is unchanged and `apply-preview` needs
  its own second flag
- preflight detects collisions — **replaced by refusing them**. Resolve's collision
  behaviour is still unknown and Phase 5 deliberately does not explore it: an occupied
  target track aborts before any insertion (D032)
- tool owns every inserted item and can identify it later — **deferred to Phase 7**. Nothing
  in this phase recognises, replaces or deletes a DAZ clip
- asset insertion is exactly the Phase 2 proven call (D013) — **shipped**, one `clipInfo`
  per call, `endFrame` exclusive
- write guards are fail-closed like the probe's (D015) — **shipped**, plus the new
  `PlanSource` validation (D029)
- placements are inserted as planned, `PlanSource` validated rather than timing re-derived —
  **shipped**. The executor makes no editorial decision at all
- no destructive overwrite of unrelated clips — **shipped**, by construction: it only ever
  writes to a duplicate, and only onto an empty track
- failure midway leaves recoverable state or performs cleanup — **shipped**: the preview is
  the transaction, and it is deleted whole (D031)

First test on a duplicate timeline: that is now the only mode there is.

Exit criteria:

- one short representative edit works end-to-end ✔ — met live: `DAZ_INPUT` → 28 placements →
  28 items on V3 of a kept preview, `RESULT: PASS`, independently re-read and audited
- rerunning does not blindly duplicate tool-owned edits — **Phase 7**: today each run makes
  its own preview and never touches an earlier one
- generated items can be removed/rebuilt without touching human edits — **Phase 7**

## Phase 6 — Reset cut alignment [DONE]

Goal: fix where the reset lands, before building any workflow on top of it.

Phase 5 delivered a correct executor and the first real preview. Human review of that preview
found a reproducible editorial defect rather than a technical one: `FACE_X0_SMOOTH` starting a
few frames *after* a video cut it should have started on. Ownership work was deliberately
postponed — there is no point making a wrong placement idempotent.

Done:

- measured every burst end against every V1 hard cut in **both** directions on the real
  `DAZ_INPUT`, with the human edit as a corroborating column
  (`.agent/reports/phase-06-cut-offset-diagnostic.txt`)
- new `[planner].cut_snap_lookback_ms` (default 120 ms = 7 frames at 60 fps), a planner
  setting, not a VAD one; no `[speech.vad]` parameter was touched
- asymmetric snap window `[base - lookback, base + window]`, ranked by proximity to the reset
  anchor, ties broken towards the later cut (D034, supersedes D027 rules 1 and 3)
- reasons split into `reset_direct` / `reset_cut_snap_forward` / `reset_cut_snap_backward`,
  with signed deltas, the window and the runner-up candidates in the decision trace
- the setting is part of `PlannerSettings`, its JSON and the `PlanSource` identity, so a plan
  built with a different lookback is refused
- live: 14 resets → 6 direct + 8 backward snaps; second preview created for A/B review

## Phase 7 — Idempotency + workflow polish [NEXT]

Goal: make repeated editing practical.

Starting point: `apply-preview` proves the executor is correct and the planner now places the
reset where the edit wants it, but every run still builds a fresh timeline and refuses a
populated target track. The next questions, in order, are how a DAZ-created clip is
*identified* later, what a second run should do with an existing preview, and only then
whether applying in place on a user timeline is safe.

Tasks:

- `plan`, `apply`, `clean`, `doctor` commands
- generated-region ownership strategy
- partial range processing
- clear summaries/errors
- config validation
- backup/test-timeline guidance

## Phase 8 — UI / Resolve launcher / packaging

Only after CLI workflow is stable.

Potential shape:

- external PySide6 app for settings + preview
- thin Resolve Scripts menu launcher
- package/install helpers for Windows/Linux/macOS

Do not make UI architecture dictate domain logic.

## Phase 9 — Advanced zoom state machine [FUTURE]

Extend the planner's roles and placements rather than bolting rules onto the executor. (The
Phase 0 `ZoomState` enum no longer exists — Phase 4 replaced the event model with placements,
D028 — so a future state machine starts from `plan_zooms` and the role constants.)

Candidate states:

- `NORMAL`
- `FACECAM_X1`
- `FACECAM_X2`
- `FACECAM_X3`
- `GAMEPLAY_*`

Candidate transition matrix:

- normal -> x1
- x1 -> x2/x3
- x2/x3 -> x1
- x1 -> gameplay
- gameplay -> x1
- gameplay -> normal
- all defined reverse/interrupt transitions

Rules may depend on duration, transcript semantics, gameplay events, edits, or manual annotations. The planner should choose states/transitions; the Resolve executor should only realize semantic actions using assets.
