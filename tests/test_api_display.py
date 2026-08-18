"""The display derivative: the mapping is asserted numerically, never looked at.

Every assertion here decodes the PNG this service would send and compares the pixels
against values computed by hand, because "the picture looks right" is not a statement
anything can be checked against.
"""

from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest

from api.app import _envelope
from api.display import (
    DISPLAY_BLOCK_KEYS,
    MAPPING_FORMULA,
    MAPPING_NAME,
    OUTPUT_MAX_VALUE,
    render_display,
)
from api.errors import DisplayError

EIGHT_BIT_MAX = 255
SIXTEEN_BIT_MAX = 65535


def _decode(png: bytes) -> np.ndarray:
    """Return the pixels a viewer would see, decoded back out of the PNG bytes."""
    decoded = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded is not None, "the derivative must decode as a PNG"
    return np.asarray(decoded)


def test_a_16_bit_image_maps_linearly_onto_the_full_8_bit_range() -> None:
    """Known input, hand-computed output: 0 -> 0, 65535 -> 255, 257 -> 1, 32768 -> 128.

    257 is chosen because 257 * 255 == 65535 exactly, so its output is exactly 1 with no
    rounding involved at all; 32768 lands at 127.502 and rounds up.
    """
    pixels = np.array([[0, SIXTEEN_BIT_MAX], [257, 32768]], dtype=np.uint16)

    derivative = render_display(pixels, SIXTEEN_BIT_MAX)

    assert np.array_equal(_decode(derivative.png), np.array([[0, 255], [1, 128]], np.uint8))


def test_a_full_scale_pixel_reaches_the_output_maximum() -> None:
    """The saturated pixel is the one value the mapping must not under-report."""
    derivative = render_display(np.full((4, 4), SIXTEEN_BIT_MAX, dtype=np.uint16), SIXTEEN_BIT_MAX)

    assert _decode(derivative.png).max() == OUTPUT_MAX_VALUE


def test_an_8_bit_image_is_reproduced_value_for_value() -> None:
    """At 8 bits the factor is exactly 1, so the derivative is the measured image."""
    pixels = np.arange(256, dtype=np.uint8).reshape(16, 16)

    derivative = render_display(pixels, EIGHT_BIT_MAX)

    assert np.array_equal(_decode(derivative.png), pixels)


def test_a_dim_image_renders_dim_rather_than_being_stretched() -> None:
    """The whole reason a percentile/window mode is not offered.

    A blot whose brightest pixel sits at 40% of full scale must not render as pure white: a
    viewer comparing that white against the absence of a ``saturated`` flag would conclude
    the flag had missed something.
    """
    peak = 26214  # 40% of 65535
    pixels = np.array([[0, peak]], dtype=np.uint16)

    derivative = render_display(pixels, SIXTEEN_BIT_MAX)

    decoded = _decode(derivative.png)
    assert decoded.max() == 102  # round(26214 * 255 / 65535)
    assert decoded.max() < OUTPUT_MAX_VALUE


def test_the_derivative_records_the_mapping_it_went_through() -> None:
    """A viewer weighing a bright region against a QC flag needs to know what happened."""
    derivative = render_display(np.zeros((3, 5), dtype=np.uint16), SIXTEEN_BIT_MAX)

    block = derivative.as_block()

    assert block["is_derivative"] is True
    assert block["media_type"] == "image/png"
    assert (block["width_px"], block["height_px"]) == (5, 3)
    assert block["mapping"] == {
        "name": MAPPING_NAME,
        "formula": MAPPING_FORMULA,
        "source_max_value": SIXTEEN_BIT_MAX,
        "output_max_value": OUTPUT_MAX_VALUE,
        "scales": True,
        "clips": False,
        "source_dn_per_output_level": 257.0,
    }


