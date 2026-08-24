# Phase 3b-1 — review cycle 3

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl`
> Source sha256: `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0`
> Location: JSONL record on line 742 of 1203 (~62% into the file), `type: user`, timestamp `2026-08-20T11:52:21.112Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-24. The source log is retained locally and is NOT committed.

---

# Review — cycle 3, `phase-3b1-collapse`

Baseline on the staged tree: `.venv/bin/python -m pytest -q` → **786 passed**, exit 0. `.venv/bin/ruff check .` → clean. `.venv/bin/python tools/check_claims.py` → OK over 16 files.

I verified independently, not by taking the record's word: `data/real/DECISION_unit_of_analysis.md` hashes `994acc30…d88d0`, matching `DECISION_SHA256` and `data/real/README.md`; both ratified amendments are digest-pinned in `RATIFIED_AMENDMENTS` and unmodified by this diff; `synth/`, `configs/`, `.claude/` and `data/ground_truth/` are untouched, and both new writers route through `require_writable_destination` before any `mkdir` (asserted in both new test files). Both CSVs regenerate **byte-identical** from `python -m tools.phase3.{designations,blot_identity}`, so they are records rather than hand-edits. Every row of both is pending; nothing was confirmed. W7 is correctly absent.

## Cycle-2 fixes: all four are real

1. **`ANALYZE_DESCRIPTION`** — now accurate. `/analyze` exposes `config`, `lane_roi` and `reference_band_id` as `Form` fields; `_analyse_upload` (`/Users/sofiapetrusenko/blotquant/api/app.py:263`) passes `reference_band_ids` and `lane_rois` to `analyze_image` and never passes `reference_designation_source`, so "the one input this API does not expose … constant across every request" is true, and the claim about `lane_roi`/`reference_band_id` is a positive statement that does not falsely exclude `config`. `RESULTS_DESCRIPTION` lists the same five inputs. Consistent.
2. **1.3.0 names both fields** — `pipeline/__init__.py:34`, the NOTES.md heading and body, and the schema `description` all name both. I enumerated every `1.3.0` site in the tree; the only singular "the field" is `DEBT.md:700` inside S19, which is legitimately scoped to S19's own field. No third site says "one field."
3. **`lossy_format`** — recorded, and I re-measured the numbers: 19 rows, 12 load, `lossy_format` true on 0, all 12 `png`, all 19 parents `.jpg`. All four are correct today. See REQUIRED 1 for the problem with how they are recorded.
4. **Three refusal branches** — all three genuinely tested: the shape guard (`2d`/`rgba`/`two-channel`), the dtype guard (`float32`/`int64`/`int16`), and the float arm of `_collapse_channels` asserted both positively (`"not one this pipeline reads at any channel count"`) and negatively (`"Export a single" not in message`).

Cycle-2 SUGGESTED items are all present and working: P2's entries read (1)…(10) in sequence, `_check_p2_composition` scans only the Evidence paragraph and requires first-occurrence order, the §2 paraphrase in `crop_names.py` is now faithful to the frozen text ("neither a loading-control band nor stain-based loading"), and both ground-truth guard tests exist.

---

# REQUIRED

### 1. `NOTES.md:3249-3251` — three of the four numbers in the `lossy_format` paragraph are asserted with nothing re-measuring them, which is the exact defect this project defines as a stale claim

&gt; "**12 crops load and `lossy_format` is true on 0 of them**; all 12 are `png` and all 19 parents in `crop_log.csv` are `.jpg`."

Of these, only "12 crops load" is pinned (`tests/test_real_channel_collapse.py::test_exactly_twelve_of_nineteen_crops_load`). "`lossy_format` true on 0", "all 12 are png" and "all 19 parents are .jpg" are re-derivable from committed bytes and are checked by **no test and no `check_claims` quantity**. I measured all three and they are true today; nothing keeps them true.

