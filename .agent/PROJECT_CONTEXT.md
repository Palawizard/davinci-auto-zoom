# Project context — davinci-auto-zoom

## Product intent

`davinci-auto-zoom` automates a repetitive editing pattern used in dynamic gaming videos edited in DaVinci Resolve.

The creator has a dedicated voice audio track containing only their voice. When they begin speaking, the tool should create a facecam-focused zoom using prebuilt Resolve assets. Once they have stopped talking for long enough, the tool should smoothly return to the normal framing. For the reset, a nearby edit/cut boundary may be preferable to a mechanically exact speech endpoint.

The user's zoom looks live as specially named reusable assets in a specially named Media
Pool bin. Phase 1 established what they actually are on Resolve Studio 21.0.4.5: Media Pool
items of `Type: Generator` whose behaviour is a keyframed Fusion composition. DAZ treats
them as opaque referenced assets and never reconstructs them (D007).

## MVP behavior

Conceptually:

- input: current Resolve project + current timeline
- input: one configured voice audio track
- input: named zoom asset bin and named asset roles
- analyze: speech activity over timeline time
- plan: `NORMAL -> FACECAM_X1 -> NORMAL`
- output: human-readable / machine-readable dry-run plan
- later: safely apply that plan on a dedicated video track

The first production MVP should only implement the `facecam x1` state and smooth reset to `x0`.

## Explicitly out of MVP, but architecture must allow it

Future behavior includes:

- facecam x2 and x3 when the creator speaks for longer or a rule escalates
- zooms toward gameplay
- transitions from x1 -> gameplay
- gameplay -> x1
- x1 -> x2/x3 and back
- all other state-to-state transitions
- semantic decisions using actual transcript words/context

Do **not** implement those now. Model them as future states/transitions so the MVP does not hard-wire one-off Resolve operations into speech code.

## Key design choice: speech activity vs transcription

For the MVP decision "is the creator speaking?", literal transcription text is not required. Because the configured track contains only the creator's voice, voice/audio activity detection is cheaper, faster, and less error-prone than forcing speech-to-text.

Phase 1 settled the open question: **the scripting API exposes no way to read transcription
text or timestamps** (D010). Transcription exists only as write actions. The provider
architecture is therefore:

1. **Silero VAD provider — implemented and proven in Phase 3.** An isolated voice-track
   render, normalized to 16 kHz mono PCM, run through the vendored Silero ONNX model on CPU
   (D022). The originally planned `ffmpeg silencedetect` baseline was dropped rather than
   deferred: it is an amplitude gate, and distinguishing voice from other audio is the whole
   job. ONNX Runtime + numpy turned out far lighter than the "heavyweight ML" the early plan
   was avoiding — no PyTorch anywhere.
2. SRT provider — useful for deterministic fixtures/manual transcript export.
3. Whisper/provider with word timestamps — future semantic rules, not required for MVP.

A Resolve-transcript provider is **ruled out**, not merely deferred.

### Speech facts vs editing decisions

The single most important boundary in the current codebase. The VAD answers *"was the
creator making speech sounds here?"*; it never answers *"should there be a zoom here?"*.

A 650 ms pause therefore stays **two** speech segments. The detector does not merge it just
because the planner will probably not reset a zoom across it — throwing that information
away at the detector stage would make the planner's decision unmakeable. Technical tuning
lives in `[speech.vad]`, editorial timing in `[planner]` (D021).

Phase 4 adds the layer above: the planner groups speech segments into **editorial bursts**.
`reset_after_silence_ms` is the **gate** for that grouping — "is this silence worth leaving
the zoom for?" — and never a delay added to a speech end (D026). The planner is offline and
already knows how long every pause lasts.

## Safety model

The project is an editor automation tool; a wrong write can ruin a timeline. Therefore:

- read-only capability discovery comes first
- dry-run is default
- planning and execution are separate
- writes should be idempotent or tool-owned and replaceable
- any temporary Resolve state change (track enablement, current timeline, render settings, etc.) must be restored in `finally`-style cleanup
- before first real write implementation, create a throwaway/test timeline and verify
  behavior there (done in Phase 2)
- read-only commands may warn on a project/timeline mismatch; **write-capable commands must
  refuse to start** and require an explicit opt-in flag (D015)

## Time model

Use **integer timeline frames** as the internal coordinate system.

Reasons:

- Resolve editing operations are frame-oriented
- avoids accumulating float/timecode rounding errors
- works across 24/25/30/50/59.94/60 fps timelines

Convert milliseconds to frames only at config/planner boundaries. Drop-frame timecode is a display concern, not the planner's core representation.

## Asset model

Do not bind planner rules directly to clip names.

Planner emits semantic roles such as:

- `facecam_x1`
- `reset_x0`

A Resolve-side asset registry maps roles to configured Media Pool names. The observed
project uses:

- bin: `DAVINCI_AUTO_ZOOM`
- `facecam_x1`: `FACE_X1` (Generator, 132 frames native, **15-frame animation**)
- `reset_x0`: `FACE_X0_SMOOTH` (Generator, 42 frames native, **15-frame animation**)

