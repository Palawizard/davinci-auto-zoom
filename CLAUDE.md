# davinci-auto-zoom — Claude Code entrypoint

Use `.agent/PROJECT_CONTEXT.md` as project context and `.agent/IMPLEMENTATION_PLAN.md` as the source-of-truth roadmap. Read `.agent/DECISIONS.md` and `.agent/HANDOFF.md` before implementation.

The installed Blackmagic Design Developer/Scripting documentation for the user's Resolve version is canonical. Never guess Resolve API capabilities. The current phase is read-only unless the active task explicitly says otherwise.

Keep Resolve integration isolated in `src/davinci_auto_zoom/resolve/`, pure editing decisions in `domain/`, and speech/transcription implementations behind `speech/` interfaces. Run tests and update `.agent/HANDOFF.md` before finishing.

Writes to Resolve are allowed through the opt-in probes (`probe-write`, `speech-probe`, each
behind its own flag, each cleaning up after itself) and through `apply-preview`, which is the
only path that intentionally leaves something behind: a new `DAZ_AUTO_PREVIEW_*` timeline
carrying the planned zooms. No command may modify a timeline the user already works in.
Everything else stays read-only.

`AGENTS.md`, `CLAUDE.md` and `.agent/` are tracked in Git and **must be committed** with the
changes they describe.

## Required tooling

A `SessionStart` hook (`~/.claude/hooks/tooling-directive.sh`) normally injects this. It is
repeated here so it survives a missing hook, another machine, or a subagent.

- **token-efficient-shell** — invoke the skill before the first large-output command
  (pytest, mypy, ruff, log or multi-file dumps). Once per session, early.
- **codebase-memory** — its tools are *deferred*: load them first with
  `ToolSearch: "select:mcp__codebase-memory__search_graph,mcp__codebase-memory__get_code_snippet,mcp__codebase-memory__index_status,mcp__codebase-memory__trace_path"`.
  This repo is indexed as project
  **`home-palawi-Documents-Projets-NAS-SYNC-Code-davinci-auto-zoom`**.
  Use `search_graph` / `trace_path` / `get_code_snippet` to locate symbols and callers
  before grepping. Re-index after large changes. `.agent/`, `CLAUDE.md` and `AGENTS.md` are
  Markdown rather than code, so they may not appear in the graph — read those directly.
- **ponytail** — already active via its own plugin hook; do not re-invoke it.
