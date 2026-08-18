# Agent handoff

## Current state

**Phase 9b (visual relevance / zoom-usefulness study) is DONE, and like 9a its main result is
a negative one** — but a much sharper one. Read
`.agent/reports/phase-09b-visual-zoom-utility-analysis.txt` before assuming any visual feature
predicts gameplay.

**Phase 8c's facecam behaviour is VISUALLY VALIDATED by the user** and remains the stable
baseline. `X0 -> FACE_X1` on a burst, `FACE_X2`/`FACE_X3` on voice valleys, cut snapping on
every transition, reset to X0, ownership/clean/rebuild/idempotence. **Do not change facecam
logic without a demonstrated bug.** Phase 9b changed none of it and proved so (fingerprint
unchanged, section 4 of `.agent/reports/phase-09b-live-workflow-report.txt`).

Phase 9b added **no timeline of any kind**. Still exactly five `DAZ_AUTO_PREVIEW_*` timelines
plus `DAZ_INPUT`, `DAZ_OUTPUT_MVP`, `DAZ_OUTPUT_MVP2` and `DAZ_OUTPUT_MVP3`. Do not delete any.

## What the user told us, and it supersedes a Phase 9a hypothesis

The creator explained the four windows Phase 9a could not:

    gap  1   friends are talking, but nothing new or interesting is visible on screen
    gap 10   something really happens in the game, but zooming would add nothing
    gap 11   a friend is talking about a subject that is not on screen
    gap 12   same as 11

**Retired**: Phase 9a's guess that gaps 10-12 were a section/recency phenomenon because they
are consecutive. They are four statements about the *picture* (D066).

Two consequences are now structural in the code and tested (D067):

    secondary audio = context, never a trigger
    creator silence = candidate population / prior, never a decision

**`X3_TO_GAMEPLAY = 15` is CONFIRMED by the creator** and is no longer flagged as inferred
anywhere — config, D062, plan, notes all corrected.

## What Phase 9b measured

**The GAMEPLAY zoom's geometry, exactly** (D064, read-only `ExportFusionComp` on 11 roles):

    state       Transform Size    Centre offset       shows
    X0                 1.00       (0.00, 0.00)        everything
    GAMEPLAY           1.25       (0.00, 0.00)        the central 80%
    FACE_X1/2/3        1.50/2.00/2.50  0.25/0.5/0.75  a corner, tightening onto the facecam

Every role is one `Transform`, keyframes at 0 and 15, no crop and no mask anywhere. All four
entries into GAMEPLAY converge on one state. `GameplayTargetROI = x[0.1, 0.9], y[0.1, 0.9]`,
**derived** by `Roi.from_transform` from those two numbers, checked against the facecam ladder.

**And that is already half the answer: the gameplay zoom privileges no region.** A centred
1.25x push-in cannot magnify a corner HUD element — it crops the outer 10% away.

**Nine spatial features later, the classes still nest** (D066): activity in/out of the ROI,
their ratio, active-cell fraction and peak, bbox area, concentration, region count,
persistence, novelty. Best single threshold 10 or 11 of 15 against a 10/15 baseline — exactly
Phase 9a's numbers with different features. `roi_ratio` spans 0.84-1.23 over *both* classes.

**The decisive form of the result: every hard negative has a manual-gameplay twin.**
Z-scored over all nine features, gap 8 (GAMEPLAY) and gap 11 (X0) are 0.92 apart, gap 5 and
gap 10 are 1.19 apart, gap 13 and gap 14 are 1.87 apart. No classifier of any shape separates
pairs that close.

**Why, specifically**: in a first-person game the mouse moves the whole picture, so a frame
difference measures the camera and not the game. Gaps 11 and 12 are visually empty corridors
and score `64-66% of cells active, bbox 0.99` — the same as gap 10, where an NPC charges the
camera.

**Ablation 2.0, live, one code path:**

    A phase9a silence+audio+video   8/15   false+ 1,10,11,12
    B visual amount only            8/15   false+ 10,11,12
    C visual spatial                7/15   false+ none, false- 0,3,4,5,6,7,8,9
    D spatial+silence prior         6/15
    E spatial+audio context         6/15
    F spatial+silence+audio         5/15
    candidate rule (no signal)     11/15   false+ 1,10,11,12
    majority baseline              10/15

C to F have zero false positives — and refuse 8 of the 10 positives with them. Precision
without recall on a 10/5 split is a conservative rule, not a solution. **No candidate rule is
proposed**, deliberately.

**The one encouraging measurement**: where a visual onset exists (4 of 10 episodes), it sits
3, 4, 29 and 43 frames from the editor's chosen entry — about twice as close as the start of
the silence (0, 10, 53, 85) and closer than the nearest cut in three of four. n=4, from an
onset detector that tracks the camera. A hypothesis for a later phase, not a rule.

**The counterexample nobody should forget**: gap 13 is 62% GAMEPLAY in the human edit and the
screen is nearly black (the outro). Some gameplay decisions are not about the picture at all.

## What changed in the code

- **`domain/visual_episodes.py` (new)** — pure. `Roi.from_transform` (the derivation),
  `GAMEPLAY_ROI`, `roi_mask`, `spatial_sample` (inside/outside, active fraction, bbox,
  concentration, connected regions), `visual_episode_features` (persistence, novelty, onset),
  `zoom_utility`, and `VisualWindowAnnotation` — the study-label model that keeps "the numbers
  say" and "a human saw" in separate columns.
