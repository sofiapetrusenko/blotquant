"""The ruled channel collapse, asserted against the 19 approved Gate 2 crops themselves.

This is the regression that matters for DEBT S19. The synthetic boundary tests in
``tests/test_pipeline_load.py`` prove the rule is implemented; this file proves it is
implemented *over the set the ruling was written about*, and in particular that the seven
crops the 2026-08-19 §7 amendment refuses are still refused after the loader learned to
collapse. A rule whose refusing side is untested is not enforced, and the refusing side is
where this amendment's load-bearing sentence lives: the 2 DN bound was named before the
divergence table was measured and is not moved to admit an image.

**What is pinned here, and where it comes from.** The group counts and boundaries are the
amendment's own, read from its ratified text: 10 crops byte-identical, 2 at the bound (both
named in ruling (a)), 6 between 3 DN and 43 DN (ruling (c)), 1 at 255 DN (ruling (d), which
names the crop). The per-crop assignment below is not quoted from anywhere -- the amendment
deliberately left its per-crop table uncommitted, because a measurement in the tree that no
step re-measures is a stale claim waiting to happen. This test *is* that step: it re-measures
every crop from the committed bytes on every run, so the assignment is checked rather than
asserted.

Read-only throughout. Nothing here writes to ``data/real/``, and Gate 1 ruling 3 applies with
full force: a failure of this file falsifies the loader, and may never be repaired by moving
the bound.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from pipeline.errors import UnsupportedImageError
from pipeline.load import CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN, load_image
from tools.phase3.blot_identity import SECTION_9_QUOTE

CROPS = Path(__file__).resolve().parents[1] / "data/real/crops"
CROP_LOG = CROPS / "crop_log.csv"

BYTE_IDENTICAL_CROPS: frozenset[str] = frozenset(
    {
        "PMC12686555_FIGURE1__A-p16p21-ACTIN.png",
        "PMC12686555_FIGURE2__C-HSPA12B-ACTIN.png",
        "PMC12956003_Figure2__A-Htt-tub.png",
        "PMC13017922_Figure8__D-ABCB1-Actin.png",
        "PMC13025488_Figure5__C-pSMAD-GAPDH.png",
        "PMC13135388_Figure4__E-Vinculin.png",
        "PMC13135410_Figure3__B-Fib-CCN2-GAPDH.png",
        "PMC13135410_Figure4__A-PDGFRa-GAPDH.png",
        "PMC13135410_Figure4__B-PDGFRa-GAPDH.png",
        "PMC13135410_Figure4__C-PDGFRa-GAPDH.png",
    }
)
"""The 10 crops whose three planes are byte-identical. Amendment table, row 1."""

AT_THE_BOUND_CROPS: frozenset[str] = frozenset(
    {
        "PMC12708318_Figure5__A1-GSDME-actin.png",
        "PMC12895598_Fig3__A-EMT-GAPDH.png",
    }
)
"""The 2 crops at the bound. Both are named in the amendment's ruling (a), verbatim."""

EXCLUDED_ABOVE_BOUND_CROPS: frozenset[str] = frozenset(
    {
        "PMC12686555_FIGURE2__K-p53p21-ACTIN.png",
        "PMC12867876_Figure1__F-Env-actin.png",
        "PMC12886003_Figure1__A-H3cr-actin.png",
        "PMC12886003_Figure1__C-H3cr-H3.png",
        "PMC13102869_Fig1__B-FXN-VCL.png",
        "PMC13102869_Fig1__C-TRKB-VCL.png",
    }
)
"""The 6 crops ruling (c) excludes, at 3 DN to 43 DN. Excluded, with disclosure."""

REAL_COLOUR_CROP = "PMC13135388_Figure4__E-TIGAR.png"
"""The crop ruling (d) rejects outright: a fully saturated colour pixel, 255 DN."""

MEASURABLE_CROPS: frozenset[str] = BYTE_IDENTICAL_CROPS | AT_THE_BOUND_CROPS
REFUSED_CROPS: frozenset[str] = EXCLUDED_ABOVE_BOUND_CROPS | {REAL_COLOUR_CROP}

pytestmark = pytest.mark.skipif(
    not CROP_LOG.is_file(), reason="the real-blot crop set is not present in this checkout"
)


def _approved_crops() -> list[str]:
    """Return the approved crop filenames from the frozen Gate 2 record, not from a glob.

    ``crop_log.csv`` is what Gate 2 approved; the directory listing is merely what is on
    disk. Reading the log means a crop added to the directory without passing the gate makes
    this test fail rather than quietly joining the set.
    """
    with CROP_LOG.open(newline="") as handle:
        return [row["crop"] for row in csv.DictReader(handle)]


def test_the_partition_covers_the_approved_set_exactly() -> None:
    """The three groups partition the crop log: nothing double-counted, nothing unassigned."""
    approved = _approved_crops()

    assert len(approved) == 19
    assert set(approved) == MEASURABLE_CROPS | REFUSED_CROPS
    assert not MEASURABLE_CROPS & REFUSED_CROPS
    assert (len(BYTE_IDENTICAL_CROPS), len(AT_THE_BOUND_CROPS)) == (10, 2)
    assert (len(EXCLUDED_ABOVE_BOUND_CROPS), len(MEASURABLE_CROPS)) == (6, 12)


@pytest.mark.parametrize("crop", sorted(BYTE_IDENTICAL_CROPS))
def test_a_byte_identical_crop_collapses_and_records_zero(crop: str) -> None:
    """Ruling (a), first case: identical planes collapse, and 0 DN is recorded, not omitted."""
    loaded = load_image(CROPS / crop)

    assert loaded.pixels.ndim == 2
    assert loaded.channel_collapse is not None
    assert loaded.channel_collapse.method == "green"
    assert loaded.channel_collapse.max_divergence_dn == 0


