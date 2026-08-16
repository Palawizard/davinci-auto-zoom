# Agent handoff

## Current state

**Phase 5 (safe MVP executor + persistent auto-preview timeline) is implemented, fully
tested on fakes, and NOT yet confirmed by a live run.** See "Live run — not performed here"
below; it is the one open item of the phase and it is blocking the completion gate.

The chain now goes all the way to real clips:

```
DAZ_INPUT A1 -> isolated audio -> Silero VAD -> deterministic plan
  -> fresh source validation (fields + structural fingerprint)
  -> DAZ_AUTO_PREVIEW_<timestamp>_<id>, a duplicate of DAZ_INPUT
  -> empty dedicated V3 -> one AppendToTimeline per placement
  -> verified 1:1 against the plan -> preview KEPT for human review
```

`DAZ_INPUT` and `DAZ_OUTPUT_MVP` are never written to. On any failure the preview this run
created is deleted and the previously active timeline is restored.

## Verified environment

Unchanged from Phase 4: Resolve **Studio 21.0.4.5**, project `davinci-auto-zoom-test` at
60.0 fps, `DAZ_INPUT` (V1-V2, A1-A3, `[216000, 219555)`), `DAZ_OUTPUT_MVP` (= input + V3 with
12 `FACE_X1` + 12 `FACE_X0_SMOOTH`), voice **A1**, cut reference **V1**, zoom target **V3**,
assets `FACE_X1` / `FACE_X0_SMOOTH` at 15/15 transition frames.

## What Phase 5 built

- `domain/fingerprint.py` — canonical SHA-256 of the structure the planner reads: range,
  frame rate, voice-track items, cut-track items. Order-independent, `GetIsTrackEnabled`
  excluded (it lies for non-current timelines, D009), unread tracks excluded (D030).
- `domain/plan_validation.py` — `build_plan_source` (one constructor, used by both the
  planning run and the validating run), `plan_source_mismatches`, `validate_plan_source`,
  `timeline_frame_rate` (canonical `60` / `60000/1001`). Total comparison, fail-closed (D029).
- `domain/apply.py` — `plan_target_track` (create missing, accept empty, refuse populated),
  `clip_info_for` (the exact D013 call), `insertion_differences`, `placement_differences`.
- `domain/probe.py` — `ApplyPreviewTarget` + `apply_preview_preflight_failures`.
- `resolve/executor.py` — the executor and its report. Duplicate → prepare track → insert
  sequentially → verify each → verify the whole track → restore active timeline → keep or
  delete the preview → audit the protected timelines.
- `domain/planner.py` — `PlanSource` gained `asset_identities` (role → name + Media ID +
  unique id) and `structural_fingerprint`; new `AssetIdentity`.
- `cli.py` — `apply-preview`, sharing the plan-probe pipeline; the global help no longer
  claims no command places a zoom.

## The rules the executor obeys

```
for placement in plan.placements:
    AppendToTimeline([{mediaPoolItem: assets[placement.role],
                       startFrame: 0,
                       endFrame:   placement.duration_frames,   # exclusive (D013)
                       trackIndex: config.zoom_video_track,
                       recordFrame: placement.start_frame}])
```

It recomputes **nothing**: no bursts, no silence gate, no cut snapping, no x1/x0 durations,
and it never reads `AssetSnapshot.frames` to pick a length.

## Safety model, as implemented

| Guard | Behaviour |
| --- | --- |
| `--confirm-create-preview-timeline` missing | refused before Resolve is contacted |
| preflight (project, timelines, bin, assets, tracks) | fail-closed, before any mutation |
| `PlanSource` mismatch (any of 12 fields + identities + fingerprint) | refused, no preview |
| plan with overlapping placements | refused |
| target track populated | refused, no preview (D032) |
| empty plan | no preview created, reported as "nothing to apply", exit 0 |
| any insertion wrong (count/name/frames/track/no Fusion comp) | whole preview deleted |
| exception mid-run | whole preview deleted, active timeline restored, audit run |
| success | preview **kept**, active timeline still restored |
| always | `SaveProject()` never called; older `DAZ_AUTO_PREVIEW_*` never touched |

## Verification results (this session)

- `pytest` — **323 passed, 1 skipped** (237 before). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 32 source files.
- `apply-preview` against live Resolve — **NOT RUN** (see below).

## Live run — not performed here

This session ran in a **cloud container with no DaVinci Resolve installation** (no
`/opt/resolve`, no scripting module, no GUI). The live `apply-preview` run required by the
phase could therefore not be executed, and no claim is made about it. The command to run on
the workstation, unchanged from what the CLI expects:

```bash
.venv/bin/python -m davinci_auto_zoom apply-preview \
  --confirm-create-preview-timeline \
  --confirm-resolve-render-test \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT \
  --reference-timeline DAZ_OUTPUT_MVP \
  --config config.example.toml
```

Expected, if the material is unchanged since Phase 4: 14 `FACE_X1` + 14 `FACE_X0` = 28
placements, 28 items on V3 of a new `DAZ_AUTO_PREVIEW_*`, `RESULT: PASS`, exit 0. Those
counts are an expectation to sanity-check, **not** a condition the code enforces — if the
same input suddenly produces a materially different plan, diagnose that before creating the
preview (the plan is printed above the apply section of the report).

After the run, save the output to `.agent/reports/phase-05-apply-preview-report.txt` and
record here: the preview's exact name and unique id, the plan counts, the verification
result, and the post-run audit of `DAZ_INPUT` / `DAZ_OUTPUT_MVP`.

## Remaining unknowns

Carried forward: pixel confirmation of the Fusion effect, the rendered voice audio has never
been listened to, isolation vs Resolve's own mixdown with buses, no hand-labelled speech
reference, cut snapping never fired on real material, over-triggering by ~2 zooms, id
stability across sessions.

New after Phase 5:

- **collision behaviour on a non-empty track is still unknown, deliberately** (D032);
- the fingerprint cannot see Fairlight/OFX changes that move no clip (D030);
- multi-`clipInfo` `AppendToTimeline` remains untested;
- whether Resolve's duplicate of a timeline is faithful in ways the structural signature
  cannot see (the executor checks tracks, items and frames, and refuses if they differ).

## Next task — Phase 6 (ownership + idempotence)

In order: how a DAZ-created clip is identified on a later run; what a second run does with an
existing preview; then `clean` / `rebuild`; and only after those, whether applying in place
on a user timeline is safe. Do not weaken the empty-target-track rule until ownership exists.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task.
