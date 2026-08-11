# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files
import sys
import os

datas = [
    ('prompt/phd_proofreader.txt', 'prompt'),
    ('prompt/phd_proofreader_creative.txt', 'prompt'),
    ('prompt/polish_general.txt', 'prompt'),
    ('prompt/polish_general_creative.txt', 'prompt'),
    ('prompt/comment_language.txt', 'prompt'),
    ('prompt/comment_technical.txt', 'prompt'),
    ('prompt/context_general.txt', 'prompt'),
    ('prompt/context_journal.txt', 'prompt'),
    ('prompt/context_phd_thesis.txt', 'prompt'),
    ('logo/logo.png', 'logo'),
    ('logo/logo.svg', 'logo'),
    ('assets/chevron-down.svg', 'assets'),
    ('sounds/proofread_start.wav', 'sounds'),
]
datas += collect_data_files('certifi')

# Use standard PyInstaller hooks for PyQt6 instead of manual collect_all
# This avoids conflicts and reduces size.

# Windows specific imports
hiddenimports = [
    'win32com', 'win32com.client', 'pythoncom', 
    'certifi', 'PyQt6', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets',
    'src.app_version', 'src.autostart', 'src.activation', 'src.generic_editing', 'src.local_model', 'src.sound',
    'pynput.keyboard', 'pynput._util.win32',
    'uiautomation', 'comtypes', 'win32clipboard', 'win32con', 'win32gui', 'win32process', 'win32api', 'winsound',
    'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.padding',
    'cryptography.hazmat.primitives.serialization', 'cryptography.hazmat.primitives.hashes',
    'cryptography.exceptions',
]

binaries = []

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ByteProof',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='logo/logo.ico' if os.path.exists('logo/logo.ico') else None,
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ByteProof',
)
