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

## Phase 9a — Gameplay trigger evidence and policy discovery [DONE — ARCHIVED RESEARCH]

> **ARCHIVED RESEARCH — GAMEPLAY IS NOT PART OF THE CURRENT PRODUCT (D068).** The
> measurements below are valid and worth reading. The code they describe was removed from
> the active tree in Phase 10; it lives in Git history.

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

## Phase 9b — Visual relevance / zoom usefulness [DONE — ARCHIVED RESEARCH]

> **ARCHIVED RESEARCH — GAMEPLAY IS NOT PART OF THE CURRENT PRODUCT (D068).**

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

## Phase 9c — Gameplay planning [CANCELLED / RETIRED]

**Not blocked — cancelled.** Two measurement phases failed to find a rule on the only reference
that exists, and the creator then made a product decision: gameplay placement depends on
context the available signals cannot see, so this direction is not pursued for this type of
video (D068).

The distinction matters. A *blocked* phase is waiting for evidence and keeps its scaffolding
alive. A *cancelled* one does not: `domain/gameplay.py`, `domain/visual_episodes.py`,
`vision.py`, `resolve/gameplay_study.py`, the `gameplay-study` command, `STATE_GAMEPLAY`, the
six gameplay transitions and their config keys were all removed in Phase 10. Nothing in the
runtime anticipates their return.

What was settled and is worth keeping on paper, should anyone ever restart this as **new
research** with a second reference edit: the exit anchor is the creator's next burst start, not
a cut (Phase 9a, 6 of 10 within one frame); both gameplay clip families are hold clips
animating in 15 frames (D062); the GAMEPLAY state is a centred 1.25x push-in showing the middle
80% of the frame (D064); and the one untried classical experiment is camera-motion
compensation. **None of that is an assignment.** It would start from a second reference edit or
not at all.

## Phase 10 — Facecam MVP consolidation / gameplay retirement / release-ready CLI baseline [DONE]

**Supersedes the earlier "Phase 10 — UI / Resolve launcher / packaging" plan**, which is moved
to *Future work* below.

Goal: no new editorial logic at all. Turn the repository into a coherent product centred on
the one thing that works and was validated — facecam X1/X2/X3 from the creator's voice — and
remove the experimental complexity that only Phases 9a/9b needed.

Delivered:

- **D068**, the durable retirement decision, with the supersession note over D053-D067;
- the state graph is exactly four states and six transitions again, with a `RETIRED_ROLES`
  table so a stale config gets an explanation rather than "unknown key";
- four modules, one CLI command, six config roles and three test files removed, plus the
  secondary-audio/video render arguments in `resolve/voice_render.py` that only the study used;
- `config.example.toml`, `README.md` and `AGENTS.md` rewritten around the facecam product;
- `tests/test_facecam_golden.py` — the validated edit written out in full from synthetic
  inputs, so a change to any facecam rule has to be read before it can be merged;
- ownership/config regression tests proving existing facecam previews stay ownable and a
  retired role fails closed;
- baseline and regression reports: `.agent/reports/phase-10-pre-cleanup-baseline.txt`,
  `.agent/reports/phase-10-live-regression-report.txt`.

The acceptance criterion was that the EDITORIAL plan is byte-identical before and after, live
on `DAZ_INPUT`. Only plan-identity metadata (the six gameplay asset entries in `PlanSource`,
the six always-zero keys in `diagnostics.role_counts`) was allowed to change.

## Phase 8b — Burst extent [DEFERRED — optional calibration research, not a blocker]

Against `DAZ_OUTPUT_MVP2` a few automatic bursts still open earlier or close later than the
human's: burst 12 opens 123 frames early, burst 7 opens 41 early, burst 8 opens 18 early,
burst 6 opens 14 early, burst 1 closes 77 late. Where the extent matches, the level matches
(9 of 9); where it does not, it does not (0 of 5).

**This is no longer treated as blocking anything.** The creator watched the applied Phase 8c
preview and validated the facecam behaviour on 2026-08-18. A human reference edit is not a
golden truth to be reproduced frame-perfect, and the divergences are a **known
calibration/generalisation limitation — not currently user-visible enough to justify changing
validated behaviour**.

