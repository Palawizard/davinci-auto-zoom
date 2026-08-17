# davinci-auto-zoom — coding-agent instructions

**This file is the single source of the cross-agent rules for this repository.** Codex,
Claude Code, future agents and the human supervisor all read it. Agent-specific files
(`CLAUDE.md`) import it and add only what is genuinely specific to that agent — they never
restate these rules, because two copies of a rule diverge.

Read these files before changing code:

1. `.agent/PROJECT_CONTEXT.md` — what the tool is and what "done" means for the product
2. `.agent/IMPLEMENTATION_PLAN.md` — the source-of-truth roadmap
3. `.agent/DECISIONS.md` — durable technical decisions, `D0xx`, never silently reversed
4. `.agent/HANDOFF.md` — exact state at the end of the last session
5. `.agent/RESEARCH_NOTES.md` — measured Resolve behaviour, including the surprises
6. `.agent/SUPERVISION_WORKFLOW.md` — how work is assigned and reviewed

For the current assignment, also read the prompt file named by the user.

## Architecture

- `domain/` — pure editing decisions. Deterministic, testable without Resolve, no Resolve
  object ever reaches it.
- `domain/transitions.py` — **the single source of which visual states exist and which moves
  between them are legal** (D044). The planner reasons in those states and emits transitions;
  a move outside the table raises rather than planning nothing (D048). Add a state here or
  nowhere.
- `resolve/` — the only place Resolve proxy objects are allowed to exist.
- `speech/` — transcription/VAD implementations behind the `speech/base.py` interfaces, plus
  the short-time energy envelope. This layer produces **objective audio facts only**: "was
  there speech here", "how loud was it here". It never decides that a dip in the voice deserves
  a tighter zoom — that reading lives in `domain/dynamics.py` (D050).
- `domain/dynamics.py` — the energy envelope as plain `(frame, dB)` pairs, and the valley +
  recovery cues read from it. Pure: no numpy, no ONNX, no file. Every threshold is **relative**,
  in dB against the burst's own voice level, so a gain change cannot change the edit (D051).
- Timeline positions are integer frames internally. Half-open ranges `[start, end)`.
- **No editorial logic below the planner.** The executor resolves a role to a clip name,
  appends it at the planned frames, verifies and tags. It does not know what a zoom level is,
  and it must not learn.

## Resolve safety

- The **installed** DaVinci Resolve Developer/Scripting documentation is canonical for API
  capabilities. Never invent a method from memory; check
  `/opt/resolve/Developer/Scripting/README.txt` (or the platform equivalent) and prefer
  methods that are neither deprecated nor unsupported there.
- **No command may modify a timeline the user already works in.** The write-capable surface
  is exactly:
  - `probe-write`, `speech-probe`, `plan-probe`, `probe-ownership` — each behind its own
    opt-in flag, each on a scratch timeline it creates and deletes, each restoring every
    piece of project state it touched;
  - `apply-preview` — the one command that intentionally leaves something behind: a new
    `DAZ_AUTO_PREVIEW_*` timeline duplicated from the source, carrying the planned zooms;
  - `clean-preview` / `rebuild-preview` — destructive, but only on a `DAZ_AUTO_PREVIEW_*`
    timeline whose items DAZ can *prove* it created, and only after a `DAZ_RECOVERY_*`
    duplicate exists.
- Everything else is strictly read-only.
- `SaveProject()` is never called.
- Ownership is never inferred from a clip name, track index, position, duration, Fusion graph
  or Media Pool asset. The only proof is a DAZ marker with valid `customData` on the
  TimelineItem instance (D035). An item DAZ cannot classify is `ambiguous`, and ambiguity
  means zero deletions.

## Git and documentation

- Work on `dev`. `main` is not touched, history is not rewritten, no force-push.
- Conventional Commits.
- **Always commit the agent documentation.** `AGENTS.md`, `CLAUDE.md` and `.agent/` are
  tracked on purpose. Include them in the same commit as the change they describe: a handoff
  that says Phase 6 while the code is Phase 7 is a defect. Only genuinely personal or secret
  material stays ignored (see `.gitignore`) — credentials, tokens, session state, caches.
- Before committing a doc for the first time, read it for tokens, keys, cookies, credentials
  and session transcripts. Ordinary technical paths (`/opt/resolve/...`, mounted volumes) are
  fine and must not be sanitized away.
- Update `.agent/HANDOFF.md` after meaningful work: exact state, commands run, results,
  blockers, next step.
- Record durable technical decisions in `.agent/DECISIONS.md`.
- Update `.agent/IMPLEMENTATION_PLAN.md` only when evidence changes the plan; preserve history
  rather than silently rewriting intent.
- Text reports worth supervising go in `.agent/reports/` and are committed too.

## Tests

- `pytest`, `ruff check .` and `mypy` (strict) must all pass before a task is reported done.
- The normal suite never needs Resolve and never touches the network.
- Live Resolve verification is separate, explicit, and reported with its exact output.
- Non-trivial logic leaves a runnable check behind. A guard without a test proving it fires is
  not a guard.

## Completion discipline

A task is not complete because code was written. It is complete when:

- acceptance criteria in the task are satisfied,
- verification commands were run and results reported,
- docs/handoff are current **and committed**,
- unresolved uncertainty is explicit,
- no out-of-scope timeline mutation was introduced.
