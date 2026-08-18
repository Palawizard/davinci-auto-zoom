# Agent handoff

## Current state

**Facecam MVP is the current supported product baseline.** davinci-auto-zoom automates the
creator's facecam zooms from their own voice, and nothing else. Phase 10 froze that behaviour,
retired gameplay from the product (D068), and cleaned the tree to match.

    branch              dev                 main untouched
    Phase 10 change     810c9a2             the retirement + consolidation
    doc corrections     aa33d02, and this   on top of it
    previously validated e817594
    Resolve tested      DaVinci Resolve Studio 21.0.4.5, Linux
    project             davinci-auto-zoom-test

A commit cannot name its own SHA, so the last line of this list is always one behind:
`git log origin/dev` is the authoritative branch tip. Everything else in this file describes
the state as of `810c9a2`, which is the commit the live validation below was run against —
the ones after it change documentation only.

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
`probe-write`, `probe-ownership`. **Retired: `gameplay-study`.**

There is no apply-in-place, and the preview workflow is the supported write model.

## Latest live validation — 2026-08-18, this session

Full output: `.agent/reports/phase-10-live-regression-report.txt`; the reference it is measured
against is `.agent/reports/phase-10-pre-cleanup-baseline.txt`, captured before any code change.

`plan-probe` on `DAZ_INPUT` (voice A1, cuts V1, zooms V3), before and after the cleanup:

    15 speech segments -> 14 editorial bursts -> 63 valleys -> 42 placements
    x0_to_face_x1 14, face_x1_to_face_x2 9, face_x2_to_face_x3 5,
    face_x1_to_x0 5, face_x2_to_x0 4, face_x3_to_x0 5
    126 decision lines
    fingerprint sha256:a1d107e1…d46a5483   unchanged since Phase 8c

**Every editorial field is byte-identical PRE vs POST**: segments, bursts, valleys, placements
(role, start, end, reason, cut, burst), diagnostics, settings, decision trace, and the
plan-vs-reference numbers. The only differences are the three pieces of plan-identity metadata
the baseline authorised in advance — the six gameplay entries leaving `PlanSource.assets` /
`asset_transition_frames` / `asset_identities`, the six always-zero keys leaving
`diagnostics.role_counts`, and the trace's asset-inventory line listing 6 animations instead
of 12.

**No `apply-preview` was run and no preview was created**, deliberately: the planner, executor
and ownership write paths did not change functionally, and the Phase 8c preview is already
applied and visually validated by the creator.

Independent post-run audit: 4/4 protected timelines present with their original unique ids;
**zero timelines added, zero removed**; still exactly 5 `DAZ_AUTO_PREVIEW_*`; no scratch, no
recovery, no temp render directory; render queue empty with pre-existing jobs preserved; no DAZ
preset left behind; Deliver state restored to the values it held before the run; current
timeline restored to `DAZ_OUTPUT_MVP3`.

## Safety guarantees, unchanged by the cleanup

Nothing in the safety model was simplified away. Still in force and still tested: source
fingerprinting, full `PlanSource` re-validation before the first write, the empty-dedicated-track
rule, marker-based ownership with a four-state classifier, `stale`/`ambiguous` fail-closed,
`DAZ_RECOVERY_*` before any deletion, transactional cleanup in `finally`, active-timeline
restoration verified rather than assumed, Deliver restoration, render-queue preservation, and
the pre/post protected audits. `SaveProject()` is never called.

## What changed in the code this session

Removed, because they existed only for the retired research and no facecam path used them:

    domain/gameplay.py, domain/visual_episodes.py, vision.py, resolve/gameplay_study.py
    the gameplay-study CLI command
    STATE_GAMEPLAY, its six transitions and six roles, GAMEPLAY_ROLES,
      Transition.is_gameplay_entry / .is_gameplay_exit
    the six gameplay roles from config.example.toml
    render_voice_track's keep_audio_tracks / export_video / render_preset arguments,
      _silence_audio_tracks, VoiceRenderReport.media_kind / .kept_audio_tracks, and
      voice_render_preflight_failures' required_preset
    tests/test_gameplay.py, tests/test_vision.py, tests/test_visual_episodes.py