If it is ever picked up, the method is fixed: measure both directions for each burst (automatic
vs manual start *and* end, against the speech segments and the bridged gaps) before touching
any parameter. Do not retune `reset_after_silence_ms` or the VAD before that measurement
exists.

Note what Phase 8 already closed: **"x1 over-triggering" was not a defect.** 14 planned cycles
against `DAZ_OUTPUT_MVP`'s 12 was the old reference being looser; `DAZ_OUTPUT_MVP2` has 14
cycles in the same places.

## Phase 7b — Apply in place on a user timeline [DEFERRED]

The preview workflow is the supported write model, and remains so. Ownership exists, which is
the precondition D032 was waiting for, but the open questions are unchanged:

1. what does a user timeline look like that DAZ may write into at all — is a dedicated,
   verified-empty zoom track still required, or is "a track holding only DAZ-owned items"
   enough now that the second is provable?
2. the recovery model. `apply-preview` leaves a preview behind; an in-place apply cannot. Is
   `DAZ_RECOVERY_*` (D041) sufficient for a timeline the user is actively working in?
3. collision behaviour is still unmeasured (D032). Either measure it on a scratch or keep
   refusing to depend on it.

Do not weaken any Phase 7 refusal to make in-place work easier. `stale` and `ambiguous` stay
fail-closed.

Also carried forward, none of it blocking: partial range processing, backup/test-timeline
guidance.

## Phase 11a — Continuous-talking facecam reset research [DONE — RESEARCH, NOT SHIPPED]

Goal: find out what decides a return to X0 in a second video type, where the creator talks
almost continuously and cuts most pauses out. Reference: `bluescreen 2`, `Timeline 1`,
labelled range `[216000, 218870)` at 60 fps. Report:
`.agent/reports/phase-11a-continuous-facecam-reset-study.txt`. Decision: **D069**.

The motivating measurement, and it settles the premise on its own: Silero finds **two** speech
segments in the whole labelled range, one per Short. The shipped silence gate would fire at
most twice where the creator made **17** resets.

Delivered:

- `tools/research/phase11a/` — content islands (N of them, never two hardcoded), manual-edit
  reconstruction through the frozen transition graph, `AlignedWord` with an exact
  seconds→frame policy, cut context that never crosses an island, discourse markers scored by
  position, acoustic features, and per-family confusion metrics. 55 tests in
  `tests/research/`, none needing Resolve, no fixture containing real `bluescreen 2` content;
- a WhisperX `large-v3` French pass with forced alignment (`VOXPOPULI_ASR_BASE_10K_FR`), run
  from a throwaway venv. **No runtime dependency was added**;
- the ablation A-G, plus H as a separate reading.

Result: **TRANSCRIPT HELPS BUT SEMANTICS REQUIRED.** Word timing is not the obstacle (360/360
aligned, median 3 frames from the picture cut at edit boundaries). Locally computable signals
top out at F1 0.476; reading meaning reaches 0.800 and still cannot separate three parallel
enumeration items where one resets and two do not.

Two things that transfer now: the loop rule ("last hard cut of each island returns to X0") is
exact 2/2, and gating on "a face state is currently held" is +0.21 precision for free. Two
intuitions the reference disproved: the ~1 s X0 dwell (measured median **350 ms**, no plateau)
and the ±120 ms reset-to-cut window (measured **0 frames**).

**No production change.** The Phase 10 facecam plan-probe is byte-identical, same fingerprint.

## Phase 11b — Sealed blind validation of the reset reading [DONE — RESEARCH, NOT SHIPPED]

The experiment Phase 11a asked for, run properly. Full detail in
`.agent/reports/phase-11b-blind-predictions.txt` (sealed at `46c5e56`) and
`.agent/reports/phase-11b-blind-validation.txt`. Decision: **D070**.

The rubric, the rhythm rule, the re-entry rule and four candidates P0-P3 were frozen and
committed while the only thing ever read from `Timeline 1` was its name and its unique id.
Short 3 of `Timeline 1 copy` — which carries no zoom at all — was the blind test; the fourth
content island was detected and excluded from everything, including the transcription, which
was run on audio physically truncated at the third island's end.

