# davinci-auto-zoom

Experimental automation tool for **DaVinci Resolve** that turns speech activity on a dedicated voice track into deterministic zoom-edit decisions, then applies prebuilt Resolve assets from a specially named Media Pool bin.

> Status: capability discovery complete, asset reuse **proven**, speech detection **proven**,
> the deterministic planner **working end to end**, and — new — a plan can now be **applied
> for real**, on a preview timeline the tool creates itself. Confirmed against DaVinci Resolve
> Studio 21.0.4.5: 28 planned zooms became 28 frame-exact clips on a new preview timeline,
> with the source timelines, the active timeline and the Deliver page left untouched.
> **No command modifies an existing timeline of yours.** The day-to-day commands are strictly
> read-only; the development probes make temporary, opt-in changes and clean up after
> themselves; and `apply-preview` places the planned zooms on a **new** `DAZ_AUTO_PREVIEW_*`
> duplicate, which it keeps on success so you can watch it, and deletes again if anything
> goes wrong. Applying to a timeline you already work in is not implemented yet.

## MVP target

For dynamic gaming edits:

1. Identify a dedicated voice track containing only the creator's voice.
2. Detect speech regions on that track.
3. When speech begins, plan a `facecam x1` zoom event.
4. When speech has stopped for a configurable amount of time, plan a smooth reset to `x0`.
5. Prefer a nearby real cut for the reset when that produces a cleaner return; otherwise use the direct speech-derived timing.
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
anchored to the clip's first frame and are **never rescaled** to its length. Three different
lengths are involved, and only two of them matter:

| Length | Example | Used for planning |
| --- | --- | --- |
| the asset's length in the Media Pool | `FACE_X0_SMOOTH` is 42 frames | **no, never** |
| the **animation** length you configure | its move finishes in 15 frames | yes |
| the instance the tool places | 15 frames for a reset; anything for a zoom-in | computed |

So a zoom-in asset animates for its 15 frames and then simply **holds** the zoom: its
instances are as long as the speech needs — 30 frames, 250, more — and the animation length is
only the floor below which the move would be cut off mid-way. A reset asset finishes its
return in 15 frames, so a 15-frame instance does the whole job even though the clip in your
bin is 42 frames long.

That is why you tell the tool the animation lengths, in
[`config.example.toml`](config.example.toml):

```toml
[assets.transition_frames]
facecam_x1 = 15
reset_x0 = 15
```

In frames, because that is how you keyframed them, and with no default: they are properties of
*your* assets, and the tool will not guess them or read your Fusion graph to find out.

## Speech strategy

The MVP needs *speech activity*, not the words themselves. The chain is now implemented and
proven end to end on the verified build:

```
configured Resolve voice track  ->  isolated temporary audio render
  ->  16 kHz mono PCM (ffmpeg)  ->  Silero VAD (ONNX, CPU)
  ->  speech segments in absolute Resolve timeline frames
```

The voice track is rendered rather than reconstructed from source files, because only
Resolve can produce what Resolve actually plays: cuts, trims, fades, levels and any clip or
track treatment are all preserved. Isolation happens on a throwaway duplicate of your
timeline, never on the timeline itself.

Detection uses **Silero VAD v6.2.1** through ONNX Runtime on CPU. The model is vendored,
version-pinned and checksum-verified — see
[`speech/models/PROVENANCE.md`](src/davinci_auto_zoom/speech/models/PROVENANCE.md) for its
origin, licence and inference contract. There is no PyTorch dependency and nothing is
downloaded at runtime.

An important boundary, deliberately enforced: the detector answers **"was the creator
speaking here?"** and nothing else. It does not decide when to zoom. A 650 ms pause stays
two speech segments; whether that pause is worth zooming out for is an editing decision, and
it belongs to the planner. That is why technical VAD tuning lives under `[speech.vad]` and
editorial timing lives under `[planner]`.

## How the plan is built

The planner is pure: no Resolve object, no model, no clock, no filesystem. The same inputs
always produce the same plan.

```
timeline range + fps + speech segments + hard cuts + your settings + your asset timing
   ->  FACE_X1 / FACE_X0 placements, each with a reason
```

The rules, in the order they apply:

1. **Speech regions become editorial bursts.** Two speech regions separated by less than
   `reset_after_silence_ms` belong to the same burst, and the zoom simply stays up across the
   pause — popping out and back in for a breath looks worse than holding. This setting is a
   *gate*, not a delay: the tool works offline and already knows how long every pause lasts,
   so it never plans a reset 650 ms after you stopped talking.
