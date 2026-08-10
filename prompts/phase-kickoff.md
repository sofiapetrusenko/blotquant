# Phase kickoff prompt — paste into Claude Code, replacing N and <short-name>

Read CLAUDE.md and PLAN.md in full. Your task: implement **Phase N (<short-name>)** exactly per PLAN.md. Do not start any other phase.

Protocol — follow it literally:

1. Create branch `phase-N-<short-name>` off `main`.
2. Delegate implementation to the **implementer** subagent. Pass it the phase name and remind it that CLAUDE.md overrides its preferences.
3. When the implementer declares done, run the **reviewer** subagent in a FRESH context on the FULL diff (`git diff main`). Do not summarize the diff for it — give it the real diff.
4. If the reviewer returns any REQUIRED items, pass them verbatim back to the implementer to fix, then re-run the reviewer on the updated full diff in a fresh context. Repeat.
5. Stop condition: reviewer returns ZERO required — or 5 review cycles have run. After 5 cycles, stop and report the unresolved REQUIRED items; do not keep grinding.
6. Run `ruff check .` and `pytest` one final time on the converged state.
7. Stage all changes (`git add`). Do NOT commit — commit messages are written by the human.
8. Open a PR with `gh pr create` if `gh` is authenticated; otherwise print the PR title and description. The description must contain: what was built, deviations from PLAN.md (if any), each new dependency with a one-line reason, and open questions for the human.
9. End with a status report: eval numbers if this phase produces them, review cycle count, and anything ambiguous you encountered.

Reminders:
- `data/ground_truth/` is written only by `python -m synth`.
- After Phase 0, `synth/` is frozen — this task does NOT authorize edits to it unless it says so explicitly.
- Tune on the dev split only; run the test split once, at the end, and log it.
