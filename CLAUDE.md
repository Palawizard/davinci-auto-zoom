# davinci-auto-zoom — Claude Code entrypoint

@AGENTS.md

Everything above applies. This file adds **only** what is specific to Claude Code; the
architecture, Resolve safety model, Git/documentation policy, test gates and completion
discipline live in `AGENTS.md` and are deliberately not repeated here.

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
