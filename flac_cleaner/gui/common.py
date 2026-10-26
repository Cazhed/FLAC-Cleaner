"""Shared GUI helpers: background tasks, theme, image helpers."""
from __future__ import annotations

import os
import sys
import threading
import traceback

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

from ..lookup import Cancelled

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff")


class Task(QThread):
    """Runs fn(task) on a worker thread. fn may emit task.progress / task.partial."""
    progress = Signal(int, int, str)
    partial = Signal(object)
    result = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            r = self._fn(self)
        except Cancelled:
            self.cancelled.emit()
        except Exception as e:  # report, don't crash the UI
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")
        else:
            self.result.emit(r)


def resource_path(*parts: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(base, *parts)


def app_icon() -> QIcon:
    p = resource_path("assets", "icon.ico")
    return QIcon(p) if os.path.exists(p) else QIcon()


def pixmap_from_bytes(data: bytes | None, size: int) -> QPixmap | None:
    if not data:
        return None
    pm = QPixmap()
    if not pm.loadFromData(data):
        return None
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def apply_theme(app: QApplication) -> None:
    """Fusion style with a palette that follows the Windows light/dark setting."""
    app.setStyle("Fusion")
    if is_dark():
        p = QPalette()
        base, alt, text, mid = QColor(32, 32, 34), QColor(43, 43, 46), QColor(232, 232, 232), QColor(58, 58, 62)
        p.setColor(QPalette.ColorRole.Window, QColor(38, 38, 40))
        p.setColor(QPalette.ColorRole.WindowText, text)
        p.setColor(QPalette.ColorRole.Base, base)
        p.setColor(QPalette.ColorRole.AlternateBase, alt)
        p.setColor(QPalette.ColorRole.ToolTipBase, alt)
        p.setColor(QPalette.ColorRole.ToolTipText, text)
        p.setColor(QPalette.ColorRole.Text, text)
        p.setColor(QPalette.ColorRole.Button, mid)
        p.setColor(QPalette.ColorRole.ButtonText, text)
        p.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
        p.setColor(QPalette.ColorRole.Highlight, QColor(76, 130, 230))
        p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        p.setColor(QPalette.ColorRole.Link, QColor(120, 170, 255))
        p.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
            p.setColor(QPalette.ColorGroup.Disabled, role, QColor(120, 120, 120))
        app.setPalette(p)
    else:
        app.setPalette(app.style().standardPalette())


WARN_COLOR = QColor(220, 140, 30)
ERROR_COLOR = QColor(220, 60, 60)
OK_COLOR = QColor(60, 170, 90)
