"""Unit tests for the MDX v800 binary parser (mdx.py)."""
import pytest
import mdx as mdxlib


class TestMDXParser:
    """Tests against the bundled Naaru.mdx example model."""

    @pytest.fixture(scope="class")
    def parsed(self, naaru_mdx_path):
        return mdxlib.parse(naaru_mdx_path)

    def test_parse_returns_dict(self, parsed):
        """Parsing should return a dict with all expected top-level keys."""
        assert isinstance(parsed, dict)
        for key in ("model", "sequences", "textures", "materials", "geosets",
                    "bones", "helpers", "attachments", "cameras", "particles",
                    "nodes", "pivots", "geosetAnims", "globalSequences"):
            assert key in parsed, f"Missing top-level key: {key}"

    def test_model_header(self, parsed):
        """MODL chunk should have a name and bounds."""
        m = parsed["model"]
        assert "name" in m
        assert m["name"] == "Naaru"
        assert "boundsRadius" in m
        assert m["boundsRadius"] > 0
        assert len(m.get("min", [])) == 3
        assert len(m.get("max", [])) == 3

    def test_version(self, parsed):
        """VERS chunk should report version 800."""
        assert parsed["model"].get("version") == 800

    def test_sequences(self, parsed):
        """SEQS chunk should contain animation sequences."""
        seqs = parsed["sequences"]
        assert len(seqs) > 0
        names = {s["name"] for s in seqs}
        expected = {"Stand - 1", "Stand - 2", "Stand - 3", "Death", "Dissipate", "Portrait"}
        assert expected.issubset(names), f"Missing sequences: {expected - names}"
        for s in seqs:
            assert "start" in s
            assert "end" in s
            assert s["end"] >= s["start"]
            assert "nonLooping" in s

    def test_textures(self, parsed):
        """TEXS chunk should list texture references."""
        texs = parsed["textures"]
        assert len(texs) > 0
        for t in texs:
            assert "replaceableId" in t
            assert "path" in t
            assert "flags" in t

    def test_materials(self, parsed):
        """MTLS chunk should have materials with layers and filter modes."""
        mats = parsed["materials"]
        assert len(mats) > 0
        for mat in mats:
            assert "layers" in mat
            assert len(mat["layers"]) > 0
            for layer in mat["layers"]:
                assert "filter" in layer
                assert "textureId" in layer
                assert "shadingFlags" in layer

    def test_geosets(self, parsed):
        """GEOS chunk should produce geosets with vertices, faces, and UVs."""
        geos = parsed["geosets"]
        assert len(geos) > 0
        for g in geos:
            assert g["nverts"] > 0
            assert g["ntris"] > 0
            assert len(g["verts"]) == g["nverts"]
            assert len(g["faces"]) == g["ntris"]
            assert "uvs" in g
            assert "materialId" in g
            assert "vertexBones" in g
            assert len(g["vertexBones"]) == g["nverts"]

    def test_bones(self, parsed):
        """BONE chunk should produce a skeleton with hierarchy."""
        bones = parsed["bones"]
        assert len(bones) > 0
        root_count = sum(1 for b in bones if b["parentId"] is None)
        assert root_count == 1, f"Expected exactly 1 root bone, got {root_count}"
        for b in bones:
            assert "name" in b
            assert "objectId" in b
            assert "tracks" in b

    def test_pivots(self, parsed):
        """PIVT chunk should have pivot points matching node count."""
        pivots = parsed["pivots"]
        node_count = len(parsed["nodes"])
        assert len(pivots) >= node_count

    def test_particles(self, parsed):
        """PRE2 chunk should parse particle emitters."""
        particles = parsed["particles"]
        # Naaru has particle emitters
        assert len(particles) > 0
        for p in particles:
            assert "name" in p
            assert "textureId" in p
            assert "emissionRate" in p
            assert "speed" in p

    def test_cameras(self, parsed):
        """CAMS chunk should have at least a portrait camera."""
        cameras = parsed["cameras"]
        assert len(cameras) > 0
        for c in cameras:
            assert "name" in c
            assert "pos" in c
            assert "fov" in c
            assert "target" in c

    def test_geoset_anims(self, parsed):
        """GEOA chunk should list geoset animations."""
        ga = parsed.get("geosetAnims", [])
        assert isinstance(ga, list)

    def test_summarize(self, parsed):
        """summarize() should return a non-empty string."""
        s = mdxlib.summarize(parsed)
        assert isinstance(s, str)
        assert len(s) > 100
        assert "Naaru" in s

    def test_nodes_indexed_by_object_id(self, parsed):
        """All nodes should be accessible via nodes[objectId]."""
        nodes = parsed["nodes"]
        for bone in parsed["bones"]:
            assert bone["objectId"] in nodes
        for helper in parsed["helpers"]:
            assert helper["objectId"] in nodes


class TestMDXReader:
    """Low-level tests for the Reader helper class."""

    def test_reader_basic(self):
        import struct
        data = struct.pack("<I4sH", 42, b"TAG1", 256)
        r = mdxlib.Reader(data)
        assert r.u32() == 42
        assert r.tag() == "TAG1"
        assert r.u16() == 256

    def test_reader_vec(self):
        import struct
        data = struct.pack("<3f", 1.0, 2.0, 3.0)
        r = mdxlib.Reader(data)
        v = r.vec(3)
        assert v == [1.0, 2.0, 3.0]

    def test_reader_cstr(self):
        data = b"hello\x00" + b"\x00" * 74  # 80 bytes total
        r = mdxlib.Reader(data)
        assert r.cstr(80) == "hello"

    def test_reader_peek_tag(self):
        data = b"TESTmore_data_here"
        r = mdxlib.Reader(data)
        assert r.peek_tag() == "TEST"
        assert r.tag() == "TEST"  # should consume it
        assert r.p == 4


class TestMDXRejectsInvalid:
    """Parser should reject invalid or non-MDX files."""

    def test_rejects_empty(self, tmp_path):
        p = tmp_path / "empty.mdx"
        p.write_bytes(b"")
        with pytest.raises((AssertionError, IndexError, Exception)):
            mdxlib.parse(str(p))

    def test_rejects_bad_magic(self, tmp_path):
        p = tmp_path / "bad.mdx"
        p.write_bytes(b"NOT_MDLX_FILE!")
        with pytest.raises(AssertionError):
            mdxlib.parse(str(p))
