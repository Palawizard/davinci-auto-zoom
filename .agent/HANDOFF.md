# Agent handoff

## Current state

**Phase 8 (multi-level facecam transition engine) is DONE** — the architecture no longer
models the edit as one zoom level and its opposite, the facecam ladder `x0 -> x1 -> x2 -> x3`
is implemented and calibrated on the user's own reference edit, and the whole
`apply -> clean -> rebuild -> rebuild` workflow was proven live on DaVinci Resolve Studio
21.0.4.5 with the new roles.

Phase 7's ownership model was not weakened anywhere. No editorial parameter from Phases 4-6 was
touched, and the live run proves it: the burst boundaries, the 14 reset frames, the 8 backward
cut snaps and the 39.9% coverage are **identical** to the Phase 7 run. What changed is only how
the 1417 zoomed frames are subdivided — 26 zoom-in clips at three levels instead of 14 at one.

There are now **four** `DAZ_AUTO_PREVIEW_*` timelines:

| | timeline | unique id | ownership |
| --- | --- | --- | --- |
| Phase 5 | `DAZ_AUTO_PREVIEW_20260816_211026_c676d5af` | `96f30d77-…` | **legacy**, 0 markers |
| Phase 6 (visually validated) | `DAZ_AUTO_PREVIEW_20260817_132005_77443d7c` | `6d0bde62-…` | **legacy**, 0 markers |
| Phase 7 | `DAZ_AUTO_PREVIEW_20260817_163637_e8787ece` | `9fb7e350-…` | 28/28 owned, x1 only |
| **Phase 8** | `DAZ_AUTO_PREVIEW_20260817_233130_203670b0` | `5a973592-620d-4dff-8063-4c7b97d5673c` | **40/40 owned, x1/x2/x3** |

**Do not delete any of them.** The three earlier ones were verified untouched after the whole
Phase 8 workflow.

Note what the Phase 7 preview now is: its markers carry `role: "facecam_x1"` / `"reset_x0"`,
which are not roles this build configures, so the classifier reads it as **`ambiguous`** and
`clean-preview` refuses it. That is the fail-closed behaviour working as designed (D037), and
it is the correct outcome for a preview we want preserved anyway. There is no migration path
and there must not be one, exactly as for the legacy previews (D039).

## What Phase 8 changed, and why

`facecam_x1` + `reset_x0` was not a small model, it was the *wrong* model. It cannot express
"the creator kept talking, so go tighter", and bolting x2/x3 onto it puts special cases in the
planner *and* the executor. So the model was inverted (D044):

- **`domain/transitions.py` (new)** — the visual states (`x0`, `face_x1`, `face_x2`, `face_x3`)
  and the six allowed transitions between them, as one closed table. `ForbiddenTransition` on
  anything else; never a silent no-op.
- **the planner reasons in states**, emitting a chain per burst: one entry, zero or more
  promotions, one reset whose asset depends on the level reached.
- **the executor did not change shape.** It resolves a role to a clip name, appends at the
  planned frames, verifies, tags. It never learned what a level is — which is the property that
  makes gameplay a table change later rather than a rewrite.

The ladder is climbed one rung at a time and never descended (D045). No `x0 -> x2`, no
`x2 -> x1`, no `x3 -> x2`. That is not an oversight: all 14 manual cycles in `DAZ_OUTPUT_MVP2`
obey it.

## The bin changed under us

`FACE_X0_SMOOTH` **no longer exists**. The bin now holds six Generators, all with a 15-frame
animation: `FACE_X1` / `FACE_X2` / `FACE_X3` (132 frames native) going in, `X1_TO_X0` /
`X2_TO_X0` / `X3_TO_X0` (42 native) coming back. Anything in the older docs naming
`FACE_X0_SMOOTH` is history.

Role mapping, now the config's key set:

```
FACE_X1  = x0_to_face_x1        X1_TO_X0 = face_x1_to_x0
FACE_X2  = face_x1_to_face_x2   X2_TO_X0 = face_x2_to_x0
FACE_X3  = face_x2_to_face_x3   X3_TO_X0 = face_x3_to_x0
```

Only `x0_to_face_x1` and `face_x1_to_x0` are required. Omitting a promotion role from both
`[assets]` and `[assets.transition_frames]` is the supported way to run a one- or two-level
edit, and the planner then simply never makes that move.

## The MVP2 analysis, and the honest part of it

Full report: `.agent/reports/phase-08-mvp2-analysis.txt`. Three findings matter for whoever
picks this up.

