"""Loading: what each container yields, and every input that must raise."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest
import tifffile

from pipeline.errors import (
    UnsupportedBitDepthError,
    UnsupportedFormatError,
    UnsupportedImageError,
)
from pipeline.load import (
    BRIGHT_ON_DARK,
    CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN,
    channel_divergence_dn,
    detect_format,
    load_image,
)


def _ramp(height: int, width: int, dtype: type[np.unsignedinteger]) -> np.ndarray:
    """Return a deterministic non-constant test image of the requested dtype."""
    values = np.arange(height * width, dtype=np.uint64).reshape(height, width)
    return (values % np.iinfo(dtype).max).astype(dtype)


def test_tiff16_round_trips_exactly(tmp_path: Path) -> None:
    """A 16-bit TIFF loads as uint16 with its own values, unscaled."""
    array = _ramp(24, 32, np.uint16)
    path = tmp_path / "blot.tiff"
    tifffile.imwrite(str(path), array, photometric="minisblack")

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert loaded.image_format == "tiff"
    assert loaded.bit_depth == 16
    assert loaded.max_value == 65535
    assert loaded.lossy_format is False
    assert loaded.width_px == 32
    assert loaded.height_px == 24
    assert loaded.pixels.dtype == np.uint16
    np.testing.assert_array_equal(loaded.pixels, array)


def test_png8_round_trips_exactly(tmp_path: Path) -> None:
    """An 8-bit PNG loads as uint8 with its own values."""
    array = _ramp(16, 20, np.uint8)
    path = tmp_path / "blot.png"
    assert cv2.imwrite(str(path), array)

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert (loaded.image_format, loaded.bit_depth, loaded.max_value) == ("png", 8, 255)
    assert loaded.lossy_format is False
    np.testing.assert_array_equal(loaded.pixels, array)


def test_jpeg_is_flagged_lossy_and_stays_8_bit(tmp_path: Path) -> None:
    """A JPEG loads as 8-bit and is reported as a lossy container."""
    array = np.full((16, 16), 120, dtype=np.uint8)
    path = tmp_path / "blot.jpg"
    assert cv2.imwrite(str(path), array, [cv2.IMWRITE_JPEG_QUALITY, 75])

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert (loaded.image_format, loaded.bit_depth) == ("jpeg", 8)
    assert loaded.lossy_format is True
    assert loaded.pixels.dtype == np.uint8


def test_format_comes_from_content_not_extension(tmp_path: Path) -> None:
    """A PNG named .tiff is still loaded, and reported, as a PNG."""
    array = _ramp(12, 12, np.uint8)
    misnamed = tmp_path / "actually_a_png.tiff"
    encoded = cv2.imencode(".png", array)[1].tobytes()
    misnamed.write_bytes(encoded)

    loaded = load_image(misnamed, BRIGHT_ON_DARK)

    assert loaded.image_format == "png"
    np.testing.assert_array_equal(loaded.pixels, array)


def test_sha256_is_the_file_digest(tmp_path: Path) -> None:
    """The recorded digest is of the file bytes and has the schema's prefix."""
    import hashlib

    array = _ramp(8, 8, np.uint8)
    path = tmp_path / "blot.png"
    assert cv2.imwrite(str(path), array)

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert loaded.sha256 == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_float_tiff_raises_unsupported_bit_depth(tmp_path: Path) -> None:
    """Float pixels raise rather than being rescaled to an integer range."""
    path = tmp_path / "float.tiff"
    tifffile.imwrite(str(path), np.zeros((8, 8), dtype=np.float32))

    with pytest.raises(UnsupportedBitDepthError, match="float32"):
        load_image(path, BRIGHT_ON_DARK)


def test_int32_tiff_raises_unsupported_bit_depth(tmp_path: Path) -> None:
    """A 32-bit integer TIFF raises rather than being squashed to 16-bit."""
    path = tmp_path / "wide.tiff"
    tifffile.imwrite(str(path), np.zeros((8, 8), dtype=np.uint32))

    with pytest.raises(UnsupportedBitDepthError, match="uint32"):
        load_image(path, BRIGHT_ON_DARK)


