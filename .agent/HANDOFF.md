# Agent handoff

## Current state

**Facecam MVP is the current supported product baseline.** davinci-auto-zoom automates the
creator's facecam zooms from their own voice, and nothing else. Phase 10 froze that behaviour,
retired gameplay from the product (D068), and cleaned the tree to match. **Phases 11a, 11b and
11c changed none of it**: they are research on a second video type, and that second profile
stays **RESEARCH / NOT SHIPPED** until the creator validates a rule and an explicit integration phase
is assigned.

    branch              dev                 main untouched
    Phase 10 change     810c9a2             the retirement + consolidation
    Phase 11a           d297e31             research, no product change
    Phase 11b blind     46c5e56             the sealed prediction checkpoint
    Phase 11b final     781cea0             research, no product change
    Phase 11c           this session        research, no product change
    Resolve tested      DaVinci Resolve Studio 21.0.4.5 — Linux (11a), Windows 11 (11b, 11c)
    projects touched    bluescreen 2 (research), davinci-auto-zoom-test (regression, BLOCKED)

`git diff 781cea0 -- src/` is **empty**. Phase 11c touched no product source.

A commit cannot name its own SHA, so `git log origin/dev` is the authoritative branch tip.

## The graph — all of it

    STATES      x0   face_x1   face_x2   face_x3

    x0       -> face_x1     x0_to_face_x1        REQUIRED
    face_x1  -> face_x2     face_x1_to_face_x2   optional
    face_x2  -> face_x3     face_x2_to_face_x3   optional
    face_x1  -> x0          face_x1_to_x0        REQUIRED
    face_x2  -> x0          face_x2_to_x0        optional
    face_x3  -> x0          face_x3_to_x0        optional

Climbed one rung at a time, never descended. No demotions (D045). **No GAMEPLAY state** — it
was removed, not deferred, and no hook is left for it (D068).

Phases 11a, 11b and 11c are independent evidence that this vocabulary generalises: three
Shorts of a second, quite different video type replayed through it with **zero state-machine
problems** (D069, D070, D072). No state, role or asset was added, and none was needed.

## Core settings, validated and not to be changed without a demonstrated bug

    reset_after_silence_ms      650    a GATE between speech segments, never a delay
    zoom_lead_in_ms / _out_ms   0 / 0
    cut_snap_window_ms          350    reset anchor, forward
    cut_snap_lookback_ms        120    reset anchor, backward (7 frames at 60 fps)
    zoom_cut_snap_window_ms     120    entries and promotions, symmetric
    zoom_cut_snap_lookback_ms   120
    promotion_min_drop_db       20     relative to the burst's own voice level
    promotion_recovery_within_db 6
    promotion_min_valley_ms     30
    promotion_max_valley_ms     650
    promotion_min_hold_ms       400
    [speech.energy]             window 30 ms, hop 10 ms, smoothing 30 ms
    [speech.vad]                Silero's own defaults (0.5 / 250 / 100 / 30)

Every promotion threshold is **relative, in dB**, so a microphone gain change cannot change the
edit. A cut never *creates* a transition; with no valid cut in the window a transition stays
exactly on its raw audio anchor.

## Supported user workflow

    doctor  ->  assets  ->  plan-probe  ->  apply-preview  ->  WATCH THE PREVIEW
                                        ->  clean-preview / rebuild-preview

Diagnostics still available: `snapshot`, `compare`, `speech-file`, `speech-probe`,
`probe-write`, `probe-ownership`. **Retired: `gameplay-study`.** Phase 11a added **no** CLI
command and no user-facing surface, and **neither Phase 11b nor Phase 11c added one**.

## Earlier sessions — Phase 11a, continuous-talking facecam reset research

Full report: `.agent/reports/phase-11a-continuous-facecam-reset-study.txt`. Decision: **D069**.
Result: **TRANSCRIPT HELPS BUT SEMANTICS REQUIRED**, with the caveat that its best family was
annotated with the manual labels visible and was therefore an upper bound, not a score.

## Earlier session — Phase 11b, the sealed blind validation of that reading