These are defaults in `config.example.toml`, not hardcoded: names differ per user.

### Native length ≠ animation length ≠ instance length (D025)

The single most important thing to know about these assets, and the one an earlier reading of
D014 got wrong:

| Quantity | Meaning | Used by the planner |
| --- | --- | --- |
| native Media Pool length (132 / **42**) | how long the asset clip is in the bin | **never** |
| animation length (15 / 15) | how long the keyframed move takes | yes, from config |
| instance length in a plan | how long the clip is on the timeline | computed |

`FACE_X1` animates in 15 frames and then **holds** the zoom, so an instance lasts as long as
the speech needs — 30 frames, 250 frames, more — with 15 only as the floor below which the
move would be truncated. `FACE_X0_SMOOTH` completes its whole return in 15 frames, so a
15-frame instance does the entire job; **the 42-frame native duration constrains nothing**.

The animation lengths are user metadata (`[assets.transition_frames]`, in frames, no
default). DAZ never inspects the Fusion graph to discover them (D007).

Phase 1 answered most of the open questions:

- the reusable object **is** a MediaPoolItem (`Type: Generator`) — confirmed
- a placed instance has **no** link back to that MediaPoolItem (`GetMediaPoolItem()` is
  `None`), so instances are recognised by name — confirmed
- instance duration/position read correctly via `GetStart`/`GetEnd`/`GetDuration`; source
  ranges are `None` for generators — confirmed
- whether such an asset can be *inserted* with its Fusion comp intact — **answered by
  Phase 2: yes.** `MediaPool.AppendToTimeline([{clipInfo}])` creates a new instance at a
  chosen track and absolute frame, at any duration, carrying the user's Fusion composition
  (D013). Its keyframes stay anchored to the clip head and are never rescaled (D014).

## Suggested runtime shape

External Python application/package, with a small Resolve-visible launcher later if useful.

Why not start as a heavy Workflow Integration UI:

- core work is automation and analysis, not UI
- Python is the native/common Resolve scripting path
- external process is easier to test, profile, package and debug
- the domain planner remains usable in unit tests and fixtures
- UI can later be PySide6 or a thin Resolve launcher without changing the engine

Do not add a GUI until the real workflow has been validated from CLI/dry-run.

## Repository boundaries

- `domain/`: immutable models, sample↔frame timebase, VAD post-processing, reporting
  statistics, the zoom planner (bursts, placements, cut snapping, decision trace), the
  source fingerprint, plan-source validation and the pure executor decisions (target-track
  policy, expected-vs-actual comparison); zero Resolve imports, and no numpy, onnxruntime or
  ffmpeg either
- `resolve/`: Blackmagic module loading, capability discovery, snapshots, asset resolution,
  voice-track render, and the executor (`resolve/executor.py`) that applies a validated plan
  to a preview timeline
- `speech/`: provider interfaces plus the ONNX engine, ffmpeg normalization and the
  analysis pipeline. Usable with no Resolve session at all — that is a requirement, tested,
  not an accident
- `cli.py`: orchestration only; no business logic
- `tests/`: primarily pure tests; Resolve integration tests should be opt-in and clearly separated

## Known uncertainty after Phase 7

Phase 7 gave DAZ a way to recognise its own work, and the shape of what it closed matters:

- **ownership is now provable, and only provable one way.** A marker on the TimelineItem
  instance carrying a versioned, namespaced record (D035), verified live on the Generator
  items this tool actually creates. Nothing else counts — name, track, position, duration,
  Fusion graph and Media Pool asset are all things a user's clip can share exactly;
- **the safety model earned its keep on the first live run.** `DeleteClips` turned out to
  no-op silently on a non-current timeline, an undocumented restriction (D042). The result was
  a clean refusal with a retained recovery and zero items removed — not a partial mutation.
  A design that fails closed converts an unknown API precondition into an inconvenience;
- **the classifier needs four states, not two** (D037). `unowned` is the safe answer, but
  "I cannot claim this" and "I cannot even classify this" have to be different, or a corrupt
  record gets quietly deleted around;
- `DuplicateTimeline` **copies** ownership markers. Measured, not assumed. The copy therefore
  reads as `stale` against its own identity and is never destructively cleanable (D038).

Still open after Phase 7:

- **collision behaviour is still unmeasured** (D032, carried since Phase 5). Phase 7 works
  only on a track it verified empty or emptied itself, so it never had to know. Phase 7b will;
- marker capacity is uncharacterised: 28 items × 1 marker each is fine, but no upper bound on
  `customData` length or markers per item was probed;
- whether marker metadata survives a project **close and reopen**. It survives a timeline
  switch and re-read within a session, which is what the destructive commands need today, but
  the longer-lived guarantee is assumed rather than measured;
- whether other write-capable calls share the "must be current" precondition. Two do. Assume
  any third does until measured;
- the Phase 7 preview is structurally identical to the visually validated Phase 6 one — same
  28 placements, same frames — but has **not itself been watched**. Structural equality is
  strong evidence and not the same thing.