def _three_channel(divergence: int) -> np.ndarray:
    """Return an 8x8 3-channel image whose maximum channel divergence is exactly ``divergence``.

    The outer two planes hold a non-constant ramp and green holds that ramp offset by
    ``divergence``, so ``|R-G|`` and ``|G-B|`` are ``divergence`` at every pixel and ``|R-B|``
    is zero. Green therefore differs from both of its neighbours *everywhere* rather than at
    one pixel, which is what lets a test assert that the collapse took green specifically and
    not merely that it took some plane.

    At ``divergence`` 0 the three planes are byte-identical, which is the amendment's first
    admissible case rather than a degenerate fixture. The offset is clipped at full scale, so
    a divergence of 255 is built by saturating green against a ramp that starts at 0 rather
    than by wrapping past it.
    """
    ramp = np.arange(64, dtype=np.uint8).reshape(8, 8) % 40
    array = np.stack([ramp, ramp, ramp], axis=2)
    array[:, :, 1] = np.clip(ramp.astype(np.int32) + divergence, 0, 255).astype(np.uint8)
    return array


def _write_png(path: Path, array: np.ndarray) -> Path:
    """Write ``array`` as a lossless PNG and return the path."""
    assert cv2.imwrite(str(path), array)
    return path


def test_channel_divergence_is_measured_without_wrapping() -> None:
    """A 255 DN divergence reports 255, not the 1 an unsigned subtraction would wrap to.

    The one crop in the Gate 2 set whose divergence is 255 is precisely the crop the §7
    amendment rejects, so a wrapped difference here would admit the image the ruling names.
    """
    array = np.zeros((2, 2, 3), dtype=np.uint8)
    array[0, 0, 0] = 255

    assert channel_divergence_dn(array) == 255


@pytest.mark.parametrize(
    "array",
    [
        np.zeros((8, 8), dtype=np.uint8),
        np.zeros((8, 8, 4), dtype=np.uint8),
        np.zeros((8, 8, 2), dtype=np.uint8),
    ],
    ids=["2d", "rgba", "two-channel"],
)
def test_channel_divergence_refuses_a_shape_the_ruling_does_not_cover(
    array: np.ndarray,
) -> None:
    """The measurement is defined for three channels, so it refuses anything else.

    ``channel_divergence_dn`` is public and reachable without going through the loader, so its
    own guards are tested rather than left to a caller's checks.
    """
    with pytest.raises(UnsupportedImageError, match="3-channel array"):
        channel_divergence_dn(array)


@pytest.mark.parametrize("dtype", [np.float32, np.int64, np.int16])
def test_channel_divergence_refuses_a_pixel_type_it_would_have_to_truncate(
    dtype: type[np.generic],
) -> None:
    """Widening a float or a 64-bit array to int32 truncates, and a truncated divergence reads low.

    Reading low is the direction that *admits* an image the ruling refuses, which is why this
    raises rather than measuring approximately. The loader cannot reach this branch -- it checks
    bit depth first -- so this is the test that represents the public caller the guard exists for.
    """
    with pytest.raises(UnsupportedBitDepthError, match="uint8 and uint16"):
        channel_divergence_dn(np.zeros((8, 8, 3), dtype=dtype))


def test_a_float_three_channel_image_is_refused_and_is_not_told_to_export_a_channel(
    tmp_path: Path,
) -> None:
    """The float arm of the bit-depth branch says something different from the 16-bit arm.

    A 16-bit 3-channel image *can* be fixed by exporting one channel; a float32 one cannot,
    because a single float32 channel raises on bit depth anyway. The message distinguishes the
    two, and a differentiated message nothing asserts is untested code.
    """
    path = tmp_path / "float.tiff"
    tifffile.imwrite(str(path), np.zeros((8, 8, 3), dtype=np.float32), photometric="rgb")

    with pytest.raises(UnsupportedImageError) as raised:
        load_image(path, BRIGHT_ON_DARK)

    message = str(raised.value)
    assert "not one this pipeline reads at any channel count" in message
    assert "Export a single" not in message, "that remedy is for the 16-bit arm, not this one"