Reports: `.agent/reports/phase-11b-blind-predictions.txt` (the checkpoint) and
`.agent/reports/phase-11b-blind-validation.txt` (the measurement). Decision: **D070**.

    project             bluescreen 2
    blind source        Timeline 1 copy   id 69b8c00e-5710-4d50-8216-864f2958ea0e
    reference           Timeline 1        id 4d8c7645-cab4-4865-b89e-b7299330808e
    islands             0 [216000,217875) Short 1   DEVELOPMENT
                        1 [218029,218870) Short 2   DEVELOPMENT
                        2 [219040,220717) Short 3   BLIND TEST
                        3 [221503,222258)          EXCLUDED, out of scope
    blind dataset       14 hard cuts, 11 manual resets (7 on a cut, 4 off it)

**The protocol held.** The rubric, the rhythm rule, the re-entry rule, the four candidates and
every predicted frame were committed at `46c5e56` while the only thing ever read from
`Timeline 1` was its name and its unique id. Nothing in that commit was edited afterwards.
The two timelines were then verified equivalent: V1 and A1 are identical item for item,
including clip names; only V2 differs (92 vs 57), which is the intended difference.

**RESULT: SEMANTICS HELP BUT GENERALISATION WEAK.**

    family                             TP  FP  FN  TN   prec    rec     F1
    P0  semantic only                   5   2   2   5  0.714  0.714  0.714
    P1  semantic + loop                 5   2   2   5  0.714  0.714  0.714
    P2  semantic + rhythm               5   4   2   3  0.556  0.714  0.625
    P3  semantic + rhythm + loop        5   4   2   3  0.556  0.714  0.625
    BASELINE  every eligible hard cut   6   6   1   1  0.500  0.857  0.632

    (development set, labels visible during annotation: P0 0.552 ... P3 0.706, BASELINE 0.714)

What transferred, and what did not — the short version, D070 has the full statement:

- **the boundary rubric transferred.** All 7 of P0's predictions correspond to a real manual
  reset, it found 6/6 semantic resets, and it scored HIGHER blind than in development;
- **decision and frame are different numbers.** P0's two "false positives" are cuts where the
  creator did reset for that boundary but placed it 82 and 73 frames early, so that his FACE_X1
  entry could land on the cut. Right decision, wrong frame — 5/7 exact;
- **the loop rule is 3/3, delta 0**, on all three Shorts;
- **"FACE_X3 never survives a hard cut"** holds with zero counterexamples in three Shorts, and
  needs **no time threshold** — but on Short 3 it is redundant with the semantics;
- **the simulated ladder position is the weak link.** It agrees with the creator only 11/28 of
  the time, and every point P2/P3 lost is traceable to it rather than to the rhythm idea. This
  was declared in the checkpoint *before* unblinding;
- **36% of the edit was invisible to this evidence.** Four of eleven resets were explained by
  nothing there. **The creator has since answered for all four (D071)** — see the Phase 11c
  section below and `.agent/reports/phase-11b-creator-followup-addendum.txt`. The 36% figure is
  what Phase 11b could see at the time, not the current reading.

### What was added to the tree

    tools/research/phase11b/     simulate (causal, label-free state simulation), policy (P0-P3,
                                 rhythm, re-entry, the frozen record), annotations (the frozen
                                 rubric and every per-cut judgement), evaluate (strict/subset
                                 scoring, category recall, one-to-one frame matching),
                                 measured (what the unblinding found), study (the driver)
    tests/research/              33 more tests, no Resolve, no network, synthetic fixtures only
    .agent/reports/              phase-11b-blind-predictions.txt, phase-11b-blind-validation.txt

`tools/research/` is still **never imported by the package**. **No runtime dependency was
added**: WhisperX/torch/CUDA ran from a throwaway venv outside the repository.

## This session — Phase 11c, the creator-grounded semantic and rhythm study