2. **One zoom-in per burst**, starting at the burst (plus an optional lead-in, 0 by default)
   and lasting until the reset. It is dropped entirely if it would be shorter than its own
   animation, because a truncated move is worse than no zoom.
3. **The reset is placed at the end of the burst** — and may be pushed forward to the *last*
   hard cut within `cut_snap_window_ms`, when returning to normal framing on a real cut reads
   as intentional. A "hard cut" means one clip ends exactly where the next begins on
   `cut_reference_video_track`; entering from black or running out into a gap is not a cut.
4. **A reset is only placed if it fits.** The whole reset animation must finish before the
   next zoom starts, or before the timeline ends. A cut that leaves too little room is
   rejected in favour of an earlier one; if nothing fits, no reset is placed and the zoom is
   held. Every one of those decisions is printed.

The result is a table of placements with absolute frames, plus the full decision trace, so you
can see *why* each zoom is where it is before anything is applied.

Whisper is explicitly **not** part of the MVP: knowing *which words* were said buys nothing
for "is there a voice here". It stays a candidate for later, semantic rules (spotting a
punchline, an exclamation) where the text actually matters.

A Resolve-transcript provider is not possible: on the verified build, the scripting API
exposes transcription only as write actions and offers **no way to read transcript text or
timestamps back**.

## Requirements

- DaVinci Resolve / Resolve Studio with scripting enabled, and running.
  Verified against **Resolve Studio 21.0.4.5**.
- Python 3.11+ for the external application.
- The Developer documentation installed with Resolve. The capability report parses it
  directly as the source of truth, so it always describes *your* build. On Linux it is at
  `/opt/resolve/Developer/Scripting/README.txt`; elsewhere set `RESOLVE_SCRIPT_API` and
  `RESOLVE_SCRIPT_LIB` as that README describes.
- **`ffmpeg` on `PATH`**, for speech analysis. It is used only to convert the rendered voice
  track to 16 kHz mono PCM — never to detect speech. A missing `ffmpeg` produces an
  actionable message, not a traceback.
- At least one writable **Media Storage** location in
  `Preferences > System > Media Storage`. Resolve refuses to render anywhere outside it, so
  the temporary voice render is written to a uniquely named directory there and deleted
  again immediately.

Python dependencies are `numpy` and `onnxruntime` (CPU). There is no PyTorch, no
torchaudio and no GPU requirement.

The repository does not vendor Blackmagic Design's scripting modules or documentation. It
*does* vendor the Silero VAD ONNX model, which is MIT-licensed and redistributed with its
licence and provenance — see
[`speech/models/PROVENANCE.md`](src/davinci_auto_zoom/speech/models/PROVENANCE.md).

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
| `speech-file AUDIO` | Speech detection on a local audio file, with **no Resolve session at all**. Takes `--fps` and `--start-frame` so the segments come out in your timeline's coordinates. |

Then, in increasing order of what they touch:

| Command | What it changes |
| --- | --- |
| `probe-write`, `speech-probe` | **Temporary, opt-in** development probes. Each works on a scratch timeline it creates and deletes, restores everything it touched, and needs its own confirmation flag. Neither leaves anything behind. |
| `plan-probe` | The full dry run. Uses the same temporary render as `speech-probe`; **never inserts a zoom**. |
| `apply-preview` | **Write-capable, and deliberately persistent.** Places the planned zooms on a **new** `DAZ_AUTO_PREVIEW_*` timeline duplicated from your source, and keeps it on success so you can inspect it. Your own timelines are never modified. |

There is still **no production command that edits a timeline you already work in.** Applying
in place, recognising and replacing DAZ's own earlier zooms, `clean` and `rebuild` are not
implemented.

Every command fails gracefully with an actionable message when Resolve is closed or the
scripting module cannot be found, and exits non-zero rather than raising.

`doctor` classifies each capability as `confirmed` (documented and exercised read-only),
`documented-but-not-runtime-verified` (a write method, reported but never called),
`documented`, `likely`, or `unsupported`. Documentation and runtime evidence are never
conflated.

### `probe-write` — development only

An integration spike used to prove asset reuse, not the executor, and built to be impossible
to trigger by accident:

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

### `speech-probe` — development only

Like `probe-write`, impossible to trigger by accident. It needs its own distinct flag, so
neither probe can be started by muscle memory for the other:

```bash
python -m davinci_auto_zoom speech-probe \
  --confirm-resolve-render-test \
  --project MY_PROJECT \
  --source-timeline MY_INPUT_TIMELINE \
  --reference-timeline MY_REFERENCE_TIMELINE   # optional, diagnostics only
```

What it does, and undoes:

- **Your timelines are never modified.** It duplicates the source timeline into a uniquely
  named `DAZ_AUDIO_SCRATCH_*` timeline and does everything there.
- On that duplicate it **deletes** every audio track except the configured voice one, then
  verifies the survivor by name and item count. (Disabling tracks was tried first and
  abandoned: on the verified build `SetTrackEnable` reports success while
  `GetIsTrackEnabled` keeps saying the track is on, so isolation could not be *proven* — and
  unprovable isolation means silently analysing the wrong audio.)
- It creates **exactly one** render job. Jobs already in your queue are recorded and never
  touched; `DeleteAllRenderJobs` is deliberately never called.
- Your Deliver page settings are snapshotted into a temporary render preset first, restored
  afterwards, and the temporary preset is deleted. Anything that does not come back is
  **reported as unrestored**, not glossed over.
- The temporary render lands in a uniquely named directory inside your Media Storage, is
  moved out immediately, and the directory is deleted.
- All of the above unwinds in a `finally` block, so an error partway through still restores
  the timeline you had open and removes what the run created.
- Finally it re-reads the protected timelines and reports any structural difference.

Add `--keep-temp-audio` to keep the rendered and normalized audio for inspection; it then
prints exactly where they are. Without it, nothing survives the run.

The rendered audio's duration is checked against the timeline range it came from, and a
mismatch beyond two frames **fails the probe** — a trimmed or padded render would silently
offset every speech frame downstream.

### `plan-probe` — the full dry run

The whole chain, ending in a plan you can read:

```bash
python -m davinci_auto_zoom plan-probe \
  --confirm-resolve-render-test \
  --project MY_PROJECT \
  --source-timeline MY_INPUT_TIMELINE \
  --reference-timeline MY_REFERENCE_TIMELINE \   # optional, diagnostics only
  --config config.toml
```

```
snapshot -> isolated voice render -> Silero VAD -> hard cuts -> planner -> plan
```

It reuses `speech-probe`'s temporary render, with the same confirmation flag and the same
cleanup, and then plans. **It never inserts a zoom**: no `AppendToTimeline` call exists on
this path, and a plan whose placements overlap fails the run rather than being reported as a
result. Output is a placement table (`role | start | end | frames | reason | cut`), the
diagnostics, and the decision trace; `--json` gives the same thing machine-readably, plus the
`source` signature a future executor will check a plan against.

It needs `[assets.transition_frames]` in your config and says so if it is missing — the tool
will not guess how long your assets take to animate.

With `--reference-timeline` it also prints a qualitative comparison against your hand-made
timeline: how many planned zooms match a manual one, how many do not, and the start/reset
offsets. That is a diagnostic, never a score — the planner is deliberately **not** tuned to
reproduce a human edit, and a human correction pass is part of how the tool is meant to work.

### `apply-preview` — the zooms, for real, on a timeline of its own

The first command that actually places zooms. It runs the whole `plan-probe` chain and then
applies the plan — to a **new** timeline, never to yours:

```bash
python -m davinci_auto_zoom apply-preview \
  --confirm-create-preview-timeline \
  --confirm-resolve-render-test \
  --project MY_PROJECT \
  --source-timeline MY_INPUT_TIMELINE \
  --reference-timeline MY_REFERENCE_TIMELINE \   # optional, diagnostics only
  --config config.toml
```

```
plan -> re-validate the plan against Resolve -> duplicate the source
  -> empty dedicated zoom track -> place every planned asset -> verify -> keep the preview
```

Two flags are required, not one: the usual render flag for the temporary voice render, and
`--confirm-create-preview-timeline` for the write itself. Without the second one the command
refuses before Resolve is even contacted.

What it guarantees:

- **Your timelines are never modified.** Everything happens on a duplicate named
  `DAZ_AUTO_PREVIEW_<timestamp>_<id>`, created by this run.
- **The plan is re-checked against reality first.** Immediately before writing, the tool
  re-reads the project and compares it with what the plan was built from: project, timeline,
  its unique id, the frame range, the frame rate, the three configured tracks, the asset
  names, the Media Pool items those names resolve to, the animation lengths, every planner
  setting — plus a **structural fingerprint** of the voice track and the cut-reference track.
  Re-cut your V1 or move a clip on your voice track after planning and the run refuses, even
  though every name and duration still matches. Nothing is created when it refuses.
