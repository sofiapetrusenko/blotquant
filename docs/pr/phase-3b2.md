## What was built

*[Sofia to write]*

## Scope and what governed it

**Polarity only.** Detection parameters do not change in this phase; the task text fixes that if
over-detection survives the polarity fix it is a separate defect needing its own pre-registration.
Gate 1 ruling 3 governs throughout: a real blot may falsify a decision, never select a parameter
or a code path.

| boundary | state on this branch |
|---|---|
| `configs/` | untouched |
| `synth/` | untouched |
| `data/ground_truth/` | untouched |
| `pipeline/` importing `synth/` | none |
| test split | not read, not tuned on |
| new dependencies | none |

## W8 — CI scoping (DEBT E3)

*[Sofia to write]*

### Trigger matrix

| workflow / job | trigger | path filter | fires on |
|---|---|---|---|
| `ci.yml` → `checks` | `push` any branch; `pull_request` | none | every push |
| `ci.yml` → `install-path` | `push` any branch; `pull_request` | none | every push |
| `figures.yml` → `recorded-figures` | `push`; `pull_request` | `evals/**`, `configs/**`, `pipeline/**`, `synth/**`, `data/**`, `requirements.txt`, `.github/workflows/figures.yml` | only when a figure could move |

Each job carries `if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name != github.repository`, so same-repository work is covered by its push and a fork PR by the `pull_request` event.

### Trade-offs, both recorded in the workflow files and in E3

| trade-off | consequence |
|---|---|
| `pull_request` skipped for same-repo PRs | a PR is tested at its branch head, never at the merge commit |
| a `paths:`-skipped workflow reports no status | if `recorded-figures` is ever required, a docs-only PR waits forever |

### Sweep step wall-clock, read from GitHub Actions on 2026-08-24

| statistic | value |
|---|---|
| non-zero runs surveyed | 33 |
| minimum | 4m54s |
| median | 6m59s |
| maximum | 7m42s |

## W9 — the polarity amendment

`data/real/AMENDMENT_2026-08-24_polarity.md`, ratified 2026-08-24, digest pinned in
`tools/check_claims.py`.

| section | what it fixes |
|---|---|
| (a) | polarity is a declared caller input; the pipeline refuses an undeclared image |
| (b) | the gold set declares `bright_on_dark`, established from the generator's own three statements |
| (c) | Option A ruled — invert at load, so everything below the loader sees one convention |
| (d) | pre-registered prediction that no recorded dev-split figure can move |
| (e) | the re-record rule, plus a dated prediction of what the real-crop over-detection would do |

*[Sofia to write]*

## W10 — the implementation

| surface | how polarity is supplied |
|---|---|
| `pipeline.load.load_image` | required positional parameter; no default; refuses an unrecognised value |
| `pipeline.analyze.analyze_image` | required keyword-only |
| `python -m pipeline run` | `--polarity`, `required=True` |
| `POST /analyze` | required form field; unrecognised value → 415, missing field → 422 |
| result document | `source.polarity`, required, enum |
| gold set | `evals.run.GOLD_SET_POLARITY` |
| approved real crops | `tools.phase3.crop_names.REAL_CROP_POLARITY` |
| `python -m tools.phase3.run_real` | `--polarity`, `required=True` |

| contract change | value |
|---|---|
| result schema version | 1.3.0 → 1.4.0 |
| `source.polarity` | required, `enum` of the amendment's two values |
| `result_id` hashed inputs | five → six |

*[Sofia to write]*

## The §(e) report — no recorded dev-split figure moved

§(e) requires the movement be reported against (d), and states that nothing-moved is still a
report rather than a silence.

| check | result |
|---|---|
| `python -m evals.sweep --check` | passes: structure, header and config digests exact, every figure within its tolerance class |
| `invert_pixels` calls while loading the gold set | zero — a pass-through, not an invert-and-undo |

## W11 — detection under the ruled polarity

Measured on staged tree `14ae5d9e40b014335a97e8a69bece2bc7d67bd88`. 3b-1 figures read from
`runs/3b1/` at measurement time; `runs/` is gitignored, so both sets are reproducible from the
committed tooling rather than stored.

