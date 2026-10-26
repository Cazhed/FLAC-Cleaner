# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller --noconfirm FLACCleaner.spec   ->  dist\FLACCleaner.exe
import os

BIN = [("bin/" + f, "bin") for f in ("flac.exe", "metaflac.exe", "FLAC.dll", "ogg.dll", "vcruntime140.dll")]
LICENSES = [(os.path.join("bin", "licenses", f), "bin/licenses") for f in os.listdir(os.path.join("bin", "licenses"))]

a = Analysis(
    ["FLACCleaner.pyw"],
    pathex=["."],
    binaries=[],
    datas=[("assets/icon.ico", "assets")] + BIN + LICENSES,  # datas = copied as-is, not dependency-scanned
    hiddenimports=[],
    excludes=["tkinter", "unittest", "pydoc", "pytest", "setuptools", "pkg_resources", "distutils", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtPdf", "PySide6.QtOpenGL", "PySide6.QtSvg", "PySide6.QtSql", "PySide6.QtTest",
              "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtXml", "PySide6.QtHelp", "PySide6.QtDesigner",
              "PySide6.QtUiTools", "PySide6.QtPrintSupport", "PySide6.QtMultimedia", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
# Drop large binaries a widgets app never uses (software OpenGL, AVIF codec, touch/direct2d plugins).
_SKIP = ("opengl32sw", "_avif", "qdirect2d", "qtuiotouch", "qminimal", "qoffscreen")
a.binaries = [b for b in a.binaries if not any(x in b[0].lower() for x in _SKIP)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FLACCleaner",
    icon="assets/icon.ico",
    version="version_info.txt",
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
