"""WC3 .mdx -> SC2 .m3 — one-command orchestrator.

Usage:
    python convert.py <model.json>                 # config-driven (recommended; see config.example.json)
    python convert.py <model.mdx> <out_dir>        # quick mode, everything auto-detected

What it does (in system Python — needs numpy + Pillow):
  1. parse the MDX
  2. resolve & convert every file-backed WC3 texture (BLP) to SC2 DDS (DXT5 + mips); particle textures get
     the additive-glow treatment automatically
  3. write a build_config.json
  4. launch Blender headless to run build_m3.py, which builds and exports the .m3 via the m3studio addon

Blender 4.4 with the m3studio addon installed is required for step 4 (see README).
"""
import os, sys, json, subprocess, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mdx as mdxlib
import textures as tex


def find_blender(cfg):
    if cfg.get("blender") and os.path.exists(cfg["blender"]):
        return cfg["blender"]
    cands = []
    for base in (r"C:\Program Files\Blender Foundation", r"C:\Program Files (x86)\Blender Foundation"):
        cands += sorted(glob.glob(os.path.join(base, "Blender *", "blender.exe")), reverse=True)
    cands += ["blender"]  # PATH fallback (Linux/macOS)
    for c in cands:
        if c == "blender" or os.path.exists(c):
            return c
    raise SystemExit("Blender not found — set \"blender\" in the config to your blender.exe path.")


def resolve_texture_src(texs_path, search_dirs, override):
    """Find the BLP/image on disk for a WC3 TEXS path, trying an explicit override then search dirs."""
    if override and os.path.exists(override):
        return override
    base = os.path.basename(texs_path.replace("\\", "/"))
    cands = []
    for d in search_dirs:
        cands.append(os.path.join(d, base))                 # by basename
        cands.append(os.path.join(d, texs_path.replace("\\", os.sep)))  # by full relative path
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def main():
    arg = sys.argv[1]
    if arg.lower().endswith(".json"):
        cfg = json.load(open(arg, "r", encoding="utf-8"))
        cfg_dir = os.path.dirname(os.path.abspath(arg))
    else:
        cfg = {"mdx": arg, "out_dir": sys.argv[2] if len(sys.argv) > 2 else "Converted"}
        cfg_dir = os.getcwd()

    def rel(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(cfg_dir, p))

    mdx_path = rel(cfg["mdx"])
    out_dir = rel(cfg.get("out_dir", "Converted"))
    os.makedirs(out_dir, exist_ok=True)
    name = cfg.get("model_name") or os.path.splitext(os.path.basename(mdx_path))[0]
    asset_dir = cfg.get("asset_texture_dir", "Assets\\Textures\\")
    search_dirs = [rel(d) for d in cfg.get("texture_search_dirs", [".", "Textures", os.path.dirname(cfg["mdx"]) or "."])]
    tex_cfg = cfg.get("textures", {})

    m = mdxlib.parse(mdx_path)
    print("=== %s: %d textures, %d materials, %d geosets, %d sequences, %d emitters ===" % (
        name, len(m["textures"]), len(m["materials"]), len(m["geosets"]), len(m["sequences"]), len(m["particles"])))

    particle_texids = {e["textureId"] for e in m["particles"]}

    # ---- convert textures ----
    tex_map = {}   # wc3 TEXS index -> output dds basename (for build_m3)
    missing = []
    for i, t in enumerate(m["textures"]):
        if t["replaceableId"] in (1, 2):
            print("  tex[%d] replaceable team texture - no file, handled as team-colour emissive" % i)
            continue
        if not t["path"]:
            continue
        ov = tex_cfg.get(str(i), {})
        src = resolve_texture_src(t["path"], search_dirs, ov.get("src"))
        out_name = ov.get("out") or (os.path.splitext(os.path.basename(t["path"].replace("\\", "/")))[0] + ".dds")
        if not src:
            missing.append((i, t["path"]))
            print("  tex[%d] %-28s MISSING (searched %s) — will reference %s but you must supply it" % (
                i, t["path"], search_dirs, out_name))
            tex_map[i] = out_name
            continue
        glow = ov.get("glow", i in particle_texids)            # particle textures default to glow treatment
        alpha_invert = ov.get("alpha_invert", False)
        size, mips = tex.convert_texture(src, os.path.join(out_dir, out_name),
                                         alpha_invert=alpha_invert, glow=glow)
        tex_map[i] = out_name
        print("  tex[%d] %-28s -> %-22s %s mips=%d%s%s" % (
            i, os.path.basename(src), out_name, size, mips,
            " [glow]" if glow else "", " [alpha-invert]" if alpha_invert else ""))

    # ---- write build config for Blender ----
    build_cfg = {
        "mdx": mdx_path, "out": os.path.join(out_dir, name + ".m3"),
        "model_name": name, "scale": float(cfg.get("scale", 1.0)),
        "asset_texture_dir": asset_dir, "textures": {str(k): v for k, v in tex_map.items()},
        "anim_names": cfg.get("anim_names", {}), "attachments": cfg.get("attachments"),
        "features": cfg.get("features", {}), "particle_rate_scale": float(cfg.get("particle_rate_scale", 1.0)),
        "team_color": bool(cfg.get("team_color", True)),
    }
    build_cfg_path = os.path.join(out_dir, "_build_config.json")
    json.dump(build_cfg, open(build_cfg_path, "w", encoding="utf-8"), indent=1)

    # ---- run Blender ----
    blender = find_blender(cfg)
    cmd = [blender, "--background", "--factory-startup", "--python", os.path.join(HERE, "build_m3.py"),
           "--", build_cfg_path]
    print("\n=== launching Blender ===\n ", " ".join('"%s"' % c if " " in c else c for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tail = [ln for ln in proc.stdout.splitlines()
            if any(k in ln for k in ("anim '", "mat[", "particle[", "attachment", "hit-test", "bounds",
                                     "baked", "EXPORT_OK", "EXPORT_FAILED", "BUILD_DONE", "warn", "Error"))]
    print("\n".join("  " + t for t in tail))
    if "EXPORT_FAILED" in proc.stdout or proc.returncode not in (0,):
        print("\n!!! build problem — full Blender stderr below:\n", proc.stderr[-2000:])
        sys.exit(1)
    print("\nDONE. Output: %s" % build_cfg["out"])
    print("Textures: %s\\%s*.dds  (import into SC2 under %s)" % (out_dir, "", asset_dir))
    if missing:
        print("NOTE: %d texture(s) were not found on disk and must be supplied: %s" % (
            len(missing), ", ".join(p for _, p in missing)))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    main()
