# Supervision workflow

The intended development loop is deliberately incremental:

1. Supervisor defines one milestone/prompt.
2. Coding agent implements only that milestone and updates `.agent/HANDOFF.md`.
3. Human runs any required manual Resolve checks, then commits/pushes the code to the public repository.
4. Human gives the supervisor:
   - public repository URL / commit SHA
   - coding agent's final report
   - `.agent/HANDOFF.md` when its local details matter
5. Supervisor reviews the public code/diff against the milestone, identifies regressions/API risks, and writes the next coding-agent prompt.

Do not let a coding agent race through multiple implementation phases in one turn. Resolve automation has enough undocumented/version-sensitive edges that each write-capable milestone should be reviewed before expanding scope.

For large changes, the supervisor should particularly review:

- separation of domain planner vs Resolve adapter
- evidence for every Blackmagic API method used
- timeline mutation safety/idempotency
- frame/time conversion
- collision behavior on real edits
- restoration of any temporary project state
- tests that reproduce the user's actual editing examples