def test_the_declared_block_keys_are_exactly_the_ones_a_block_carries() -> None:
    """``DISPLAY_BLOCK_KEYS`` is what a *stored* record is checked against on the way out.

    It is a second statement of ``as_block``'s shape, so it can go stale: a key added to the
    block and not to the tuple would never be checked on retrieval, and a key in the tuple
    that ``as_block`` does not emit would condemn every stored record as damaged. Pinning them
    to each other is what stops either.
    """
    block = render_display(np.zeros((3, 5), dtype=np.uint16), SIXTEEN_BIT_MAX).as_block()

    assert tuple(block) == DISPLAY_BLOCK_KEYS


def test_the_record_states_how_many_source_dn_one_output_level_spans() -> None:
    """So that "255 in the PNG" cannot be read as "saturated".

    At 16 bits an interior output level covers 257 source DN and the top one covers 129 --
    it is half-width because the mapping rounds rather than floors -- so every source value
    from 65407 up renders as 255 and only one of those 129 is saturation. At 8 bits the step
    is 1 and the rendering is the measurement. A consumer that knows the step cannot mistake
    the brightest colour the format has for a QC judgement. The recorded field is the
    interior step, ``max_value / 255``, which is the quantization the mapping applies.
    """
    wide = render_display(np.zeros((2, 2), dtype=np.uint16), SIXTEEN_BIT_MAX)
    exact = render_display(np.zeros((2, 2), dtype=np.uint8), EIGHT_BIT_MAX)

    assert wide.as_block()["mapping"]["source_dn_per_output_level"] == 257.0
    assert exact.as_block()["mapping"]["source_dn_per_output_level"] == 1.0
    near_full_scale = _decode(
        render_display(np.array([[65407, SIXTEEN_BIT_MAX]], dtype=np.uint16), SIXTEEN_BIT_MAX).png
    )
    assert near_full_scale.tolist() == [[255, 255]], "the unsaturated pixel renders identically"


def test_the_envelope_adds_the_png_to_the_block_and_changes_nothing_else() -> None:
    """The shape a caller actually receives, assembled by the code that assembles it.

    ``_envelope`` is what both endpoints return through, so it is what this asserts: the
    stored block reaches the caller unaltered, with the encoded image as the one addition.
    """
    derivative = render_display(np.zeros((3, 5), dtype=np.uint16), SIXTEEN_BIT_MAX)
    result = {"result_id": "0123456789abcdef"}

    envelope = _envelope(result, derivative.as_block(), derivative.png)

    assert set(envelope) == {"result", "display"}
    assert envelope["result"] == result
    assert base64.b64decode(envelope["display"]["png_base64"]) == derivative.png
    assert {
        key: value for key, value in envelope["display"].items() if key != "png_base64"
    } == derivative.as_block()


@pytest.mark.parametrize(
    ("pixels", "max_value", "expected"),
    [
        (np.zeros((2, 2, 3), dtype=np.uint8), EIGHT_BIT_MAX, "2D single-channel"),
        (np.zeros((0, 4), dtype=np.uint8), EIGHT_BIT_MAX, "empty image"),
        (np.zeros((2, 2), dtype=np.uint8), 0, "full scale must be at least 1"),
    ],
)
def test_an_unrenderable_image_raises_rather_than_producing_a_picture(
    pixels: np.ndarray, max_value: int, expected: str
) -> None:
    """These can only arise from an internal inconsistency, and they say so."""
    with pytest.raises(DisplayError, match=expected):
        render_display(pixels, max_value)


def test_a_pixel_above_the_declared_full_scale_is_refused_rather_than_wrapped() -> None:
    """The response claims nothing was clipped, so the claim is checked, not assumed.

    Unchecked, this value would wrap through ``astype(uint8)`` and render a bright pixel as
    a dark one while the record still said the mapping does not clip.
    """
    pixels = np.array([[0, 400]], dtype=np.uint16)

    with pytest.raises(DisplayError, match="refusing to emit a derivative"):
        render_display(pixels, EIGHT_BIT_MAX)
