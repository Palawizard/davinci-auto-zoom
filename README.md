# davinci-auto-zoom

Experimental automation tool for **DaVinci Resolve** that turns speech activity on a dedicated voice track into deterministic zoom-edit decisions, then applies prebuilt Resolve assets from a specially named Media Pool bin.

> Status: capability discovery complete, and asset reuse is now **proven** on the verified
> build. Every day-to-day command remains **strictly read-only**; the single write-capable
> command is a development spike that refuses to run without an explicit confirmation flag.

## MVP target

For dynamic gaming edits:

1. Identify a dedicated voice track containing only the creator's voice.
2. Detect speech regions on that track.
3. When speech begins, plan a `facecam x1` zoom event.
4. When speech has stopped for a configurable amount of time, plan a smooth reset to `x0`.
5. Prefer a nearby eligible edit boundary for the reset when that produces a cleaner cut; otherwise use the direct speech-derived timing.
6. Resolve the required prebuilt zoom assets by **bin name + clip name**, never by fragile timeline position.
7. Preview the complete plan before any timeline mutation.

Automation is a first pass, not a replacement for the editor. A human review/correction pass
after the tool runs is expected: the goal is to remove the repetitive, obvious decisions.

## Why this architecture

The project deliberately separates:

- **Resolve integration** — reading projects, timelines, tracks, bins, clips, and later applying edits.
- **Speech analysis** — producing normalized speech intervals; implementation can change without touching editing logic.
- **Planning** — pure deterministic code turning timeline facts + speech intervals into zoom actions.
- **Execution** — the only layer allowed to mutate Resolve.

This makes future states such as facecam x2/x3, gameplay zooms, and transitions between any two zoom states an extension of the planner/state model instead of a rewrite.

## User-supplied zoom assets

The tool does **not** build zoom effects. You create and configure them yourself, as
reusable assets in a dedicated Media Pool bin, using whatever combination of Transform,
keyframes, Fusion, OFX or plugins suits your edit. davinci-auto-zoom looks them up by name,
places instances of them, and never inspects or rebuilds their contents. That is what lets
it work across different editing styles, and what will let facecam x2/x3 and gameplay zooms
be added later without touching the engine.

This is now verified rather than aspirational. On Resolve Studio 21.0.4.5, placing a new
instance of a user's asset preserves its Fusion composition exactly — same tools, same
wiring, same keyframe values and Bezier handles as the asset the user built — at any
requested duration, including durations longer than the asset's own native length. The
proof is a structural comparison of exported `.comp` files against a hand-made reference
instance, not merely "a composition exists".

One consequence worth knowing when you build your assets: an instance's keyframes stay
anchored to the clip's first frame and are **never rescaled** to its length. A zoom whose
move takes 15 frames will simply be cut off mid-move if the tool ever places a 10-frame
instance, so the planner's minimum zoom duration has to respect your asset's own animation
length.

## Speech strategy

The MVP needs *speech activity*, not the words themselves, so the plan is an isolated
voice-track render plus configurable audio/VAD analysis.

A Resolve-transcript provider is not possible: on the verified build, the scripting API
exposes transcription only as write actions and offers **no way to read transcript text or
timestamps back**. External transcription (for example Whisper) remains an optional future
provider for semantic rules.

## Requirements

- DaVinci Resolve / Resolve Studio with scripting enabled, and running.
  Verified against **Resolve Studio 21.0.4.5**.
- Python 3.11+ for the external application.
- The Developer documentation installed with Resolve. The capability report parses it
  directly as the source of truth, so it always describes *your* build. On Linux it is at
  `/opt/resolve/Developer/Scripting/README.txt`; elsewhere set `RESOLVE_SCRIPT_API` and
  `RESOLVE_SCRIPT_LIB` as that README describes.
- `ffmpeg` will be required only once the automatic audio-analysis milestone is implemented.

The repository does not vendor Blackmagic Design's scripting modules or documentation.

## Quick start — current scaffold

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\\Scripts\\activate      # Windows
python -m pip install -e '.[dev]'

python -m davinci_auto_zoom doctor
pytest
```

Available read-only commands (all accept `--json` and `--config`):

| Command | What it does |
| --- | --- |
| `doctor` | Environment, connectivity, and a capability matrix built by cross-checking the installed Blackmagic documentation against the live API surface. |
| `snapshot` | Normalized snapshot of the open project: timelines, tracks, clips, frames, derived edit boundaries, and asset-bin discovery. |
| `assets` | Just the asset bin: which prebuilt zoom assets were found and which configured role each fills. |
| `compare A B` | Structural diff of two timelines: which items exist only in B, their durations, and how their boundaries relate to existing cuts. |

Every command fails gracefully with an actionable message when Resolve is closed or the
scripting module cannot be found, and exits non-zero rather than raising.

`doctor` classifies each capability as `confirmed` (documented and exercised read-only),
`documented-but-not-runtime-verified` (a write method, reported but never called),
`documented`, `likely`, or `unsupported`. Documentation and runtime evidence are never
conflated.

### `probe-write` — development only

There is one write-capable command. It is an integration spike used to prove asset reuse,
not the future executor, and it is built to be impossible to trigger by accident:

```bash
python -m davinci_auto_zoom probe-write \
  --confirm-resolve-write-test \
  --project MY_PROJECT \
  --source-timeline MY_INPUT_TIMELINE \
  --reference-timeline MY_REFERENCE_TIMELINE
```

Without `--confirm-resolve-write-test` it refuses and changes nothing. The expected project
and timeline names are required arguments rather than config values, and a mismatch is a
hard refusal, not a warning. It then duplicates the source timeline into a uniquely named
`DAZ_SCRATCH_*` timeline, adds an empty video track, performs every insertion there,
restores the timeline you had open, deletes the scratch timeline it created — and only that
one — and finally re-reads both original timelines to verify nothing changed. If it cannot
delete its own scratch timeline it says so by name and leaves everything else alone.

## Configuration

Copy the example file before later milestones:

```bash
cp config.example.toml config.toml
```

Names are explicit and fully configurable, since they differ per editor:

- bin: `DAVINCI_AUTO_ZOOM`
- facecam x1 asset: `FACE_X1`
- reset x0 asset: `FACE_X0_SMOOTH`
- the voice audio track, as a 1-based index

The voice track must be configured rather than detected: Resolve exposes no metadata
identifying which audio track holds the creator's voice, and in a typical project every
track is named `Audio N` and shares one source clip.

## Development safety rules

- Dry-run first.
- Read-only commands may warn about a project/timeline mismatch; write-capable ones must
  refuse to start and require an explicit opt-in flag.
- Timeline writes only ever target a throwaway timeline created by the run itself, and are
  cleaned up in a `finally` block.
- Never depend on undocumented API behavior without recording the evidence/version in the local agent notes.
- Keep frame math in integer timeline frames; convert milliseconds only at configuration boundaries.
- Keep raw Resolve proxy objects inside the `resolve` package.
- Pure planning code must be unit-testable without Resolve installed.

## Agent-local documentation

This repo ships with local agent guidance (`AGENTS.md`, `CLAUDE.md`, `.agent/`). Those files are intentionally ignored by Git so they can be maintained by coding agents without being published to the public repository.
