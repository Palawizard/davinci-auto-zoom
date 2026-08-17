# Agent handoff

## Current state

**Phase 8c (intra-burst voice dynamics + cut snapping for every transition) is DONE.** Facecam
levels are no longer earned by elapsed talking time. They are earned by what the voice *does*
inside a burst: a dip of at least 20 dB below the cycle's own voice level, followed by a clear
pick-up. The pick-up is the anchor. First qualifying recovery -> `face_x2`, second ->
`face_x3`, everything after that ignored.

Cut snapping now applies to **every** facecam transition — entry, promotions and reset —
through one shared helper with per-class windows.

The whole `apply -> clean -> rebuild -> rebuild` workflow was proven live on DaVinci Resolve
Studio 21.0.4.5 with the new plan, and no Phase 7 refusal was weakened anywhere.

There are now **five** `DAZ_AUTO_PREVIEW_*` timelines:

| | timeline | unique id | ownership |
| --- | --- | --- | --- |
| Phase 5 | `DAZ_AUTO_PREVIEW_20260816_211026_c676d5af` | `96f30d77-…` | **legacy**, 0 markers |
| Phase 6 (visually validated) | `DAZ_AUTO_PREVIEW_20260817_132005_77443d7c` | `6d0bde62-…` | **legacy**, 0 markers |
| Phase 7 | `DAZ_AUTO_PREVIEW_20260817_163637_e8787ece` | `9fb7e350-…` | ambiguous by design, x1 only |
| Phase 8 — the duration-based **BEFORE** | `DAZ_AUTO_PREVIEW_20260817_233130_203670b0` | `5a973592-…` | 40/40 owned |
| **Phase 8c — the dynamics-based AFTER** | `DAZ_AUTO_PREVIEW_20260818_005127_086a1483` | `2848d398-2e48-4c9a-97d4-b4031710b55e` | **42/42 owned** |

**Do not delete any of them.** All four earlier ones were verified byte-for-structure unchanged
after the whole Phase 8c workflow, with their original unique ids.

**The A/B to watch is Phase 8 vs Phase 8c**, same source, same resets, same burst boundaries —
only the level decisions and the zoom-in anchors differ.

## What changed, and why

Phase 8's rule predicted the level from the *length* of a burst. That is why D047 had to
record a divergence it could not fix: a 204-frame cycle the editor kept at x2 while a
138-frame one went to x3. No monotonic function of duration produces both.

Measurement first, code second (`.agent/reports/phase-08c-voice-dynamics-analysis.txt`):
**all seven manual promotions in `DAZ_OUTPUT_MVP2` sit within 8 frames of a detected voice
recovery, six within 3, median +1.** The editor is cutting on a breath, not counting seconds.

- **`speech/energy.py` (new)** — short-time RMS -> dBFS envelope, from the *same* normalized
  16 kHz PCM the VAD already decoded (D050). One render, one ffmpeg pass, two readers.
- **`domain/dynamics.py` (new)** — `EnergySettings`, `EnergyEnvelope`, `VoiceValley` and the
  valley/recovery detector. Pure: no numpy, no ONNX, no file. Every threshold is a **dB
  difference against the burst's own 75th-percentile level**, so a gain change cannot change
  the edit (D051).
- **`domain/planner.py`** — `_zoom_chain` walks cues instead of clocks; one `_snap_to_cut`
  helper serves every transition class (D052); `PlannerSettings` loses four keys and gains
  five plus the zoom-in window; `PlanSource` gains `energy_settings`.
- **`config.py`** — `[speech.energy]`, and the four superseded `[planner]` keys are a hard
  **error** naming their replacement, never a silent ignore.

`resolve/executor.py`, `resolve/owned_preview.py`, `domain/ownership.py` and
`resolve/ownership.py` needed **no change at all** — again. The executor still does not know
what a level is.

## The honest result, and it is not the highest score

Peak level against the human edit: **9 of 14**, where Phase 8's duration rule got 11.

That is deliberate, and it is the better outcome, because of how the 9 split:

- **9 of 9** on the cycles whose automatic burst matches the human's cycle;
- **0 of 5** on the cycles whose burst does not (opens 14-123 frames early, or closes 77 late).

The correlation is total. Every remaining level divergence is a **burst-extent** divergence,
which is one named, already-planned problem instead of a bag of unexplained taste. Phase 8's 11
included cases it could not explain at all.

Two more things worth knowing before touching the thresholds:

1. **No local feature of a single valley separates the 7 used from the 56 unused.** Depth and
   duration overlap completely across the 63 valleys in this material. What makes the model
   work is that the ladder has two rungs and takes the first two *usable* cues. Adding a fifth
   threshold will fit noise on n=7;
2. **frame agreement is weaker than level agreement.** The planner takes the first usable cue;
   the human sometimes takes a later one, and nothing in the audio distinguishes them.

## Live results — PERFORMED, 2026-08-18

Full output: `.agent/reports/phase-08c-live-workflow-report.txt`.

