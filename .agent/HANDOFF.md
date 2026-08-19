# Agent handoff

## Current state

**Facecam MVP is the current supported product baseline.** davinci-auto-zoom automates the
creator's facecam zooms from their own voice, and nothing else. Phase 10 froze that behaviour,
retired gameplay from the product (D068), and cleaned the tree to match. **Phase 11a changed
none of it**: it is research on a second video type, and it is **RESEARCH / NOT SHIPPED**.

    branch              dev                 main untouched
    Phase 10 change     810c9a2             the retirement + consolidation
    Phase 11a           this session        research only, no product change
    Resolve tested      DaVinci Resolve Studio 21.0.4.5, Linux
    projects touched    bluescreen 2 (research), davinci-auto-zoom-test (regression)

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

Phase 11a is independent evidence that this vocabulary generalises: a second, quite different
video type replayed through it with **zero state-machine problems** (D069). No state, role or
asset was added, and none was needed.

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
command and no user-facing surface.

## This session — Phase 11a, continuous-talking facecam reset research

Full report: `.agent/reports/phase-11a-continuous-facecam-reset-study.txt`. Decision: **D069**.

    reference       bluescreen 2 / Timeline 1  (id 4d8c7645-cab4-4865-b89e-b7299330808e)
    labelled range  [216000, 218870) at exactly 60 fps — nothing after 218870 was used
    islands         0: [216000,217875) 17 clips 16 cuts   1: [218029,218870) 13 clips 12 cuts
    dataset         28 hard cuts, 15 reset-positive (2 of them loop), 13 no-reset,
                    17 manual resets in total (2 of which are not on any cut)

**The measurement that motivated everything:** Silero finds **two** speech segments in the
whole labelled range, one per Short. The shipped silence gate would fire at most twice where
the creator made 17 resets. It is not mistuned here — it has no events to fire on.

**Result: TRANSCRIPT HELPS BUT SEMANTICS REQUIRED.**

    A  cut only                            prec 0.500  rec 1.000  F1 0.667
    B  cut + energy valley >= 20 dB              0.385  0.385  0.385
    C  cut + ASR sentence boundary               0.625  0.385  0.476
    D  cut + discourse marker in position        0.429  0.231  0.300
    E  cut + lexical/punct + acoustic            0.200  0.077  0.111
    F  agent semantic continuity                 0.625  0.769  0.690
    G  F + deterministic loop override           0.667  0.800  0.727
    H  F, only where a face state is held        0.833  0.769  0.800   (separate reading)

Word timing is **not** the obstacle: 360/360 tokens aligned, word interiors 8.8 dB above the
inter-word floor, and at edit boundaries the word overhangs the cut by a median of **3 frames**.

Findings that transfer, and the intuitions the reference disproved, are in D069. The short
version:

- the **loop rule is exact** — last hard cut of each island returns to X0, 2/2, delta 0;
- **gating on "a face state is currently held" is free precision** (+0.21). It is only the
  transition graph, and it removes four of six false positives;
- the **"~1 second at X0" intuition is wrong**: median pure X0 dwell is **350 ms**, spread
  continuously with no plateau. The entry is anchored; the dwell falls out;
- the **reset-to-cut window is 0 frames**, not ±120 ms. 15 of 17 sit exactly on a cut;
- **`et donc` / `du coup` are too sparse to be a rule**: 10 of 13 semantic resets carry no
  marker at all.

### What was added to the tree

    tools/research/phase11a/     structure, manual, words, lexical, acoustic, dataset,
                                 ablation, reference, render_reference_audio, audio_features,
                                 transcribe, study
    tests/research/              55 tests, no Resolve, no network, synthetic fixtures only
    pyproject.toml               mypy now also checks tools/research (strict); ignore rules
                                 for the deliberately-absent whisperx/torch/faster_whisper
    .gitignore                   user media and research working data

`tools/research/` is **never imported by the package**; the dependency points research ->
product only. **No runtime dependency was added** — WhisperX/torch/CUDA ran from a throwaway
venv outside the repository, which has been removed.

## Verification results (this session)

    pytest                    618 passed   (563 before; +55 research tests)
    ruff check .              All checks passed
    mypy (strict)             Success: no issues found in 52 source files
    live plan-probe           PASS, editorial plan byte-identical to the Phase 10 baseline
    Resolve safety audit      PASS on bluescreen 2, zero timelines added

### Phase 10 regression, run live on `davinci-auto-zoom-test` / `DAZ_INPUT`

    structural fingerprint  sha256:a1d107e1…d46a5483   unchanged since Phase 8c
    15 speech segments -> 14 editorial bursts -> 63 valleys -> 42 placements
    x0_to_face_x1 14, face_x1_to_face_x2 9, face_x2_to_face_x3 5,
    face_x1_to_x0 5, face_x2_to_x0 4, face_x3_to_x0 5
    126 decision lines
    clean: true, audit differences: none, current timeline restored to DAZ_OUTPUT_MVP3

Identical to the Phase 10 baseline on every editorial field.

### Resolve safety, `bluescreen 2`

Read-only throughout. The only mutation was the audio render, through the unmodified
`resolve/voice_render.py`: scratch duplicate, one render job, Deliver captured and restored,
transactional cleanup, post-run audit. `RESULT: PASS`, `audit differences: none`, and the
rendered audio verified at delta **+0.0000 frames** against the timeline range.

Post-run audit: reference timeline unchanged (V1 49 items, V2 57 items spanning exactly
[216000, 218870)); timeline count 1, zero added, zero removed; no scratch, no recovery, no
preview, no temp render directory; render queue empty; no DAZ preset left behind; active
timeline restored to `Timeline 1`. `SaveProject()` never called.

**No `apply-preview`, `clean-preview` or `rebuild-preview` was run on `bluescreen 2`.**

The project was switched to `davinci-auto-zoom-test` for the regression and switched back;
`bluescreen 2` was the open project before and after.

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
- only tested on Resolve Studio 21.0.4.5 on Linux.

New to Phase 11a, and specific to the research:

- **the semantic annotation was made with the manual labels visible.** Family F is an upper
  bound on what semantics could explain, not a classifier score. This is the single biggest
  caveat on the whole result;
- **human listening validation not performed** — no audio playback in the agent environment;
- one reference, 47 labelled seconds, 28 cuts, 2 islands;
- island 1 came back essentially unpunctuated from the ASR, which is what breaks family C
  there. Whether that is systematic is unmeasured;
- the state gate (H) was found by inspecting failures on the same 28 cuts. It is motivated by
  the transition graph rather than fitted, but it has not been validated elsewhere;
- `bluescreen 2` uses **held** reset instances (variable length carrying the dwell) while the
  shipped executor places fixed-length resets. Diagnostic, not scope;
- whether X2/X3 promotion behaviour really matches the shipped planner on this material was
  **not** measured. The creator asserted it and this phase took it as given.

## The next task, and it is not assigned

**Phase 11b — a blind semantic annotation of a second continuous-talking Short.**

Family F is currently unfalsifiable because the annotation saw the answers. Label a second
Short, annotate its hard cuts **before** looking at the manual zooms, and see whether F1 ~0.8
survives. That one number decides whether the transcript direction is real. Re-check the loop
rule on a third island and re-measure the X0 dwell while the data is there.

Do **not** build before that number exists: a profile abstraction, an embedding or LLM
classifier, any runtime NLP dependency, or any reset policy in the planner (D069).

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
