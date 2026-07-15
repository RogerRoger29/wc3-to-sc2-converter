# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for wc3toSC2 GUI executable."""
import os, sys

a = Analysis(
    ['main_window.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('mdx.py', '.'), ('blp.py', '.'), ('textures.py', '.'), ('build_m3.py', '.'),
        ('convert.py', '.'), ('discovery.py', '.'), ('diagnostics.py', '.'), ('healer.py', '.'),
        ('fuzzy_anims.py', '.'), ('mpq_reader.py', '.'), ('actor_gen.py', '.'),
        ('blender_manager.py', '.'), ('auto_updater.py', '.'),
        ('team_color_mask.py', '.'), ('normal_map_gen.py', '.'), ('lod_gen.py', '.'),
        ('m3_validator.py', '.'), ('mdx_cache.py', '.'), ('preview.py', '.'),
        ('config_schema.json', '.'), ('config.example.json', '.'),
    ],
    hiddenimports=['numpy', 'PIL', 'PIL._imaging', 'PySide6', 'PySide6.QtCore',
                   'PySide6.QtWidgets', 'PySide6.QtGui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='wc3toSC2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
