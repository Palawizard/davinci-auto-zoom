# davinci-auto-zoom — coding-agent instructions

Read these files before changing code:

1. `.agent/PROJECT_CONTEXT.md`
2. `.agent/IMPLEMENTATION_PLAN.md`
3. `.agent/DECISIONS.md`
4. `.agent/HANDOFF.md`

For the current assignment, also read the prompt file named by the user.

## Operating rules

- Treat the **installed DaVinci Resolve Developer/Scripting documentation** as canonical for API capabilities. Do not invent API methods from memory.
- Do not mutate a real Resolve timeline unless the current milestone explicitly authorizes writes.
- Keep Resolve proxy objects inside `src/davinci_auto_zoom/resolve/`.
- Keep planning logic deterministic and testable without Resolve.
- Express timeline positions internally as integer frames.
- Prefer a small, evidence-backed change over speculative architecture expansion.
- Run the relevant tests/lint for every implementation milestone.
- Update `.agent/HANDOFF.md` after meaningful work: exact state, commands run, results, blockers, next step.
- Record durable technical decisions in `.agent/DECISIONS.md`.
- Update `.agent/IMPLEMENTATION_PLAN.md` only when evidence changes the plan; preserve history rather than silently rewriting intent.
- **Always commit the agent documentation.** `AGENTS.md`, `CLAUDE.md` and `.agent/` are
  tracked on purpose: a decision log or handoff whose history does not match the code's is
  worthless, and the work happens on several machines and in cloud sessions. Include them in
  the same commit as the change they describe. Only local tool configuration stays ignored.

## Completion discipline

A task is not complete because code was written. It is complete when:

- acceptance criteria in the task are satisfied,
- verification commands were run and results reported,
- docs/handoff are current,
- unresolved uncertainty is explicit,
- no out-of-scope timeline mutation was introduced.
