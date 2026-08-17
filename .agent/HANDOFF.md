# Agent handoff

## Current state

**Phase 7 (persistent ownership + safe clean/rebuild + idempotence) is DONE** — implemented,
tests green, and proven against DaVinci Resolve Studio 21.0.4.5 with a full live
`apply → clean → rebuild → rebuild` workflow plus an independent 22/22 safety audit.

Phase 6 was confirmed **visually** by the user before this phase started, and no editorial
parameter was touched. The live Phase 7 plan is identical to the Phase 6 one, placement for
placement.

There are now **three** `DAZ_AUTO_PREVIEW_*` timelines:

| | timeline | unique id | ownership |
| --- | --- | --- | --- |
| Phase 5 | `DAZ_AUTO_PREVIEW_20260816_211026_c676d5af` | `96f30d77-…` | **legacy**, 0 markers |
| Phase 6 (visually validated) | `DAZ_AUTO_PREVIEW_20260817_132005_77443d7c` | `6d0bde62-…` | **legacy**, 0 markers |
| Phase 7 | `DAZ_AUTO_PREVIEW_20260817_163637_e8787ece` | `9fb7e350-345f-465c-99a8-60ef1c85d972` | 28/28 owned |

**Do not delete any of them.** The two legacy previews were verified untouched after the whole
Phase 7 workflow, still hold their 28 items each, and carry **zero markers of any kind**. They
are `unowned` permanently and by design — there is no migration path and there must not be one
(D039). Both are refused by `clean-preview` with
`legacy preview: no verifiable DAZ ownership metadata`, verified live.

## What Phase 7 changed, and why

Phase 5/6 could create a preview but had no way to recognise it afterwards. Two `FACE_X1`
items — one DAZ's, one the user's — are indistinguishable by name, track, position, duration,
Fusion comp and Media Pool asset. So ownership had to come from somewhere else entirely.

**The primitive: a marker on the TimelineItem instance, carrying a versioned JSON record in
`customData` (D035).** Chosen from the installed README (documented, non-deprecated) and then
*proven* on the real thing before anything depended on it, because our items are Generators —
no Media Pool item, no source frames — which is exactly the item type an API might treat
differently.

The record:

```json
{"asset":"FACE_X1","end":216132,"namespace":"davinci-auto-zoom","placement_id":"3b8eef7c486c0f9600797b6edecd095f",
 "preview_id":"9fb7e350-…","role":"facecam_x1","schema":1,"source_fingerprint":"sha256:a1d107e1…","start":216046}
```

`placement_id = sha256(canonical JSON of {version, source_fingerprint, role, start, end,
asset})[:32]`. No clock, no randomness, no counter, and deliberately not the preview id — a
placement belongs to a plan, not to whichever preview holds it (D043).

Four states, not two (D037): `owned`, `unowned`, `stale`, `ambiguous`. `unowned` is the safe
answer; `ambiguous` means **zero deletions on the whole track**.

## Code changes

- `domain/ownership.py` **(new)** — record, canonical serialize/parse, `placement_id`,
  `classify_item` / `classify_track`, `free_marker_frame`. Pure, no Resolve.
- `resolve/ownership.py` **(new)** — `read_markers`, `snapshot_owned_item/track`, `tag_item`.
  The only module that reads or writes a marker. Never destructive.
- `resolve/ownership_probe.py` **(new)** — `probe-ownership`, the isolated live spike.
- `resolve/owned_preview.py` **(new)** — `clean-preview` and `rebuild-preview`.
- `resolve/executor.py` — claims every created item and verifies the whole track through the
  classifier; rolls the preview back if anything is not owned. Three helpers were made to
  return data instead of mutating an `ApplyReport` (`prepare_target_track`,
  `verify_target_track`, `verify_ownership`, `restore_previous_timeline`) so the new commands
  share them rather than reimplementing safety properties.
- `cli.py` — `probe-ownership`, `clean-preview`, `rebuild-preview`.
- `tests/fake_resolve.py` — TimelineItem markers, `DeleteClips`, and the measured
  "current timeline only" restriction (D042).
- `.gitignore`, `AGENTS.md`, `CLAUDE.md` — the agent docs are now tracked (see below).

**The planner was not touched.** `domain/planner.py` is byte-identical to `d12553b`.

## Verification results (this session)

- `pytest` — **451 passed** (361 before; +90). No Resolve, no network.
- `ruff check .` — All checks passed.
- `mypy` (strict) — Success: no issues found in 36 source files.
- `probe-ownership` live — **PASS**, 14/14 checks.
- `apply-preview` live — **PASS**, 28/28 inserted, 28/28 owned.
- `clean-preview` live — **PASS**, 28 removed, recovery created and deleted.
- `rebuild-preview` live ×2 — **PASS**, byte-identical structure.
- foreign-item selective-clean proof, disposable timeline — **PASS**, 13/13.
- independent post-run audit — **PASS**, 22/22.

## Live results — PERFORMED, 2026-08-17

**Plan unchanged from Phase 6.** 15 segments → 28 placements, coverage 39.9%, the same 14 x0
frames (`216132 216442 216519 216793 216930 217039 217312 217749 217999 218183 218297 218469
218762 219127`) with the same 8 backward cut snaps. Fingerprint
`sha256:a1d107e1fa95b6152dd06be89ccc0fbd39de8d02faa906f91d3d7fc6d46a5483`, identical to Phases
5 and 6 — the source has not moved.

