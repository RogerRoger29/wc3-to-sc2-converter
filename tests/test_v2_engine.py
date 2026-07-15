"""Tests for v2.0 engine modules: diagnostics, healer, fuzzy_anims, discovery, actor_gen."""
import os
import pytest
import json
import numpy as np
from PIL import Image
import diagnostics
import healer
import fuzzy_anims
import discovery
import actor_gen


# ── Diagnostics ───────────────────────────────────────────────

class TestDiagnostics:
    def test_empty_model_produces_report(self):
        data = {
            "model": {"version": 800, "boundsRadius": 50, "min": [0, 0, 0], "max": [100, 100, 100]},
            "geosets": [], "bones": [], "helpers": [], "sequences": [],
            "textures": [], "materials": [], "particles": [],
            "geosetAnims": [], "pivots": [], "nodes": {}, "cameras": [],
            "attachments": [], "events": [], "globalSequences": [],
        }
        r = diagnostics.run_checks(data, "test.mdx")
        assert r.is_healthy == False
        assert len(r.errors) >= 1
        assert any(i.code == "NO_GEOSETS" for i in r.errors)

    def test_version_warning(self):
        data = {
            "model": {"version": 700, "boundsRadius": 50, "min": [0, 0, 0], "max": [100, 100, 100]},
            "geosets": [{"nverts": 3, "ntris": 1, "verts": [[0,0,0],[1,0,0],[0,1,0]],
                         "faces": [[0,1,2]], "uvs": [[0,0],[1,0],[0,1]], "materialId": 0,
                         "vertexBones": [[0],[0],[0]]}],
            "bones": [{"name": "root", "objectId": 0, "parentId": None, "flags": 0, "tracks": {}}],
            "helpers": [], "sequences": [], "textures": [], "materials": [],
            "particles": [], "geosetAnims": [], "pivots": [[0,0,0]],
            "nodes": {0: {"name": "root", "objectId": 0}}, "cameras": [],
            "attachments": [], "events": [], "globalSequences": [],
        }
        r = diagnostics.run_checks(data)
        assert any(i.code == "VERSION" for i in r.warnings)

    def test_degenerate_faces_detected(self):
        data = {
            "model": {"version": 800, "boundsRadius": 50, "min": [0, 0, 0], "max": [100, 100, 100]},
            "geosets": [{"nverts": 4, "ntris": 2,
                         "verts": [[0,0,0],[1,0,0],[0,1,0],[1,1,0]],
                         "faces": [[0,1,2],[0,0,1]],  # second face is degenerate
                         "uvs": [[0,0],[1,0],[0,1],[1,1]], "materialId": 0,
                         "vertexBones": [[0],[0],[0],[0]]}],
            "bones": [{"name": "root", "objectId": 0, "parentId": None, "flags": 0, "tracks": {}}],
            "helpers": [], "sequences": [], "textures": [], "materials": [],
            "particles": [], "geosetAnims": [], "pivots": [[0,0,0]],
            "nodes": {0: {"name": "root", "objectId": 0}}, "cameras": [],
            "attachments": [], "events": [], "globalSequences": [],
        }
        r = diagnostics.run_checks(data)
        assert any(i.code == "DEGENERATE_FACES" for i in r.warnings)

    def test_html_report_generation(self):
        r = diagnostics.DiagnosticReport("TestModel")
        r.add("TEST", "warning", "Test warning")
        html = r.to_html()
        assert "<html" in html
        assert "TestModel" in html
        assert "Test warning" in html

    def test_json_report_generation(self):
        r = diagnostics.DiagnosticReport("TestModel")
        r.add("TEST", "error", "Test error")
        d = json.loads(r.to_json())
        assert d["model"] == "TestModel"
        assert d["summary"]["errors"] == 1


# ── Healer ────────────────────────────────────────────────────

