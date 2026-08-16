# Phase 1 prompt — Resolve capability discovery (READ ONLY)

You are implementing **Phase 1** of `davinci-auto-zoom` in the repository you are currently inside.

## Objective

Turn the current scaffold into a reliable, **read-only capability discovery + timeline snapshot tool** for the exact DaVinci Resolve version installed on this machine.

This phase exists to eliminate API guesses before we implement any timeline mutation.

## Read first

Before editing anything, read:

- `AGENTS.md` or `CLAUDE.md` (whichever your agent uses)
- `.agent/PROJECT_CONTEXT.md`
- `.agent/IMPLEMENTATION_PLAN.md`
- `.agent/DECISIONS.md`
- `.agent/HANDOFF.md`
- the current source/tests

Then locate and read the **Blackmagic Design Developer/Scripting documentation installed with this exact Resolve build**. On Linux it may be under `/opt/resolve/Developer/`, but search rather than assuming. Record the exact documentation path and Resolve version in `.agent/HANDOFF.md`.

The installed Blackmagic documentation is canonical. Do not invent method names from memory and do not implement undocumented behavior merely because an online example suggests it.

## Non-negotiable safety constraint

**READ ONLY. Do not create, append, move, rename, delete, trim, enable/disable, render, mark, or otherwise mutate any real project/timeline/media-pool object in this phase.**

Even if the API documents a write method, only report its presence/signature for the capability matrix. Do not call it.

## Work to perform

### 1. Improve Resolve bootstrap/doctor

Make the bootstrap robust for the current OS while keeping reasonable Windows/macOS candidates.

- Verify module/API/library paths against the installed Blackmagic docs.
- Give actionable errors when Resolve is closed, scripting is unavailable, or the module/native library cannot load.
- Do not vendor Blackmagic modules.

### 2. Build a documented capability matrix

Create typed internal structures and a CLI-visible report for the exact installed API. Determine, from the installed docs, whether supported methods exist for these capabilities and record the exact documented method/signature or `unsupported/not found`:

- current project and current timeline
- Resolve/version information
- video/audio track counts and names
- track enable state query
- timeline item enumeration by track
- timeline item start/end/duration and source/media-pool relationship
- recursive Media Pool bin/sub-bin enumeration
- Media Pool item name/type/properties
- appending/adding a Media Pool item at a specified timeline record frame and track **(report only; never call)**
- deletion/removal of timeline items **(report only; never call)**
- timeline duplication/deletion **(report only; never call)**
- render APIs relevant to producing an audio-only temporary render **(report only; never call)**
- item/timeline markers, names, colors, metadata, or other supported ownership tags **(report only; never mutate)**
- transcription/audio-transcription APIs, especially any supported way to retrieve transcript text/timestamps

Do not use Python `dir()` output alone as proof that a Resolve method is supported; the installed docs are the source of truth.

### 3. Implement a normalized read-only timeline snapshot

Add a command such as:

```bash
davinci-auto-zoom snapshot --json
```

It should serialize plain Python data only, never opaque Resolve proxy objects.

Capture at minimum when supported:

- Resolve version
- project name
- timeline name
- timeline frame rate / relevant timing settings
- timeline start/end frame if available
- every video/audio track: index, name, type, enabled state if queryable
- every timeline item: track, name, start/end/duration, media-pool item name/type/id-like stable field if supported
- useful edit boundaries derived read-only from video-track items

Keep the model general enough for later cut-aware planning.

### 4. Implement recursive asset discovery

Using the configured/default names from `config.example.toml`, inspect the Media Pool recursively and report:

- whether bin `DAVINCI_AUTO_ZOOM` exists
- all items in it
- exact matches for `DAZ_FACE_X1` and `DAZ_RESET_X0`
- available item properties/type information that might tell us whether these reusable zoom assets can later be inserted by the scripting API
- duplicates/ambiguity clearly

Do not change or import anything.

If the real project uses different names, do not silently hardcode what you happen to find. Keep names configurable and report useful candidates.

### 5. Preserve architecture boundaries

- Raw Resolve objects stay in `src/davinci_auto_zoom/resolve/`.
- Add domain/snapshot dataclasses that contain plain values only.
- `domain/planner.py` must remain independent of Resolve.
- Do not add GUI, Whisper, VAD, ffmpeg integration, or write execution yet.
- Avoid heavyweight dependencies unless clearly necessary for this read-only milestone.

### 6. Tests and fixtures

Add tests for all pure normalization/serialization logic without requiring Resolve.

If useful, create a small fake Resolve object graph in tests to exercise adapter code. Do not make normal `pytest` require Resolve to be open.

## Required verification

Run and fix:

```bash
pytest
ruff check .
mypy
```

Then, with Resolve open, run the new doctor/capability/snapshot command(s) against the current project **read-only** and save a sanitized example report under `.agent/` if it helps future agents. Do not place private project data in tracked files.

## Documentation updates before finishing

Update:

- `.agent/HANDOFF.md` with exact state, Resolve version, installed docs path, commands run/results, capabilities found, asset-discovery result, blockers, and the recommended Phase 2 next task.
- `.agent/DECISIONS.md` for any durable architecture/API decisions.
- `.agent/IMPLEMENTATION_PLAN.md` only if actual evidence requires changing the plan.

Do not remove the gitignore rules for agent-local documentation.

## Completion criteria

Do not stop at "I added the code." This phase is complete only when:

1. the installed Blackmagic scripting docs have been located and used as authority;
2. the capability matrix is based on that documentation;
3. a normalized read-only snapshot works against the current open Resolve timeline;
4. recursive bin/asset discovery works or produces a precise evidence-backed blocker;
5. no real Resolve object was mutated;
6. tests/lint/typecheck pass, or any unavoidable exception is narrow and documented;
7. `.agent/HANDOFF.md` accurately tells the next agent/supervisor what happened.

At the end, give a concise report containing: files changed, verified Resolve version/docs path, capability highlights, zoom-asset findings, verification results, and remaining blockers. Do not begin Phase 2 or any write implementation.