@pytest.mark.parametrize("crop", sorted(AT_THE_BOUND_CROPS))
def test_a_crop_at_the_bound_collapses_and_records_the_bound(crop: str) -> None:
    """Ruling (a), second case: these two crops sit exactly on the bound and are admitted."""
    loaded = load_image(CROPS / crop)

    assert loaded.channel_collapse is not None
    assert loaded.channel_collapse.max_divergence_dn == CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN


@pytest.mark.parametrize("crop", sorted(EXCLUDED_ABOVE_BOUND_CROPS))
def test_an_excluded_crop_is_still_refused_and_the_refusal_names_its_divergence(
    crop: str,
) -> None:
    """Ruling (c): the six crops above the bound stay refused after the loader change.

    The refusal must name a divergence in the amendment's stated 3-43 DN range. Asserting the
    range rather than a per-crop constant keeps this test to what the ratified text says,
    while still failing if a crop drifts into the admitted band.
    """
    with pytest.raises(UnsupportedImageError, match="single-channel") as raised:
        load_image(CROPS / crop)

    message = str(raised.value)
    # Parsed, not substring-matched: "43 DN" contains "3 DN", so a membership test over the
    # range would pass on a message naming almost anything in it.
    measured = int(re.search(r"divergence is (\d+) DN", message).group(1))
    assert 3 <= measured <= 43, f"outside the amendment's stated range; got: {message}"
    assert f"{CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN} DN" in message


def test_the_pseudocoloured_crop_is_still_refused_at_full_scale() -> None:
    """Ruling (d): E-TIGAR carries a fully saturated colour pixel and is rejected under §7."""
    with pytest.raises(UnsupportedImageError, match="single-channel") as raised:
        load_image(CROPS / REAL_COLOUR_CROP)

    assert "255 DN" in str(raised.value)


def test_exactly_twelve_of_nineteen_crops_load() -> None:
    """The amendment's consequence (e), asserted as a count over the whole approved set.

    Stated as a count rather than only per-crop because the count is the quantity the phase
    turns on: if this is ever 13, a bound was moved.
    """
    loaded, refused = [], []
    for crop in _approved_crops():
        try:
            load_image(CROPS / crop)
        except UnsupportedImageError:
            refused.append(crop)
        else:
            loaded.append(crop)

    assert set(loaded) == MEASURABLE_CROPS
    assert set(refused) == REFUSED_CROPS
    assert (len(loaded), len(refused)) == (12, 7)


def test_lossy_format_fires_on_no_crop_the_collapse_admits() -> None:
    """The W4 consequence NOTES.md records, re-measured rather than asserted.

    §6 of the pre-registration expected ``lossy_format`` to fire on the entire real set, because
    every parent figure is distributed as JPEG. §9 then required crops exported as PNG, and the
    flag is derived from the container the pipeline is handed -- so it fires on none of them, and
    the image-level flag and normalization's ``reference_band_lossy_format`` warning are
    unreachable across this corpus. DEBT draft D7 predicted it and could not check it, because no
    crop loaded; W4 makes it checkable and this is the check.

    Every figure NOTES.md quotes for it is measured here, not just the count: a paragraph whose
    numbers no step re-measures is the class of stale claim this project defines as a defect, and
    the divergence figures next door already get this treatment.
    """
    loaded = []
    for crop in _approved_crops():
        try:
            loaded.append(load_image(CROPS / crop))
        except UnsupportedImageError:
            continue

    assert len(loaded) == 12
    assert [image.lossy_format for image in loaded] == [False] * 12
    assert {image.image_format for image in loaded} == {"png"}


def test_every_approved_crop_has_a_jpeg_parent() -> None:
    """The other half of the same finding: the loss is real and is upstream of the file we see."""
    with CROP_LOG.open(newline="") as handle:
        parents = [row["parent"] for row in csv.DictReader(handle)]

    assert len(parents) == 19
    assert all(parent.endswith(".jpg") for parent in parents)


def test_the_readme_states_the_bound_the_loader_enforces() -> None:
    """The last unpinned hop: the number a *user* reads against the number the code applies.

    The schema's ``maximum`` is already bound to the loader constant by
    ``tests/test_schema.py``. The README is the surface a user decides against, and until this
    test it restated the bound with nothing tying it to anything -- which is how a limitation
    section comes to describe the rule a tool used to have.
    """
    readme = (CROPS.parents[2] / "README.md").read_text(encoding="utf-8")
    bullet = next(
        line for line in readme.splitlines()
        if line.startswith("- **Single-channel grayscale chemiluminescence only.**")
    )

    assert f"at most {CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN} DN" in bullet
    assert "green channel" in bullet
    assert "still raises" in bullet, "and that everything else multi-channel is refused"


def test_the_section_9_quote_is_verbatim_against_the_frozen_pre_registration() -> None:
    """`blot_identity.csv`'s header quotes §9 to report a conflict; a quote must be a quote.

    The pre-registration is digest-pinned so it cannot drift, but a typo introduced on this side
    today would pass every other check. Compared with whitespace normalised, because the quote is
    rewrapped to fit a CSV comment line.
    """
    decision = (CROPS.parents[2] / "data/real/DECISION_unit_of_analysis.md").read_text(
        encoding="utf-8"
    )

    assert " ".join(SECTION_9_QUOTE.split()) in " ".join(decision.split())
