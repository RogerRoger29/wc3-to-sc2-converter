"""Unit tests for the texture DDS converter (textures.py)."""
import os
import pytest
from PIL import Image
import numpy as np
import textures as tex


class TestTextureConversion:
    """Tests for BLP -> DDS DXT5 conversion."""

    def test_convert_blp_to_dds(self, naaru_blp_path, tmp_output_dir):
        """Convert a BLP to DDS and verify the output file exists."""
        out = os.path.join(str(tmp_output_dir), "test_output.dds")
        size, mips = tex.convert_texture(naaru_blp_path, out)
        assert os.path.exists(out)
        assert os.path.getsize(out) > 128  # at least the DDS header
        assert mips >= 1

    def test_convert_with_glow(self, naaru_blp_path, tmp_output_dir):
        """Glow treatment should produce a valid DDS."""
        out = os.path.join(str(tmp_output_dir), "test_glow.dds")
        size, mips = tex.convert_texture(naaru_blp_path, out, glow=True)
        assert os.path.exists(out)
        assert mips >= 1

    def test_convert_with_alpha_invert(self, naaru_blp_path, tmp_output_dir):
        """Alpha inversion should produce a valid DDS."""
        out = os.path.join(str(tmp_output_dir), "test_alpha_inv.dds")
        size, mips = tex.convert_texture(naaru_blp_path, out, alpha_invert=True)
        assert os.path.exists(out)
        assert mips >= 1

    def test_convert_with_custom_glow_dim(self, naaru_blp_path, tmp_output_dir):
        """Custom glow_dim should be accepted."""
        out = os.path.join(str(tmp_output_dir), "test_glow_dim.dds")
        size, mips = tex.convert_texture(naaru_blp_path, out, glow=True, glow_dim=0.3)
        assert os.path.exists(out)
        assert mips >= 1

    def test_load_blp_image(self, naaru_blp_path):
        """load_image should handle BLP files."""
        img = tex.load_image(naaru_blp_path)
        assert isinstance(img, Image.Image)
        assert img.mode == "RGBA"

    def test_load_png_image(self, tmp_path):
        """load_image should handle non-BLP formats via Pillow."""
        p = tmp_path / "test.png"
        img = Image.new("RGBA", (16, 16), (255, 0, 0, 255))
        img.save(str(p))
        loaded = tex.load_image(str(p))
        assert loaded.size == (16, 16)
        assert loaded.mode == "RGBA"


class TestDDSEmbedded:
    """Low-level tests for DDS assembly."""

    def test_dxt5_blocks_roundtrip(self):
        """DXT5 blocks from a simple image should produce bytes."""
        img = Image.new("RGBA", (16, 16), (128, 64, 32, 255))
        blocks = tex._dxt5_blocks(img)
        assert isinstance(blocks, bytes)
        assert len(blocks) > 0

    def test_radial_glow_shape(self):
        """Radial glow kernel should match image dimensions."""
        g = tex._radial_glow(64, 64)
        assert g.shape == (64, 64, 1)
        # Center should be bright, corner should be dark or zero
        assert g[32, 32, 0] > 0.5
        assert g[0, 0, 0] < 0.1 or g[0, 0, 0] == 0.0

    def test_write_dds_mipped(self, tmp_output_dir):
        """write_dds_dxt5_mipped should create a valid DDS file."""
        out = os.path.join(str(tmp_output_dir), "mip_test.dds")
        img = Image.new("RGBA", (64, 64), (0, 255, 0, 128))
        n = tex.write_dds_dxt5_mipped(out, img)
        assert os.path.exists(out)
        assert n >= 2  # at least mip 0 + mip 1 for 64x64
        with open(out, "rb") as f:
            magic = f.read(4)
            assert magic == b"DDS "


class TestTextureRejects:
    """Error handling for bad inputs."""

    def test_convert_nonexistent_file(self, tmp_output_dir):
        """Converting a nonexistent source should raise an error."""
        out = os.path.join(str(tmp_output_dir), "should_not_exist.dds")
        with pytest.raises((FileNotFoundError, Exception)):
            tex.convert_texture("nonexistent_file_xyz.blp", out)