@pytest.mark.parametrize("divergence", [0, 1, 2])
def test_divergence_at_or_below_the_bound_collapses_to_green(
    tmp_path: Path, divergence: int
) -> None:
    """0, 1 and 2 DN all collapse: the ruled bound is inclusive, and 0 is not a special case.

    2 DN is the bound itself and is the value two real crops carry, so the inclusive side of
    the boundary is exercised by a value the ruling actually admits.
    """
    array = _three_channel(divergence)
    path = _write_png(tmp_path / "colour.png", array)

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert loaded.pixels.ndim == 2
    assert loaded.pixels.dtype == np.uint8
    np.testing.assert_array_equal(loaded.pixels, array[:, :, 1])
    assert loaded.channel_collapse is not None
    assert loaded.channel_collapse.method == "green"
    assert loaded.channel_collapse.max_divergence_dn == divergence


def test_the_collapse_takes_green_and_not_a_neighbouring_plane(tmp_path: Path) -> None:
    """The plane taken is green specifically -- an off-by-one index would pass the shape checks.

    Written as an inequality against the other two planes rather than only as an equality
    against green, because on an image whose planes agree everywhere both assertions pass and
    neither one is testing the index.
    """
    array = _three_channel(2)
    path = _write_png(tmp_path / "colour.png", array)

    loaded = load_image(path, BRIGHT_ON_DARK)

    np.testing.assert_array_equal(loaded.pixels, array[:, :, 1])
    assert not np.array_equal(loaded.pixels, array[:, :, 0])
    assert not np.array_equal(loaded.pixels, array[:, :, 2])


def test_green_is_taken_through_a_decoder_that_does_not_use_bgr(tmp_path: Path) -> None:
    """The same claim, written by a different library, so it is not cv2 round-tripping itself.

    ``_three_channel`` is written and read back through OpenCV, which hands planes over as BGR
    in both directions -- so a test built on it pins "index 1" rather than "the file's green
    channel". ``tifffile`` writes and returns RGB. Green is the middle plane under both
    orderings, which is exactly the property :data:`GREEN_CHANNEL_INDEX` claims, and this is the
    test that checks it against a second ordering rather than assuming it.

    Values 10/11/12 give |R-G| = 1, |G-B| = 1 and |R-B| = 2, so the divergence is at the bound
    and every plane holds a distinct constant.
    """
    array = np.zeros((8, 8, 3), dtype=np.uint8)
    array[:, :, 0], array[:, :, 1], array[:, :, 2] = 10, 11, 12
    path = tmp_path / "rgb.tiff"
    tifffile.imwrite(str(path), array, photometric="rgb")

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert loaded.channel_collapse is not None
    assert loaded.channel_collapse.max_divergence_dn == 2
    assert np.all(loaded.pixels == 11), "the green plane, not the red (10) or the blue (12)"


def test_the_collapsed_plane_is_contiguous(tmp_path: Path) -> None:
    """The collapse hands back a plain 2D array, not a strided view of the decode buffer."""
    path = _write_png(tmp_path / "colour.png", _three_channel(0))

    assert load_image(path, BRIGHT_ON_DARK).pixels.flags["C_CONTIGUOUS"]


@pytest.mark.parametrize("divergence", [3, 43, 255])
def test_divergence_above_the_bound_raises_and_names_the_measurement(
    tmp_path: Path, divergence: int
) -> None:
    """3 DN -- one above the bound -- refuses, as do the real set's 43 DN and 255 DN cases.

    The message must name *both* the measured divergence and the bound: a refusal that says
    only "not single-channel" leaves whoever reads it unable to tell a crop that missed by
    1 DN from one that is genuinely in colour, which is the distinction the amendment's
    rulings (c) and (d) turn on.
    """
    path = _write_png(tmp_path / "colour.png", _three_channel(divergence))

    with pytest.raises(UnsupportedImageError, match="single-channel") as raised:
        load_image(path, BRIGHT_ON_DARK)

    message = str(raised.value)
    assert f"{divergence} DN" in message, "the refusal must name what it measured"
    bound = f"{CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN} DN"
    assert bound in message, "and the bound it measured against"


