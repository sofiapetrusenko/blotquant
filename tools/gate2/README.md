# Gate 2 acquisition scripts

The scripts that produced `data/real/`, archived in the order they were used. **They are a
record of how the set was built, not maintained tooling** — they ran once, against a live
Europe PMC and a live PMC article CDN, and nothing in CI runs them.

| order | script | what it did |
|---|---|---|
| 1 | `gate2_epmc.py` | queried Europe PMC for open-licence articles with western-blot-looking figures; wrote the candidate table |
| 2 | `refilter.py` | narrowed candidates to CC BY with a lane-ish figure caption; produced the shortlist |
| 3 | `fetch_v2.py` | resolved each shortlisted figure to its CDN URL, downloaded it, recorded sha256, pixel size and a whole-figure colour fraction, and wrote `provenance.md` |
| 4 | `log_crops.py` | after cropping by hand, recorded each crop's sha256, pixel size and parent linkage into `crop_log.csv` |

`refetch_sources.py` is **not** one of the four. It was written afterwards, for this
repository, to make the uncommitted sources recoverable and the committed ones re-verifiable.
It is stdlib plus an optional `certifi` (see Dependencies); the four archived scripts are not.

## The two things that bit us

**1. PMC figure URLs cannot be constructed — they must be read from the article page.**
The article declares a figure by a bare filename (`jciinsight-11-200105-g294.jpg`), but the
bytes are served from a CDN path carrying an opaque per-blob hash:

```
https://cdn.ncbi.nlm.nih.gov/pmc/blobs/<opaque>/<pmcid>/<opaque>/<graphic>.jpg
```

There is no rule from the first to the second. `fetch_v2.py` therefore fetches the article
page and regexes out the `src` attributes, mapping bare filename to current CDN URL. That is
why `data/real/sources.csv` records `graphic_href` and `article_url` and **not** a figure URL:
a recorded CDN URL would have been a link that rots without saying so. `refetch_sources.py`
repeats the same resolution, which is the only reason recovery is possible at all.

**2. Preview's Cmd+S overwrites the source, and the sha256 chain caught it twice.**
Cropping was done by hand in Preview. Cmd+S there writes back to the *open* file — the source
figure — rather than exporting a new one, silently re-encoding the JPEG in the process. Twice
during Gate 2 a parent figure's sha256 stopped matching the value `fetch_v2.py` had recorded
at download, which is how the overwrite was noticed at all: the pixels look identical and
nothing else would have flagged it. Both times the source was re-downloaded and the crop
redone. **This is the whole argument for the parent-sha256 column in `crop_log.csv`**: a crop
whose parent has been silently re-encoded is a measurement against an artefact that no longer
exists, and without the chain it is undetectable. `tools/check_claims.py` now re-verifies both
halves of that chain on every CI run.

## Dependencies

`log_crops.py` imports **Pillow**, and `fetch_v2.py` uses it when present (guarded — it
degrades to recording no colour fraction). Pillow is **not** a project dependency and is not in
`requirements.txt`: the pipeline images through OpenCV, and adding a runtime dependency that
only an archived script imports would make that file's opening claim — "Runtime dependencies
actually imported by this repository" — false in the other direction. To re-run scripts 3 or 4,
`pip install Pillow` first. `gate2_epmc.py` and `refilter.py` are stdlib-only.

`refetch_sources.py` imports **certifi** optionally, and it is in `requirements.txt`. The first
networked run failed on every article URL with `CERTIFICATE_VERIFY_FAILED: self signed
certificate in certificate chain` — TLS interception on the operator's network re-signing traffic
with a CA the Python default store does not carry, not a PMC failure; the same URLs returned 200
under certifi's bundle, and curl reached them throughout with its own. The tool builds its
`SSLContext` from certifi when importable and from the system store otherwise, logging the
fallback. **Verification is never disabled**: every fetched byte is checked against a recorded
sha256, and fetching over a connection nobody authenticated before hashing the result would prove
only that the bytes match what *someone* served — which is the question the digest exists to
answer.

## Two of these scripts report failure with a success exit code

Found while fixing the same shape in `refetch_sources.py`, and recorded rather than fixed —
editing an archived script would make it differ from the one that produced `data/real/`:

- **`fetch_v2.py`** prints `N did not download:` and lists the failures, then `return 0`. A
  partial or total download failure exits successfully. The compensating control is that the
  count it reached is written into the artefact — `provenance.md` ends `Downloaded 21 of 21
  marked rows.` — so the record states the outcome even though the exit code did not.
- **`log_crops.py`** prints `!! <crop>: no parent match` and continues, then ends normally. A
  crop whose parent cannot be matched is silently omitted from `crop_log.csv` with a success
  exit. The compensating control is now `tools/check_claims.py`, which fails if a crop on disk
  is absent from the log or hashes differently.

Neither is load-bearing today: the digests in `crop_log.csv` and `sources.csv` are verified in
CI, so an omission or a partial download that slipped through would surface there. They are
listed because "the script exited 0" is not evidence either of them succeeded.

## What is not committed

`candidates.csv` (255 rows) and `shortlist.csv` are the intermediate selection tables, retained
outside the repository. What survives of them is `data/real/sources.csv`, which carries the 21
downloaded rows with full sha256, DOI, licence and the fields recovery needs. The rejected
candidates are not in the tree; the *rule* that rejected them is, in
`data/real/DECISION_unit_of_analysis.md` §§6–8, which is the part a reader needs to judge
whether the set was cherry-picked.
