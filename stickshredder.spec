# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for StickShredder — single-file Windows GUI executable."""

import os

block_cipher = None

# Collect certificate templates if the directory has content
cert_templates = os.path.join("src", "cert", "templates")
datas = []
if os.path.isdir(cert_templates) and os.listdir(cert_templates):
    datas.append((cert_templates, os.path.join("cert", "templates")))

# Include SVG icons for the GUI stylesheet
icons_dir = os.path.join("src", "gui", "icons")
if os.path.isdir(icons_dir):
    datas.append((icons_dir, os.path.join("gui", "icons")))

a = Analysis(
    ["src/main.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "wmi",
        "win32com",
        "win32api",
        "pythoncom",
        "win32com.client",
        "win32com.server",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="StickShredder",
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
    onefile=True,
    icon="assets/icon.ico",
    uac_admin=True,
    version_info={
        "FileVersion": (1, 0, 0, 0),
        "ProductVersion": (1, 0, 0, 0),
        "FileDescription": "StickShredder — Secure USB Wipe Tool",
        "InternalName": "StickShredder",
        "OriginalFilename": "StickShredder.exe",
        "ProductName": "StickShredder",
        "CompanyName": "Robin Oertel",
        "LegalCopyright": "MIT License",
    },
)
