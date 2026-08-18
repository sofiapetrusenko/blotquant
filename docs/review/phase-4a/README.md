# Phase 4a — review record

**These files are verbatim extracts from Claude Code session logs, made on 2026-08-18.**
Each `cycle-N.md` contains the exact text of one reviewer's verdict — its REQUIRED items,
their wording, and its final count — copied byte for byte from the session log, including
any typos. Nothing here is paraphrased, summarised, or corrected.

**The raw logs are retained locally and are not committed**, because they contain private
conversation content well beyond the review record. They are held under `.review-sources/`,
which is gitignored. Their sha256s:

| file | sha256 | covers |
|---|---|---|
| `e1cac5de-37df-4dc3-b3c1-ecaa0100139b.jsonl` | `63af2401073c7616a6f8b9f73169a78830c7f160bfd8c4fb8a86bbf6f292338e` | **all six Phase 4a cycles**; branch `phase-4a-api`, 2026-08-17 to 2026-08-18 |
| `179c84b1-9bc5-4de2-ab0b-61888eab5c32.jsonl` | `9c24f2f9d6b4162d73dc90054af3f863eb3e96beab3aee777fad956a08bbaa66` | Phases 1–3 and the docs branches, 2026-08-11 to 2026-08-14 — **no Phase 4a content** |
| `f18d5865-a8c1-4f53-b404-c0c103eb3743.jsonl` | `86525c3801897bc9088a39220c1efab0e61b86576d0f36643bdca6e05d5f7881` | Phase 0, 2026-08-10 to 2026-08-11 — **no Phase 4a content** |

Three logs were supplied as the source. Only one of them turned out to contain any Phase 4a
review cycle; the other two are recorded above with what they do cover, so that a reader is
not left to assume all three contributed. All six cycles come from a single file.

**Any cycle that could not be found in the logs is listed below as "not recovered", never
reconstructed from memory.** All six were found.

## How the extraction was located

Each reviewer ran as a subagent. Its output reached the session inside a
`<task-notification>` block, and what each `cycle-N.md` reproduces is the exact content of
that block's `<result>` element. Records were found by scanning for `type: user` entries on
branch `phase-4a-api` containing both `VERDICT` and `REQUIRED`; six matched, and every one
is reproduced. Each file's header names its source line number and timestamp. The extracted
bodies were re-compared against the source after writing and are byte-identical.

## Index

| cycle | date (UTC) | REQUIRED | what was contradicted | source |
|---|---|---|---|---|
| [1](cycle-1.md) | 2026-08-17 | **6** | empty `lane_rois` silently re-enabling detection; dead `as_response_block` duplicating the envelope; false `NormalizationError`→400 justification routing internal defects to the caller; `_require_valid`'s message wrong on the `GET` path; three untested guarantees; NOTES.md asserting a carry-forward it had not performed | log line 246 |
| [2](cycle-2.md) | 2026-08-17 | **2** | NOTES.md documenting empty-ROI behaviour the code no longer had, justified by a false FastAPI claim; a sub-2px lane rectangle dying with a message naming neither the rectangle nor a remedy the caller has | log line 317 |
| [3](cycle-3.md) | 2026-08-17 | **3** | the minimum-extent bound's smoothing half pinned by no test (proved by reverting it and observing all 605 tests still pass); DEBT.md's "Only P2 has been updated" false in the same diff; `errors="replace"` silently repairing a damaged store | log line 396 |
| [4](cycle-4.md) | 2026-08-17 | **4** | an unmeasured "about two seconds" timing justifying the synchronous design; DEBT P2's "open Phase 3 items" list wrong in both directions; `ResultStore.save`'s byte-for-byte identity claim false; DEBT E10 claiming documentation that does not exist | log line 507 |
| [5](cycle-5.md) | 2026-08-17 | **2** | OpenAPI text promising an identical document on an identical request; the gold set's dimensions transposed to 192×256 | log line 639 |
| [6](cycle-6.md) | 2026-08-18 | **4** | DEBT S18's edge-lane width given as 8px where the ROI construction yields 7; the PR body's stale "six → seven deviations"; DEBT P1's "none of them was a figure" contradicted by the same diff; a test docstring giving the top display bin as 257 DN where it is 129 | log line 835 |  <!-- claims-check: allow-retracted -->

**Totals: 21 REQUIRED across six cycles; 17 across the five that fell within PLAN.md's cap.**
No cycle returned zero. Cycle 6 was a deliberate one-cycle extension past that cap, narrowed
to claim surfaces (DEBT.md P2 entry 8).

## What the record shows about behaviour versus claims

Cycles 4 and 5 classified every one of their six items as a claim defect rather than a
behaviour defect, in their own words:

- **Cycle 4:** "All four REQUIRED items are in the design record or in docstrings that
  justify behaviour, not in the measurement path — no number this project reports is wrong
  because of them."
- **Cycle 5:** "two items, both in claim-text rather than behaviour, both small … neither
  touches a measured number."

Both cycles reviewed the full diff with behaviour in scope, so this is a finding rather than
an artefact of scoping. **One qualification a reader should not have to dig for:** cycle 4's
item 3 was classified as a false docstring claim, but the fact it uncovered — that two POSTs
of identical bytes under different filenames share a `result_id`, produce different
documents, and silently overwrite one another — is a behavioural property, recorded as
DEBT E10 item 3. It was reported as a wrong claim, not as a defect to fix, and the
distinction is the reviewer's rather than this file's.
