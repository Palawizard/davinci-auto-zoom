# davinci-auto-zoom

A DaVinci Resolve tool that automates **facecam zooms** from the creator's own voice, using
the zoom assets you already built. It listens to a dedicated voice track, decides where the
framing should tighten and where it should return, and places *your* Media Pool assets on a
preview timeline it creates itself.

It never touches a timeline you are working in.

## What it does

```
you start talking                      ->  zoom in to facecam x1
your voice dips and picks back up      ->  tighten to x2
it dips and picks back up again        ->  tighten to x3
you stop talking long enough           ->  return to normal framing, with the asset
                                           that matches the level you were on
```

Plus two things that make it look deliberate rather than mechanical:

- **every transition can land on a nearby real cut** in your edit, when there is one close to
  the moment the voice asked for. A cut never *creates* a zoom — it only adjusts where an
  already-decided one lands;
- **a pause too short to be worth zooming out for keeps the zoom up.** Popping out and back in
  for a breath looks worse than holding.

Everything is a **preview**: the tool duplicates your timeline and puts the zooms on the copy,
so you watch the result and keep, rework or throw it away.

Automation is a first pass, not a replacement for the editor. A human correction pass after
the tool runs is expected — the goal is to remove the repetitive, obvious decisions.

## What it does NOT do

- **no gameplay zoom automation.** See *Non-goals* below;
- **no semantic understanding.** It does not know what you said, only that you were speaking
  and how loudly;
- **no transcript required**, and none is used;
- **no effect generation.** It never builds, edits or inspects a zoom effect — it places
  instances of the assets you made;
- **no apply-in-place.** There is no command that adds zooms to a timeline you already work
  in. Only the preview workflow is supported.

## Requirements

**Known supported and tested:**

- **DaVinci Resolve Studio 21.0.4.5 on Linux**, with scripting enabled and the application
  running. This is the only configuration the tool has been verified against.
- **Python 3.11+** (developed and tested on 3.14).
- **`ffmpeg` on `PATH`.** Used only to convert the rendered voice track to 16 kHz mono PCM,
  never to detect speech. A missing `ffmpeg` produces an actionable message, not a traceback.
- **The Developer documentation installed with Resolve.** The capability report parses it
  directly, so it always describes *your* build. On Linux it is at
  `/opt/resolve/Developer/Scripting/README.txt`.
- **At least one writable Media Storage location** in `Preferences > System > Media Storage`.
  Resolve refuses to render anywhere outside it, so the temporary voice render is written to a
  uniquely named directory there and deleted again immediately.
- **A dedicated audio track holding only your voice**, and you have to tell the tool which one
  it is. Resolve exposes no metadata identifying it, and in a typical project every track is
  named `Audio N` and shares one source clip.

**May work but unverified:** Windows and macOS, other Resolve versions, and the non-Studio
edition. Nothing in the code is Linux-specific, but nothing has been tested elsewhere either —
treat those as unknown, not as supported.

Python dependencies are `numpy` and `onnxruntime` (CPU). There is no PyTorch, no torchaudio
and no GPU requirement. Nothing is downloaded at runtime.

The repository does not vendor Blackmagic Design's scripting modules or documentation. It
*does* vendor the Silero VAD ONNX model, which is MIT-licensed and redistributed with its
licence and provenance — see
[`speech/models/PROVENANCE.md`](src/davinci_auto_zoom/speech/models/PROVENANCE.md).

## Your zoom assets

The tool does **not** build zoom effects. You create them yourself, as reusable assets in a
dedicated Media Pool bin, using whatever combination of Transform, keyframes, Fusion, OFX or
plugins suits your edit. davinci-auto-zoom looks them up by name, places instances, and never
inspects or rebuilds their contents. That is what lets it work across editing styles.

There are six **transition roles** — the moves, not the levels:

| Role | Move | Required? | This project's clip |
| --- | --- | --- | --- |
| `x0_to_face_x1` | normal framing -> facecam x1 | **yes** | `FACE_X1` |
| `face_x1_to_x0` | facecam x1 -> normal framing | **yes** | `X1_TO_X0` |
| `face_x1_to_face_x2` | x1 -> x2 | optional | `FACE_X2` |
| `face_x2_to_x0` | x2 -> normal framing | optional | `X2_TO_X0` |
| `face_x2_to_face_x3` | x2 -> x3 | optional | `FACE_X3` |
| `face_x3_to_x0` | x3 -> normal framing | optional | `X3_TO_X0` |

**x2 and x3 are optional.** Configure only `x0_to_face_x1` and `face_x1_to_x0` and you get a
complete, supported one-level edit: it zooms in when you speak and returns when you stop, and
never promotes. Add a promotion role and its matching reset to unlock the next rung.

The ladder is climbed one rung at a time and never descended: there is no `x0 -> x2` and no
`x2 -> x1`. The way out of any level is all the way out.

