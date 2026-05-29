# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for AutoCAD Block Search Tool.

Build command (from project root):
    pyinstaller build.spec

Output:
    dist/BlockSearchTool/          <- --onedir distribution folder
    dist/BlockSearchTool/main.exe  <- main executable
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 (SPECPATH injected by PyInstaller)

block_cipher = None

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Application resources
        (str(ROOT / "resources"), "resources"),
        (str(ROOT / "config.json"), "."),
        # setup_oda.py bundled so the in-app ODA download still works
        (str(ROOT / "setup_oda.py"), "."),
        # Vendor ODA (included if already present; otherwise downloaded at runtime)
        (str(ROOT / "vendor"), "vendor"),
    ],
    hiddenimports=[
        # PySide6 plugins
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtSvg",
        # ezdxf
        "ezdxf",
        "ezdxf.addons",
        "ezdxf.addons.odafc",
        "ezdxf.addons.drawing",
        "ezdxf.addons.drawing.matplotlib",
        # win32com
        "win32com",
        "win32com.client",
        "win32com.client.gencache",
        "pywintypes",
        "pythoncom",
        # rapidfuzz
        "rapidfuzz",
        "rapidfuzz.fuzz",
        "rapidfuzz.process",
        "rapidfuzz.utils",
        # matplotlib (ezdxf drawing addon)
        "matplotlib",
        "matplotlib.pyplot",
        "matplotlib.backends.backend_agg",
        # standard library helpers
        "sqlite3",
        "json",
        "logging",
        "logging.handlers",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "test",
        "unittest",
        "email",
        "html",
        "http",
        "xmlrpc",
        "pydoc",
        "doctest",
        "difflib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlockSearchTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # no console window
    icon=str(ROOT / "resources" / "icon.ico") if (ROOT / "resources" / "icon.ico").exists() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BlockSearchTool",
)
