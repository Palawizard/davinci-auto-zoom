# Agent handoff

## Current state

**Phase 6 (reset cut-alignment refinement) is DONE — measured, implemented, tests green, and
confirmed by a live run against DaVinci Resolve Studio 21.0.4.5.** There are now **two**
`DAZ_AUTO_PREVIEW_*` timelines in the project, deliberately, waiting for the user's A/B
comparison:

| | timeline | unique id |
| --- | --- | --- |
| BEFORE (Phase 5) | `DAZ_AUTO_PREVIEW_20260816_211026_c676d5af` | `96f30d77-f302-4be5-93f3-c7fbbfd58813` |
| AFTER (Phase 6) | `DAZ_AUTO_PREVIEW_20260817_132005_77443d7c` | `6d0bde62-788c-4c04-b32c-685ebd84e9e3` |

**Do not delete either.** The Phase 5 preview was re-read after the Phase 6 run and is
byte-identical to what it was.

Phase 7 (ownership/idempotence) has **not** been started, on purpose — see the bottom of this
file.

## What Phase 6 changed, and why

The Phase 5 preview was technically perfect: 28/28 insertions frame-exact, verified 1:1
against the plan. Human review of it found an *editorial* defect the automated checks could
never see — `FACE_X0_SMOOTH` starting a few frames after a video cut it should have started
on.

The measurement (`.agent/reports/phase-06-cut-offset-diagnostic.txt`) found the cause and it
was not the one Phase 4 had guessed. Phase 4 measured only the distance to the *next* cut,
found a minimum of 35 frames, and concluded the 21-frame window was possibly too small. The
opposite direction had never been measured:

```
burst end -> previous hard cut, all 14 bursts (frames)
 -7 -151 -228   -4  -30 -139   -4 -148   -4   -6 -114   -4   -5   -7
```

Eight bursts have a hard cut **4-7 frames before** the detected speech end. None has one
within 21 frames after it. The forward-only search was structurally blind to the entire case.
In all 8 of those bursts, the human edit's manual reset sits on *exactly* that cut.

Two rules changed (D034, superseding D027 rules 1 and 3):

1. the snap window is asymmetric — `[base - cut_snap_lookback, base + cut_snap_window]`;
2. the **nearest** usable candidate to the reset anchor wins, not the last one, with ties
   broken towards the later cut.

`[planner].cut_snap_lookback_ms = 120` (7 frames at 60 fps). 100 ms would have missed two of
the eight cases at -7. **No `[speech.vad]` parameter was touched** — `speech_pad_ms` correctly
pads the segment so no phoneme is clipped, and compensating for that is an editing decision,
not a detector one. The lookback is a snap mechanism only: with no cut in the window the reset
stays exactly at `base_reset`, never earlier.

## Verified environment

Unchanged: Resolve **Studio 21.0.4.5**, project `davinci-auto-zoom-test` at 60.0 fps,
`DAZ_INPUT` (V1-V2, A1-A3, `[216000, 219555)`, 21 items on V1 → 20 hard cuts),
`DAZ_OUTPUT_MVP` (= input + V3 with 12 `FACE_X1` + 12 `FACE_X0_SMOOTH`), voice **A1**, cut
reference **V1**, zoom target **V3**, assets at 15/15 transition frames.

Asset `unique_id`s are the same values Phase 5 recorded
(`27698b68-…` / `63dfcbff-…`), so they survive Resolve restarts.

## Code changes

- `domain/planner.py` — `PlannerSettings.cut_snap_lookback_ms`; `_choose_reset` takes a floor
  and a lookback and ranks by `(abs(cut - base_reset), -cut)`; `_reset_reason`;
  `REASON_RESET_SNAPPED` split into `REASON_RESET_SNAPPED_FORWARD` /
  `REASON_RESET_SNAPPED_BACKWARD`; `forward_snapped_resets` / `backward_snapped_resets`
  properties with `snapped_resets` kept as their sum; decision trace prints the signed delta,
  the window and the runner-up candidates.
- `config.py` — the new key in `[planner]`, unknown keys still an error.
- `config.example.toml` — the new setting, documented against `cut_snap_window_ms`; the
  "takes the LAST one" wording is gone.
- `README.md`, `.agent/DECISIONS.md` (D027 amended, D034 added), `.agent/RESEARCH_NOTES.md`,
  `.agent/PROJECT_CONTEXT.md`, `.agent/IMPLEMENTATION_PLAN.md` (Phase 6 = reset cut alignment,
  ownership renumbered to Phase 7).

The executor was **not** touched. It still recomputes nothing.

## Verification results (this session)

- `pytest` — **361 passed** (346 before; +15). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 32 source files.
- `plan-probe` against live Resolve — **PASS**.
- `apply-preview` against live Resolve — **PASS**, first attempt.

The regression test `test_the_regression_case_observed_in_the_phase_5_preview` reproduces
burst 3 of the real material and was confirmed to fail against the old rule
(`reset_direct` at 216797) and pass against the new one (`reset_cut_snap_backward` at 216793).

## Live results — PERFORMED, 2026-08-17