class TestHealer:
    def test_fix_degenerate_faces(self):
        geosets = [{"nverts": 4, "ntris": 2,
                    "faces": [[0, 1, 2], [0, 0, 1]],  # second is deg
                    "verts": [[0,0,0],[1,0,0],[0,1,0],[1,1,0]]}]
        fixed, removed = healer.fix_degenerate_faces(geosets)
        assert removed == 1
        assert len(fixed[0]["faces"]) == 1

    def test_fix_bone_creates_root(self):
        bones = [{"name": "orphan", "objectId": 1, "parentId": 99, "flags": 0, "tracks": {}}]
        fixed, log = healer.fix_bone_hierarchy(bones, [], [])
        assert len(fixed) == 2  # original + new root
        assert fixed[0]["name"] == "_root_auto" or fixed[1]["name"] == "_root_auto"

    def test_fix_alpha_invert_detects_inverted(self):
        arr = np.full((64, 64, 4), 255, dtype=np.uint8)  # all opaque
        img = Image.fromarray(arr, "RGBA")
        _, was_fixed = healer.fix_alpha_invert(img)
        assert was_fixed  # border is all opaque → should trigger inversion

    def test_fix_alpha_invert_skips_normal(self):
        arr = np.zeros((64, 64, 4), dtype=np.uint8)  # all transparent
        arr[..., :3] = 128
        img = Image.fromarray(arr, "RGBA")
        _, was_fixed = healer.fix_alpha_invert(img)
        assert not was_fixed

    def test_fix_particle_lifespan(self):
        particles = [{"lifespan": 0.01}, {"lifespan": 100.0}, {"lifespan": 1.0}]
        fixed, clamped = healer.fix_particle_lifespan(particles)
        assert clamped >= 2
        assert 0.3 <= fixed[0]["lifespan"] <= 8.0
        assert 0.3 <= fixed[1]["lifespan"] <= 8.0


# ── Fuzzy Anims ────────────────────────────────────────────────

class TestFuzzyAnims:
    def test_exact_match(self):
        token, conf = fuzzy_anims.fuzzy_match("Stand - 1")
        assert token == "Stand"
        assert conf == 1.0

    def test_fuzzy_match_typo(self):
        token, conf = fuzzy_anims.fuzzy_match("Atack - 1")  # missing 't'
        assert conf > 0.5

    def test_build_anim_map(self):
        names = ["Stand - 1", "Stand - 2", "Death"]
        m = fuzzy_anims.build_anim_map(names)
        assert m["Stand - 1"] == "Stand"
        assert m["Stand - 2"] == "Stand 02"
        assert m["Death"] == "Death"

    def test_deduplication(self):
        names = ["Stand - 1", "Stand - 2", "Stand - 3"]
        m = fuzzy_anims.build_anim_map(names)
        # All should have unique SC2 tokens
        assert len(set(m.values())) == 3


# ── Discovery ──────────────────────────────────────────────────

class TestDiscovery:
    def test_estimate_scale_unit(self):
        data = {"model": {"boundsRadius": 80, "min": [-50,-50,0], "max": [50,50,100]},
                "sequences": [{"moveSpeed": 2.5}]}
        scale, conf = discovery.estimate_scale(data)
        assert scale in (0.04, 0.05)

    def test_estimate_scale_building(self):
        data = {"model": {"boundsRadius": 300, "min": [-200,-200,0], "max": [200,200,400]},
                "sequences": []}
        scale, conf = discovery.estimate_scale(data)
        assert scale >= 0.06

    def test_find_blender_returns_none_or_path(self):
        result = discovery.find_blender()
        assert result is None or os.path.exists(result)


# ── Actor Gen ──────────────────────────────────────────────────

class TestActorGen:
    def test_generate_actor_xml(self):
        xml = actor_gen.generate_actor_xml("Footman", "Assets\\Units\\Footman.m3", scale=1.0)
        assert "CActorModel" in xml
        assert "Footman" in xml
        assert "Stand" in xml

    def test_generate_all(self):
        result = actor_gen.generate_all("Test", "Assets\\Test.m3", generate_unit=True)
        assert "actor" in result
        assert "unit" in result