Why this is REQUIRED and not stylistic: this is the standard the diff sets for itself one section earlier. The ratified amendment this phase implements says in terms that "a measurement in the tree that no CI step re-measures is the class of stale claim `tools/check_claims.py` exists to catch," and `tests/test_real_channel_collapse.py`'s own docstring says "This test *is* that step: it re-measures every crop from the committed bytes on every run, so the assignment is checked rather than asserted." The W4 divergence figures got that treatment; the W4 `lossy_format` figures — recorded specifically because cycle 2 required them — did not. A single sentence added to `tests/test_real_channel_collapse.py` re-measuring `loaded.lossy_format`, `loaded.image_format` and the crop-log parent suffixes closes it.

(Same class, weaker instance, at `tools/phase3/blot_identity.py:5` — "Five of the 13 parent figures contribute more than one crop." I verified it: 13 distinct parents, 5 with &gt;1 crop. Also unpinned; fixing it in the same test is nearly free.)

### 2. `tools/phase3/crop_names.py:64-75` (`CropSetError`) — the hierarchy cannot do the thing its docstring says it exists to do

The docstring's closing claim:

&gt; "…and a caller that wants the corpus failures specifically catches this."

This is false as built. `CropSetError` is the **base**, so `except CropSetError` catches `DesignationError`, `BlotIdentityError` and `CropNameError` as well. There is no expression that catches corpus failures specifically. `_verify_crop_digest`'s docstring (`tools/phase3/designations.py:127`) repeats the false separation — "Raises `CropSetError`, **not a table error**" — but `isinstance(DesignationError(...), CropSetError)` is `True`, so the type does not encode that distinction. `DesignationError`'s own docstring makes the same claim ("a wrong *table* and a wrong *corpus* send a reader to different files").

The half of the docstring that *is* true — "a caller that catches either of those still catches nothing it should not" — is a property of subclassing that would hold under any arrangement, so it does not rescue the design. To answer the question the task posed: the direction is wrong. What the docstrings describe requires a sibling, e.g. `CorpusError(CropSetError)` raised by `_verify_crop_digest`, with `DesignationError`/`BlotIdentityError` staying as siblings under `CropSetError`. Then `except CorpusError` isolates the corpus and `except CropSetError` is the deliberate catch-all.

No call site currently over-catches — I grepped the whole tree and **nothing catches `CropSetError` at all**, in `tools/`, `tests/`, `pipeline/` or `api/`. So the blast radius today is zero and this is a claim defect, not a behaviour defect. But an abstraction introduced this cycle whose only justification is a docstring, and whose docstring is wrong about it, has to be either corrected or fixed before it acquires a caller that believes the docstring.

### 3. `README.md:161` (and `README.md:5`, `pipeline/__main__.py:28`, `api/display.py:148`) — the user-facing statement of the MVP scope is now false, and only the internal registers were updated

`README.md:161`, inside the section the README itself introduces as "the part a user needs before trusting a number":

&gt; "- **Single-channel grayscale chemiluminescence only.** 8/16-bit TIFF, PNG, JPEG. Multichannel fluorescence, dot blots and 2D gels are out of scope."

After W4 the loader accepts a 3-channel 8-bit image at ≤ 2 DN divergence and measures its green plane. I confirmed this end to end: a 3-channel PNG is analysed, `source.channel_collapse` is written, and the document validates. A user reading that bullet would believe their RGB export is refused.

The diff shows the implementer recognised the scope widened — `DEBT.md` P2 entry (10) records it explicitly as a PLAN.md deviation ("`pipeline/load.py` now accepts 3-channel input, where PLAN.md's 'Scope (MVP)' says 'Single-channel grayscale gel-doc images'"), and NOTES.md records three other W4 consequences. Having identified the widening, leaving the surface a *user* reads asserting the pre-W4 rule is the same defect the Phase 4a sixth cycle existed to sweep, on the one surface that faces outward. The other three sites carry the same stale claim:

- `README.md:5` — "blotquant quantifies protein bands in single-channel gel-doc images";
- `pipeline/__main__.py:28` — `help="path to an 8/16-bit grayscale TIFF, PNG or JPEG"`, the text a user sees from `--help`;
- `api/display.py:148` — "the pipeline only measures single-channel images, so this is an internal inconsistency".

The fix is narrow and should be as narrow as the ruling: the collapse is bounded, recorded in provenance, printed by the CLI, and everything else multi-channel still raises. Say that, rather than deleting the limitation.

---

# SUGGESTED

- **`channel_divergence_dn`'s `UnsupportedBitDepthError` is the right class, but the function has an unguarded unit hazard.** Answering the task's question directly: `UnsupportedBitDepthError`'s own docstring is "The input pixel type is outside the supported set (8-bit and 16-bit unsigned)", which is precisely what the guard tests, and `load_image` never reaches it (`_collapse_channels` checks depth first and raises `UnsupportedImageError`, which is correct for a *multi-channel* refusal). Both are `PipelineError`. Keep it. The real gap is that the function *accepts* `uint16` and returns a divergence on a 65535 scale, while the amendment defines DN against an 8-bit full scale of 255 — so a public caller can get a number that is not comparable to `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN`. One sentence in the docstring saying the returned DN is in the array's own full scale would close it.
- **Confining the P2 marker scan to the Evidence paragraph opened no new blind spot.** I checked: an entry added outside Evidence, an entry added without a marker, and a marker added without incrementing the count all fail, because the stated count must equal `1..N` in first-occurrence order. `P2_EVIDENCE`'s terminator correctly survives the entries that themselves open with bold runs. `"Why (7) is not a licence"` sits outside the scan and cannot be mistaken for an entry. No change needed.
- **`tests/test_reference_designation.py:2161` (`test_the_cli_refuses_a_source_without_a_designation`)** asserts only `code == 1` and `"error:" in stderr`. Under `housekeeping_single` with no `--reference-band`, `_resolve_references` raises the missing-reference error *before* `_resolve_designation_source` is reached, so the test's docstring ("Naming an origin without naming a reference exits non-zero") describes a path the assertion does not distinguish. The unit-level equivalent (`test_the_source_is_refused_when_it_describes_no_designation`) does match `"nothing designated"` and is sound; matching the same phrase here would make the CLI test say what it claims.
- **`pytestmark = skipif(not …_PATH.is_file())` in `tests/test_designations.py` and `tests/test_blot_identity.py`** guards on files this diff commits. Deleting or renaming either CSV turns ~20 tests, including `test_the_committed_table_is_what_a_fresh_build_produces`, into a silent green skip. `tests/test_real_channel_collapse.py` has the same shape on `crop_log.csv`. Loud failure would be more in keeping.
- **`tests/test_blot_identity.py:1060`** pins only `COLUMNS[3] == "blot_id"`, where `tests/test_designations.py` pins the full column tuple. Same asymmetry, same one-line fix.
- **`data/real/README.md`'s "what | where | count" table** does not list the two new CSVs. It already omits both amendments, so the table is evidently not treated as an inventory contract — but two more unlisted files in a directory whose README enumerates its contents is drift worth arresting.
- **On the D7 disposition** (task question): NOTES-only is *defensible* under the established convention — `runs/` is gitignored, Phase 3b-0 explicitly left D1–D7 as drafts and promoted only S19, and NOTES.md records "statements in DEBT.md may not cite figures that only exist there." So I am not calling the absence of a DEBT entry REQUIRED. But note what a DEBT entry would have bought: entries are counted by `_check_register_composition` and their figures fall under the register's discipline, which is exactly the pinning REQUIRED 1 is asking for. Promoting D7 would fix REQUIRED 1 as a side effect, and I'd recommend raising it with the human rather than deferring it silently.

---

# Mutation outcomes