### The one thing to know when you build them

An instance's keyframes stay anchored to the clip's first frame and are **never rescaled** to
its length. Three different lengths are involved, and only two of them matter:

| Length | Example | Used for planning |
| --- | --- | --- |
| the asset's length in the Media Pool | `X1_TO_X0` is 42 frames | **no, never** |
| the **animation** length you configure | its move finishes in 15 frames | yes |
| the instance the tool places | 15 frames for a reset; as long as needed for a zoom-in | computed |

A **zoom-in or promotion** asset animates for its configured frames and then simply **holds**
the level it reached, so its instances are as long as the speech needs — 30 frames, 250, more.
The animation length is only the floor below which the move would be cut off mid-way.

A **reset** asset finishes its return in its configured frames, so a 15-frame instance does the
whole job even though the clip in your bin is 42 frames long.

That is why you tell the tool the animation lengths, in
[`config.example.toml`](config.example.toml):

```toml
[assets.transition_frames]
x0_to_face_x1 = 15
face_x1_to_face_x2 = 15
face_x2_to_face_x3 = 15
face_x1_to_x0 = 15
face_x2_to_x0 = 15
face_x3_to_x0 = 15
```

In frames, because that is how you keyframed them, and with no default: they are properties of
*your* assets, and the tool will not guess them or read your Fusion graph to find out. The two
tables — `[assets]` and `[assets.transition_frames]` — must always list the same roles.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
python -m pip install -e '.[dev]'
```

Copy the example config and edit it for your project:

```bash
cp config.example.toml config.toml
```

Set at least: `voice_audio_track` (which audio track is your voice), `zoom_video_track` (an
empty video track for the zooms), `cut_reference_video_track` (your edited footage track),
`asset_bin`, and the `[assets]` / `[assets.transition_frames]` tables.

**1. Check the environment.** With Resolve open:

```bash
davinci-auto-zoom doctor
```

**2. Check your assets are found.**

```bash
davinci-auto-zoom assets --config config.toml
```

**3. See the plan, before anything is placed.**

```bash
davinci-auto-zoom plan-probe --confirm-resolve-render-test --project MY_PROJECT --source-timeline MY_TIMELINE --config config.toml
```

This prints every planned transition with its frames and the reason it is there. It places
nothing.

**4. Apply it to a preview timeline.**

```bash
davinci-auto-zoom apply-preview --confirm-create-preview-timeline --confirm-resolve-render-test --project MY_PROJECT --source-timeline MY_TIMELINE --config config.toml
```

**5. Open the `DAZ_AUTO_PREVIEW_*` timeline it names and watch it.** That is the only way to
judge whether the edit is good; the tool can only prove it is structurally what was planned.

**6. Optionally rework it.** `clean-preview` takes DAZ's own clips back out of one named
preview; `rebuild-preview` does that and re-applies a freshly computed plan.

```bash
davinci-auto-zoom clean-preview --confirm-clean-owned-preview --project MY_PROJECT --source-timeline MY_TIMELINE --preview-timeline DAZ_AUTO_PREVIEW_20260818_163637_e8787ece --config config.toml
```

## Commands

**The everyday ones:**

| Command | What it touches |
| --- | --- |
| `doctor` | Nothing. Environment, connectivity and a capability matrix built by cross-checking the installed Blackmagic documentation against the live API surface. |
| `assets` | Nothing. Which prebuilt zoom assets were found in your bin, and which role each fills. |
| `plan-probe` | A scratch timeline it creates and deletes, for the temporary voice render. **Never places a zoom.** |
| `apply-preview` | Creates a new `DAZ_AUTO_PREVIEW_*` timeline and places the zooms there. Your timelines are never modified. |
| `clean-preview` / `rebuild-preview` | **Destructive**, on exactly the one preview you name, and only on clips DAZ can prove it created. |

**Diagnostics, if you need them:**

| Command | What it touches |
| --- | --- |
| `snapshot` | Nothing. Normalized dump of the open project: timelines, tracks, clips, frames, derived edit boundaries. |
| `compare A B` | Nothing. Structural diff of two timelines. |
| `speech-file AUDIO` | Nothing — no Resolve session at all. Speech detection on a local audio file, with `--fps` and `--start-frame` so the segments come out in your timeline's coordinates. |
| `speech-probe` | The same scratch render as `plan-probe`, reporting speech segments and stopping there. |
| `probe-write` / `probe-ownership` | Development spikes. Each works on a scratch timeline it creates and deletes and needs its own confirmation flag. |

Every command accepts `--json` and `--config`. Every write-capable command requires its own
explicit confirmation flag, and one flag never authorises another. Every command fails
gracefully with an actionable message when Resolve is closed or the scripting module cannot be
found, and exits non-zero rather than raising.

## How detection works

```
configured Resolve voice track  ->  isolated temporary audio render
  ->  16 kHz mono PCM (ffmpeg)  ->  Silero VAD (ONNX, CPU)
                               \->  short-time energy envelope (dBFS)
  ->  speech segments + voice dynamics, in absolute Resolve timeline frames