- **It writes only to an empty, dedicated zoom track.** Missing video tracks are added until
  the configured index exists (V3 by default). A track that already holds *anything* — your
  clips, or a previous run's zooms — is a refusal. Nothing is ever overwritten, shifted or
  deleted, and no other track is touched.
- **Every insertion is verified as it is made**, then the finished track is compared with the
  whole plan: same count, same order, same frames, no extras, no overlaps, and the user's
  Fusion composition present on each created instance.
- **The preview is the transaction.** If anything at all fails, the timeline you had open is
  restored and *that* preview is deleted and confirmed gone. A preview from an earlier run is
  never reused, overwritten or deleted.
- **The timeline you had open is put back, and that is checked, not assumed.** The tool
  records its name and unique id before it starts, and afterwards re-reads what Resolve
  actually has open and compares. If it cannot prove the restore worked, the run fails and
  says so — and in that one case it deliberately **does not** delete the preview, because the
  preview may still be the timeline Resolve has open and deleting it would not be safe.
- **Your originals are re-checked at the end.** After the writes, the tool re-reads the source
  and reference timelines and your assets and compares them with how they looked before. Any
  difference fails the run and deletes the preview it made. Keeping the preview is the last
  decision, taken only once everything else has passed.
- **On success the preview is kept**, on purpose — it is the thing you open and watch. The
  report ends with the preview's exact name and unique id.
- An empty plan creates no timeline at all and reports "nothing to apply".
- `SaveProject()` is never called.

What it proves, and what it does not: the result is **structurally and rhythmically exactly
the plan**, placed with the assets you built. Whether the edit *looks* good is a human
judgement the API cannot make — open the preview and watch it.

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
track is named `Audio N` and shares one source clip. `voice_audio_track = 1` means A1.

### Detection settings vs editing settings

These are two different questions and the config keeps them apart on purpose:

| Block | Question it answers | Example |
| --- | --- | --- |
| `[speech.vad]` | *Was the creator making speech sounds here?* | `min_silence_ms = 100` — how long the voice must stop before an utterance has really ended |
| `[planner]` | *What should the edit do about it?* | `reset_after_silence_ms = 650` — how long a silence must last to be worth zooming back out for |
| `[assets.transition_frames]` | *How long do your assets take to animate?* | `facecam_x1 = 15` — in frames, no default, never guessed |

The `[speech.vad]` values are Silero's own defaults. Change them when the detector is
visibly wrong about the *audio*; change `[planner]` when you disagree with the *edit*. The
older config had a single `[speech]` block where `min_silence_ms = 650` made an editing
preference look like a property of the detector — that is the ambiguity this split removes.

Two `[planner]` keys from the early scaffold are gone rather than re-tuned:

- **`min_zoom_ms = 450`** — an editorial guess nobody measured. The only real minimum for a
  zoom-in is its own animation length, which now comes from `[assets.transition_frames]`.
- **`zoom_lead_in_ms = 80` / `zoom_lead_out_ms = 120`** — also guesses. The baseline is now
  `0` / `0`: the zoom starts on the burst and the reset is placed at its end. Both keys still
  exist if you want anticipation.

## Development safety rules

- Dry-run first.
- Read-only commands may warn about a project/timeline mismatch; write-capable ones must
  refuse to start and require an explicit opt-in flag.
- Timeline writes only ever target a timeline created by the run itself. The probes delete
  theirs unconditionally; `apply-preview` deletes its preview on failure and keeps it on
  success, and either way the `finally` block restores the timeline you had open.
- A plan is re-validated against a fresh snapshot, in full, before the first write.
- Zooms go on a dedicated track that has been verified empty. Nothing is overwritten.
- Never depend on undocumented API behavior without recording the evidence/version in the local agent notes.
- Keep frame math in integer timeline frames; convert milliseconds only at configuration boundaries.
- Keep raw Resolve proxy objects inside the `resolve` package.
- Pure planning code must be unit-testable without Resolve installed.

## Agent-local documentation

This repo ships with local agent guidance (`AGENTS.md`, `CLAUDE.md`, `.agent/`). Those files
are **tracked in Git and must be committed** alongside the code they describe: the roadmap,
the decision log and the handoff notes are only useful if their history matches the history
of the source. Only local tool configuration directories stay ignored.