Reports: `.agent/reports/phase-11b-creator-followup-addendum.txt` (what the four answers change
and what they do not), `.agent/reports/phase-11c-semantic-rhythm-study.txt` (the study) and
`.agent/reports/phase-11c-frozen-next-blind-policy.txt` (immutable from this commit).
Decisions: **D071** (the creator's answers) and **D072** (what the study measured).

**The creator answered the four questions D070 asked.**

    219354    VISUAL_PRESENTATION_RESET   back to X0 to show the whole avatar
    219525    SEMANTIC_RESET              a concessive pivot, the "even if..." kind
    219784    RHYTHM_REFRESH_RESET        X0 to regain the room to climb X1 -> X2 -> X3 again
    220442    RHYTHM_REFRESH_RESET        X0 to re-energise and be able to zoom in again after

**Short 3's creator-grounded taxonomy is now known.** Nothing in `46c5e56` or in the blind
validation report was edited; the answers live in a new layer and the blind P0-P3 metrics are
unchanged.

    creator-grounded taxonomy, three Shorts   17 SEMANTIC  7 RHYTHM  3 LOOP  1 VISUAL  0 AMBIG
    islands studied                          0, 1, 2 only. Island 3 never transcribed.
    dataset                                  42 hard cuts, 28 resets, 25 FACE_X1 entries

**RESULT: SEMANTICS READY, RHYTHM STILL UNRESOLVED.**

- **rubric V2 adds `CONTRAST_OR_CONCESSION`**, the category V1 had no slot for. Six
  clause-initial concessive/adversative pivots in three Shorts all carry a reset; the one
  mid-clause occurrence carries none. Position and function, never a word list. It repairs two
  known misses (217373, 219525). On the decision universe V2 + loop scores 0.840 / 0.808 /
  0.824 strict — **a fit, not a score: all three Shorts are development data now**;
- **rhythm is PROSPECTIVE and it separates.** `headroom_gain >= 1` — resetting unlocks a
  promotion that keeping does not, over a horizon that is the next discourse boundary — fires
  on 4 of 7 rhythm resets and **0 of 10 no-reset controls**. Leave-one-Short-out re-derives the
  same integer in all three folds. All 18 frame-level gate windows are followed by a reset;
- **the three misses are FACE_X2 resets with gain 0 or negative** and stay unexplained;
- **the state simulator's fault is the PROMOTION ENGINE, not the reset history.** Given the
  creator's own cycle boundaries it still gets the promotion count wrong in 9 of 28 cycles,
  lands a median 15 frames away on the rest, and in 4 cycles he promoted where the envelope
  offers no qualifying valley at all. **No promotion threshold was changed.** This is why the
  rhythm layer is ORACLE_ONLY and is excluded from the frozen policy;
- **a word boundary is not an anchor**: off-cut resets sit 2-6 frames from the nearest word
  start and the chance baseline over every frame is median 4.0. `reset + 36` survives as the
  re-entry baseline (median error 9 over 25 entries) and does not split by reset reason;
- **entry-first placement is 4/6** off-cut resets: the reset is derived backwards from a
  FACE_X1 entry that lands on a cut (0, 0, 11, 14 frames from it);
- the loop rule is still 3/3, delta 0.

### What was added to the tree

    tools/research/phase11c/     creator_feedback (the D071 overlay, layered over Phase 11b
                                 without mutating it), taxonomy (the three-Short taxonomy and
                                 the entry-first anchoring rule), headroom (ZoomHeadroomFeatures
                                 and the KEEP/RESET counterfactual), semantics_v2 (rubric V2 and
                                 every marker occurrence, positive and negative), study (driver)
    tests/research/              33 more tests, no Resolve, no network, synthetic fixtures only
    .agent/reports/              phase-11b-creator-followup-addendum.txt,
                                 phase-11c-semantic-rhythm-study.txt,
                                 phase-11c-frozen-next-blind-policy.txt

`tools/research/` is still **never imported by the package**, and **no runtime dependency was
added**: WhisperX/torch ran from a throwaway venv outside the repository, on CPU this time.

## Verification results (this session)

    pytest                    683 passed, 1 failed   (see below — pre-existing, Windows-only)
    ruff check .              All checks passed
    mypy (strict)             Success: no issues found in 65 source files
    tests/test_facecam_golden.py                     PASSES
    product source diff vs 781cea0 (src/)            EMPTY
    Resolve safety audit      PASS on bluescreen 2, zero timelines added

**The one failing test is pre-existing and platform-specific.**
`tests/test_voice_render.py::test_an_unwritable_media_storage_volume_is_reported` expects
`chmod` to make a directory unwritable, which Windows does not honour. It was reproduced on the
clean tree at `d297e31` before any Phase 11b file existed, the test file is untouched by 11b and
11c, and it fails identically now. It is a Windows portability defect in the test. Fixing it is
not in scope for a research phase and is not assigned.

### Phase 10 regression — **BLOCKED, and this is the one outstanding item**

`davinci-auto-zoom-test` was built on the Linux machine and its Media Pool still points at
`\home\palawi\Documents\Fast Documents\Videos\converted medias\...`. On Windows every clip of
`DAZ_INPUT` is OFFLINE, so `plan-probe` rendered **digital silence** (`min_db = max_db =
-140.0`) and produced 0 speech segments and 0 placements instead of 15 and 42.

The render path itself behaved correctly and safely — scratch created and deleted, queue
restored, Deliver restored, temp directory removed, current timeline restored, `clean: true`.

The media file IS on this machine, at
`F:\Fast Documents\Videos\converted medias\2026-07-19 15-28-02.mov`. Only the stored path is
wrong. **Relinking was NOT done**: it permanently modifies that project's Media Pool, which is
outside the write surface `AGENTS.md` allows.

TO CLOSE THIS: relink the media of `davinci-auto-zoom-test` (in the Resolve UI, or run the
regression on the Linux machine where the paths are already right), then

    plan-probe --confirm-resolve-render-test --project davinci-auto-zoom-test \
        --source-timeline DAZ_INPUT --reference-timeline DAZ_OUTPUT_MVP2 \
        --config config.example.toml --json

and check against the Phase 10 baseline: fingerprint `sha256:a1d107e1…d46a5483`, 15 speech
segments, 14 bursts, 63 valleys, 42 placements, 126 decision lines.

**Phase 11c did not attempt it.** The media was not relinked (that is a user action and it
permanently modifies another project's Media Pool), and no project other than `bluescreen 2`
was opened this session. It does not block Phase 11c, because no product code was changed — but
it must be closed before this research profile is ever integrated into `src/`.

Until then the evidence that the facecam profile did not move is: the product source is
byte-identical to `781cea0` and to `d297e31`, nothing in `davinci_auto_zoom` imports
`tools/research/`, and the golden facecam test passes.

### Resolve safety, `bluescreen 2`

Read-only throughout, in Phase 11c as in Phase 11b. The only mutation this session was one
audio render of **`Timeline 1 copy`** through the unmodified `resolve/voice_render.py`: scratch
duplicate `DAZ_AUDIO_SCRATCH_20260820_154702_d72040a5`, one render job (Complete in 7.4 s),
Deliver captured and restored, transactional cleanup, post-run audit. `RESULT: PASS`,
`audit differences: none`, 1 668 800 samples = 104.300 s, delta **+0.0000 frames** against
[216000, 222258). `Timeline 1` itself was only ever READ (V1 clips and V2 placements).

Post-run audit: `Timeline 1` unchanged (V1 51, V2 92, V3 0, A1 51); `Timeline 1 copy` unchanged
(V1 51, V2 57, V3 0, A1 51); both unique ids unchanged; timeline count 2, zero added, zero
removed; no scratch, no recovery, no preview, no temp render directory; render queue empty;
no DAZ preset left behind; active timeline restored to `Timeline 1`; `bluescreen 2` was the only
project opened. `SaveProject()` never called.

**No `apply-preview`, `clean-preview` or `rebuild-preview` was run on `bluescreen 2`.** No
asset was placed on any timeline, and no adjustment layer was created, moved or deleted.

## Known limitations

Everything from Phase 10 still stands:

- **calibrated on one creator and one reference workflow.** The thresholds sit on broad
  plateaus rather than spikes, which is the best available evidence they transfer to other
  material — and still not evidence that they do;
- **burst extent (Phase 8b) is a deferred, accepted calibration limitation, not a blocker.**
  **Do not retune the VAD or `reset_after_silence_ms`** without first measuring both directions
  per burst;
- a dedicated voice track is required, and its index is configuration, not detection;
- the rendered voice audio has never been listened to, and there is no hand-labelled speech
  reference;
- the fingerprint cannot see Fairlight or OFX changes that move no clip (D030);
- collision behaviour on a non-empty track is unmeasured, by choice (D032);
- marker capacity is uncharacterised, and survival across a project close/reopen is assumed;
- tested on Resolve Studio 21.0.4.5, now on both Linux and Windows 11.

Specific to the 11a/11b/11c research:

- **one creator, three Shorts, 42 cuts, 28 manual resets in total.** Every number is a
  description of these edits, not a calibration;
- **the semantic rubric is `AGENT_SEMANTIC_REASONING`.** DAZ cannot compute it today at any
  quality, and no local model was built or benchmarked against it;
- **the ladder position cannot be reconstructed, and the fault is the promotion engine.** Phase
  11b measured 11/28 exact agreement and blamed the reset history; Phase 11c handed the engine
  the creator's own cycle boundaries and it still got the promotion COUNT wrong in 9 of 28
  cycles (D072). Any future use of a rung-level state gate on unlabelled material inherits that;
- **Phase 11b originally measured 4 unexplained resets in Short 3 (36%); the creator's
  follow-up later resolved all four in D071.** One is visual, one was a semantic miss, two are
  prospective rhythm refreshes. The 36% figure is HISTORICAL and is not the current state;
- **exactly one reset in three Shorts is VISUAL** (219354, creator-confirmed). The composited
  picture still cannot be observed here, so no other reset may be called visual;
- human listening validation still not performed; no audio playback in this environment;
- **all three Shorts are development data now.** Short 3's Phase 11b numbers remain the only
  blind score this research has ever produced; every Phase 11c number is a fit;
- `bluescreen 2` uses **held** reset instances (variable length carrying the dwell) while the
  shipped executor places fixed-length resets. Diagnostic, not scope;
- whether X2/X3 promotion behaviour really matches the shipped planner on this material was
  first measured in Phase 11c (D072 §6.1) and the measurement does **not** support the
  creator's assertion. Whether the gap is the detector, the creator or the research simulator's
  dropped `promotion_min_hold` check is NOT established, and nothing was retuned;
- the Phase 11c transcript was produced on **CPU** (torch 2.8.0+cpu, `large-v3`, int8) where
  11a/11b used CUDA float16. Word timings are therefore not guaranteed identical to 11a's.

## The next task, and it is not assigned

**Phase 11d — blind-test the frozen policy on a genuinely new completed Short.**

`.agent/reports/phase-11c-frozen-next-blind-policy.txt` states, in full and immutably, what
will be predicted: the loop rule, semantic rubric V2, the entry-first placement rule, the
`reset + 36` re-entry and a coarse state gate computed from the policy's own history. Annotate
the new Short's hard cuts against V2 and **commit the predictions before its zoom track is read
at all**, exactly as Phase 11b did. One Short, one checkpoint, one measurement.

That is the only thing that can turn Phase 11c's fit back into a score.

Two hard constraints on it:

- **do not run it on `bluescreen 2`'s fourth island** ([221503, 222258)). Those are spare clips
  kept for later, not a completed Short, and spending them on a test they cannot answer wastes
  the only unused material in the project;
- **the rhythm layer does not fire.** It is ORACLE_ONLY (D072): it needs the ladder position,
  and the ladder position is not reconstructable while the promotion engine disagrees with the
  creator on 9 of 28 cycles with perfect boundaries.

Do **not** build before that measurement: no profile abstraction, no reset policy in the
planner, no runtime NLP or vision dependency, and **no promotion retune** — retuning
`promotion_min_drop_db` and friends to fit `bluescreen 2` is explicitly forbidden (D072). If
the promotion gap is ever worth closing it is its own phase, on its own evidence.

The other outstanding item is the **blocked Phase 10 live regression** above. It needs a media
relink, which is a user action.

## Deferred work — none of it assigned

- **Phase 8b, burst-extent calibration research.** Optional. Measure before tuning.
- **Phase 7b, apply in place.** The preview workflow is the supported write model.
- **UI / Resolve launcher / distribution.**

## Retired research

Phases 9a and 9b studied whether a GAMEPLAY zoom could be placed automatically. Both produced
negative results, both are archived, and **neither is a next task**. The creator retired the
direction (D068). Phase 9c is **cancelled, not blocked**. If gameplay is ever revisited it
starts as new research from a second reference edit — not from anything left behind in this
tree. **Phase 11a is not related to gameplay and revived none of it.**

`DAZ_OUTPUT_MVP3` is kept as a research archive. Do not delete it, or any of the five
`DAZ_AUTO_PREVIEW_*` timelines.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions (also
append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it with the
code it describes. This file always begins by naming the supported product baseline.
