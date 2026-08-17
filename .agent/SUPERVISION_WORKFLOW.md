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
- **new states or transitions**: every addition to `domain/transitions.py` needs a reference
  edit and a measurement behind it, never a guess (D048). The x3 thresholds are the cautionary
  example — they rest on a single manual clip, and the report says so in those words.

## A note on editorial thresholds

Anything that decides *when* an edit happens must be able to answer "measured against what?".
Phase 6 (cut snapping) and Phase 8 (level promotions) both derived their numbers from the
user's own reference timelines, and both reports state the sample size, including when it is 1.

Two failure modes to reject on sight:

- a threshold tuned until the automatic plan matches a reference timeline. `DAZ_OUTPUT_MVP*`
  are heuristic references, never ground truth, and a planner fitted to one of them has learned
  that timeline rather than the edit style;
- a divergence from the human edit quietly worked around instead of reported. Phase 8 ships
  three known level mismatches out of 14 cycles and names the cause of each; that is the
  expected shape of a report, not a shortfall in it.
