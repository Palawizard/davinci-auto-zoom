# Agent handoff

## Current state

**Phase 9a (gameplay reference analysis, multimodal policy discovery, dry run) is DONE.**
It is a measurement phase, and **its main result is a negative one**: on the user's own
gameplay edit, nothing measurable predicts which silence becomes GAMEPLAY. Read
`.agent/reports/phase-09a-mvp3-gameplay-analysis.txt` before assuming otherwise.

**Phase 8c's facecam behaviour is now VISUALLY VALIDATED by the user** and is the stable
baseline. `X0 -> FACE_X1` on a burst, `FACE_X2` on the first voice valley/recovery, `FACE_X3`
on the second, cut snapping on every transition, reset to X0, ownership/clean/rebuild/
idempotence. **Do not change facecam logic without a demonstrated bug.** Every doc that
described it as unwatched or aesthetically unknown has been corrected.

Phase 9a added **no timeline of any kind**. There are still exactly five
`DAZ_AUTO_PREVIEW_*` timelines, unchanged, plus `DAZ_INPUT`, `DAZ_OUTPUT_MVP`,
`DAZ_OUTPUT_MVP2` and the new human reference `DAZ_OUTPUT_MVP3`. Do not delete any of them.

## What Phase 9a found

**`DAZ_OUTPUT_MVP3` respects the announced graph exactly.** All 42 clips on V3 walk cleanly
from X0 back to X0 through `domain/transitions.py`. Five of the six authorised gameplay moves
appear; `face_x3_to_gameplay` appears nowhere, so it rests on the user's statement rather than
on evidence. No forbidden move appears — had one, the analysis would have stopped and reported
it rather than interpreting it.

    42 transitions, 10 gameplay episodes, final state x0
    entries : x0_to_gameplay 6, face_x1_to_gameplay 3, face_x2_to_gameplay 1
    exits   : gameplay_to_face_x1 9, gameplay_to_x0 1

**Silence duration does not separate the classes — the ranges NEST** (D056):

    gameplay windows: 46, 47, 53, 91, 103, 128, 160, 183, 211, 222 frames
    X0 windows      : 55, 97, 126, 154, 421 frames

The shortest gap that became gameplay (46) is shorter than the shortest that stayed X0 (55),
and the **longest silence in the timeline, 421 frames / 7.0 s, stayed X0**. Neither does
secondary-audio activity, mean level, dynamic range, onset count, mean motion, p90 motion,
active fraction or hard-cut density — every one has fully nested class ranges, and every
single-threshold rule scores 10 or 11 of 15 against a majority baseline of 10.

**The ablation says ship neither signal** (D059):

    A silence only            5/15
    B silence + audio         8/15    false+ 1, 11
    C silence + video         8/15    false+ 10, 11, 12
    D silence + audio + video 8/15    false+ 1, 10, 11, 12
    candidate (no signal)    11/15    false+ 1, 10, 11, 12

B, C and D all land *below* the baseline, and D inherits every false positive of B and of C
while fixing one false negative — two signals wrong in different places, not two that combine.
The secondary-audio and video renders stay as measurement in `gameplay-study`; **neither
belongs in a runtime planner.**

**The candidate rule therefore uses no signal at all** and has zero fitted parameters:

    a would-be-X0 window becomes GAMEPLAY
      unless it is shorter than 500 ms, or it is the tail of the timeline

**One thing is solved sharply: the exit anchor** (D057). Gameplay exits are placed on the
creator's next burst start — 6 of 10 within one frame, 8 of 10 within 20 — while the nearest
hard cut is 87-152 frames away in seven of nine cases. The editor is watching the voice, not
the cut list. This is the mirror image of the facecam resets, which *do* snap to cuts.

**The entry anchor is not solved at all.** No cluster, no signal; 7 of 10 proposals open the
game *earlier* than the editor did, by up to 170 frames, mean |delta| 62.

## What changed in the code

- **`domain/transitions.py`** — `STATE_GAMEPLAY` and exactly six moves (D053). Closed graph:
  `GAMEPLAY -> FACE_X2/FACE_X3/GAMEPLAY` raise. `is_promotion` redefined positively (both ends
  in `FACECAM_LADDER`) so leaving a level *for the game* is not counted as a promotion.
- **`vision.py` (new)** — rendered video -> ffmpeg -> 64x36 grayscale at 10/s -> motion
  envelope. No new dependency; frames are mean-subtracted before differencing so a uniform
  brightness change reads as zero motion (D055). `motion_from_frames` is split out so the
  metric is testable on hand-built arrays with no ffmpeg anywhere near it.
- **`domain/gameplay.py` (new)** — pure. Manual state reconstruction, silence windows, relative
  audio/video features, and `decide_gameplay`, which keeps "gameplay or X0" and "exactly where"
  apart (D057) and emits no `AssetPlacement` (D061).
- **`resolve/gameplay_study.py` + `gameplay-study` CLI (new)** — the diagnostic. Three renders
  through the same `render_voice_track` primitive, so the same audit, cleanup and refusal
  behaviour. Never calls `AppendToTimeline`.
- **`resolve/voice_render.py`** — three optional keyword arguments (`keep_audio_tracks`,
  `export_video`, `render_preset`), each defaulting to Phase 3's exact behaviour. Plus
  `_silence_audio_tracks`, see below.
- **`domain/planner.py`** — `_snap_to_cut`/`_Snap` made public as `snap_to_cut`/`Snap` for
  reuse; `top_state_counts` skips non-ladder states. No behaviour change.
- **`config.example.toml`** — all twelve roles, with the gameplay six marked optional and
  `face_x3_to_gameplay = 15` explicitly flagged as *inferred, not measured*.