**Result: SEMANTICS HELP BUT GENERALISATION WEAK.** The boundary rubric transferred (blind F1
0.714 against 0.552 in development; 7 of 7 predictions correspond to a real manual reset; 6 of
6 semantic resets found). The loop rule is now **3/3 with delta 0**. "FACE_X3 never survives a
hard cut" holds with zero counterexamples in three Shorts and needs no threshold — but it is
redundant with the semantics on Short 3, and its simulated form actively hurt. The weak link is
the **simulated ladder position**, which agrees with the creator only 11/28 of the time. Four of
eleven resets (36%) are explained by nothing measured, and it could not be proved whether they
are the visual "show the avatar" resets the creator described.

- `tools/research/phase11b/` — a causal, label-free state simulator; the frozen candidates,
  rubric and per-cut judgements; strict/subset scoring with one-to-one frame matching; the
  measured Short 3 taxonomy; the dev/blind/unblind driver. Never imported by the package,
  strict mypy, 33 tests on synthetic fixtures.
- **No production change.** `git diff d297e31 -- src/` is empty.
- The Phase 10 live regression is **outstanding, not skipped**: `davinci-auto-zoom-test` still
  carries Linux media paths, its clips are offline on Windows, and relinking is outside the
  allowed write surface. See `.agent/HANDOFF.md` for the exact command to re-run once relinked.

## Phase 11c — Four yes/no answers from the creator [RECOMMENDED, NOT ASSIGNED]

The smallest step the blind result justifies, and deliberately not implemented.

For each of Short 3's four unexplained resets — **219354, 219525, 219784, 220442** — does it
exist to show the avatar in full? If they are visual, the transcript ceiling on this edit is
about 64% and the real question becomes whether a two-thirds-complete pass is useful at all. If
they are not, a rhythm signal exists that Phase 11b failed to find.

Do **not** build before that answer: no profile abstraction, no reset policy in the planner, no
runtime NLP or vision dependency, no re-entry model, and **no fix to the state simulator** —
fixing it now would mean fitting it to the only blind Short that exists (D070).

## Future work — NOT active assignments

Listed so nobody has to rediscover them, and deliberately without technical proposals attached.
Do not start any of these without an explicit assignment.

- **Other video types / profiles.** Research on a second type has now started (Phases 11a and
  11b above, D069 and D070) and produced data, not architecture. That does not change the rule: no profile
  interface, no video-type enum, no plugin system, no strategy hierarchy until a rule is
  validated. What makes such work possible is the boundary that already exists — objective
  facts, then a pure domain planner, then placements, then an executor that makes no editorial
  decision.
- **UI / Resolve launcher / distribution** (the old Phase 10). Potential shape: an external
  PySide6 app for settings and preview, a thin Resolve Scripts menu launcher, package/install
  helpers for Windows/Linux/macOS. Only after the CLI workflow has been used in anger. Do not
  let UI architecture dictate domain logic.
- **Apply in place** (Phase 7b above).
- **Burst-extent calibration research** (Phase 8b above).
- **Gameplay**, only ever as new research starting from a second reference edit (D068). It is
  **not** a next milestone.

## Phase 9 — Gameplay states [SPLIT: 9a and 9b DONE above, 9c CANCELLED above]

> **ARCHIVED — kept for the record of what the preconditions were and how each was met.**

The three preconditions were all met:

1. ~~a reference edit that actually uses gameplay zooms~~ — `DAZ_OUTPUT_MVP3`, 42 transitions,
   10 gameplay episodes, five of the six authorised moves exercised;
2. ~~an asset family in the bin with known animation lengths~~ — six assets, all 45 frames in
   the Media Pool, all measured at 15 animation frames via read-only Fusion-comp export (D062);
3. **evidence about what triggers them** — measured twice, and the answer is that nothing
   measurable does: not motion amount (D056), not motion topology (D066). The prediction in the
   original text ("speech duration will not be the answer") was right, and so was the reason
   for making it: guessing here would have repeated D047's mistake. What the phases did instead
   was measure, report the negative, and refuse to fit a rule to 15 windows.

Meeting all three preconditions and still finding no rule is what turned this from a deferred
feature into a retired one (D068).

Deliberately still closed: demotions between facecam levels (D045).
