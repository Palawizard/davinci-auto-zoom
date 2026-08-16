# Agent handoff

## Current state

**Phase 4 (pure zoom planner + dry-run integration) is complete and passed.** Phase 5 is not
started.

The chain now runs end to end on real material, and stops exactly where it was told to:

```
DAZ_INPUT A1 -> isolated audio -> Silero VAD -> 15 SpeechSegments [216046..219134)
  -> 14 editorial bursts -> 14 FACE_X1 + 14 FACE_X0 placements, zero overlaps
  -> printed. Nothing was written to any timeline.
```

There is still **no executor**. No code path inserts, moves or deletes a zoom clip; the only
write-capable modules remain the two Phase 2/3 spikes, and `plan-probe` reuses Phase 3's
temporary render rather than adding a new mutation.

## Verified environment

Unchanged from Phase 3. Resolve **Studio 21.0.4.5**, project `davinci-auto-zoom-test` at
60.0 fps, `DAZ_INPUT` (V1-V2, A1-A3, `[216000, 219555)`) and `DAZ_OUTPUT_MVP` (= input + V3
with 12 `FACE_X1` + 12 `FACE_X0_SMOOTH`), voice track **A1**, cut reference **V1**, zoom
target **V3**, ffmpeg n9.0.1, onnxruntime 1.28.0 CPU, Silero VAD v6.2.1.

## The three Phase 3 review points, fixed

1. **Delivery transaction.** `SaveAsNewRenderPreset` is the run's first mutating call, and it
   now happens *inside* the `try/finally`, with the state object published on the report
   before the call so a mid-way failure still leaves cleanup something to undo. It is also
   fail-closed: if the preset cannot be saved the run raises **before** `LoadRenderPreset`,
   `SetRenderSettings` or `AddRenderJob`, because a Deliver page that cannot be snapshotted
   cannot be restored (D020). `_restore_delivery` deletes the preset whenever it actually
   exists, not only when the save was recorded, and skips the format/mode comparison entirely
   when nothing was captured (which used to invent "unrestored" differences).
   Tests: fail-closed path leaves zero downstream calls; a failure right after the preset was
   created still deletes it; an undeletable preset is reported and marks the run unclean.
2. **`neg_threshold`.** `threshold_stability` was silently rebuilding `VadSettings` without
   it, so every trial ran with Silero's default exit threshold instead of the configured one.
   It is now preserved, capped at the trial threshold (a `neg_threshold` above the entry
   threshold is not a valid state machine). Tested both ways: with the explicit 0.45 the
   probe run splits into two segments, without it into one.
3. **CLI help.** The global description no longer claims every command is read-only. It now
   says which commands are (`doctor`, `snapshot`, `assets`, `compare`, `speech-file`), states
   that the development probes *do* make temporary opt-in changes behind their own
   confirmation flags, and leads with the guarantee that matters: no command places, moves or
   deletes a zoom. No protection was weakened. Tested.

## What Phase 4 built

- `domain/planner.py` — rewritten from the scaffold. `PlannerSettings` (editorial ms),
  `AssetTiming` (animation frames), `AssetPlacement`, `PlanSource`, `ZoomPlan`, `plan_zooms`,
  `frames_from_ms` (exact `Fraction`, half-up). Pure: no Resolve, ONNX, ffmpeg, filesystem or
  clock.
- `domain/models.py` — `ZoomState` / `ZoomActionKind` / `ZoomAction` **deleted** (D028), with
  a comment recording why. `SpeechSegment` / `normalize_speech_segments` unchanged.
- `domain/snapshot.py` — `hard_cuts(track)`: a frame where one clip ends *and* another begins.
  `edit_boundaries` kept for reporting, documented as the looser superset (D027).
- `domain/speech_report.py` — `compare_plan_to_reference` / `PlanReferenceDiagnostics`.
- `cli.py` — `plan-probe`, sharing the probe body with `speech-probe` and adding the planning
  stage; refuses without `[assets.transition_frames]`.
- `config.py` / `config.example.toml` — `[planner]` and `[assets.transition_frames]` parsed
  with unknown-key rejection, `cut_reference_video_track`, and `min_zoom_ms` /
  the 80/120 ms lead-in/out defaults removed.

## Planner rules, exactly as implemented

```
bursts:   gap < reset_after_silence  -> same burst (x1 held across the pause)
          gap >= reset_after_silence -> a reset may be planned at the END of the first burst
x1:       [max(timeline.start, burst.start - lead_in), reset)   length >= x1 animation
          shorter than the animation -> the whole cycle is dropped
base:     reset_base = min(burst.end + lead_out, timeline.end)
snap:     hard cuts in [base, base + snap_window) before the next zoom;
          usable when cut + x0_frames <= next_zoom_start; take the LAST usable one
fallback: no usable cut -> base, if base + x0_frames <= next_zoom_start
          otherwise -> NO reset; x1 stays open into the next burst (or to timeline.end)
x0:       [reset, reset + x0_frames)   exactly the animation length, never the native 42
```

`reset_after_silence_ms` is a **gate**, never a delay added to a speech end (D026). The
asset's native Media Pool duration is used nowhere (D025).

