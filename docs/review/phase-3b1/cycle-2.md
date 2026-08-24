# Phase 3b-1 — review cycle 2

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl`
> Source sha256: `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0`
> Location: JSONL record on line 638 of 1203 (~53% into the file), `type: user`, timestamp `2026-08-20T11:34:50.021Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-24. The source log is retained locally and is NOT committed.

---

## REQUIRED

**1. `api/app.py:71-72` (`ANALYZE_DESCRIPTION`) — the new sentence is false, and it is the one sentence a caller will act on.**

```
...the supplied lane ROIs and the reference-designation
source, so re-posting the identical request returns the same id. This API varies none of the
last three, so for its callers the id turns on the bytes and the config alone.
```

"The last three" are the reference band ids, the supplied lane ROIs, and the designation source. This API varies **two of them**: `POST /analyze` declares `lane_roi` and `reference_band_id` as Form fields (`api/app.py:320-329`) and `_analyse_upload` passes both straight into `analyze_image` (`api/app.py:261-265`). Only the designation source is not varied. So "the id turns on the bytes and the config alone" is wrong, and it is wrong in the direction that misleads: a caller who reads this concludes that two posts of the same image under the same config are the same result, then gets a different id the moment they change `reference_band_id`. The claim also contradicts the parameter list rendered directly beneath it in the same OpenAPI page. The pre-existing text was correct; the sentence added in this diff broke it. Nothing tests OpenAPI description prose, so only review catches this.

**2. `pipeline/__init__.py:34` and `NOTES.md:3193` — the 1.3.0 version history names one of the two fields the bump adds.**

`pipeline/__init__.py:34` opens "1.3.0 adds one *optional* field, ``source.channel_collapse``" and never mentions `normalization.reference_designation_source`, which this same diff adds to `schema/result.schema.json` as a second new optional field. `schema/result.schema.json:5` gets it right — it names both. So the two records of what version 1.3.0 *is* now disagree, and the disagreeing one is the module docstring that the file itself advertises as the version history ("NOTES.md's Phase 3b-1 section records the bump with this reasoning"). `NOTES.md:3193`'s section heading carries the same error ("goes to 1.3.0, for one optional field") and its body likewise discusses only `channel_collapse`; the designation source is described three sections later without ever being tied to the bump. This is precisely the duplicated-claim-goes-stale failure `tools/check_claims.py` exists for, introduced in the same diff that extends that checker.

**3. `pipeline/load.py:351` — W4 makes 12 JPEG-derived crops loadable, and every one of them will now assert `source.lossy_format: false`. The `lossy_format` QC flag and the `reference_band_lossy_format` warning are structurally unreachable across the entire real corpus, and nothing records it.**

`lossy_format` is derived from the *container* (`lossy_format=image_format in LOSSY_FORMATS`, LOSSY_FORMATS = {jpeg}). The 19 Gate 2 crops are PNG re-encodes of JPEG parents — `data/real/provenance.md:10-29` tags every single parent `;lossy_format_expected`. Confirmed by running the loader on a real crop:

```
lossy_format: False | bit_depth: 8 | collapse: ChannelCollapse(method='green', max_divergence_dn=0)
```

Consequence: `pipeline/qc.py:269`'s image-level `lossy_format` flag cannot fire on any real crop, and `normalize`'s `reference_band_lossy_format` warning cannot fire on any real ratio — on the one corpus where lossy provenance is *known and recorded* to apply. Before this phase the point was moot because no crop loaded. This diff makes it live and is silent about it, while carefully recording two other W4 consequences (the `result_id` change, the 16-bit refusal).

This is a **record** fix, not a code fix: a DEBT entry or a NOTES.md paragraph under Phase 3b-1. It must **not** be fixed by having the loader consult the crop log, the parent format, or anything else that special-cases the real set — that would be exactly the generator/artifact special-casing and the real-image-selects-a-code-path move the project forbids.

**4. `pipeline/load.py:229-239` and `:270-273` — three refusal branches added in this diff have no test at all.**

- `channel_divergence_dn`'s shape guard (`ndim != 3 or shape[2] != 3` → `UnsupportedImageError`) and its dtype guard (`:235`, → `UnsupportedBitDepthError`): zero coverage. The only test touching this function is `test_channel_divergence_is_measured_without_wrapping`. The dtype guard's entire stated justification is that the function is **public** and "gates its own input rather than relying on its caller's checks" — the loader itself can never reach it, because `_collapse_channels` requires `bit_depth == 8` first. So its only justification is a caller no test represents, and there is no evidence it fires.
- `_collapse_channels`'s float arm at `:270-273`. The code comment says outright that "Two different situations share this branch and the message distinguishes them" — and only the uint16 situation is asserted (`test_a_16_bit_three_channel_image_raises`, `match="8-bit"`). The float situation is reachable from a float32 3-channel TIFF and its bespoke remedy string ("This pixel type is not one this pipeline reads at any channel count") is never exercised.

