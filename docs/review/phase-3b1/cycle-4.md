# Phase 3b-1 — review cycle 4

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl`
> Source sha256: `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0`
> Location: JSONL record on line 799 of 1203 (~66% into the file), `type: user`, timestamp `2026-08-20T12:15:57.017Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-24. The source log is retained locally and is NOT committed.

---

I reviewed the full staged diff, read the changed files and both ratified amendments in full, ran the verification commands, and applied every mutation.

## Verification

- `.venv/bin/ruff check .` — clean.
- `.venv/bin/python -m pytest -q` — **791 passed**, 123.75 s.
- `.venv/bin/python tools/check_claims.py` — OK over 16 files.
- `check_claims` still bites: mutating DEBT's `Seventeen … 15` produced *"the register says 14 Open; 15 '**Status.** Open' lines exist"*; mutating P2's `Ten … twelve` produced *"P2 says 11 numbered entries; the markers … appear first in the order [1..10]"*. `_check_p2_composition` counts markers out of the register rather than comparing one sentence to another, which is the stronger arrangement it claims to be.
- `data/real/DECISION_unit_of_analysis.md` and both amendments are digest-pinned and pass. No writes to `data/ground_truth/`, `.claude/`, `configs/`, or `synth/`. `pipeline/` imports nothing from `synth/`. `tools/phase3/` imports `pipeline`, not the reverse.

## Cycle-3 items — verified

1. **Unpinned numbers.** `/Users/sofiapetrusenko/blotquant/tests/test_real_channel_collapse.py::test_lossy_format_fires_on_no_crop_the_collapse_admits` re-measures all three figures NOTES.md asserts (12 loaded, `lossy_format` False on all 12, `image_format` `{"png"}`), and `test_every_approved_crop_has_a_jpeg_parent` pins the 19 `.jpg` parents. `tests/test_blot_identity.py::test_the_module_docstrings_shared_parent_count_is_measured_not_asserted` re-derives 13 parents / 5 shared from the frozen log. All four break under mutation (below). Real fix.
2. **`CropSetError` restructure.** `CorpusError`, `CropNameError`, `DesignationError`, `BlotIdentityError` are now siblings under an explicit catch-all base; the docstrings describe that arrangement accurately, including why the old one could not work. `_verify_crop_digest` raises `CorpusError` and is reached from both builders. Call sites catch correctly — `measurable_crops` catches only `UnsupportedImageError`, and I confirmed `UnsupportedBitDepthError` is a *sibling* under `PipelineError`, not a subclass, so the docstring's "they propagate" is true. Real fix.
3. **Widened-input surfaces.** README:5, README:161, `pipeline/__main__.py`'s `image` help and `api/display.py`'s error text are each accurate against the loader and the amendment; the `#limitations` anchor resolves to `## Limitations` (README:157); the CLI help renders correctly under `--help` (argparse rewraps the multi-line string). **Incomplete** — see REQUIRED 1.

## REQUIRED

1. **`/Users/sofiapetrusenko/blotquant/api/app.py:319` — the API's public statement of accepted input still asserts grayscale-only, after this diff widened what the API accepts.**

   ```python
   image: Annotated[UploadFile, File(description="8/16-bit grayscale TIFF, PNG or JPEG")],
   ```

   This is the same sentence as the CLI's `image` help, which cycle 3 required and this diff corrected to name the collapse. `_analyse_upload` (`api/app.py:261`) calls `load_image`, so `POST /analyze` now accepts a collapsible 3-channel upload exactly as the CLI does, and this description — the one that renders in the OpenAPI document and Swagger UI — is the surface a caller actually reads. `api/display.py`'s *internal-inconsistency* message was updated while the API's public input contract was not, which inverts the priority. Two further sites of the same class, both now over-broad:
   - `api/errors.py:89-91` — the comment justifying the 415 mapping says the upload is "an image this service cannot quantify: a container outside TIFF/PNG/JPEG, a pixel type outside uint8/uint16, **a multi-channel image**, or a payload that does not decode." A multi-channel image is no longer per se unquantifiable.
   - `pipeline/errors.py:28` — `UnsupportedImageError`: *"The input decodes to something other than a single-channel 2D image."* That is now the condition under which the class is **not** necessarily raised; the refusal is a multi-channel image *the ruled collapse does not admit*.

   Why it matters: this is precisely the defect cycle 3 raised, on the highest-visibility remaining instance of it. A caller reading the OpenAPI document concludes their colour PNG will be refused, and the mapping comment tells a maintainer the 415 covers a case it no longer covers.

