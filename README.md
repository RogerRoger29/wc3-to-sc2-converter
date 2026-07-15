# WC3 → SC2 Model Converter

Convert **Warcraft 3 `.mdx` models (classic format, version 800)** into **StarCraft 2 `.m3` models**, preserving
mesh, skeleton, *all* animations, textures, team colour, particle emitters, hit-test volume and portrait camera.

It works on any WC3 v800 model: material, texture, team-colour and animation settings are **derived from the MDX
itself** (not hardcoded). It's bundled with a complete worked example — the **Naaru** — that you can run as-is.

There is **no third-party WC3 importer** in the pipeline. A hand-written MDX parser builds the scene directly in
Blender, then the **m3studio** addon writes the `.m3`.

---

## What it produces

| Aspect | Handling |
|---|---|
| Geometry + UVs | 1:1 (WC3 and Blender are both Z-up); V flipped; edge-split for hard facet normals |
| Skinning | rigid (WC3 matrix groups → vertex groups, equal weights) |
| Skeleton + animations | every `SEQS` baked per-frame from `KGTR/KGRT/KGSC` (world-matrix accurate) |
| Materials | blend mode, two-sided, unshaded — all derived from WC3 `filterMode` + `shadingFlags` |
| Team colour | WC3 replaceable textures (team colour / team glow) → SC2 `TEAMEMIS` emissive |
| Textures | each `.blp` → `.dds` (DXT5 + full mip chain); particle textures get an additive-glow treatment |
| Animated fades | material alpha `KMTA` × geoset alpha `KGAO` baked into the diffuse (e.g. death dissolve) |
| Particles | `PRE2` emitters → SC2 particle systems, with `KP2V` emission gating (approximate — see Limitations) |
| Bounds / hit-test | from `MODL` bounds (a tight sphere so the unit is selectable) |
| Portrait camera | from `CAMS` |

---

## Requirements

1. **Blender 4.4** (tested on 4.4.3). The M3 timeline is hardcoded to 30 fps; other 4.x versions may work but are
   untested.
2. **m3studio addon** by Solstice245 — install it into Blender and enable it:
   <https://github.com/Solstice245/m3studio>
   (Edit ▸ Preferences ▸ Add-ons ▸ Install… ▸ pick the downloaded zip ▸ tick to enable. The addon folder must be
   named `m3studio-main`, which is the default when you download the repo zip.)
3. **Python 3.8+** on your PATH (the system Python, *outside* Blender), with:
   ```
   pip install numpy pillow
   ```
   Pillow must have DDS-write support (the standard wheels do).

You do **not** launch Blender yourself — `convert.py` runs it headless for you.

---

## Quick start — the included Naaru example

```
python convert.py examples/Naaru/Naaru.json
```

This converts the bundled Naaru and writes `examples/Naaru/out/Naaru.m3` plus its `.dds` textures. (A pre-built
`out/` is included so you can see the expected result.)

If Blender isn't found automatically, set the `"blender"` path in the JSON to your `blender.exe`.

---

## Converting your own model

**Place the model's textures where the tool can find them** — the `.blp` files next to the `.mdx`, or in a
`Textures/` subfolder, or list locations in `texture_search_dirs`, or point at each with a per-texture `"src"`.
Custom model textures usually ship with the model; standard WC3 textures (e.g. `star4.blp`) come from your
Warcraft 3 install's MPQs and must be extracted first.

**Option A — quick (auto-detect everything):**
```
python convert.py path/to/YourModel.mdx OutputDir
```

**Option B — config file (recommended, repeatable, tunable):** copy `config.example.json`, edit it, then:
```
python convert.py your-model.json
```
Paths in the config are resolved relative to the config file.

---

## Config reference