```

**One render, one normalization, two readers.** The loudness envelope the level promotions are
read from is computed from the same decoded PCM the VAD consumes, so a run needs exactly one
render of your voice track and the two signals can never drift apart.

The voice track is rendered rather than reconstructed from source files, because only Resolve
can produce what Resolve actually plays: cuts, trims, fades, levels and any clip or track
treatment are all preserved. Isolation happens on a throwaway duplicate of your timeline,
never on the timeline itself.

Detection uses **Silero VAD v6.2.1** through ONNX Runtime on CPU. The model is vendored,
version-pinned and checksum-verified.

Then, in pure code with no Resolve object, no model and no clock — the same inputs always
produce the same plan:

1. **Speech regions become editorial bursts.** Two speech regions separated by less than
   `reset_after_silence_ms` belong to the same burst. This setting is a *gate*, not a delay:
   the tool works offline and already knows how long every pause lasts, so it never plans a
   reset 650 ms after you stopped talking — it plans it at the end of the burst.
2. **One zoom-in per burst**, at the burst start. It is dropped entirely if it would be
   shorter than its own animation, because a truncated move is worse than no zoom.
3. **The level climbs when the voice breaks and comes back.** Inside a burst, when your level
   drops by at least `promotion_min_drop_db` for at least `promotion_min_valley_ms` and then
   comes back to within `promotion_recovery_within_db` of your normal speaking level, that
   pick-up is a **promotion cue**. The first cue tightens to x2, the second to x3, and any
   further cue is ignored. A cue is skipped (never moved) if the previous transition has not
   finished animating, or if the new level could not be held for `promotion_min_hold_ms`
   before the reset. Every threshold is *relative* to your own voice level in dB, so changing
   microphone gain does not change the edit.
4. **Every transition may land on a real hard cut.** Each one has a raw audio anchor — the
   burst start for the entry, a recovery cue for a promotion, the burst end for the reset —
   and looks for a hard cut near it. The **nearest** cut wins, with the later cut preferred on
   an exact tie. The zoom-in window is symmetric and small; the reset's is **asymmetric**,
   wider after the burst end than before it, because the detector pads each segment so no
   phoneme is clipped, which puts the detected end a few frames after the perceptual one. With
   no cut in the window the transition stays exactly on its audio anchor, and a cut that would
   break the chain gives way to the next valid candidate. A "hard cut" means one clip ends
   exactly where the next begins on `cut_reference_video_track`; entering from black or
   running out into a gap is not a cut.
5. **A reset is only placed if it fits.** The whole reset animation must finish before the next
   zoom starts, or before the timeline ends. If nothing fits, no reset is placed and the zoom
   is held.

Every one of those decisions is printed, so you can see *why* each zoom is where it is before
anything is applied.

## Safety model

This is an editor automation tool, and a wrong write can ruin a timeline. So:

- **Your source is never edited.** No command modifies a timeline you already work in. The
  zooms go on a `DAZ_AUTO_PREVIEW_<timestamp>_<id>` duplicate the run creates.
- **The plan is re-checked against reality before the first write.** Project, timeline, its
  unique id, frame range, frame rate, the three configured tracks, the asset names, the Media
  Pool items those names resolve to, the animation lengths, every planner setting — plus a
  **structural fingerprint** of the voice track and the cut-reference track. Re-cut your V1 or
  move a clip on your voice track after planning and the run refuses, even though every name
  and duration still matches. Nothing is created when it refuses.
- **Zooms go only on an empty, dedicated track.** Missing video tracks are added until the
  configured index exists. A track that already holds *anything* is a refusal. Nothing is
  overwritten, shifted or deleted, and no other track is touched.
- **Every clip DAZ creates is tagged as its own**, with a namespaced, versioned marker record
  on the timeline item, which is then read back and verified. Either 100% of the items are
  tagged or the whole preview is rolled back.
- **Ownership is never inferred from appearance.** Not from a clip's name, track, position,
  duration, Fusion composition or Media Pool asset — two identical clips can be yours and
  DAZ's. The marker is the only proof. A clip with no DAZ metadata **is yours and is never
  deleted**, even if it is identical to one DAZ would have made.
- **Anything unclassifiable fails closed.** Corrupt metadata, an unknown schema version,
  contradictory markers, or metadata naming a different preview stops the whole run and
  *nothing* is deleted. A half-cleaned track is worse than an uncleaned one. Previews made
  before ownership existed hold no metadata, are never adopted, never retro-tagged and never
  deleted.
- **A recovery copy exists before anything is deleted.** `clean-preview` and `rebuild-preview`
  first duplicate the target as `DAZ_RECOVERY_<timestamp>_<id>`, and keep it if anything fails.
  DAZ will not undo the run for you — you decide, with the copy in front of you.
- **Every temporary change is restored and verified**, in a `finally` block: the timeline you
  had open, the Deliver page settings, the render queue (pre-existing jobs are never touched
  and `DeleteAllRenderJobs` is never called), the temporary render directory. Anything that
  does not come back is **reported as unrestored**, not glossed over.
- **The preview is the transaction.** If anything at all fails, it is deleted and confirmed
  gone. A preview from an earlier run is never reused, overwritten or deleted.
- **`SaveProject()` is never called.**
- Deletes are never ripple deletes, and no video track is ever removed.

Running `rebuild-preview` twice changes nothing the second time: same source, same config,
same assets, same placements at the same frames with the same identities. Clips are replaced,
never stacked.

## Configuration

The config keeps two different questions apart on purpose:

| Block | Question it answers | Example |
| --- | --- | --- |
| `[speech.vad]` | *Was the creator making speech sounds here?* | `min_silence_ms = 100` — how long the voice must stop before an utterance has really ended |
| `[speech.energy]` | *How is loudness measured?* | `window_ms = 30` — the RMS window the envelope is built from |
| `[planner]` | *What should the edit do about it?* | `reset_after_silence_ms = 650` — how long a silence must last to be worth zooming back out for; `promotion_min_drop_db = 20` — how big a dip in the voice deserves a tighter level |
| `[assets.transition_frames]` | *How long do your assets take to animate?* | `x0_to_face_x1 = 15` — in frames, no default, never guessed |
| `[assets]` | *Which clip performs which transition?* | `face_x1_to_face_x2 = "FACE_X2"` — omit a role to never make that move |

The `[speech.vad]` values are Silero's own defaults. Change them when the detector is visibly
wrong about the *audio*; change `[planner]` when you disagree with the *edit*.

A misspelled or unsupported key is an **error, not a warning**. Silently ignoring one would
leave you believing it still tunes something. Two groups of keys are rejected by name with an
explanation rather than a generic message:

- the old duration-based promotion keys (`promote_to_face_x2_after_ms` and friends), replaced
  by the voice-dynamics ones;
- the experimental GAMEPLAY roles, removed from the product (see below).

## Non-goals and historical research

**Gameplay zoom automation was researched and intentionally not shipped**, because placement
proved too context-dependent for the generic signals tested. Two measurement phases — one on
audio and motion amount, one on spatial/visual structure — each failed to separate the windows
the editor zoomed from the ones they left alone, on the only reference edit available. Rules
using no signal at all scored better than every rule using one. The measurements are kept in
`.agent/reports/phase-09a-*` and `.agent/reports/phase-09b-*` as research evidence; nothing in
the runtime, the config or the CLI anticipates the feature returning.

Also deliberately absent: demotions between facecam levels (`x3 -> x2`, `x2 -> x1`), semantic
rules based on transcript words, and applying zooms in place on a timeline you work in.

## Limitations

Honest ones, current as of the facecam release:

- **calibrated and validated primarily on one creator and one reference workflow.** The
  thresholds sit on broad plateaus rather than spikes, which is the best available evidence
  that they transfer — and still not evidence that they do;
- **a dedicated voice track is required**, and its index is configuration, not detection;
- **a human correction pass is expected.** The tool removes repetitive decisions, not
  judgement;
- **burst extent is not perfect.** On the reference material a few bursts open earlier or close
  later than the human's equivalent. The result was watched and accepted; it is a known
  calibration limitation, not a defect being worked around;
- **the preview proves structure and timing, never how the edit looks.** Watching it is part of
  the workflow, not an optional extra;
- no gameplay automation, no semantic understanding, no apply-in-place;
- only tested on Resolve Studio 21.0.4.5 on Linux.

## Development

```bash
python -m pip install -e '.[dev]'
pytest              # never needs Resolve, never touches the network
ruff check .
mypy                # strict
```

Live Resolve verification is separate, explicit, and reported with its exact output. Saved runs
and analysis live in [`.agent/reports/`](.agent/reports/).

The project deliberately separates **Resolve integration** (the only layer allowed to mutate
anything), **speech analysis** (objective audio facts only), **planning** (pure, deterministic,
testable with no Resolve installed) and **execution** (realizes placements, makes no editorial
decision). `tests/test_facecam_golden.py` holds the validated edit written out in full from
synthetic inputs, so a change to any facecam rule has to be read before it can be merged.

This repo ships with local agent guidance (`AGENTS.md`, `CLAUDE.md`, `.agent/`). Those files
are **tracked in Git and committed** alongside the code they describe: a decision log whose
history does not match the code's is worthless.
