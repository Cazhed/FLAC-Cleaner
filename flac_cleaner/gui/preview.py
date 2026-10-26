"""'Preview changes' dialog: nothing is written until the user clicks Apply."""
from __future__ import annotations

import html
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

from ..cover import image_info
from ..processor import Plan
from .common import ERROR_COLOR, WARN_COLOR, pixmap_from_bytes

COLS = ["Old name", "New name", "Tags removed", "Tags changed", "Cover", "Conversion"]


class PreviewDialog(QDialog):
    def __init__(self, plans: list[Plan], overwrite: bool, parent=None):
        super().__init__(parent)
        self.plans = plans
        self.setWindowTitle("Preview changes")
        self.resize(1250, 720)
        self._icons: dict[int, QIcon] = {}

        ok = [p for p in plans if not p.error]
        covers = sum(1 for p in ok if p.cover_action == "embed")
        conv = sum(1 for p in ok if p.converts)
        warn = sum(1 for p in ok if p.cover_warning)
        errs = len(plans) - len(ok)
        where = "originals will be replaced" if overwrite else f"output folder: {os.path.dirname(ok[0].dst) if ok else ''}"
        summary = QLabel(
            f"<b>{len(ok)}</b> file(s) ready · <b>{covers}</b> cover(s) to embed · <b>{conv}</b> conversion(s)"
            + (f" · <span style='color:{WARN_COLOR.name()}'><b>{warn}</b> cover warning(s)</span>" if warn else "")
            + (f" · <span style='color:{ERROR_COLOR.name()}'><b>{errs}</b> can't be processed</span>" if errs else "")
            + f"<br><small>{html.escape(where)} · Nothing has been written yet.</small>")

        self.table = QTableWidget(len(plans), len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(40, 40))
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((230, 230, 95, 95, 330, 170)):
            self.table.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        for r, p in enumerate(plans):
            self._fill_row(r, p)
        self.table.currentCellChanged.connect(lambda r, *_: self._show_details(r))

        self.cover = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover.setMinimumSize(300, 300)
        self.cover.setStyleSheet("border: 1px solid palette(mid);")
        self.cover_caption = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover_caption.setWordWrap(True)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.cover)
        rl.addWidget(self.cover_caption)
        rl.addWidget(self.details, 1)

        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([880, 340])

        buttons = QDialogButtonBox()
        self.apply_btn = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.apply_btn.setEnabled(bool(ok))
        self.apply_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(summary)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)
        if plans:
            self.table.selectRow(0)
            self._show_details(0)

    def _icon(self, data: bytes) -> QIcon:
        key = id(data)
        if key not in self._icons:
            pm = pixmap_from_bytes(data, 40)
            self._icons[key] = QIcon(pm) if pm else QIcon()
        return self._icons[key]

    def _fill_row(self, r: int, p: Plan) -> None:
        def item(text: str, tip: str = "") -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            it.setToolTip(tip or text)
            return it

        self.table.setItem(r, 0, item(p.item.name, p.item.path))
        if p.error:
            bad = item("Error: " + p.error)
            bad.setForeground(ERROR_COLOR)
            self.table.setItem(r, 1, bad)
            return
        new = os.path.basename(p.dst)
        self.table.setItem(r, 1, item(new + ("" if new != p.item.name else "  (same)"), p.dst))
        t = p.tags
        self.table.setItem(r, 2, item(f"{len(t.removed) + len(t.privacy_removed)}",
                                      ", ".join(t.privacy_removed + t.removed)))
        self.table.setItem(r, 3, item(f"{len(t.changed)}", "\n".join(f"{k}: {o} → {n}" for k, o, n in t.changed)))
        c = item(p.cover_text + (f"  ⚠ {p.cover_warning}" if p.cover_warning else ""))
        if p.cover_jpeg:
            c.setIcon(self._icon(p.cover_jpeg))
        if p.cover_warning:
            c.setForeground(WARN_COLOR)
        self.table.setItem(r, 4, c)
        conv = item(p.conversion_text)
        if p.converts:
            conv.setForeground(WARN_COLOR)
        self.table.setItem(r, 5, conv)

    def _show_details(self, r: int) -> None:
        if r < 0 or r >= len(self.plans):
            return
        p = self.plans[r]
        e = html.escape
        if p.cover_jpeg:
            self.cover.setPixmap(pixmap_from_bytes(p.cover_jpeg, 300))
            info = image_info(p.cover_jpeg)
            self.cover_caption.setText(f"<b>{info.width} × {info.height} px</b> · {info.size_bytes / 1024:.0f} KB · "
                                       f"baseline JPEG<br><small>{e(p.cover_text)}</small>")
        else:
            self.cover.clear()
            self.cover.setText("(existing cover kept)" if p.cover_action == "keep" and p.item.cover else "(no cover)")
            self.cover_caption.setText(e(p.cover_text))
        if p.error:
            self.details.setHtml(f"<p style='color:{ERROR_COLOR.name()}'>{e(p.error)}</p>")
            return
        t = p.tags
        parts = [f"<p><b>From:</b> {e(p.item.path)}<br><b>To:</b> {e(p.dst)}</p>",
                 "<p><b>Final tags</b><br>" + "<br>".join(f"{e(k)} = {e(v)}" for k, v in t.tags) + "</p>"]
        if t.changed:
            parts.append("<p><b>Changed</b><br>" + "<br>".join(f"{e(k)}: {e(o)} → {e(n)}" for k, o, n in t.changed) + "</p>")
        if t.privacy_removed:
            parts.append(f"<p><b style='color:{WARN_COLOR.name()}'>Private data removed</b><br>"
                         + ", ".join(e(k) for k in t.privacy_removed) + "</p>")
        if t.removed:
            parts.append(f"<p><b>Removed ({len(t.removed)})</b><br>" + ", ".join(e(k) for k in t.removed) + "</p>")
        parts.append(f"<p><b>Audio:</b> {e(p.conversion_text)}</p>")
        if p.item.lookup_note:
            parts.append("<p><b>Cover lookup results</b><br>" + e(p.item.lookup_note).replace("\n", "<br>") + "</p>")
        self.details.setHtml("".join(parts))