CLAUDE.md: "pytest passes (failure modes, not just happy path)". A deliberately differentiated message that nothing asserts is untested code.

## SUGGESTED

- **`DEBT.md:1163-1180` — entries (9) and (10) are inserted *before* (8).** P2's Evidence paragraph now reads 1…7, 9, 10, 8. `_check_p2_composition` uses a set, so it passes, but the register is the artefact that is supposed to be readable.
- **`tools/check_claims.py::P2_MARKER` blind spot, measured.** I inserted a bare `(3)` into P2's prose: check stays green (absorbed into the existing set). A bare `(42)`: fails loudly with a clear message. So the failure mode is safe, but any bare `(N)` with N ≤ the stated entry count is silently swallowed. Anchoring the scan to the Evidence paragraph, or requiring first-occurrence markers in ascending order, would close it.
- **`tools/check_claims.py::_check_p2_composition`** does `P2_COMPOSITION.search(text)` over the whole file rather than over `section.group(0)`; a similar sentence elsewhere in DEBT.md would be matched in preference to P2's.
- **`P2_EXTRA_DEVIATIONS = 2`** encodes "entry (7) is the only multi-deviation entry," but nothing checks that premise against P2's text. The constant is a real invariant, not a fudge (see judgement below) — but its own basis is unchecked.
- **`tools/phase3/designations.py::_verify_crop_digest` raises `DesignationError` from `measurable_crops`, which `tools/phase3/blot_identity.py::build_blot_identities` also calls.** A caller of the identity builder catching `BlotIdentityError` will miss it. Both subclass `ValueError` so nothing breaks today, and both messages are actionable, but a shared `CropSetError` (or a re-export) would name the thing correctly.
- **The ground-truth write guard in `write_designations` / `write_blot_identities` has no test**, unlike the precedent for `create_app` at `tests/test_api.py:628`. Low risk — it delegates to `require_writable_destination`, which is directly and thoroughly tested at `tests/test_pipeline_result.py:490-570`.
- **`tools/phase3/crop_names.py:119-121`** paraphrases §2 as "a blot whose caption states **no loading control** is REJECTED"; §2 actually says "states **neither**" (neither a stated loading-control band nor stated stain-based loading). Narrower than the source. It is not in quotes, so this is a precision point, not a misquotation.
- **The API has no counterpart to `--reference-designation-source`.** An API caller can designate reference bands but cannot record where the designation came from. If that asymmetry is deliberate it deserves a line; the docstring currently only asserts (wrongly, per REQUIRED 1) that the API varies none of it.

## Mutation results

| # | Mutation | Result |
|---|---|---|
| 1 | `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 2 → 3` | **10 failures.** Synthetic: `test_divergence_above_the_bound_raises_and_names_the_measurement[3]`, `test_the_bound_is_not_moved_by_a_single_dn`. Real-set regression: both `test_a_crop_at_the_bound_collapses_and_records_the_bound[…]`, `…refusal_names_its_divergence[PMC13102869_Fig1__B-FXN-VCL.png]`, `test_exactly_twelve_of_nineteen_crops_load`. Plus the schema pin (`test_result_schema_and_loader_declare_the_same_collapse_rule`, `…accepts_a_collapse_at_the_bound`) and both derived tables. Caught from four independent directions. |
| 2a | `confirmed_reference_label` returns the parsed candidate for a pending row | **3 failures**, `test_asking_for_a_confirmation_on_a_pending_row_raises[×3]`, "DID NOT RAISE". |
| 2b | Deny-list: accept any non-pending source as confirmed | **1 failure**, `test_an_unrecognised_source_is_never_read_as_a_confirmation`. This is the test the docstring says a "not pending, therefore confirmed" reader passes everywhere else and fails here — it does. |
| 3 | `propose_blot_id` keyed on the PMC accession | **3 failures.** The direct one (`test_two_figures_of_one_article_do_not_share_a_proposed_id`) plus `validate_blot_ids` firing on the *real* data: `{'PMC13135410_B': [('PMC13135410_Figure3','B'), ('PMC13135410_Figure4','B')]}`. The corpus genuinely contains the collision, so the invariant is checked against real bytes and not only a synthetic pair. |
| 4a | P2 composition sentence → "Eleven … thirteen" (markers unchanged) | **Fires**, exit 1: "P2 says 11 numbered entries; the markers actually present are [1…10], which is not 1..11". |
| 4b | "twelve deviations" → "fourteen" (entries unchanged) | **Fires**: "a difference of 4 … should be 2. If another multi-deviation entry was added, move `P2_EXTRA_DEVIATIONS` in the same commit". |
| 4c | DEBT.md's historical figure changed to disagree with the Phase 4a PR body | **Fires**, 4 numeric-consistency hits naming both sites in both files. The historical quantities are genuinely two-sited. |
| 4d–f | Bare `(42)` in P2 prose / bare `(3)` in P2 prose | `(42)` fires loudly; `(3)` is absorbed silently. See SUGGESTED. |

