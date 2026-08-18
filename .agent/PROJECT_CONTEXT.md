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
- plan: a chain of state transitions per burst, `X0 -> FACE_X1 [-> FACE_X2 [-> FACE_X3]] -> X0`
- output: human-readable / machine-readable dry-run plan
- apply that plan on a dedicated video track of a preview timeline

Phases 4-7 implemented the `facecam x1` state and the smooth reset to `x0`. **Phase 8
generalised that into a real state/transition model** and added the facecam ladder (D044-D047).
**Phase 8c changed what earns a rung of that ladder** (D049-D052): not elapsed talking time, but
a dip in the voice followed by a clear pick-up, read from an energy envelope of the same PCM the
VAD consumes. Every transition — entry, promotion and reset — may now land on a nearby hard cut.

## Explicitly out of scope, but architecture must allow it

Future behavior includes:

- zooms toward gameplay
- transitions from facecam -> gameplay and gameplay -> facecam
- semantic decisions using actual transcript words/context

Do **not** implement those now, and do **not** stub them either (D048). They become entries in
`domain/transitions.py`'s table when there is a reference edit and an asset family to measure
against — one honest change with evidence behind it, rather than a half-built abstraction that
constrains the measurement in advance.

Deliberately closed by Phase 8 and **not** reopened: demotions. `x3 -> x2` and `x2 -> x1` do
not exist and are not planned. The way out of any facecam level is all the way out (D045).

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

The single most important boundary in the current codebase. The audio layer answers *"was the
creator making speech sounds here?"* and *"how loud were they here?"*; it never answers
*"should there be a zoom here?"* or *"does that dip deserve a tighter level?"*. Phase 8c added
a second objective signal on the same side of that line — a dBFS energy envelope — and put its
editorial reading (valleys, recoveries, promotion cues) in `domain/dynamics.py`, above it
(D050).

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

The planner emits **transition roles** — the moves of the state graph, not the levels
(D044/D045):

```
x0      -> face_x1      x0_to_face_x1          face_x1 -> x0   face_x1_to_x0
face_x1 -> face_x2      face_x1_to_face_x2     face_x2 -> x0   face_x2_to_x0
face_x2 -> face_x3      face_x2_to_face_x3     face_x3 -> x0   face_x3_to_x0
```

A Resolve-side asset registry maps roles to configured Media Pool names. The observed project
uses bin `DAVINCI_AUTO_ZOOM` and, all Generators with a **15-frame animation**:

| role | clip | native frames |
| --- | --- | --- |
| `x0_to_face_x1` | `FACE_X1` | 132 |
| `face_x1_to_face_x2` | `FACE_X2` | 132 |
| `face_x2_to_face_x3` | `FACE_X3` | 132 |
| `face_x1_to_x0` | `X1_TO_X0` | 42 |
| `face_x2_to_x0` | `X2_TO_X0` | 42 |
| `face_x3_to_x0` | `X3_TO_X0` | 42 |

`FACE_X0_SMOOTH`, the Phase 5-7 reset asset, no longer exists in the bin; `X1_TO_X0` replaced
it. These are defaults in `config.example.toml`, not hardcoded: names differ per user, and only
`x0_to_face_x1` / `face_x1_to_x0` are required.

### Native length ≠ animation length ≠ instance length (D025)

The single most important thing to know about these assets, and the one an earlier reading of
D014 got wrong:

| Quantity | Meaning | Used by the planner |
| --- | --- | --- |
| native Media Pool length (132 / **42**) | how long the asset clip is in the bin | **never** |
| animation length (15) | how long the keyframed move takes | yes, from config |
| instance length in a plan | how long the clip is on the timeline | computed |

Every **promotion** asset (`FACE_X1`, `FACE_X2`, `FACE_X3`) animates in 15 frames and then
**holds the level it reached**, so an instance lasts as long as the burst needs — 26 frames,
142 frames, more — with 15 only as the floor below which the move would be truncated. Every
**reset** asset (`X1_TO_X0`, `X2_TO_X0`, `X3_TO_X0`) completes its whole return in 15 frames, so
a 15-frame instance does the entire job; **the 42-frame native duration constrains nothing**.

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

## Known uncertainty after Phase 9a/9b

Phases 9a and 9b are both measurement phases and both main results are negative ones. Read
`.agent/reports/phase-09a-mvp3-gameplay-analysis.txt` and
`.agent/reports/phase-09b-visual-zoom-utility-analysis.txt` before assuming anything about
gameplay triggers.

- **the Phase 8c preview has been WATCHED and validated by the user.** The facecam behaviour
  — `X0 -> FACE_X1` on a burst, `FACE_X2` on the first voice valley/recovery, `FACE_X3` on the
  second, cut snapping on every transition, the reset back to X0 — is now confirmed good
  visually as well as structurally, and is to be treated as stable. Do not change facecam
  logic without a demonstrated bug. This retires the "no preview has been watched since Phase
  6" uncertainty carried below;