**1. The x2 threshold is grounded in a gap in the data, not a curve fit.** On
`DAZ_OUTPUT_MVP2`, the cycles the editor left at x1 span at most 75 frames; the shortest they
promoted spans 87. No overlap, 11-frame gap. Any "promote at +T if R remains" rule with
`T + R` in (75, 87] reproduces all fourteen human decisions. The chosen 1000 + 350 ms = 81
frames sits in the middle of that gap.

**2. The x3 threshold rests on one observation.** There is exactly one `FACE_X3` in the entire
reference timeline. 1800 + 500 ms = 138 frames is exactly that cycle's span. It is implemented
because the phase needs a working x3 rule and this is the only evidence there is. It is one
config line to change, and it is the weakest thing in the phase.

**3. Promotions are not on cuts, and resets are.** Same timeline, same measurement, opposite
answers: 1 of 7 manual promotions on a hard cut (chance, given 20 cuts in 3555 frames), against
8 of 14 manual resets. So promotions are never cut-snapped and D034's reset rule is untouched
(D046).

**"x1 over-triggering" was never a defect.** The 14-planned-vs-12-manual figure carried since
Phase 4 was measured against `DAZ_OUTPUT_MVP`, an older looser edit. `DAZ_OUTPUT_MVP2` has 14
cycles, aligned 1:1 with the planner's 14 bursts. Struck from the uncertainty list.

## Live results — PERFORMED, 2026-08-17

Full output: `.agent/reports/phase-08-live-workflow-report.txt` and
`.agent/reports/phase-08-plan-comparison.txt`.

**Plan.** 15 speech segments -> 14 bursts -> **40 placements**: 14 `x0_to_face_x1`, 8
`face_x1_to_face_x2`, 4 `face_x2_to_face_x3`, 6 `face_x1_to_x0`, 4 `face_x2_to_x0`, 4
`face_x3_to_x0`. Peaks: 6 cycles at x1, 4 at x2, 4 at x3. Zero overlaps. Fingerprint
`sha256:a1d107e1fa95b6152dd06be89ccc0fbd39de8d02faa906f91d3d7fc6d46a5483`, identical to Phases
5-7 — the source has not moved.

**Against the human edit:** peak level matches on **11 of 14 cycles**. Median x1->x2 promotion
delta +1 frame; the single x2->x3 comparison lands within 1 frame. Median reset delta 0.

**The 3 mismatches, and their causes** (all predicted by the analysis before the planner ran):

- cycles 1 and 12 — **burst-extent disagreements, not promotion errors.** In cycle 1 the
  automatic reset lands 77 frames after the human's; in cycle 12 the burst opens 123 frames
  early (it is the one burst built from two speech segments). Both make the automatic cycle far
  longer, and a longer cycle promotes. Fixing burst extent removes 2 of the 3;
- cycle 7 — genuine human nuance. The **longest** cycle in the timeline (204 frames) was
  deliberately kept at x2 while the 138-frame closing cycle went to x3. No monotonic duration
  rule produces both. Documented, not worked around.

**Workflow:** `apply-preview` 40/40 inserted, 40/40 owned, audit differences none.
`clean-preview` 40 removed, `DAZ_RECOVERY_20260817_233554_933a3341` created and deleted,
0 unowned. `rebuild-preview` x2 — rebuild #1 against the emptied track, rebuild #2 against #1's
output (so it exercised the full delete-then-reapply path): **0 differences in `(role, start,
end, placement_id)` across all 40 placements, 0 duplicate ids.**

**Independent post-run audit: 20/20.** All three earlier previews plus `DAZ_INPUT`,
`DAZ_OUTPUT_MVP` and `DAZ_OUTPUT_MVP2` byte-for-byte unchanged with their original unique ids;
the asset bin unchanged; every non-V3 track of the new preview equal to `DAZ_INPUT`'s; no
leftover recovery, scratch or temp artefacts; render queue empty and the project's own render
preset restored.

The commands, for reruns:

```bash
.venv/bin/python -m davinci_auto_zoom plan-probe \
  --confirm-resolve-render-test \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT --reference-timeline DAZ_OUTPUT_MVP2 \
  --config config.example.toml
```

```bash
.venv/bin/python -m davinci_auto_zoom rebuild-preview \
  --confirm-rebuild-owned-preview --confirm-resolve-render-test \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT --reference-timeline DAZ_OUTPUT_MVP2 \
  --preview-timeline DAZ_AUTO_PREVIEW_20260817_233130_203670b0 \
  --config config.example.toml
```

