# blotquant — rules for Claude Code

## Scope discipline
- Follow PLAN.md. Work ONLY on the phase named in the current task. Never start the next phase, even if finished early — stop and report instead.
- All work happens on a branch named `phase-N-<short-name>`. Open a PR; never push to `main`.

## Hard boundaries
- NEVER write to `data/ground_truth/` from pipeline, api, web, or eval code. The ONLY writer is `python -m synth`.
- After Phase 0 is merged, `synth/` is FROZEN. No edits without an explicit human instruction in the task text. If a change is authorized: bump `SYNTH_VERSION` and add a break marker in `evals/history.md`.
- `pipeline/` must never import from `synth/` and must never special-case generator artifacts (check `synth/MODELS.md` for what would count as special-casing).
- Parameter iteration happens on the dev split ONLY. The test split is run once per phase, at the end. Never tune on test.
- Do not add dependencies beyond `requirements.txt` / `package.json` without listing each new one in the PR description with a one-line reason.
- Do not touch `.env`, credentials, or deployment config unless the task explicitly says so.

## Engineering rules
- Prefer loud failure over silent fallback. Unsupported bit depth raises; a missing config key raises. No placeholder defaults.
- Every processing parameter lives in the config object and is written into result provenance. No magic numbers in function bodies.
- QC annotates, never silently drops. A flagged value is reported with its flags; exclusion from ratios is explicit and recorded.
- Numeric correctness is asserted against fixtures or ground truth — never eyeballed. Image-processing code without numeric assertions is untested code.
- Type hints everywhere. Errors: specific exceptions with actionable messages. Never `except: pass`.

## Definition of done (per task)
1. `ruff check .` passes.
2. `pytest` passes (failure modes, not just happy path; determinism test for synth output).
3. The reviewer agent (`.claude/agents/reviewer.md`) has been run on the full diff in a fresh context and returned ZERO required changes.
4. PR is open with: what was built, deviations from PLAN.md (if any), new dependencies with reasons (if any), and open questions for the human.

## Communication
- When PLAN.md is ambiguous, ask the human rather than deciding silently. List questions at the end of the session output.
- Commit messages are written by the human. Stage changes; do not commit unless explicitly told to.