## SUGGESTED

- **`README.md:161`'s `2 DN` is not tied to `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN`.** `tests/test_schema.py::test_result_schema_and_loader_declare_the_same_collapse_rule` binds the schema `maximum` to the loader constant; nothing binds the README. Adding the README's occurrences to a `check_claims` `Quantity`, or asserting them against the constant, would close the last hop.
- **`tools/phase3/blot_identity.py:58` `SECTION_9_QUOTE` is verbatim (I checked it against §9 modulo line wrapping) but nothing re-checks it.** The pre-registration is digest-pinned so it cannot drift, but a typo in the quote today would pass. A one-line whitespace-normalised containment test would make the quote a measurement.
- **No test posts a collapsible image through the API.** `api/display.py`'s new message asserts the loader hands the display path 2D pixels for every image it accepts; `tests/test_api_display.py:159` covers the refusal side only.
- **`read_designations` / `read_blot_identities` drop every line starting with `#` before parsing**, which would corrupt a quoted `notes` field containing an embedded newline followed by `#`. Not reachable today; a `csv.reader`-level skip would be sturdier.
- **`NOTES.md` "Two of the twelve rows carry something else"** is verified only transitively, via `test_the_committed_table_is_what_a_fresh_build_produces` plus the two per-row tests. A direct count of non-`PARSED_PENDING` rows would pin the sentence itself.
- **Out of scope, for the human:** `README.md:6` and `:9` are stale independently of this diff — `api/` is on `main` (commit `c6941ad`) while the status bullet still says Phase 4a is "staged on `phase-4a-api`, not yet merged", and the test counts read "529 on `main`, 627 on the unmerged `phase-4a-api`" against 791 now. `check_claims` does not pin either. This phase makes the test-count line further stale without touching it.

## Mutation outcomes

| # | Mutation | Result |
|---|---|---|
| 1 | `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 2 → 3` | **9 failed.** Synthetic: `test_divergence_above_the_bound_raises_and_names_the_measurement[3]`, `test_the_bound_is_not_moved_by_a_single_dn`. Real set: both `test_a_crop_at_the_bound_collapses_and_records_the_bound` params, `test_an_excluded_crop_is_still_refused…[PMC13102869_Fig1__B-FXN-VCL.png]`, `test_exactly_twelve_of_nineteen_crops_load`, `test_lossy_format_fires_on_no_crop_the_collapse_admits`. Plus 2 schema pins. |
| 2a | `confirmed_reference_label` returns the parsed candidate for a pending row | **3 failed** — all params of `test_asking_for_a_confirmation_on_a_pending_row_raises`. |
| 2b | drop the `CONFIRMED_SOURCES` allow-list check (any non-pending source = confirmed) | **1 failed** — `test_an_unrecognised_source_is_never_read_as_a_confirmation`. |
| 3 | `propose_blot_id` keyed on the PMC accession, not the full parent stem | **4 failed** — `test_two_figures_of_one_article_do_not_share_a_proposed_id`, plus three that trip `validate_blot_ids` (`PMC13135410_B` spanning Figure3/B and Figure4/B). |
| 4a-i | one crop reports `lossy_format=True` | **1 failed** — `test_lossy_format_fires_on_no_crop_the_collapse_admits` (line 199). |
| 4a-ii | one crop reports `image_format="jpeg"` | **1 failed** — same test, line 200. |
| 4b | parent expectation `13/5 → 14/6`; parent suffix `.jpg → .png` | **2 failed** — `test_the_module_docstrings_shared_parent_count_is_measured_not_asserted`, `test_every_approved_crop_has_a_jpeg_parent`. |

Every mutation produced a failure; none passed vacuously. All restored via `git checkout --`; `git diff` is empty and `git status --short` matches the opening snapshot exactly (25 entries, unchanged).

## VERDICT: CHANGES REQUIRED

One item, narrow and mechanical. W7 correctly not done; nothing in either CSV is confirmed; the 6 excluded crops and E-TIGAR are asserted refused over the real set; the bound, method and provenance field name match the ratified amendment; `configs/`, `synth/` and `data/ground_truth/` are untouched.
