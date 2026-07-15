"""Unit tests for the BLP1 texture decoder (blp.py)."""
import pytest
from PIL import Image
import blp


class TestBLPDecoder:
    """Tests using the bundled Naaru.blp example."""

    def test_decode_blp_returns_image(self, naaru_blp_path):
        """decode_blp should return a PIL RGBA Image."""
        img = blp.decode_blp(naaru_blp_path)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGBA"

    def test_decode_blp_dimensions(self, naaru_blp_path):
        """Decoded image should have reasonable dimensions."""
        img = blp.decode_blp(naaru_blp_path)
        assert img.width > 0
        assert img.height > 0
        assert img.width <= 4096  # sanity upper bound
        assert img.height <= 4096

    def test_decode_blp_non_empty_pixels(self, naaru_blp_path):
        """Decoded image should have actual pixel data."""
        img = blp.decode_blp(naaru_blp_path)
        # At least one pixel should have non-zero alpha
        import numpy as np
        arr = np.array(img)
        assert arr[..., 3].max() > 0, "Alpha channel is completely empty"


class TestBLPRejectsInvalid:
    """BLP decoder should reject invalid files gracefully."""

    def test_rejects_blp2(self, tmp_path):
        """BLP2 (WoW format) should raise a clear ValueError."""
        p = tmp_path / "test.blp"
        # Craft a minimal BLP2 header
        import struct
        header = struct.pack("<4s", b"BLP2") + b"\x00" * 152
        p.write_bytes(header)
        with pytest.raises(ValueError, match="BLP2"):
            blp.decode_blp(str(p))

    def test_rejects_bad_magic(self, tmp_path):
        """A file that isn't BLP at all should raise ValueError."""
        p = tmp_path / "not_a_blp.blp"
        p.write_bytes(b"GARBAGE_DATA" * 20)
        with pytest.raises(ValueError, match="not a BLP1"):
            blp.decode_blp(str(p))

    def test_rejects_empty_file(self, tmp_path):
        """Empty file should raise an error."""
        p = tmp_path / "empty.blp"
        p.write_bytes(b"")
        with pytest.raises((ValueError, AssertionError, Exception)):
            blp.decode_blp(str(p))
