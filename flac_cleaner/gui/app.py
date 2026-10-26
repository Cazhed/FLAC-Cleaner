"""Application entry point."""
from __future__ import annotations

import sys


def selftest(folder: str, out_dir: str) -> int:
    """Headless check of the full pipeline (used to verify a build): Ginkgo preset on every FLAC in folder."""
    import json
    import os
    import ssl

    from .. import __version__, audio, processor
    from ..presets import Settings

    report = {"version": __version__, "flac": audio.flac_version(), "files": []}
    ctx = ssl.create_default_context()
    report["ssl"] = f"{ssl.OPENSSL_VERSION}, {len(ctx.get_ca_certs())} CA certs"
    s = Settings(output_dir=out_dir)
    items = [processor.scan_file(f, r) for f, r in processor.collect_flac_paths([folder])]
    for p in processor.build_plans(items, s):
        e = processor.apply_plan(p, s) if not p.error else {"status": "ERROR", "error": p.error}
        report["files"].append(e)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    return 0 if all(e.get("status") == "OK" for e in report["files"]) else 1


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2], sys.argv[3])
    from PySide6.QtWidgets import QApplication

    from .. import APP_NAME
    from .common import app_icon, apply_theme
    from .main_window import MainWindow

    if sys.platform == "win32":
        try:  # own taskbar icon instead of python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FLACCleaner.App")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    apply_theme(app)
    try:
        app.styleHints().colorSchemeChanged.connect(lambda *_: apply_theme(app))
    except AttributeError:
        pass
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1:
        w.add_paths(sys.argv[1:])
    return app.exec()
