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

## Phase 7 — Persistent ownership + safe clean/rebuild [DONE]

Goal: let DAZ recognise the clips it created, distinguish them from a user's even when the
assets are identical, remove only its own, and rebuild the same plan without stacking
duplicates.

Delivered, and proven live on Studio 21.0.4.5 (see
`.agent/reports/phase-07-ownership-probe-report.txt` and
`.agent/reports/phase-07-live-workflow-report.txt`):

- **`domain/ownership.py`** — versioned, namespaced record; canonical serialization;
  deterministic `placement_id`; four-state classifier (`owned` / `unowned` / `stale` /
  `ambiguous`); free-marker-frame selection. Pure, no Resolve (D035, D036, D037, D043).
- **`resolve/ownership.py`** — the only place a marker is read or written. `AddMarker` onto a
  local frame proven free, then a re-read that must return the record byte-identically.
- **`probe-ownership`** — isolated live capability probe on a scratch it creates and deletes.
  14/14 checks. Established that markers attach to the *instance* not the shared asset,
  survive a timeline switch, and are **copied by `DuplicateTimeline`** (D038).
- **`apply-preview`** — now claims every created item and re-reads the whole track through the
  classifier. 100% owned or the whole preview rolls back.
- **`clean-preview`** / **`rebuild-preview`** — destructive, each behind its own flag, each on
  one explicitly named preview, each preceded by a `DAZ_RECOVERY_*` duplicate (D040, D041).

Live proof: `apply` → 28/28 owned → `clean` → 28 removed → `rebuild` → 28/28 owned →
`rebuild` again → byte-identical `(role, start, end, placement_id)` for all 28, no duplicates
(D043). Independent post-run audit: 22/22.

One live bug found and fixed: `DeleteClips` silently no-ops on a non-current timeline (D042).

**Explicitly out of scope, and still is:** applying in place on `DAZ_INPUT` or any user
timeline; collision resolution; ripple editing; replacing a user clip. The empty-target-track
rule (D032) is unchanged.

## Phase 8 — Multi-level facecam transition engine [DONE]

Goal: stop modelling the edit as one zoom level and its opposite, and let the framing tighten
while the creator keeps talking.

Delivered, and proven live on Studio 21.0.4.5 (see
`.agent/reports/phase-08-mvp2-analysis.txt`, `.agent/reports/phase-08-plan-comparison.txt` and
`.agent/reports/phase-08-live-workflow-report.txt`):

- **`domain/transitions.py`** — the visual states (`x0`, `face_x1`, `face_x2`, `face_x3`), the
  six allowed transitions as a closed table, and role lookup. A forbidden move raises; it is
  never a silent no-op (D044, D045).
- **the planner reasons in states**, emitting a chain per burst: one entry, zero or more
  promotions, one reset whose asset depends on the level reached. `AssetTiming` became a
  role->frames map and doubles as the capability list — an unconfigured role is a move this
  project cannot make (D045).
- ~~**promotions are earned by sustained speech**, never cut-snapped (D046). Four configurable
  thresholds, calibrated on `DAZ_OUTPUT_MVP2` (D047).~~ **Superseded by Phase 8c** (D049,
  D052): the four duration thresholds are gone and every transition may snap to a cut.
- **the executor did not change shape**: resolve role -> asset name, append at the planned
  frames, verify, tag. It never learned what a level is, which is the property that keeps
  gameplay a table change later.

Live proof on `DAZ_INPUT`: 15 speech segments -> 14 bursts -> **40 placements** (14 entries, 8
x2 promotions, 4 x3 promotions, 14 resets across all three return assets), 40/40 inserted,
40/40 owned; `clean` removed all 40; two rebuilds produced identical `(role, start, end,
placement_id)`. The reset frames, the burst boundaries and the 39.9% coverage are **identical
to Phases 6 and 7** — the multi-level model changed how the zoomed frames are subdivided and
nothing else.

Peak level matches the human edit on 11 of 14 cycles. Of the 3 misses, 2 are burst-extent
disagreements inherited from Phase 6 and 1 is human nuance no duration rule can express
(D047).

**Explicitly out of scope, and still is:** gameplay states (D048, not even stubbed), demotions
(`x3 -> x2`, `x2 -> x1` — closed by D045, not deferred), apply in place, collision resolution.

## Phase 8c — Intra-burst voice dynamics + cut snapping for every transition [DONE]

Goal: stop predicting the level from the *length* of a burst, and read what the voice actually
does inside it. Delivered, and proven live on Studio 21.0.4.5 (see
`.agent/reports/phase-08c-voice-dynamics-analysis.txt`,
`.agent/reports/phase-08c-plan-comparison.txt` and
`.agent/reports/phase-08c-live-workflow-report.txt`):