Plan: 15 speech segments → 14 bursts → 28 placements (unchanged counts), but the resets moved:
**6 direct, 8 `reset_cut_snap_backward`, 0 forward.** Zoom coverage 41.0% → 39.9%.

| burst | old x0 | new x0 | cut | delta | reason |
| --- | --- | --- | --- | --- | --- |
| 0 | 216139 | **216132** | 216132 | -7 | cut_snap_backward |
| 1 | 216442 | 216442 | - | - | direct |
| 2 | 216519 | 216519 | - | - | direct |
| 3 | 216797 | **216793** | 216793 | -4 | cut_snap_backward |
| 4 | 216930 | 216930 | - | - | direct |
| 5 | 217039 | 217039 | - | - | direct |
| 6 | 217316 | **217312** | 217312 | -4 | cut_snap_backward |
| 7 | 217749 | 217749 | - | - | direct |
| 8 | 218003 | **217999** | 217999 | -4 | cut_snap_backward |
| 9 | 218189 | **218183** | 218183 | -6 | cut_snap_backward |
| 10 | 218297 | 218297 | - | - | direct |
| 11 | 218473 | **218469** | 218469 | -4 | cut_snap_backward |
| 12 | 218767 | **218762** | 218762 | -5 | cut_snap_backward |
| 13 | 219134 | **219127** | 219127 | -7 | cut_snap_backward |

Every one of the 8 new x0 frames is exactly a V1 hard cut, and exactly where the human editor
put theirs. Median reset offset against `DAZ_OUTPUT_MVP` went **5 frames → 0**; the x1/x0
counts against the human edit (14/12) did not change, because nothing about zoom-in triggering
was touched.

**Apply.** `RESULT: PASS`, exit 0, first attempt. Preview
`DAZ_AUTO_PREVIEW_20260817_132005_77443d7c`, source validation 14 fields + fingerprint
`sha256:a1d107e1fa95b6152dd06be89ccc0fbd39de8d02faa906f91d3d7fc6d46a5483` (identical to Phase
5 — the source has not moved), V3 added and verified empty, 28/28 insertions `ok` all
reporting `comps=1`, `verified: True`, active timeline restored `proven=True`, `audit
differences: none`.

**Independent post-run audit** (separate read-only script, 17/17 checks passed): `DAZ_INPUT`
range/hard-cuts/V1 items unchanged and still has no V3; `DAZ_OUTPUT_MVP` V3 identical item for
item; the Phase 5 preview still present with its 28 items and its 14 original x0 frames; the
new preview's V3 equal to the new plan placement for placement, no overlap, every x1 ending
exactly where its x0 starts; exactly two `DAZ_AUTO_PREVIEW_*` timelines and no third; no
`DAZ_AUDIO_SCRATCH_*` / `DAZ_SCRATCH_*` / `DAZ_RENDER_TMP_*` left; no timeline disappeared.

Full output: `.agent/reports/phase-06-cut-alignment-report.txt`. Diagnostic:
`.agent/reports/phase-06-cut-offset-diagnostic.txt`.

The command, for reruns:

```bash
.venv/bin/python -m davinci_auto_zoom apply-preview \
  --confirm-create-preview-timeline \
  --confirm-resolve-render-test \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT \
  --reference-timeline DAZ_OUTPUT_MVP \
  --config config.example.toml
```

The 15/14/28 and 6-direct/8-backward counts are an expectation to sanity-check, **not** a
condition the code enforces.

## Remaining unknowns

Carried forward: pixel confirmation of the Fusion effect, the rendered voice audio has never
been listened to, isolation vs Resolve's own mixdown with buses, no hand-labelled speech
reference, collision behaviour on a non-empty track (D032, deliberate), the fingerprint cannot
see Fairlight/OFX changes that move no clip, multi-`clipInfo` `AppendToTimeline` untested.

New after Phase 6:

- the 120 ms lookback is calibrated on **one timeline**. The margin here is 4x, but a
  faster-cut edit could place unrelated cuts inside it. Most likely thing to revisit;
- whether a backward snap ever clips a final phoneme visibly. Bounded to 7 frames at 60 fps
  and the human edit does the same on the same frames, but only viewing confirms it;
- **x1 over-triggering is still there** — 14 planned vs 12 manual. Untouched on purpose: one
  editorial rule at a time, and there is no measurement behind this one yet.

Resolved by this phase: cut snapping does fire on real material once the window is
two-sided; asset `unique_id`s survive sessions.

Still open, and unchanged: whether the effect looks right on screen. Both previews exist
precisely so a human can answer that, now by comparison rather than in the abstract.

## Next task — Phase 7 (ownership + idempotence)

**Wait for the A/B verdict first.** If the new x0 alignment is wrong, that is a planner fix
and it is much cheaper before ownership exists.

Then, in order: how a DAZ-created clip is identified on a later run; what a second run does
with an existing preview; then `clean` / `rebuild`; and only after those, whether applying in
place on a user timeline is safe. Do not weaken the empty-target-track rule until ownership
exists.

If the user raises x1 over-triggering, treat it as its own measured phase — start from the
burst/zoom-in offsets against `DAZ_OUTPUT_MVP`, and measure both directions this time.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task.