| Field | Meaning |
|---|---|
| `mdx` | path to the `.mdx` |
| `out_dir` | output folder for the `.m3` + `.dds` |
| `model_name` | base name for the `.m3` and internal names (default: mdx filename) |
| `scale` | world scale (WC3 units are large; ~`0.04`–`0.06` is typical) |
| `blender` | path to `blender.exe` (auto-detected if omitted) |
| `asset_texture_dir` | the SC2 asset path the `.m3` references textures by (default `Assets\Textures\`) |
| `texture_search_dirs` | folders to search for the source `.blp`/image files |
| `textures` | per-WC3-texture overrides, keyed by TEXS index: `out` (dds name), `src` (explicit source), `glow` (bool), `alpha_invert` (bool) |
| `anim_names` | override WC3 sequence name → SC2 animation token (merged over the built-in defaults) |
| `attachments` | `null` = auto (`Ref_Origin`/`Ref_Center` on root), or a list of `{ "name", "bone" }` |
| `features` | toggle `animations` / `attachments` / `particles` / `hittest` / `camera` |
| `particle_rate_scale` | multiply all emission rates (WC3 rates are often low; raise to make sparse effects visible) |
| `particle_size_scale` | multiply all particle sizes |
| `team_color` | map WC3 replaceable team textures to SC2 team-colour emissive |

---

## How the auto-derivation works

- **Materials** — the first file-backed layer is the diffuse; its WC3 `filterMode` sets the SC2 blend mode
  (`Blend`→`ALPHAB`, `Additive`/`AddAlpha`→`ADD`, `Modulate`→`MOD`, `Modulate2x`→`MOD2`, `None`→`OPAQUE`,
  `Transparent`→`ALPHAA`). `shadingFlags` set unshaded / two-sided. Any layer that uses a **replaceable team
  texture** (replaceableId 1 = team colour, 2 = team glow) adds a `TEAMEMIS` emissive layer, masked by the
  diffuse texture, so the unit glows in the player's colour.
- **Textures** — every file-backed `TEXS` entry is decoded (`blp.py`) and re-encoded to DDS DXT5 with mipmaps
  (`textures.py`). Textures used by particle emitters get the glow treatment automatically: premultiply by
  luminance + a radial fade with a hard border, so an additive sprite reads as a round glow instead of a square.
- **Animations** — each `SEQS` entry becomes an SC2 animation group; names map through the built-in
  `DEFAULT_ANIM_NAMES` table (override via `anim_names`). Bone motion is baked per 30-fps frame as
  `matrix_basis = rest⁻¹ · L_node · rest`.
- **Particles** — `PRE2` parameters map across: size from WC3 `scaling`, emission from `emissionRate`, the
  three colour/alpha segments to a colour+alpha envelope, `filterMode` to blend, and the `KP2V` track gates
  emission per sequence.

---

## Importing into SC2

1. Copy the generated `*.dds` into your map/mod so they live under the `asset_texture_dir` path the model
   expects (default `Assets\Textures\`).
2. Import the `.m3` (Importer, or drop it into the mod's asset tree).
3. Build the unit/actor as usual. `Ref_Origin` and `Ref_Center` attachment points are present for actor hosting.
4. Tweak final on-screen size with the Actor's Scale if needed.

---

## Limitations & notes

- **Format scope:** classic WC3 MDX **version 800**, and **BLP1** textures (JPEG-content and palettized). The
  WoW **BLP2** format is rejected.
- **Particles are approximate.** WC3 and SC2 particle systems are not 1:1; emitters are mapped sensibly and
  clamped to stay sane, but expect to fine-tune with `particle_rate_scale` / `particle_size_scale` or in the SC2
  editor. WC3 emission rates are often very low — raise `particle_rate_scale` if an effect looks too sparse.
- **Rotation interpolation between keyframes is slerp**; WC3 hermite/bezier *rotation* tangents are ignored
  (translation/scale tangents are honoured). The difference is visually negligible.
- **Team colour** is rendered as a `TEAMEMIS` emissive masked by the diffuse texture.
- **WC3 BLP-JPEG alpha is stored inverted**, and the decoder accounts for that. If a specific texture's
  transparency comes out backwards, set `"alpha_invert": true` for it in the config.
- **Textures must exist on disk.** The tool reports any it can't find; supply them and re-run.

---

## Files

| File | Role |
|---|---|
| `convert.py` | **run this** — orchestrator (converts textures, writes the build config, launches Blender) |
| `mdx.py` | Warcraft 3 MDX (v800) parser → plain Python dicts |
| `blp.py` | BLP1 texture decoder → RGBA |
| `textures.py` | BLP/image → DDS DXT5 + mip chain (+ particle-glow treatment) |
| `build_m3.py` | runs **inside Blender**: builds the scene from the MDX and exports the `.m3` via m3studio |
| `config.example.json` | config template |
| `examples/Naaru/` | complete worked example (model + textures + config) |

---

## Credits

- **m3studio** (M3 import/export) by **Solstice245** — <https://github.com/Solstice245/m3studio>
- Pipeline built and validated by porting the **Naaru** (a community WC3 model) to StarCraft 2.