`resolve/executor.py`, `resolve/owned_preview.py`, `domain/ownership.py` and
`resolve/ownership.py` needed **no change at all** — fourth phase running.

## The guard that fired, and it is the good news

The first secondary-audio render **refused**:

    VoiceRenderFailed: the surviving audio track(s) are [('Audio 1', 21), ('Audio 2', 21)],
    expected [('Audio 2', 21), ('Audio 3', 21)]

Deleting A1 to leave A2+A3 renumbers *and renames* the survivors on 21.0.4.5, and since all
three tracks here carry the same 21 items, the post-condition could no longer prove which track
survived. It stopped rather than analysing a mix it could not identify.

The fix was not to weaken the check. `_silence_audio_tracks` empties unwanted tracks with
`DeleteClips`, so dropped tracks end at zero items while kept tracks keep index, name and item
count — strictly stronger, and positional rather than name-based (D063). `_isolate_voice_track`
is untouched and still runs the proven Phase 3 single-track path.

Consequence: **which of A2/A3 is the game and which is other people is never claimed anywhere.**

## Live results — PERFORMED, 2026-08-18

Full output: `.agent/reports/phase-09a-live-workflow-report.txt`.

**`gameplay-study`** — PASS. Three renders (voice 7.0 s, secondary audio 5.0 s, program video
18.0 s), each on its own scratch duplicate. All three: `clean` true, `error` null,
`audit_differences` empty, scratch absent after cleanup, render queue restored, no unrestored
Deliver state, render directory removed. 11/15 windows classified as the human did.

**`plan-probe` facecam regression** — PASS, and this is the important one. Before and after:

    42 placements IDENTICAL in (role, start, end, reason, cut_frame, burst_index)
    14 bursts, 15 segments, 63 valleys / 14 used — identical
    126-line decision trace — 125 lines identical
    fingerprint sha256:a1d107e1…d46a5483 — unchanged since Phase 5

The only two differences anywhere are "the config declares more assets": `role_counts` now
lists the six gameplay roles at **0**, and `PlanSource` records six more configured assets.
Adding `STATE_GAMEPLAY` moved no frame (D054).

**Independent post-run audit** — PASS. 9/9 protected timelines unchanged with their original
unique ids; **zero timelines added**; no leftover recovery, scratch or temp timeline; 12/12
Media Pool items identical; render queue empty; no DAZ render preset left; project
format/codec restored to mov/ProRes422HQ; current timeline restored to `DAZ_OUTPUT_MVP3`; no
temp directories under `/tmp`.

## Verification results (this session)

- `pytest` — **615 passed** (539 before; +76). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 42 source files.
- `gameplay-study` live — PASS, 3 clean renders, 11/15 agreement.
- `plan-probe` live — PASS, 42 placements byte-identical to the Phase 8c baseline.
- independent post-run audit — PASS, zero timelines added.

## Operational note

`gameplay-study` takes a few minutes (three renders + VAD + envelope + ffmpeg video decode).
Run it without a short command timeout, for the same reason as `apply-preview`: the safety
model is transactional against *failures*, not against the process being killed. A kill mid-run
leaves a `DAZ_AUDIO_SCRATCH_*` timeline and possibly a temporary render preset, both disposable
by name, because the thing that would clean them up is the thing that got killed.

## Remaining unknowns

New after Phase 9a:

- **the gameplay entry anchor is unexplained.** No cluster, no signal, systematically early.
  This is the single largest open question in the gameplay model;
- **the four "whether" errors share a location, not a feature.** Gaps 10, 11 and 12 are three
  *consecutive* windows in the one stretch where the creator talks in short bursts and the
  editor never cut to the game; gap 1 is the timeline's second window. Nothing measured sees
  that stretch. Asking the creator is worth more than another feature;
- **everything rests on one reference timeline and 15 windows**, 10 of them labelled gameplay.
  The negative result is robust to that — nested ranges are not a small-sample artifact — but
  the candidate rule's 11/15 is not;
- `face_x3_to_gameplay` has **no manual instance**: both its place in the graph and its
  15-frame length are inferred (D062). Confirm with the creator before Phase 9b uses it;
- `mixed` windows are scored as gameplay for "whether". Under a stricter reading the rule does
  worse, and the entry deltas are exactly why: the human fills 9-70% of a mixed window, the
  proposal fills all of it;
- the motion metric has never been checked against anyone's eyes, only against clip positions.

Carried forward: the rendered voice audio has never been listened to; no hand-labelled speech
reference; the fingerprint cannot see Fairlight/OFX changes that move no clip; collision
behaviour is still unmeasured (D032); marker capacity untested at scale; the detector is
calibrated on one speaker.

**Retired by this phase:** "no preview has been watched since Phase 6" — the user has now
watched and validated the Phase 8c preview.

## Next task — NOT Phase 9b, and not automatically Phase 8b either

Phase 9a's own recommendation, in descending order of expected value:

1. **A second reference edit that uses gameplay.** Worth more than everything below combined.
2. **Ask the creator about gaps 1, 10, 11 and 12** — why did the game not come up there? Four
   windows, one question.
3. **Phase 8b (burst extent).** Still open, and now relevant to gameplay too: three of ten
   manual gameplay entries sit inside a detected burst, and one is exactly Phase 8c's recorded
   burst-1 overrun of 77 frames. It contaminates the entry measurements.
4. Only then Phase 9b: the entry-anchor rule and `AssetPlacement` emission.

Phase 8b is no longer the *imposed* next milestone — the user's facecam rendering is good
enough — but it is still the cleanest remaining defect, and it now blocks gameplay entry
modelling as well.

Phase 7b (apply in place) remains open; see `.agent/IMPLEMENTATION_PLAN.md`.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it
with the code it describes.
