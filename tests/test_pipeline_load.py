"""Loading: what each container yields, and every input that must raise."""

from __future__ import annotations

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
from pipeline.load import detect_format, load_image


def _ramp(height: int, width: int, dtype: type[np.unsignedinteger]) -> np.ndarray:
    """Return a deterministic non-constant test image of the requested dtype."""
    values = np.arange(height * width, dtype=np.uint64).reshape(height, width)
    return (values % np.iinfo(dtype).max).astype(dtype)


def test_tiff16_round_trips_exactly(tmp_path: Path) -> None:
    """A 16-bit TIFF loads as uint16 with its own values, unscaled."""
    array = _ramp(24, 32, np.uint16)
    path = tmp_path / "blot.tiff"
    tifffile.imwrite(str(path), array, photometric="minisblack")

    loaded = load_image(path)

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

    loaded = load_image(path)

    assert (loaded.image_format, loaded.bit_depth, loaded.max_value) == ("png", 8, 255)
    assert loaded.lossy_format is False
    np.testing.assert_array_equal(loaded.pixels, array)


def test_jpeg_is_flagged_lossy_and_stays_8_bit(tmp_path: Path) -> None:
    """A JPEG loads as 8-bit and is reported as a lossy container."""
    array = np.full((16, 16), 120, dtype=np.uint8)
    path = tmp_path / "blot.jpg"
    assert cv2.imwrite(str(path), array, [cv2.IMWRITE_JPEG_QUALITY, 75])

    loaded = load_image(path)

    assert (loaded.image_format, loaded.bit_depth) == ("jpeg", 8)
    assert loaded.lossy_format is True
    assert loaded.pixels.dtype == np.uint8


def test_format_comes_from_content_not_extension(tmp_path: Path) -> None:
    """A PNG named .tiff is still loaded, and reported, as a PNG."""
    array = _ramp(12, 12, np.uint8)
    misnamed = tmp_path / "actually_a_png.tiff"
    encoded = cv2.imencode(".png", array)[1].tobytes()
    misnamed.write_bytes(encoded)

    loaded = load_image(misnamed)

    assert loaded.image_format == "png"
    np.testing.assert_array_equal(loaded.pixels, array)


def test_sha256_is_the_file_digest(tmp_path: Path) -> None:
    """The recorded digest is of the file bytes and has the schema's prefix."""
    import hashlib

    array = _ramp(8, 8, np.uint8)
    path = tmp_path / "blot.png"
    assert cv2.imwrite(str(path), array)

    loaded = load_image(path)

    assert loaded.sha256 == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_float_tiff_raises_unsupported_bit_depth(tmp_path: Path) -> None:
    """Float pixels raise rather than being rescaled to an integer range."""
    path = tmp_path / "float.tiff"
    tifffile.imwrite(str(path), np.zeros((8, 8), dtype=np.float32))

    with pytest.raises(UnsupportedBitDepthError, match="float32"):
        load_image(path)


def test_int32_tiff_raises_unsupported_bit_depth(tmp_path: Path) -> None:
    """A 32-bit integer TIFF raises rather than being squashed to 16-bit."""
    path = tmp_path / "wide.tiff"
    tifffile.imwrite(str(path), np.zeros((8, 8), dtype=np.uint32))

    with pytest.raises(UnsupportedBitDepthError, match="uint32"):
        load_image(path)


def test_multichannel_image_raises(tmp_path: Path) -> None:
    """A colour image raises instead of a channel being picked silently."""
    path = tmp_path / "colour.png"
    assert cv2.imwrite(str(path), np.zeros((8, 8, 3), dtype=np.uint8))

    with pytest.raises(UnsupportedImageError, match="single-channel"):
        load_image(path)


def test_unsupported_container_raises(tmp_path: Path) -> None:
    """A container outside TIFF/PNG/JPEG raises with the supported list."""
    path = tmp_path / "blot.bmp"
    assert cv2.imwrite(str(path), np.zeros((8, 8), dtype=np.uint8))

    with pytest.raises(UnsupportedFormatError, match="tiff"):
        load_image(path)


def test_empty_file_raises(tmp_path: Path) -> None:
    """An empty file raises a format error naming the problem."""
    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    with pytest.raises(UnsupportedFormatError, match="empty file"):
        load_image(path)


def test_truncated_png_raises(tmp_path: Path) -> None:
    """A file with a PNG signature but no payload raises rather than returning garbage."""
    path = tmp_path / "truncated.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    with pytest.raises(UnsupportedFormatError, match="cannot decode"):
        load_image(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    """A path that is not a file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_image(tmp_path / "nothing.tiff")


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