def test_the_bound_is_not_moved_by_a_single_dn(tmp_path: Path) -> None:
    """The pair either side of the boundary, asserted together in one test.

    Both halves in one place so that a change relaxing the comparison to `<` or widening the
    bound cannot be made to pass by editing one test and leaving its partner green.
    """
    inside = _write_png(tmp_path / "inside.png", _three_channel(2))
    outside = _write_png(tmp_path / "outside.png", _three_channel(3))

    assert load_image(inside, BRIGHT_ON_DARK).channel_collapse is not None
    with pytest.raises(UnsupportedImageError):
        load_image(outside, BRIGHT_ON_DARK)


def test_a_four_channel_image_raises(tmp_path: Path) -> None:
    """RGBA is outside the ruling entirely and is refused, not collapsed on three of four."""
    path = _write_png(tmp_path / "rgba.png", np.zeros((8, 8, 4), dtype=np.uint8))

    with pytest.raises(UnsupportedImageError, match="single-channel"):
        load_image(path, BRIGHT_ON_DARK)


def test_a_16_bit_three_channel_image_raises(tmp_path: Path) -> None:
    """The ruled bound is stated in 8-bit DN, so it is not extrapolated to 16-bit input."""
    path = tmp_path / "deep.tiff"
    tifffile.imwrite(str(path), np.zeros((8, 8, 3), dtype=np.uint16), photometric="rgb")

    with pytest.raises(UnsupportedImageError, match="8-bit"):
        load_image(path, BRIGHT_ON_DARK)


def test_a_single_channel_image_records_no_collapse(tmp_path: Path) -> None:
    """No collapse block on an image that never needed one -- absence is the record."""
    path = _write_png(tmp_path / "grey.png", _ramp(8, 8, np.uint8))

    loaded = load_image(path, BRIGHT_ON_DARK)

    assert loaded.channel_collapse is None
    assert "channel_collapse" not in loaded.as_source()


def test_the_source_block_carries_the_collapse_in_the_ruled_shape(tmp_path: Path) -> None:
    """Provenance carries exactly the two fields the amendment writes, and their values."""
    path = _write_png(tmp_path / "colour.png", _three_channel(2))

    source = load_image(path, BRIGHT_ON_DARK).as_source()

    assert source["channel_collapse"] == {"method": "green", "max_divergence_dn": 2}


def test_the_digest_still_identifies_the_file_as_delivered(tmp_path: Path) -> None:
    """sha256 is of the bytes on disk, not of the collapsed plane.

    The amendment is explicit that the collapse happens on the way in and rewrites no
    approved artefact; a digest taken after the collapse would no longer match the crop log.
    """
    array = _three_channel(0)
    path = _write_png(tmp_path / "colour.png", array)
    expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    assert load_image(path, BRIGHT_ON_DARK).sha256 == expected


def test_unsupported_container_raises(tmp_path: Path) -> None:
    """A container outside TIFF/PNG/JPEG raises with the supported list."""
    path = tmp_path / "blot.bmp"
    assert cv2.imwrite(str(path), np.zeros((8, 8), dtype=np.uint8))

    with pytest.raises(UnsupportedFormatError, match="tiff"):
        load_image(path, BRIGHT_ON_DARK)


def test_empty_file_raises(tmp_path: Path) -> None:
    """An empty file raises a format error naming the problem."""
    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    with pytest.raises(UnsupportedFormatError, match="empty file"):
        load_image(path, BRIGHT_ON_DARK)


def test_truncated_png_raises(tmp_path: Path) -> None:
    """A file with a PNG signature but no payload raises rather than returning garbage."""
    path = tmp_path / "truncated.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    with pytest.raises(UnsupportedFormatError, match="cannot decode"):
        load_image(path, BRIGHT_ON_DARK)


def test_missing_file_raises(tmp_path: Path) -> None:
    """A path that is not a file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_image(tmp_path / "nothing.tiff", BRIGHT_ON_DARK)


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"\xff\xd8\xff\xe0", "jpeg"),
        (b"II\x2a\x00", "tiff"),
        (b"MM\x00\x2a", "tiff"),
    ],
)
def test_detect_format_reads_signatures(signature: bytes, expected: str) -> None:
    """Each supported signature maps to its format."""
    assert detect_format(signature + b"rest") == expected