Every mutation was restored and verified: `git diff --stat` empty, `git status --short --untracked-files=all` shows only the 23 staged paths. Final state: **777 passed**, `ruff check .` 0, `tools/check_claims.py` 0.

## Judgement on `tools/check_claims.py`

**Legitimate, and the result is stronger than what it replaced — not a weakened check.**

The diagnosis is correct and verifiable: DEBT.md P2's *running total* was pinned to the Phase 4a PR body's *historical transition*. Those are two quantities that happened to coincide, and the pin was a countdown to a false positive on an unchanged, correct document. Fixing the checker rather than the documents was the right call.

On your specific questions:

- **Is the running total still genuinely checked?** Yes, and by a better mechanism. `check_numeric` reports *disagreement between sites*, so the two "current" quantities — whose patterns both match the same sentence — are effectively single-site and would pass vacuously. But `_check_p2_composition` counts P2's actual `(N)` markers and requires exactly `1..N`. That is a tally measured from the register rather than compared against another sentence that can be stale the same way, which is what the old arrangement did. Mutations 4a and 4b confirm both halves bite and exit 1.
- **Does any quantity now have effectively a single assertion site?** The two "current" quantities do — but they are the ones `_check_p2_composition` covers mechanically, so nothing is unchecked. Both "as of Phase 4a" quantities are genuinely two-sited across two files (mutation 4c produced hits naming `DEBT.md:1139` and `docs/pr/phase-4a.md:118` for both). Retiring the PR-body patterns, which the inline comment says was considered and rejected, would have left an unchecked number in the tree; keeping them was right.
- **Is `P2_EXTRA_DEVIATIONS = 2` a real invariant or a fudge?** Real. It encodes a checkable fact about the register's structure (deviations exceed entries only by multi-deviation entries), it fails loudly when violated, and its docstring instructs moving it in the same commit as the prose. It is not tuned to make anything pass — 8 entries/10 deviations and 10/12 both satisfy it, and 10/14 fails. Its weakness is that its own premise ("entry (7) is the only multi-deviation entry") is asserted rather than checked.
- **Is the `(\d+)` scan over the whole P2 section vulnerable?** Partially, and asymmetrically in the safe direction: a bare `(N)` above the stated count fails loudly with an actionable message; a bare `(N)` at or below it is silently absorbed. Worth tightening; not a reason to reject.

One thing I want on the record: the sentence added to DEBT.md P2 exists partly so the historical figure keeps two agreeing sites. That is mildly circular — a claim written to satisfy a checker. But it is a true, useful sentence that a reader benefits from, and it is the mechanism this file was designed around (detect disagreement between duplicated claims), so I do not count it against the change.

## VERDICT: CHANGES REQUIRED

Also confirmed, on the items you flagged:

- **`crop_sha256` verification cannot fail open.** `_verify_crop_digest` runs before `load_image` on every crop-log row, raises on a missing file and on a mismatch, and a missing `crop_sha256` column raises `KeyError`. There is no path that skips it.
- **Narrowing to `UnsupportedImageError` behaves as intended.** `UnsupportedFormatError` and `UnsupportedBitDepthError` now propagate out of `measurable_crops` uncaught, aborting table generation with the pipeline's own actionable message rather than silently reclassifying a corrupt crop as an amendment exclusion and shrinking both tables' row counts. In practice `_verify_crop_digest` would fire first on a corrupt file, which is louder still.
- **Nothing in `api/` or `evals/` breaks on the bump.** `evals/run.py` reads only `source.lossy_format` and `source.max_value`. `api/app.py` has no hardcoded `1.2.0`; `_require_valid` and `get_result` already report the stale-stored-document case generically (`api/app.py:163-177`, the Phase 4a cycle-1 fix). The `result_id` change is recorded in NOTES.md, and nothing in the tree pins a literal id (`tests/test_api_display.py:144` uses a synthetic `0123456789abcdef`). The one unrecorded consequence I found is REQUIRED 1.
- **The implementer confirmed nothing they were not allowed to.** Every designation row carries one of the three pending values; every identity row is `proposed_pending_human`; the 2 DN bound and `green` are read from the ratified amendment; `configs/`, `synth/`, `data/ground_truth/`, `.claude/` and `DECISION_unit_of_analysis.md` are untouched (the digest pin passes). No shipped parameter moved.
- **Every number I checked is traceable.** 32 entry headings / 15 `Status. Open` / 17 settled, counted from the file. 10 P2 markers, 12 deviations. 13 distinct parent stems, 5 with more than one crop (computed from `crop_log.csv`). 12 designation rows, 2 non-`parsed_pending_human`. 10/2/6/1 crop groups re-measured from committed bytes on every test run. 2/65535 ≈ 0.003%, 2/255 ≈ 0.8%. The §2, §5 and §9 quotations are verbatim against `data/real/DECISION_unit_of_analysis.md`, and "minimum of 10 blots" is verbatim at `AMENDMENT_2026-08-19_delta_and_power.md:317`.
- **W7's absence is correct** and is not reported as missing work.
