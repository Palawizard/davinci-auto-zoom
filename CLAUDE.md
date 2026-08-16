# davinci-auto-zoom — Claude Code entrypoint

Use `.agent/PROJECT_CONTEXT.md` as project context and `.agent/IMPLEMENTATION_PLAN.md` as the source-of-truth roadmap. Read `.agent/DECISIONS.md` and `.agent/HANDOFF.md` before implementation.

The installed Blackmagic Design Developer/Scripting documentation for the user's Resolve version is canonical. Never guess Resolve API capabilities. The current phase is read-only unless the active task explicitly says otherwise.

Keep Resolve integration isolated in `src/davinci_auto_zoom/resolve/`, pure editing decisions in `domain/`, and speech/transcription implementations behind `speech/` interfaces. Run tests and update `.agent/HANDOFF.md` before finishing.

Writes to Resolve are allowed **only** through `probe-write` (Phase 2 spike, opt-in flag). Everything else stays read-only.

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
  before grepping. Re-index after large changes. Note `.agent/`, `CLAUDE.md` and
  `AGENTS.md` are gitignored and therefore **not** in the graph — read those directly.
- **ponytail** — already active via its own plugin hook; do not re-invoke it.