**Plan.** 15 speech segments -> 14 bursts -> **42 placements**: 14 `x0_to_face_x1`, 9
`face_x1_to_face_x2`, 5 `face_x2_to_face_x3`, 5 `face_x1_to_x0`, 4 `face_x2_to_x0`, 5
`face_x3_to_x0`. 63 valleys found, 14 used. Peaks 5/4/5 at x1/x2/x3. Zero overlaps.
Fingerprint `sha256:a1d107e1…d46a5483`, identical to Phases 5-8.

**Cut snapping.** Entries 10 direct / 1 backward / 3 forward — and all four snapped entries
land on the exact frame the human's x1 starts, with no false positive. Promotions 13 direct /
1 backward / 0 forward, as the measurement predicted (only 1 of 7 manual promotions is on a
cut). Resets 6 direct / 8 backward / 0 forward: **frame-identical to Phases 6, 7 and 8.**

**Workflow.** `apply-preview` 42/42 inserted, 42/42 owned, audit differences none.
`clean-preview` 42 removed, `DAZ_RECOVERY_20260818_005646_93a1b0c0` created and deleted, 0
unowned. `rebuild-preview` x2 — #1 against the emptied track, #2 against #1's output (so the
full delete-then-reapply path): **0 differences in `(role, start, end, placement_id)` across
all 42, 0 duplicate ids.**

**Independent post-run audit.** 7/7 protected timelines unchanged with their original unique
ids; 6/6 Media Pool assets identical; exactly one timeline added (the intended preview); no
leftover recovery, scratch or temp timeline; render queue empty, no DAZ render preset left, the
project's own format/codec restored; no temp audio directory left under `/tmp`.

## Verification results (this session)

- `pytest` — **539 passed** (497 before; +42). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 39 source files.
- `plan-probe` live — PASS, 42 placements, no overlaps.
- `apply-preview` live — PASS, 42/42 inserted, 42/42 owned, audit clean.
- `clean-preview` live — PASS, 42 removed, recovery created and deleted.
- `rebuild-preview` live x2 — PASS, structurally identical, 0 duplicate placement ids.
- independent post-run audit — PASS.

## Operational note, unchanged and still true

`apply-preview` and `rebuild-preview` on this material take **several minutes**. Run them
without a short command timeout. The safety model is transactional against *failures*, not
against the process being killed: a kill mid-apply leaves a well-formed partial preview on its
own timeline (harmless, disposable by name), because the thing that would roll it back is the
thing that got killed.

Also new, and worth remembering: `numpy.convolve(..., mode="same")` zero-pads, so smoothing a
dBFS curve that way invents a loud burst at each end of the render — exactly where real bursts
start. Use edge padding. It cost one confusing calibration round.

## Remaining unknowns

Carried forward: neither the Phase 8 nor the Phase 8c preview has been **watched** —
structural correctness is proven, how it looks is not, and the level model in particular (does
a 28-frame x3 read as a move or as a glitch?) can only be settled by viewing; the rendered
voice audio has never been listened to; no hand-labelled speech reference; the fingerprint
cannot see Fairlight/OFX changes that move no clip; collision behaviour is still unmeasured
(D032); marker capacity untested at scale.

New after Phase 8c:

- **burst extent is now the only thing between the planner and the human edit.** Not the
  highest-value target any more — the *last* one at this level of the model;
- **the detector is calibrated on one speaker and one timeline**, 14 cycles and 7 promotions.
  The parameters sit on plateaus rather than spikes, which is the best available evidence they
  transfer, and is not evidence that they do;
- the 63-valley census is a property of this delivery. A faster or breathier speaker produces
  more valleys per second; the two-rung ladder bounds the damage, but which two would change;
- the envelope has never been checked against anyone's ears, only against clip positions;
- `DeleteClips` was exercised with 42 items in one batch (Phase 8: 40, Phase 7: 28). Still no
  upper bound.

Resolved by this phase: whether the human's level choices correspond to anything audible inside
the burst (they do, to within a frame or two); whether promotions should be cut-snapped (yes,
but the window almost never fires — 1 of 14 cues — which is exactly the behaviour that makes it
safe); whether the Phase 7/8 ownership and executor machinery is genuinely level-agnostic
(third phase running, still no change needed).

## Next task — Phase 8b (burst extent)

The offsets to explain, from `.agent/reports/phase-08c-voice-dynamics-analysis.txt` section 4:

    burst 12 opens 123 frames early     burst 1 closes 77 frames late
    burst  7 opens  41 frames early     burst 8 opens 18 early, burst 6 opens 14 early

Measure both directions before changing anything, as Phase 6 should have from the start. Do
not tune `reset_after_silence_ms` before that measurement exists, and do not touch the voice
dynamics thresholds to compensate for a burst problem — that is exactly the trade Phase 8c was
careful not to make.

Phase 7b (apply in place) and Phase 9 (gameplay states) both remain open; see
`.agent/IMPLEMENTATION_PLAN.md`. Gameplay is blocked on evidence, not on code (D048).

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it
with the code it describes.
