# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
import os
import re

try:
    with open('src/settings.py', 'r', encoding='utf-8') as _version_file:
        APP_VERSION = re.search(
            r'APP_VERSION\s*=\s*"([^"]+)"', _version_file.read()
        ).group(1)
except Exception:
    APP_VERSION = '1.0.0'

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

binaries = []
hiddenimports = ['certifi', 'PyQt6', 'src.app_version', 'src.autostart', 'src.activation', 'src.generic_editing', 'src.local_model', 'src.sound', 'PyQt6.QtCore', 'PyQt6.QtGui', 'PyQt6.QtWidgets', '_pyi_rth_utils', 'pynput.keyboard', 'AppKit', 'ApplicationServices', 'Quartz', 'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.padding', 'cryptography.hazmat.primitives.serialization', 'cryptography.hazmat.primitives.hashes', 'cryptography.exceptions']

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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='x86_64',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ByteProof',
)

app = BUNDLE(
    coll,
    name='ByteProof.app',
    icon='logo/logo.icns',
    bundle_identifier='nz.co.bytemind.byteproof',
    info_plist={
        'NSPrincipalClass': 'NSApplication',
        'NSAppleScriptEnabled': False,
        'NSHighResolutionCapable': 'True',
        'LSBackgroundOnly': 'False',
        'LSUIElement': 'False',
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'CFBundleURLTypes': [
            {
                'CFBundleURLName': 'nz.co.bytemind.byteproof',
                'CFBundleURLSchemes': ['byteproof'],
            }
        ],
        'NSAppleEventsUsageDescription': 'ByteProof needs to communicate with Microsoft Word for proofreading.',
    },
)