Carried forward unchanged: x1 over-triggering (14 planned vs 12 manual, still untouched and
still unmeasured), the 120 ms lookback calibrated on one timeline, the unlistened voice render,
the absent hand-labelled speech reference.

## Known uncertainty after Phase 6

Phase 6 closed the largest Phase 4 unknown, and the way it closed is the point:

- **cut snapping has now fired on real material** — 8 of 14 resets on `DAZ_INPUT`. It never
  did before because the search only looked *forward*. Measuring both directions showed the
  editorially relevant cut sits 4-7 frames *before* the detected speech end, never after
  (D034). The window is now asymmetric and ranks candidates by proximity to the reset anchor;
- the first live preview was **technically perfect and editorially wrong**, which is exactly
  the failure mode structural verification cannot catch. Every automated check passed on the
  Phase 5 preview; a human watching it found the defect in minutes. Preserving previews for
  A/B review is therefore part of the method, not a convenience.

Still open after Phase 6:

- the lookback is calibrated on **one timeline**. 120 ms has a 4x margin to the nearest
  non-matching cut here, but a faster-cut edit could put unrelated cuts inside it. This is the
  single most likely thing to need revisiting on new material;
- x1 **over-triggering is untouched and still present**: the planner places 14 zoom-ins where
  the human placed 12. Out of scope for Phase 6 on purpose — one editorial rule was changed at
  a time, and this one has no measurement behind it yet;
- whether snapping the reset *backward* ever cuts a final phoneme visibly short. The tolerance
  is bounded by design (7 frames at 60 fps) and the human edit does the same thing on the same
  frames, but only viewing can confirm it;
- asset **`unique_id`s are stable across sessions** (Phase 6 read back the same values Phase 5
  recorded, a day and several Resolve restarts later). `media_id` differs between the two
  observations; nothing depends on it, and one pair of readings is not enough to characterise
  it.

## Known uncertainty after Phase 5

Phase 5 closed one of the Phase 4 unknowns and deliberately refused to explore another:

- **applying a plan** — resolved and positive, **confirmed live** on Resolve Studio 21.0.4.5
  (2026-08-16): a validated plan becomes real `FACE_X1` / `FACE_X0_SMOOTH` instances on a
  dedicated track of a `DAZ_AUTO_PREVIEW_*` duplicate, 1:1 with the plan and frame-exact
  (D029-D033), each instance keeping its Fusion composition. Verified by an independent
  read-back, not only by the run's own report;
- **collision behaviour on a non-empty track** — still unknown, *on purpose*. The MVP writes
  only to a track it has verified is empty and refuses otherwise (D032), so Resolve's
  overwrite/shift/refuse semantics never come into play. Discovering them is only worth doing
  when ownership and `clean`/`rebuild` need it.

New after Phase 5:

- the fingerprint proves API-observable structure only; Fairlight and OFX changes that move
  no clip are invisible to it (D030);
- an item that legitimately has no stable id hashes as "no id", so two different clips with
  identical name and frames would hash alike. Not observed on this project;
- multi-`clipInfo` `AppendToTimeline` is still untested: the executor inserts sequentially
  by choice, and has no reason to batch yet;
- the preview is proof of *structure and timing*, never of how the edit looks. Human visual
  review remains the only way to certify that.

## Known uncertainty after Phase 4

Three former unknowns are now closed:

- transcription retrieval — **resolved and negative**, the installed API has no transcript
  getter (D010);
- asset insertion — **resolved and positive**, `AppendToTimeline` preserves the user's
  Fusion composition and honours track, absolute frame and arbitrary duration (D013).
  No `ExportFusionComp`/`ImportFusionComp` fallback is required;
- speech detection — **resolved and positive**, the configured voice track can be rendered
  in isolation and turned into speech segments in absolute timeline frames, with every piece
  of touched project state restored and verified (D017-D024).

What remains open:

- Phase 2's proof is **structural, not visual**: identical Fusion graph, splines and
  handles, but the rendered pixels have not been confirmed by a human;
- collision behaviour when inserting onto a track that is not empty;
- multi-clipInfo calls in one `AppendToTimeline`;
- ownership/idempotency of tool-created clips across Resolve sessions;
- **the rendered voice audio has never been listened to.** The probe proves which track it
  rendered (by name and item count), not what that track contains. That A1 is voice-only is
  the user's statement, and the 40.5% speech ratio is consistent with it, but unverified;
- whether track-deletion isolation matches Resolve's own mixdown when buses or
  timeline-level Fairlight processing are involved; the test project has neither;
- there is no hand-labelled speech reference, and `DAZ_OUTPUT_MVP` is explicitly not one;
- ~~**cut snapping has never fired on real material.**~~ On `DAZ_INPUT` the nearest hard cut
  after a burst end is 35 frames away at best (median ~190), and the 350 ms window is 21
  frames, so all 14 resets in the live plan were direct. **Closed by Phase 6** — and the
  conclusion drawn here ("the window may be too small") was wrong: the measurement was
  one-sided. The relevant cuts were *behind* the burst end all along. See D034.

See `.agent/HANDOFF.md` for the full evidence and `.agent/reports/` for saved runs.