Added:

    RETIRED_ROLES in domain/transitions.py, so a config still naming a gameplay role gets an
      error explaining the retirement rather than "unknown key"
    tests/test_facecam_golden.py — the validated edit written out in full from synthetic inputs
    ownership tests: existing facecam previews stay ownable, a retired role fails closed,
      placement_id is unchanged
    config tests: both retirement messages, malformed TOML, role/timing mismatch in both
      directions, zero-length animation, the two-role minimum

`domain/planner.py`, `domain/dynamics.py`, `resolve/executor.py`, `resolve/owned_preview.py`,
`domain/ownership.py` and `resolve/ownership.py` needed **no functional change** — sixth phase
running.

Docs rewritten: `README.md` (a real user document now, not a development chronology),
`AGENTS.md`, `config.example.toml`, `.agent/PROJECT_CONTEXT.md`, `.agent/IMPLEMENTATION_PLAN.md`
and `.agent/RESEARCH_NOTES.md` (split into active evidence and archived research).
`CLAUDE.md` still imports `@AGENTS.md` and was not changed.

## Verification results (this session)

    pytest                    563 passed   (642 before; see the report for the accounting)
    ruff check .              All checks passed
    mypy (strict)             Success: no issues found in 39 source files
    config.example.toml load  OK, exactly the six facecam roles
    CLI --help smoke          OK, 12 subcommands, no gameplay-study
    packaging smoke           OK in a clean venv: install, console script, import,
                              vendored Silero model + checksum, wheel build
    live plan-probe           PASS, editorial plan byte-identical to the baseline
    independent audit         PASS, zero timelines added

## Known limitations

- **calibrated on one creator and one reference workflow.** The thresholds sit on broad
  plateaus rather than spikes, which is the best available evidence they transfer to other
  material — and still not evidence that they do;
- **burst extent (Phase 8b) is a deferred, accepted calibration limitation, not a blocker.**
  Against `DAZ_OUTPUT_MVP2` a few bursts open earlier or close later than the human's (worst:
  -123 frames on burst 12, +77 on burst 1). The creator watched the applied Phase 8c preview
  and validated it. A human reference edit is not a golden truth to reproduce frame-perfect.
  **Do not retune the VAD or `reset_after_silence_ms`** without first measuring both directions
  per burst;
- a dedicated voice track is required, and its index is configuration, not detection;
- the rendered voice audio has never been listened to, and there is no hand-labelled speech
  reference;
- the fingerprint cannot see Fairlight or OFX changes that move no clip (D030);
- collision behaviour on a non-empty track is unmeasured, by choice (D032);
- marker capacity is uncharacterised, and survival across a project close/reopen is assumed
  rather than measured;
- only tested on Resolve Studio 21.0.4.5 on Linux. Windows and macOS are unverified, not
  supported.

## Deferred work — none of it assigned

- **Phase 8b, burst-extent calibration research.** Optional. Measure before tuning.
- **Phase 7b, apply in place.** The preview workflow is the supported write model.
- **UI / Resolve launcher / distribution.** The old Phase 10, now future work.
- **Other video types.** The creator may later want to study how DAZ could work on a different
  kind of video. Nothing is designed for it and nothing should be built in advance — no profile
  interface, no video-type enum, no plugin system. The boundary that makes it possible later
  (objective facts -> pure planner -> placements -> executor) already exists.

## Retired research

Phases 9a and 9b studied whether a GAMEPLAY zoom could be placed automatically. Both produced
negative results, both are archived, and **neither is a next task**:

- 9a: creator silence, secondary-audio activity and visual motion amount all have fully nested
  class ranges. Ablation 8/15, below a 10/15 majority baseline; a no-signal rule scored 11/15;
- 9b: nine spatial features, same outcome, with the reason nameable — a frame difference in a
  first-person game measures the player's camera, not the game's events. Every hard negative
  has a manual-gameplay twin within about one standard deviation.

The creator then decided not to pursue the direction for this type of video. Phase 9c is
**cancelled, not blocked** (D068). The measurements stay in `.agent/reports/phase-09*`, each
carrying an ARCHIVED RESEARCH banner, and the code lives in Git history.

**The next task is not camera-motion compensation, and it is not gameplay.** If gameplay is
ever revisited it starts as new research from a second reference edit that uses it — not from
anything left behind in this tree.

`DAZ_OUTPUT_MVP3` is kept as a research archive. Do not delete it, or any of the five
`DAZ_AUTO_PREVIEW_*` timelines.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions (also
append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it with the
code it describes.
