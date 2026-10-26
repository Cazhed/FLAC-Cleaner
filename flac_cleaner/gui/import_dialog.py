"""Dialog to import a song list (Spotify playlist CSV export or 'Artist - Title' lines)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..naming import sanitize
from ..tracklist import TrackInfo, looks_like_csv, parse_csv, parse_text
from .common import WARN_COLOR

HELP = ("<b>Paste a song list or open a playlist CSV.</b><br>"
        "• <b>Spotify playlist / album:</b> export it as CSV with "
        "<a href='https://exportify.app'>exportify.app</a> (log in with Spotify, click <i>Export</i>), then "
        "open the .csv here. Songs then get Spotify's exact title, artist, album, track number, date and the "
        "640×640 Spotify cover. For an album or someone else's playlist, first select all songs in Spotify "
        "(Ctrl+A) → <i>Add to playlist</i> → <i>New playlist</i>, then export that playlist.<br>"
        "• <b>Plain list:</b> one song per line as <code>Artist - Title</code>.")


class ImportDialog(QDialog):
    def __init__(self, parent=None, path: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Download a song list")
        self.resize(1000, 640)
        self.setAcceptDrops(True)
        self.tracks: list[TrackInfo] = []

        info = QLabel(HELP)
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        info.setTextFormat(Qt.TextFormat.RichText)

        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Panchiko - Ginkgo\nKalman - In Love With Myself\n…\n\n"
                                     "(or drop / open a .csv file)")
        btn_open = QPushButton("Open CSV…")
        btn_open.clicked.connect(self._open)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.text, 1)
        ll.addWidget(btn_open, alignment=Qt.AlignmentFlag.AlignLeft)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Title", "Artist", "Album", "Length", "Spotify info"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((220, 150, 170, 55)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setStretchLastSection(True)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.table)
        split.setSizes([330, 670])

        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setOpenExternalLinks(True)
        self.warn.setStyleSheet(f"color: {WARN_COLOR.name()};")
        self.ed_sub = QLineEdit()
        self.ed_sub.setPlaceholderText("(none: straight into the Cleaned files folder)")
        self.cb_skip = QCheckBox("Skip songs that are already in the cleaned folder")
        self.cb_skip.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(QLabel("Save into sub-folder:"))
        row.addWidget(self.ed_sub, 1)
        row.addWidget(self.cb_skip)

        self.buttons = QDialogButtonBox()
        self.ok_btn = self.buttons.addButton("Download", QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(split, 1)
        lay.addWidget(self.warn)
        lay.addLayout(row)
        lay.addWidget(self.buttons)

        self.text.textChanged.connect(self._parse)
        self._parse()
        if path:
            self.load_file(path)

    @property
    def subfolder(self) -> str:
        return sanitize(self.ed_sub.text()).strip(" .")

    @property
    def skip_existing(self) -> bool:
        return self.cb_skip.isChecked()

    def _open(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open playlist CSV", "", "CSV / text (*.csv *.txt);;All files (*)")
        if p:
            self.load_file(p)

    def load_file(self, path: str):
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as fh:
                data = fh.read()
        except OSError as e:
            self.warn.setText(f"Can't read the file: {e}")
            return
        self.text.setPlainText(data)
        if not self.ed_sub.text():
            self.ed_sub.setText(os.path.splitext(os.path.basename(path))[0].replace("_", " "))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.load_file(u.toLocalFile())
                break

    def _parse(self):
        text = self.text.toPlainText()
        links: list[str] = []
        warn = ""
        try:
            if looks_like_csv(text):
                self.tracks = parse_csv(text)
            else:
                self.tracks, links = parse_text(text)
        except ValueError as e:
            self.tracks, warn = [], str(e)
        if links:
            warn = (f"{len(links)} Spotify link(s) were skipped: reading Spotify links needs a Spotify developer "
                    "key, which Spotify only gives to Premium accounts. Export the playlist or album as CSV with "
                    "<a href='https://exportify.app'>exportify.app</a> and open that file instead.")
        self.warn.setText(warn)
        self.warn.setVisible(bool(warn))
        self.table.setRowCount(len(self.tracks))
        for r, t in enumerate(self.tracks):
            vals = [t.title, t.artist, t.album, f"{t.duration // 60}:{t.duration % 60:02d}" if t.duration else "",
                    "✓ tags + cover" if t.from_spotify and t.image_url else ("✓ tags" if t.from_spotify else "")]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.ok_btn.setText(f"Download {len(self.tracks)} song(s)" if self.tracks else "Download")
        self.ok_btn.setEnabled(bool(self.tracks))
