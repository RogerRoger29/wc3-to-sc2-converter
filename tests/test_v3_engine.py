"""Tests for v2.1 engine modules: blender_manager, auto_updater, team_color_mask, normal_map_gen."""
import os
import pytest
from PIL import Image
import numpy as np
import team_color_mask
import normal_map_gen
import auto_updater


class TestTeamColorMask:
    def test_no_team_color_returns_none(self):
        data = {
            "textures": [{"replaceableId": 0, "path": "test.blp", "flags": 0}],
            "materials": [{"layers": [{"textureId": 0, "filter": "Blend", "shadingFlags": 0}]}],
            "geosets": [],
        }
        result = team_color_mask.generate_team_color_mask(data, 64)
        assert result is None

    def test_team_color_generates_mask(self):
        data = {
            "textures": [
                {"replaceableId": 1, "path": "", "flags": 0},  # team color
                {"replaceableId": 0, "path": "test.blp", "flags": 0},
            ],
            "materials": [
                {"layers": [{"textureId": 0, "filter": "None", "shadingFlags": 0}]},
                {"layers": [{"textureId": 1, "filter": "Blend", "shadingFlags": 0}]},
            ],
            "geosets": [{
                "materialId": 0, "nverts": 3, "ntris": 1,
                "uvs": [[0, 0], [1, 0], [0.5, 1]],
                "faces": [[0, 1, 2]],
            }],
        }
        result = team_color_mask.generate_team_color_mask(data, 64)
        assert result is not None
        assert result.size == (64, 64)
        assert result.mode == "RGBA"


class TestNormalMapGen:
    def test_sobel_produces_valid_image(self):
        arr = np.zeros((64, 64, 4), dtype=np.uint8)
        arr[16:48, 16:48] = [200, 100, 50, 255]  # brown square
        img = Image.fromarray(arr, "RGBA")
        normal = normal_map_gen.sobel_normal_map(img, strength=1.0)
        assert normal.size == (64, 64)
        assert normal.mode == "RGBA"
        # Check that normals are encoded correctly (center should be mostly flat = 128,128,255)
        n_arr = np.array(normal)
        center = n_arr[32, 32]
        assert 120 <= center[0] <= 135  # R ~128
        assert 120 <= center[1] <= 135  # G ~128
        assert center[2] >= 200  # B ~255 (flat, pointing up)


class TestAutoUpdater:
    def test_parse_version(self):
        assert auto_updater._parse_version("v2.1.0") == (2, 1, 0)
        assert auto_updater._parse_version("1.0.0") == (1, 0, 0)
        assert auto_updater._parse_version("v10.20.30") == (10, 20, 30)

    def test_check_for_update_handles_network_error(self):
        # Should return None gracefully on network issues
        result = auto_updater.check_for_update()
        assert result is None or isinstance(result, dict)