All mutations applied, observed, and restored. Final state verified: `git status --short` matches the opening snapshot exactly and `git diff` (unstaged) is **0 lines**.

| # | Mutation | Result |
|---|---|---|
| 1 | `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 2 → 3` | **8 failures.** Synthetic boundary: `test_divergence_above_the_bound_raises…[3]`, `test_the_bound_is_not_moved_by_a_single_dn`. **Real set**: both `test_a_crop_at_the_bound_collapses_and_records_the_bound` cases, `test_an_excluded_crop_is_still_refused…[PMC13102869_Fig1__B-FXN-VCL.png]` (which is the crop sitting at exactly 3 DN, so the widened bound admits it), and `test_exactly_twelve_of_nineteen_crops_load`. Plus schema/loader pins: `test_result_schema_and_loader_declare_the_same_collapse_rule`, `test_result_schema_accepts_a_collapse_at_the_bound`. Both required sides fail. |
| 2a | `confirmed_reference_label` returns the parsed candidate for a pending row | **3 failures** — all params of `test_asking_for_a_confirmation_on_a_pending_row_raises`. |
| 2b | Any non-pending source treated as confirmed (allow-list check removed) | **1 failure** — `test_an_unrecognised_source_is_never_read_as_a_confirmation`. |
| 3 | `propose_blot_id` keyed on the PMC accession instead of the full parent stem | **4 failures**, and `validate_blot_ids` fires first with the merge named: `{'PMC13135410_B': [('PMC13135410_Figure3','B'), ('PMC13135410_Figure4','B')]}`. |
| 4a | P2 entry count `Ten → Eleven` | `check_claims` fails: markers `[1..10]` "is not 1..11 in sequence", plus the deviation-difference hit. |
| 4b | P2 deviation count `twelve → thirteen` | fails: "a difference of 3 … should be 2". |
| 4c | Swap the `(9)` and `(10)` markers | fails: "appear first in the order [1,2,3,4,5,6,7,8,10,9]". |
| 4d–g | Each of the four "as of Phase 4a" sites moved independently | Each fails with both sites named. Confirmed genuinely two-sited across two files: `numbered P2 entries as of Phase 4a` = `DEBT.md:1139` + `docs/pr/phase-4a.md:118`; `deviations recorded in P2 as of Phase 4a` = the same two files. |
| bonus | Register composition `Seventeen/15 → Eighteen/14` | fails: "the register says 14 Open; 15 '**Status.** Open' lines exist" — confirming S19's flip to `Accepted` is what actually moved the split. |

Every mutation produced a failure. Note for the record: the two *current* P2 quantities (`"numbered P2 entries, current"`, `"deviations recorded in P2, current"`) each match a single site, and `check_numeric` only fires on disagreement between two or more — so those two `Quantity` entries are vacuous as consistency checks. That is not a defect, because `_check_p2_composition` measures the entry count from the markers and derives the deviation count via `P2_EXTRA_DEVIATIONS`, which mutations 4a–4c show is strictly stronger. The docstring already says so.

---

**VERDICT: CHANGES REQUIRED**

Three items. None is a behaviour defect in the collapse itself — W4's numeric core is the strongest part of this diff, tested on both sides of the bound synthetically *and* re-measured over all 19 committed crops on every run, with the refusing side tested as hard as the admitting side. Items 1 and 3 are claim-surface defects of the kind this project treats as first-class, and item 2 is a new abstraction whose stated purpose its own shape cannot serve.

Files central to the findings:
- `/Users/sofiapetrusenko/blotquant/NOTES.md` (lines 3249-3251)
- `/Users/sofiapetrusenko/blotquant/tools/phase3/crop_names.py` (lines 64-75)
- `/Users/sofiapetrusenko/blotquant/README.md` (lines 5, 161), `/Users/sofiapetrusenko/blotquant/pipeline/__main__.py` (line 28), `/Users/sofiapetrusenko/blotquant/api/display.py` (line 148)