- **`vision.py`** — `activity_from_frames` / `activity_frames`: the same mean-subtracted
  absolute frame difference `motion_from_frames` already computes, kept per cell instead of
  averaged, on a 32x18 grid. No new dependency, no second render (D065).
- **`domain/gameplay.py`** — `GameplayWindowFeatures.visual`, and a second shape for question
  A: `use_zoom_utility` (off by default) makes the visual verdict the only thing that can say
  yes, with `candidate_silence_ms` and `require_secondary_audio` as gates that can only remove
  a candidate (D067).
- **`resolve/gameplay_study.py`** — decodes the same rendered file a second time, measures the
  15 windows spatially, emits the annotation per window, and runs the six-family ablation.
- **`config.example.toml`** — `face_x3_to_gameplay = 15` no longer marked unconfirmed; the
  measured zoom geometry documented next to the roles.

`domain/planner.py`, `resolve/executor.py`, `resolve/owned_preview.py`, `domain/ownership.py`
and `resolve/ownership.py` needed **no change at all** — fifth phase running.

## Live results — PERFORMED, 2026-08-18

Full output: `.agent/reports/phase-09b-live-workflow-report.txt`.

- **read-only Fusion probe** — 11/11 comps exported and parsed, nothing modified.
- **`gameplay-study`** — PASS. Two renders (secondary audio 7.0 s, program video 15.0 s), each
  on its own scratch duplicate: `clean` true, `error` null, `audit_differences` empty, scratch
  absent after cleanup, render queue restored, no unrestored Deliver state, render directory
  removed. Total 86.4 s.
- **`plan-probe` facecam regression** — PASS. 42 placements, 14 bursts, 15 segments, 63
  valleys, 126 decision lines, fingerprint `sha256:a1d107e1…d46a5483` — every one identical to
  the Phase 8c/9a baseline.
- **Independent post-run audit** — PASS. 9/9 protected timelines unchanged with their original
  unique ids; **zero timelines added**; no leftover scratch/recovery/temp timeline; 12/12 Media
  Pool items identical; render queue empty; no DAZ render preset; format/codec restored to
  mov/ProRes422HQ; current timeline restored to `DAZ_OUTPUT_MVP3`; no temp directories left.

**Media**: the visual review rendered the program video and extracted frames and contact
sheets under `/tmp`, and all of it was deleted afterwards. Nothing of the user's footage is in
this public repository, by rule.

## Verification results (this session)

- `pytest` — **642 passed** (615 before; +27). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 43 source files.
- `gameplay-study` live — PASS, 2 clean renders.
- `plan-probe` live — PASS, plan byte-identical to the Phase 8c baseline.
- independent post-run audit — PASS, zero timelines added.

## Runtime cost of the new layer

Per minute of timeline: 3.5 s to decode the 32x18 grid, 0.1 s for the pure statistics. About
6% of realtime, linear, no GPU. Cheap — and it buys 7/15 against a no-signal 11/15, so it
stays a measurement tool and enters no runtime path.

## Remaining unknowns

New after Phase 9b:

- **"would a zoom help here" is not expressible with frame differences.** The three properties
  that distinguish the classes — is there a subject at all, how big is it on screen, is it
  where the zoom keeps it — are all statements about *objects*, and this pipeline has no notion
  of one. The next classical experiment is camera-motion compensation (see below); after that,
  only semantics remain;
- **the entry anchor is still unexplained**, though the visual-onset hypothesis is now on the
  table with 4 supporting measurements;
- **gap 13 shows even a perfect visual reader would be insufficient** — the editor cut to a
  near-black outro;
- everything still rests on **one reference timeline and 15 windows**.

Carried forward from earlier phases: the rendered voice audio has never been listened to; no
hand-labelled speech reference; burst extent (Phase 8b) is still wrong by up to 77 frames and
contaminates three of the ten entry measurements; the fingerprint cannot see Fairlight/OFX
changes that move no clip; collision behaviour unmeasured (D032); marker capacity untested at
scale; the detector is calibrated on one speaker.

**Retired by this phase**: "gaps 10/11/12 may be a section effect" (the creator explained
them); "`X3_TO_GAMEPLAY = 15` is inferred" (confirmed); "the motion metric has never been
checked against anyone's eyes" (it has now, and it disagrees with them — see D066).

## Next task — NOT Phase 9c

In descending order of expected value:

1. **A second reference edit that uses gameplay.** Unchanged from Phase 9a's recommendation and
   now twice as well-founded: two different feature families have failed on the same 15
   windows. Fifteen windows from one delivery cannot support a rule.
2. **Camera-motion compensation, the one classical experiment left.** Estimate a global
   translation per sample (a coarse cross-correlation over the 32x18 grid is pure numpy, no new
   dependency), subtract it, and re-measure everything in section 5 of the analysis report. It
   would turn "the whole frame moved" into "the frame moved 6 cells left and this blob did
   not" — the first measurement in this project that describes an *object*. Cheap, bounded, and
   the honest prerequisite for any subject-scale feature.
3. **Phase 8b (burst extent).** Still open, still the cleanest remaining defect, and still
   contaminating the gameplay entry measurements.
4. Only after 1 and 2: a Phase 9c that emits gameplay `AssetPlacement`s. **Phase 9b does not
   recommend it yet** — there is no rule to place with.

If 2 fails as well, the recommendation is a semantic study whose classification contract is
already written down (analysis report, section 10) — and even then it stays offline research:
DAZ's runtime is ffmpeg + numpy + Silero, and a rule DAZ cannot recompute locally cannot ship
(D065).

Phase 7b (apply in place) remains open; see `.agent/IMPLEMENTATION_PLAN.md`.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it
with the code it describes.