## Live results (2026-08-16)

Saved run: `.agent/reports/phase-04-plan-probe-report.txt`. `RESULT: PASS`, exit 0.

| Stage | Result |
| --- | --- |
| Render + VAD | unchanged from Phase 3: 15 segments, delta 0.000 frames |
| Editorial bursts | **14** (one 17-frame pause bridged, below the 39-frame gate) |
| `FACE_X1` placements | **14**, durations 30 / 93 / 250 frames (min/typical/max) |
| `FACE_X0` placements | **14**, every one exactly **15** frames |
| Resets | 14 direct, **0 cut-snapped** |
| Suppressed | 0 resets without room, 0 cycles below the animation length, 0 cuts rejected |
| Zoom coverage | 1458 frames = **41.0%** of the range |
| Overlaps | **none**; placements sorted; nothing outside `[216000, 219555)` |

Cut snapping never fired, and that is a finding, not a bug: measured after the run, the
nearest hard cut after a burst end is `+35 +199 +122 +54 +382 +273 +97 +53 +180 +205 +97 +289
+360 +352` frames away, against a 21-frame window. Deliberately **not** tuned — see
`RESEARCH_NOTES.md`.

## Comparison with `DAZ_OUTPUT_MVP` (qualitative, not a score)

| Observation | Value |
| --- | --- |
| planned x1 / manual x1 | 14 / 12 |
| planned x0 / manual x0 | 14 / 12 |
| planned x1 overlapping a manual one | **12** |
| planned with no manual equivalent | 2 |
| manual with no planned equivalent | **0** |
| start offset (planned − manual) | min/med/max = −129 / **0** / +2 frames |
| duration ratio (planned ÷ manual) | min/med/max = 103 / **108** / 420 % |
| reset offset (planned − manual) | min/med/max = −106 / +5 / +87 frames |

Read as: the planner covers everything the editor zoomed, lands on the same onsets (median
offset 0), and over-triggers by 2 — consistent with the Phase 3 finding that 3 speech regions
are deliberately left un-zoomed. Zooms are slightly longer than the human's (median +8%) and
the outlier at 420% is the burst the editor cut short. Nothing was tuned against these
numbers, and nothing should be.

## Safety

- **Zero zoom insertions.** No `AppendToTimeline` on any Phase 4 path; asserted by test.
- Independent post-run audit: timelines exactly `DAZ_INPUT`, `DAZ_OUTPUT_MVP`; `DAZ_INPUT`
  still V1-V2/A1-A3 with no V3; `DAZ_OUTPUT_MVP` V3 still 12 `FACE_X1` + 12
  `FACE_X0_SMOOTH`; current timeline `DAZ_OUTPUT_MVP`; render queue empty; no `DAZ_` preset;
  Deliver back to `mov`/`ProRes422HQ` mode 1; no `DAZ_RENDER_TMP_*` on any Media Storage
  volume. The probe's own audit reports no differences.
- `SaveProject()` was never called.

## Verification results (this session)

- `pytest` — **237 passed** (188 before), no Resolve, no network, ffmpeg-dependent tests skip.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 28 source files.
- `plan-probe` against live Resolve — **PASS**, audit clean, exit 0.
- Independent Resolve audit after the run — clean, as above.

## Commands

```bash
.venv/bin/python -m davinci_auto_zoom plan-probe \
  --confirm-resolve-render-test \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT \
  --reference-timeline DAZ_OUTPUT_MVP \
  --config config.example.toml
```

## Remaining unknowns

Carried unchanged from Phase 3: whether A1 is truly voice-only (nobody has listened to the
render), track-deletion isolation vs Resolve's own mixdown with buses, no hand-labelled speech
reference, behaviour on much longer timelines, drop-frame display, and the Phase 2 items
(pixel confirmation, collision on a non-empty track, multi-clipInfo `AppendToTimeline`, id
stability across sessions).

New after Phase 4:

- **cut snapping is untested on real material** — the code path never fired on `DAZ_INPUT`
  (pure tests only). Whether `cut_snap_window_ms` should be larger is an editorial question
  that needs more than one timeline;
- **over-triggering is unsolved and expected.** The planner zooms on every burst; the editor
  does not. Nothing in speech alone distinguishes the 2-3 regions they leave alone. This is
  what the human correction pass is for, and what a semantic provider might address much
  later;
- the 15/15 frame animation lengths are the user's statement about their own assets, checked
  against the Phase 2 keyframe evidence (keyframes at 0 and 15) but not independently
  re-measured this phase;
- `PlanSource` is recorded but nothing validates it yet — deliberately Phase 5.

## Next task — Phase 5 (safe Resolve executor)

Insert the placements a plan already contains, on the configured zoom video track, on a
duplicate timeline first. Do **not** recompute timing: `AssetPlacement` gives `recordFrame`
and the exact duration for the proven `AppendToTimeline` call (D013). Validate `PlanSource`
against the live project and refuse on mismatch, fail-closed like the probes (D015).
Establish collision behaviour on a non-empty track *before* the first real apply — Phase 2
only ever inserted onto empty space. Ownership/idempotency comes after that.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task.