| | 3b-1 (wrong declaration) | 3b-2 (ruled) |
|---|---|---|
| crops producing a result document | 12 of 12 | 12 of 12 |
| lanes | 106 | 116 |
| bands | 430 | 420 |
| bands carrying any QC flag | 404 | 238 |
| `saturated` bands | 286 | 68 |
| `overlapping` bands | 195 | 75 |
| `unresolved_shoulder` bands | 255 | 142 |
| image `saturated` | 12 of 12 crops | 12 of 12 crops |
| image `low_dynamic_range` | 7 of 12 crops | 0 of 12 crops |

### D8 — lanes on the `PMC13135410` panels, against the §(e) prediction

| panel | 3b-1 | 3b-2 | §(e) predicted |
|---|---|---|---|
| `PMC13135410_Figure3__B` | 13 | 15 | 12 |
| `PMC13135410_Figure4__A` | 13 | 15 | 12 |
| `PMC13135410_Figure4__B` | 13 | 13 | 12 |
| `PMC13135410_Figure4__C` | 13 | 14 | 12 |

| direction across all crops | crops |
|---|---|
| lane count rose | 9 |
| lane count fell | 1 |
| lane count unchanged | 2 |

### The falsifier

**It fired on the LANE condition**, which is the branch §(e) states in disjunction: *"band and
lane counts that are unchanged, or that rise."* Lane counts rose.

**The band counts are not a second met condition.** They fell by a small amount, which is neither
branch of the pre-registered wording. Reading that as "essentially unchanged" would apply a
tolerance §(e) never fixed. D10's prediction of a substantial fall failed separately — a
prediction failing is not a falsifier firing.

### Detection status in v1.0 (ruling 3, 2026-08-25)

Detection ships as **beta**. QC vocabulary validated against polarity on the real crops;
detection counts are not validated — the over-detection is real, unexplained by polarity, and
pre-registered for separate investigation. **"Not a polarity artefact" is never presented as
"correct".**

## Review record

| cycle | REQUIRED |
|---|---|
| 1 | 8 |
| 2 | 8 |
| 3 | 6 |
| 4 | 4 |
| 5 | 4 |

| loop closure | state |
|---|---|
| cap | 5 cycles, reached |
| zero REQUIRED | not reached |
| cycle 5's fixes | carry tests and mutation testing; no fresh-reviewer pass |
| sixth cycle | replaced, not skipped — authorship freeze, report-only check, direct human review |
| recorded as | DEBT P4 |

| finding class after cycle 2 | count |
|---|---|
| requiring a behaviour change | 0 |
| claim- or record-accuracy | all remaining |

Two cycle-5 edits touched measurement code and neither changes executable code, proven by AST
comparison with every bare string statement stripped:

| file | change | before/after executable code |
|---|---|---|
| `pipeline/load.py` | `invert_pixels` docstring | identical |
| `evals/run.py` | `GOLD_SET_POLARITY` docstring | identical |

*[Sofia to write]*

## Deviations and disclosures

*[Sofia to write]*

| deviation | recorded in |
|---|---|
| the gold-set declaration was first placed in `synth/`, which the ratified §(b) forecloses; reverted | NOTES.md |
| the task text and the ratified amendment disagree on that placement; the amendment won | NOTES.md |
| the W11 path gate was ruled satisfied on evidence rather than by path | NOTES.md, ratified deviation |
| the polarity amendment's digest was re-pinned twice in an uncommitted tree | NOTES.md, `RATIFIED_AMENDMENTS` docstring |
| the review loop closed at the cap without zero | DEBT P4 |
| agent-authored prose may not assert a count | NOTES.md, authorship freeze |

## Register movement

| entry | what this phase did |
|---|---|
| S21 | added |
| S22 | added |
| S23 | added |
| S24 | added |
| E11 | added |
| P4 | added |
| P5 | added |
| S14 | re-evidenced and half-ruled |
| E3 | re-evidenced |
| E8 | re-evidenced |

**Closed by this phase: none.**

## Open questions for the human

*[Sofia to write]*

## Verification

`ruff check .` exit 0. `pytest` exit 0. `tools/check_claims.py` exit 0, with the prose-count
advisories report-only under ruling 2. `python -m evals.sweep --check` exit 0. Every figure in
this body is a table row read from an artefact.