- **`speech/energy.py`** — short-time RMS -> dBFS envelope, built from the same normalized
  16 kHz PCM the VAD already decoded. One render, one ffmpeg pass, two readers (D050).
- **`domain/dynamics.py`** — `EnergyEnvelope` / `VoiceValley` and the valley + recovery
  detector. Pure, numpy-free, and entirely relative in dB against the cycle's own voice level,
  so a gain change cannot change the edit (D051).
- **the planner climbs the ladder on cues, not clocks** (D049): first qualifying recovery ->
  `face_x2`, second -> `face_x3`, further cues ignored. A cue is skipped, never moved, when the
  previous animation has not finished or the new level could not be held 400 ms.
- **one cut-snapping helper for every transition class** (D052), with per-class windows:
  `[-120, +350]` ms for a reset (Phase 6's, unchanged), `[-120, +120]` ms for entries and
  promotions. A cut never creates a transition, and a nearer invalid cut gives way to the next
  valid one.
- **`[speech.energy]` is part of `PlanSource`** and is compared before any write.

Live proof on `DAZ_INPUT`: 15 speech segments -> 14 bursts -> **42 placements**, 63 valleys of
which 14 became promotions. Peak level matches the human on 9 of 14 — **9 of 9 where the burst
extent matches the human's cycle, 0 of 5 where it does not.** All 14 resets are frame-identical
to Phases 6, 7 and 8; the source fingerprint is unchanged.

**Explicitly out of scope, and still is:** gameplay (D048), demotions (D045), burst-extent
changes (Phase 8b — deliberately not touched to keep this phase's measurement clean), apply in
place, collision resolution.

**The preview was watched and validated by the user on 2026-08-18.** The facecam behaviour is
confirmed good editorially, not only structurally, and is now the stable baseline: do not
change it without a demonstrated bug.

## Phase 9a — Gameplay trigger evidence and policy discovery [DONE — measurement, dry run only]

Goal: find out *when* a region that would be X0 deserves to be GAMEPLAY instead, from the
user's own manual edit, and add the gameplay states to the graph without disturbing the
facecam. Delivered; see `.agent/reports/phase-09a-mvp3-gameplay-analysis.txt`,
`.agent/reports/phase-09a-gameplay-policy-comparison.txt` and
`.agent/reports/phase-09a-live-workflow-report.txt`.

- **`domain/transitions.py` gains `STATE_GAMEPLAY` and exactly six moves** (D053), all six
  observed in `DAZ_OUTPUT_MVP3` before being added, with `GAMEPLAY -> FACE_X2/X3` and
  `GAMEPLAY -> GAMEPLAY` forbidden and raising like any other move outside the table.
- **The six roles are optional capabilities** (D054), and that is proven rather than claimed:
  `plan-probe` before and after produces 42 placements identical in
  `(role, start, end, reason, cut, burst)`, the same bursts, valleys and fingerprint.
- **`vision.py`** — rendered video -> ffmpeg -> 64x36 grayscale at 10/s -> a motion envelope.
  Objective visual facts only, no new dependency, no semantics (D055).
- **`domain/gameplay.py`** — pure. Reconstructs the manual state sequence, builds the
  silence-window population, computes relative audio/video features, and decides. It keeps
  "gameplay or X0" and "exactly where" apart (D057) and emits no `AssetPlacement` (D061).
- **`gameplay-study`** — the diagnostic CLI. Three renders through the same `render_voice_track`
  primitive, therefore the same audit, cleanup and refusal behaviour. Never places anything.

**The result is a negative one, and it is the phase's main finding.** The prior hypothesis —
that the length of the creator's silence is the main gameplay signal — is measurably wrong
here (D056), and so is every other signal measured: silence duration, secondary-audio activity
and visual motion all have fully nested class ranges. The ablation puts silence+audio,
silence+video and all-three at 8/15, *below* the majority baseline of 10, while a rule using no
signal at all scores 11/15 with zero fitted parameters. **Neither the secondary-audio render
nor the video render belongs in a runtime planner** on this evidence (D059).

What *was* solved: the exit anchor. Gameplay exits are placed on the creator's next burst
start (6 of 10 within one frame), not on the cut list (87-152 frames away in seven of nine
cases). The entry anchor is untouched — no cluster, no signal, systematically ~62 frames early.

**Explicitly out of scope, and still is:** gameplay placements in a production `ZoomPlan`,
`apply-preview` for gameplay, gameplay ownership/rebuild, apply in place, any semantic CV,
transcription, learned classifiers, further gameplay states or transitions.

## Phase 9b — Visual relevance / zoom usefulness [DONE — measurement, dry run only]

Goal: not "what is moving" (Phase 9a's failed question) but **"is there something on screen the
GAMEPLAY zoom would actually help show"**, and can that be represented by generic, explainable
visual features. Delivered; see `.agent/reports/phase-09b-visual-zoom-utility-analysis.txt`,
`.agent/reports/phase-09b-policy-comparison.txt` and
`.agent/reports/phase-09b-live-workflow-report.txt`.

- **The zoom's geometry is now measured, not assumed** (D064). Read-only `ExportFusionComp` on
  one instance of each of the eleven roles that exist in `DAZ_OUTPUT_MVP3`: every one is a
  single Fusion `Transform`, keyframes at 0 and 15, no crop and no mask anywhere. `GAMEPLAY` is
  `Size 1.25` with the centre untouched; the facecam ladder is `1.5/2.0/2.5` with the centre
  walked into the corner the facecam inset occupies. All four entries converge on one state.
- **`GameplayTargetROI` is derived from those two numbers** by `Roi.from_transform`, not typed
  in: the central 80% of the frame. The derivation is validated by reproducing the facecam
  ladder's corner rectangles.
- **`vision.activity_frames`** keeps the existing frame-difference measurement per cell on a
  32x18 grid — one extra ffmpeg pass over the file the study already renders, no new
  dependency, no second Resolve render (D065).
- **`domain/visual_episodes.py`** — pure: ROI masks, inside/outside activity, active-cell
  fraction and peak, bounding box, concentration, connected regions, persistence, novelty,
  `zoom_utility`, and `VisualWindowAnnotation` for the study labels.
- **`domain/gameplay.py`** gains a second shape for question A (D067): with `use_zoom_utility`
  on, the visual verdict is the only thing that can say *yes*, while creator silence and
  secondary audio become gates that can only ever remove a candidate. Off by default.

**The result is negative again, and this time the reason is nameable** (D066). Nine spatial
features, every class range nested, every best single threshold 10 or 11 of 15 against a 10/15
baseline — and, decisively, **each hard negative has a manual-gameplay twin within about one
standard deviation** in the full feature space. The ablation runs A-F live: 8, 8, 7, 6, 6, 5,
against the no-signal candidate rule's 11. Families C-F have zero false positives *and* refuse
eight of the ten positives, which is a conservative rule, not a discovery.

Why: in a first-person game the mouse translates the whole picture, so a frame difference
measures the camera and not the game. Visually empty corridors (gaps 11, 12) score the same
"activity" as an NPC charging the camera (gap 10). The three properties that actually separate
the classes — is there a subject, how big is it on screen, is it where the zoom keeps it — are
all statements about *objects*, and this pipeline has no notion of one.

**No candidate rule is proposed**, deliberately: fitting one to 15 windows whose classes contain
twins would bake in exactly the overfit D056 refused.

The four user explanations (gaps 1, 10, 11, 12) are recorded in D066 and retire Phase 9a's
section/recency hypothesis. `X3_TO_GAMEPLAY = 15` is **confirmed by the creator** and no longer
marked inferred anywhere (D062).

**Explicitly out of scope, and still is:** gameplay placements in a production `ZoomPlan`,
`apply-preview` for gameplay, gameplay ownership/rebuild, apply in place, VLM or cloud runtime,
object detectors, game-specific logic, semantic transcription, facecam changes.

## Phase 9c — Gameplay planning [BLOCKED — there is no rule to plan with]

Turning proposals into real placements. **Two phases of measurement have now failed to find a
rule on the only reference that exists**, so this stays blocked, and the blocking is the point.

In descending order of expected value:

1. **A second reference edit that uses gameplay.** Everything below is worth less than this.
2. **Camera-motion compensation** — the one classical experiment not yet run, and the honest
   prerequisite for any "how big is the subject" feature. Estimate a global translation per
   sample over the existing 32x18 grid (pure numpy, no new dependency), subtract it, and
   re-measure Phase 9b's nine features. If activity that does not follow the camera separates
   the classes, there is a rule; if it does not, the classical approach is exhausted and the
   next step is the semantic contract in section 10 of the Phase 9b analysis report — as
   offline research, never as a runtime dependency (D065).
3. **Phase 8b (burst extent) first.** Three of ten manual gameplay entries sit inside a detected
   burst, and one of them is exactly Phase 8c's recorded burst-1 overrun. The entry measurements
   are contaminated by it.
4. Only then: the entry-anchor rule and `AssetPlacement` emission for the six gameplay roles.

Two things are already settled for whenever it is written. The **exit anchor** is the creator's
next burst start, not a cut (Phase 9a, 6 of 10 within one frame). Both gameplay clip families
are **hold** clips animating in 15 frames (D062), so a `*_to_gameplay` placement runs from the
move to whatever comes next and a `gameplay_to_*` placement has the same shape — the `FACE_X*`
rule, not the `X*_TO_X0` one. `X3_TO_GAMEPLAY`'s 15 frames are confirmed; its endpoint state is
known from the other three entries (D064).

A third is a hypothesis worth testing rather than a settled fact: where a **visual event onset**
exists, it predicted the editor's entry frame about twice as closely as the start of the silence
did (|delta| 3, 4, 29, 43 versus 0, 10, 53, 85) — on four episodes, from a detector that tracks
the camera. Re-measure it after step 2 before believing it.

## Phase 8b — Burst extent [OPEN — no longer the imposed next milestone]

**Phase 8c raised the value of this phase from "highest available" to "the single remaining
cause of every level divergence".** Every one of the 5 cycles where Phase 8c disagrees with
`DAZ_OUTPUT_MVP2` about a level is a cycle whose burst extent disagrees first; every one of the
9 where the extent matches, the level matches too. The 8c report's section 4 is the table.

The offsets to explain, from that table: burst 12 opens 123 frames early, burst 7 opens 41
early, burst 8 opens 18 early, burst 6 opens 14 early, burst 1 closes 77 late.

Two of the three level divergences of Phase 8 were already this problem:

1. cycle 1: the automatic reset lands **77 frames** after the human's, turning a 74-frame
   manual cycle into a 149-frame automatic one, which then promotes twice;
2. cycle 12: the one burst built from two speech segments. The planner opens it **123 frames**
   before the human does (40 manual frames against 163 automatic).

Both are pre-existing Phase 6 behaviour that only became visible once level depended on burst
length. Measure both directions, as Phase 6 should have from the start: for each burst, the
offset between the automatic and manual start *and* end, against the speech segments and the
bridged gaps. Do not tune `reset_after_silence_ms` before that measurement exists.

Note what this phase already closed: **"x1 over-triggering" was not a defect.** 14 planned
cycles against `DAZ_OUTPUT_MVP`'s 12 was the old reference being looser; `DAZ_OUTPUT_MVP2` has
14 cycles in the same places. That entry can be struck from the carried-uncertainty list.

## Phase 7b — Apply in place on a user timeline [DEFERRED]

Ownership now exists, which is the precondition D032 was waiting for. The open questions, in
order:

1. what does a user timeline look like that DAZ may write into at all — is a dedicated,
   verified-empty zoom track still required, or is "a track holding only DAZ-owned items"
   enough now that the second is provable?
2. the recovery model. `apply-preview` leaves a preview behind; an in-place apply cannot. Is
   `DAZ_RECOVERY_*` (D041) sufficient for a timeline the user is actively working in, or does
   in-place work need something stronger?
3. collision behaviour is still unmeasured (D032). Either measure it on a scratch or keep
   refusing to depend on it.

Do not weaken any Phase 7 refusal to make in-place work easier. `stale` and `ambiguous` stay
fail-closed.

Also carried forward from the earlier Phase 7 list, none of it blocking:

- partial range processing
- config validation
- backup/test-timeline guidance

## Phase 10 — UI / Resolve launcher / packaging

Only after CLI workflow is stable.

Potential shape:

- external PySide6 app for settings + preview
- thin Resolve Scripts menu launcher
- package/install helpers for Windows/Linux/macOS

Do not make UI architecture dictate domain logic.

## Phase 9 — Gameplay states [SPLIT: 9a and 9b DONE above, 9c BLOCKED above]

**The state machine itself now exists** (Phase 8, D044), and as of Phase 9a so do the gameplay
states in it (D053). This section is kept for the record of what the preconditions were and
how each was met.

The three preconditions, all of which **are** now met:

1. ~~a reference edit that actually uses gameplay zooms~~ — `DAZ_OUTPUT_MVP3`, 42 transitions,
   10 gameplay episodes, five of the six authorised moves exercised;
2. ~~an asset family in the bin with known animation lengths~~ — six assets, all 45 frames in
   the Media Pool, all measured at 15 animation frames via read-only Fusion-comp export
   (D062). `X3_TO_GAMEPLAY` alone has no manual instance; its 15 frames are **confirmed by the
   creator** rather than measured, and Phase 9b measured the state it has to land on (D064);
3. **evidence about what triggers them** — measured twice, and the answer is that nothing
   measurable does: not motion amount (D056), not motion topology (D066). The prediction in the original text ("speech duration will not be the answer")
   was right, and so was the reason for making it: guessing here would have repeated D047's
   mistake. What Phase 9a did instead was measure, report the negative, and ship a rule with
   zero fitted parameters.

Deliberately still closed: demotions between facecam levels (D045), and any gameplay state or
transition beyond the six (D053). Nothing observed so far wants either.

Later rules may depend on transcript semantics, gameplay events or manual annotations. The
planner chooses states and transitions; the executor only realizes them with assets. That
boundary is not up for renegotiation.