## Operational note for the next agent

`apply-preview` and `rebuild-preview` on this material take **several minutes** (render + VAD +
40 insertions with a marker and a re-read each). Run them without a short command timeout. One
`apply-preview` in this session was killed by a 2-minute harness timeout at item 16 of 40; the
partial preview it left could not be rolled back by the executor, because the executor was the
thing that got killed. It was disposed of by name and the run repeated cleanly.

This is worth knowing about the safety model's limits: it is transactional against *failures*,
not against the process being killed. Nothing was corrupted — the debris was a well-formed
partial preview on its own timeline, and the protected timelines were never touched.

## Code changes

- `domain/transitions.py` **(new)** — states, allowed transitions, role lookup, ladder helpers.
- `domain/planner.py` — `AssetTiming` is now a role->frames map that doubles as the capability
  list; `PlannerSettings` gains the four promotion thresholds; `_zoom_chain` climbs the ladder;
  `ZoomPlan` reports per-role counts and peak levels instead of `x1_placements` /
  `x0_placements`. The burst, reset and cut-snap logic is unchanged.
- `config.py` — `[assets]` and `[assets.transition_frames]` are keyed by transition role and
  must agree; a file that names any asset replaces the default table rather than merging.
- `domain/speech_report.py` — `reference_zooms` takes several names; `merge_adjacent` collapses
  a multi-clip cycle into the one zoom a viewer sees.
- `cli.py`, `resolve/write_probe.py`, `resolve/ownership_probe.py` — new role names.
- `tests/fake_resolve.py` — the fake bin now holds all six generators.
- `tests/test_transitions.py` **(new)**, plus multi-level sections in `test_planner.py` and
  `test_owned_preview.py`.

**`domain/ownership.py`, `resolve/ownership.py`, `resolve/owned_preview.py` and
`resolve/executor.py` needed no change for the new roles** — they were already role-agnostic.
That was the Phase 7 design paying off, and the new tests assert it rather than assume it.

## Verification results (this session)

- `pytest` — **497 passed** (451 before; +46). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 37 source files.
- `plan-probe` live — PASS, 40 placements, no overlaps.
- `apply-preview` live — PASS, 40/40 inserted, 40/40 owned, audit clean.
- `clean-preview` live — PASS, 40 removed, recovery created and deleted.
- `rebuild-preview` live x2 — PASS, structurally identical, 0 duplicate placement ids.
- independent post-run audit — PASS, 20/20.

## Remaining unknowns

Carried forward: the Phase 8 preview has **not been watched** — structural correctness is
proven, how it looks is not, and the level model in particular (does a 26-frame x2 read as a
move or as a glitch?) can only be settled by viewing; the rendered voice audio has never been
listened to; no hand-labelled speech reference; the fingerprint cannot see Fairlight/OFX changes
that move no clip; the 120 ms lookback is calibrated on one timeline; collision behaviour is
still unmeasured (D032); marker capacity untested at scale.

New after Phase 8:

- **the x3 thresholds rest on n=1** and are the least trustworthy numbers in the config;
- **burst extent is now load-bearing.** It always mattered, but with a fixed x1 level a burst
  that ran 77 frames long only produced a late reset. It now also produces a level the human
  did not choose. This is the highest-value measured target available;
- whether any of the four thresholds transfer to other material at all. One timeline;
- `DeleteClips` was exercised with 40 items in one batch (Phase 7: 28). Still no upper bound.

Resolved by this phase: whether the architecture could carry more than two states; whether the
Phase 7 ownership model was genuinely role-agnostic (it was); whether x1 over-triggering was
real (it was not).

## Next task — Phase 8b (burst extent), recommended

Measure, before changing anything: for each of the 14 bursts, the offset between the automatic
and the manual cycle start *and* end, against the speech segments and the bridged gaps. Both
directions — the Phase 6 mistake was measuring one. Cycle 12 (the two-segment burst) and cycle 1
(the 77-frame late reset) are the two cases to explain first.

Do not tune `reset_after_silence_ms` before that measurement exists, and do not touch the
promotion thresholds to compensate for a burst problem.

Phase 7b (apply in place) and Phase 9 (gameplay states) both remain open; see
`.agent/IMPLEMENTATION_PLAN.md`. Gameplay is blocked on evidence, not on code: there is no
reference edit that uses it, no asset family for it, and no measurement of what triggers it
(D048).

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it
with the code it describes.