**Ownership primitive, measured:** markers attach to the *instance*, not the shared asset (no
other `FACE_X1` in the project acquired metadata); the record round-trips byte-identically;
it survives switching timelines and back; an identical untagged twin classifies `unowned`; a
pre-existing user marker at local frame 0 was stepped around (DAZ took frame 1) and came back
untouched; **`DuplicateTimeline` copies the markers**, and the copy classifies as `stale`
against its own identity (D038).

**Idempotence.** Rebuild #1 vs rebuild #2, all 28 placements, `(role, start, end,
placement_id)` compared line by line: **0 differences, 0 duplicate placement ids, 28 items
after each.** Resolve's own item ids change, as expected, and are not part of the comparison.

**Foreign-item proof** (disposable timeline, deleted afterwards): 3 tagged DAZ items + 1
untagged `FACE_X1` on the same track. The 3 went; the sentinel survived with the **same unique
id** `3c56a31a-…`, the same frames, and every other track item-for-item identical.

**Refusals, live:** both legacy previews → `legacy preview: no verifiable DAZ ownership
metadata`; `DAZ_INPUT` and `DAZ_OUTPUT_MVP` → protected + not-a-preview. All exit 4, all before
any mutation.

Full output: `.agent/reports/phase-07-live-workflow-report.txt` and
`.agent/reports/phase-07-ownership-probe-report.txt`.

The commands, for reruns:

```bash
.venv/bin/python -m davinci_auto_zoom probe-ownership \
  --confirm-ownership-probe \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT --reference-timeline DAZ_OUTPUT_MVP \
  --config config.example.toml
```

```bash
.venv/bin/python -m davinci_auto_zoom rebuild-preview \
  --confirm-rebuild-owned-preview --confirm-resolve-render-test \
  --project davinci-auto-zoom-test \
  --source-timeline DAZ_INPUT --reference-timeline DAZ_OUTPUT_MVP \
  --preview-timeline DAZ_AUTO_PREVIEW_20260817_163637_e8787ece \
  --config config.example.toml
```

## Bug found live

**`DeleteClips` silently no-ops on a non-current timeline (D042).** Symptom: the first live
`clean-preview` reported `DeleteClips returned False`, removed 0 of 28 items, kept its recovery
and restored the active timeline. Cause: like `AppendToTimeline`, `Timeline.DeleteClips` only
acts on the current timeline — undocumented in the installed README. Fix: make the preview
current after the recovery exists and before the delete, and verify by re-reading
`GetCurrentTimeline().GetUniqueId()`. Regression test:
`test_the_preview_is_made_current_before_the_delete`, which fails against the old code because
`tests/fake_resolve.py` now reproduces the restriction.

The safety model behaved correctly throughout: an undocumented API restriction produced a
clean refusal with a retained recovery, not a partial mutation.

## Agent documentation is now tracked

`.gitignore` previously excluded `.agent/`, `AGENTS.md` and `CLAUDE.md` while the README
claimed the opposite. Fixed: they are in the index. `AGENTS.md` is the single source of the
cross-agent rules; `CLAUDE.md` imports it with `@AGENTS.md` and keeps only Claude-Code
specifics. The ignore rules are narrowed to genuinely local material (session state, caches,
credentials). Docs were scanned for tokens/keys/credentials before the first commit; the only
absolute paths are `/opt/resolve/...` and the measured Media Storage volumes, which are the
technical finding itself.

**From now on: docs and reports are committed with the code they describe.**

## Remaining unknowns

Carried forward: pixel confirmation of the Fusion effect on the Phase 7 preview (structural
correctness is proven; how it *looks* is not — though it is the same plan as the visually
validated Phase 6 one); the rendered voice audio has never been listened to; no hand-labelled
speech reference; the fingerprint cannot see Fairlight/OFX changes that move no clip; the 120 ms
lookback is calibrated on one timeline; **x1 over-triggering is still there** (14 planned vs 12
manual, untouched on purpose).

New after Phase 7:

- **collision behaviour is still unmeasured** (D032). Phase 7 works entirely on a dedicated
  track it verified empty or emptied itself, so it never needed to know. In-place apply will;
- marker capacity is untested at scale — 28 items × 1 marker is fine, but nothing establishes
  an upper bound on `customData` length or marker count per item;
- `DeleteClips` was only ever called with one batch of ≤28 items. Batching limits unknown;
- whether a *third* write-capable call shares the "must be current" precondition (D042). Assume
  yes until measured.

Resolved by this phase: how a DAZ clip is identified later; what a second run does with an
existing preview; whether repeated runs stack duplicates (they do not).

## Next task — Phase 7b (apply in place on a user timeline)

Ownership now exists, which is the precondition D032 was waiting for. **Do not start by
weakening a Phase 7 refusal.** In order:

1. decide what a user timeline must look like before DAZ may write into it — is a
   verified-empty dedicated track still required, or is "a track holding only provably owned
   items" enough now that the second is provable?
2. the recovery model: `DAZ_RECOVERY_*` (D041) was designed for a preview, not for a timeline
   the user is actively editing. Decide whether it is sufficient before writing any code;
3. measure collision behaviour on a scratch, or keep refusing to depend on it.

If the user raises x1 over-triggering instead, treat it as its own measured phase — start from
the burst/zoom-in offsets against `DAZ_OUTPUT_MVP`, and measure both directions, as Phase 6
should have from the start.

## Update protocol

After each meaningful session, replace this file with: working-tree state, exact Resolve
version, what changed, commands/tests run and results, manual Resolve checks, decisions
(also append durable ones to `DECISIONS.md`), blockers, and the exact next task. Commit it
with the code it describes.