- **nothing measured predicts which silence becomes gameplay** (D056). Silence duration,
  secondary-audio activity, and visual motion all have *fully nested* class ranges on
  `DAZ_OUTPUT_MVP3`: the shortest gap that became gameplay is shorter than the shortest that
  stayed X0, and the longest silence in the timeline stayed X0. Every single-threshold rule
  scores 10 or 11 of 15 against a majority baseline of 10;
- **the ablation says do not ship either signal.** Silence+audio, silence+video and all three
  together each score 8/15 — *below* the baseline — while the no-signal rule scores 11/15.
  Both renders stay as measurement tools; neither belongs in a runtime planner (D059);
- **the exit anchor is solved, the entry anchor is not** (D057). Gameplay exits are placed on
  the creator's next burst start (6 of 10 within one frame), not on the cut list (87-152 frames
  away in seven of nine cases). Entries have no cluster and no signal, and the proposal is
  systematically ~62 frames early;
- **the four "whether" errors are explained by the creator, not by a feature** (D066). Gap 1:
  friends talk but nothing new is visible. Gap 10: something happens, but zooming would add
  nothing. Gaps 11 and 12: the friend is talking about something that is not on screen. This
  *retires* Phase 9a's guess that the three consecutive windows were a section/recency effect;
- **motion topology fails exactly as motion amount did** (D066). Phase 9b measured activity
  inside and outside the zoom's own region, their ratio, active-cell fraction and peak,
  bounding-box area, concentration, region count, persistence and novelty: every class range
  nests, every best threshold is 10 or 11 of 15, and each hard negative has a manual-gameplay
  twin within about one standard deviation of it. The ablation A-F scores 8, 8, 7, 6, 6, 5
  against the no-signal rule's 11;
- **the reason is nameable**: a frame difference in a first-person game measures the player's
  camera, not the game's events — visually empty corridors score the same "activity" as an NPC
  charging the camera. What separates the classes is *subject, apparent size, and position*,
  and this pipeline has no notion of an object (D066);
- **the gameplay zoom privileges no region** (D064). Measured from the Fusion comps: a centred
  1.25x push-in showing the middle 80%. It cannot magnify a corner HUD element; it crops the
  outer 10%. Any "is the event inside the zoom's ROI" rule is therefore nearly content-free for
  GAMEPLAY, though meaningful for the facecam states;
- **secondary audio is context and creator silence is a prior** — neither may ever trigger a
  gameplay move on its own. Structural in `decide_gameplay` and tested (D067);
- `face_x3_to_gameplay` has **no manual instance anywhere in MVP3**, but its 15-frame length is
  now **confirmed by the creator** and the state it must land on is measured (D062, D064);
- everything above rests on **one reference timeline and 15 windows**. The negative result is
  robust to that (nested ranges are not a small-sample artifact); the candidate rule's 11/15 is
  not.

Still true, and now more sharply: **burst extent (Phase 8b) is not just a facecam problem.**
Three of the ten manual gameplay entries sit inside a detected burst, and episode 1's -77
frames is exactly the burst-1 overrun Phase 8c recorded. Fixing burst extent would clean up the
gameplay entry measurements before anyone tries to model them.

## Known uncertainty after Phase 8c

- **the level model is now only as good as the burst extent.** Where the automatic burst
  matches the human's cycle, the peak level matches 9 times out of 9; where it does not, 0 of 5.
  Nothing else explains any divergence. Phase 8b is no longer an improvement, it is the
  remaining defect;
- **no local feature of a single valley separates a used cue from an unused one.** Depth and
  duration overlap completely across the 63 valleys the reference material contains. The
  separation comes from the ladder having two rungs, not from the detector being clever. A
  successor that wants better frame agreement needs a different kind of evidence, not a tighter
  threshold;
- **frame agreement is weaker than level agreement.** Where both promote, the planner takes the
  *first* usable cue and the human sometimes takes a later one, so deltas run to -50 frames even
  when the level is right;
- the thresholds sit on plateaus rather than spikes, which is the best available evidence they
  transfer to other material — and still not evidence that they do. One speaker, one timeline,
  7 manual promotions;
- the envelope has never been listened to alongside the video. It is calibrated against clip
  positions in `DAZ_OUTPUT_MVP2`, not against anyone's ears.

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
  strong evidence and not the same thing. (Superseded for the *behaviour* by Phase 9a: the
  Phase 8c preview, which supersedes this one editorially, has now been watched and validated.
  This particular timeline still has not been, and no longer needs to be.)

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
