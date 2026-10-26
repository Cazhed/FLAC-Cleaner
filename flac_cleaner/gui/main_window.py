"""Main window."""
from __future__ import annotations

import datetime as _dt
import html
import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
                               QScrollArea, QSpinBox, QSplitter, QTableView, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, audio, presets, processor
from ..cover import COVER_SIZES, image_info
from ..naming import TOKENS, fields_from_tags, render
from ..presets import Settings
from ..tags import clean_tags
from .common import (ERROR_COLOR, IMAGE_EXTS, OK_COLOR, WARN_COLOR, Task, app_icon, pixmap_from_bytes)
from .preview import PreviewDialog

COLUMNS = ["File", "Title", "Artist", "Album", "Bits", "Rate", "Cover", "Tags", "Status"]


# ------------------------------------------------------------------ model

class TrackModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.items: list[processor.TrackItem] = []
        self.min_px = 600

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def cover_text(self, it: processor.TrackItem) -> str:
        cur = "none" if not it.cover else f"{it.cover.width}×{it.cover.height}"
        if it.cover and it.cover.side < self.min_px:
            cur += " (low)"
        if it.manual_cover:
            return f"{cur} → your image"
        if it.found:
            return f"{cur} → {it.found.width}×{it.found.height} {it.found.source}"
        if it.looked_up and (not it.cover or it.cover.side < self.min_px):
            return f"{cur} (not found online)"
        return cur

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        it, c = self.items[index.row()], index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if role == Qt.ItemDataRole.ToolTipRole and c == 0:
                return it.path
            if role == Qt.ItemDataRole.ToolTipRole and c == 6 and it.lookup_note:
                return it.lookup_note
            return [
                it.name, it.tag("TITLE"), it.tag("ARTIST"), it.tag("ALBUM"),
                f"{it.bits}" if it.bits else "", f"{it.rate / 1000:g} kHz" if it.rate else "",
                self.cover_text(it), str(len(it.tags)), it.status,
            ][c]
        if role == Qt.ItemDataRole.ForegroundRole:
            if c == 8:
                s = it.status
                if s.startswith("Error") or s.startswith("FAILED"):
                    return ERROR_COLOR
                if s.startswith("Done"):
                    return OK_COLOR
            if c == 6 and (not it.cover or it.cover.side < self.min_px) and not (it.manual_cover or it.found):
                return WARN_COLOR
            if c == 4 and it.bits > 16:
                return WARN_COLOR
        if role == Qt.ItemDataRole.TextAlignmentRole and c in (4, 5, 7):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def add(self, items):
        known = {os.path.normcase(i.path) for i in self.items}
        items = [i for i in items if os.path.normcase(i.path) not in known]
        if not items:
            return 0
        self.beginInsertRows(QModelIndex(), len(self.items), len(self.items) + len(items) - 1)
        self.items.extend(items)
        self.endInsertRows()
        return len(items)

    def remove_rows(self, rows):
        for r in sorted(set(rows), reverse=True):
            self.beginRemoveRows(QModelIndex(), r, r)
            del self.items[r]
            self.endRemoveRows()

    def clear(self):
        self.beginResetModel()
        self.items.clear()
        self.endResetModel()

    def refresh(self):
        if self.items:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.items) - 1, len(COLUMNS) - 1))


# ------------------------------------------------------------------ drop-aware widgets

def _split_drop(event) -> tuple[list[str], list[str]]:
    paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
    images = [p for p in paths if p.lower().endswith(IMAGE_EXTS)]
    others = [p for p in paths if p not in images]
    return images, others


class TrackTable(QTableView):
    filesDropped = Signal(list)
    imageDropped = Signal(str, int)  # image path, row under cursor (-1 = none)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        images, others = _split_drop(e)
        if images:
            idx = self.indexAt(e.position().toPoint())
            self.imageDropped.emit(images[0], idx.row() if idx.isValid() else -1)
        if others:
            self.filesDropped.emit(others)
        e.acceptProposedAction()


class CoverDrop(QLabel):
    imageDropped = Signal(str)

    def __init__(self, text):
        super().__init__(text)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(170, 170)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setWordWrap(True)

    def dragEnterEvent(self, e):
        imgs, _ = _split_drop(e)
        if imgs:
            e.acceptProposedAction()

    def dropEvent(self, e):
        imgs, _ = _split_drop(e)
        if imgs:
            self.imageDropped.emit(imgs[0])
            e.acceptProposedAction()


# ------------------------------------------------------------------ main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1440, 860)
        self.setAcceptDrops(True)
        self.task: Task | None = None
        self.model = TrackModel()
        settings, self.preset_name = presets.load_last_settings()
        self._loading = False

        self._build_ui()
        self.set_settings(settings)
        self._refresh_presets(self.preset_name)
        self._check_tools()

    # ---------------- UI construction
    def _build_ui(self):
        # toolbar row
        top = QHBoxLayout()
        self.btn_add = QPushButton("Add Files…")
        self.btn_add_dir = QPushButton("Add Folder…")
        self.btn_remove = QPushButton("Remove")
        self.btn_clear = QPushButton("Clear")
        self.btn_find = QPushButton("Find HD covers")
        self.btn_preview = QPushButton("Preview changes…")
        self.btn_preview.setStyleSheet("font-weight: bold; padding: 6px 16px;")
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(240)
        self.btn_save_preset = QToolButton(text="Save as…")
        self.btn_del_preset = QToolButton(text="Delete")
        for w in (self.btn_add, self.btn_add_dir, self.btn_remove, self.btn_clear):
            top.addWidget(w)
        top.addSpacing(20)
        top.addWidget(QLabel("Preset:"))
        top.addWidget(self.preset_box)
        top.addWidget(self.btn_save_preset)
        top.addWidget(self.btn_del_preset)
        top.addStretch(1)
        top.addWidget(self.btn_find)
        top.addWidget(self.btn_preview)

        # table
        self.table = TrackTable()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((190, 130, 100, 140, 38, 62, 150, 40, 150)):
            self.table.setColumnWidth(i, w)
        hh.setStretchLastSection(True)
        self.empty_hint = QLabel("Drop .flac files or folders here\n(or use Add Files / Add Folder)\n\n"
                                 "Drop an image onto a row to use it as that track's cover.\n"
                                 "Select several rows first to give a whole album the same cover.")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet("color: palette(placeholder-text); font-size: 14px;")
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tl = QVBoxLayout(self.table.viewport())
        tl.addWidget(self.empty_hint)

        # right side panel
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(4, 0, 4, 0)
        sl.addWidget(self._cover_panel())
        sl.addWidget(self._tags_group())
        sl.addWidget(self._name_group())
        sl.addWidget(self._cover_group())
        sl.addWidget(self._audio_group())
        sl.addWidget(self._output_group())
        sl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(420)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 440])

        # bottom progress
        bottom = QHBoxLayout()
        self.status = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(360)
        self.progress.setVisible(False)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setVisible(False)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.progress)
        bottom.addWidget(self.btn_cancel)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(bottom)
        self.setCentralWidget(central)

        # signals
        self.btn_add.clicked.connect(self.add_files_dialog)
        self.btn_add_dir.clicked.connect(self.add_folder_dialog)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear_all)
        self.btn_find.clicked.connect(lambda: self.find_covers())
        self.btn_preview.clicked.connect(self.preview)
        self.btn_cancel.clicked.connect(self.cancel_task)
        self.btn_save_preset.clicked.connect(self.save_preset)
        self.btn_del_preset.clicked.connect(self.delete_preset)
        self.preset_box.activated.connect(lambda _: self.load_preset(self.preset_box.currentText()))
        self.table.filesDropped.connect(self.add_paths)
        self.table.imageDropped.connect(self._image_dropped_on_table)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self.update_cover_panel())
        self.model.rowsInserted.connect(self._update_empty)
        self.model.rowsRemoved.connect(self._update_empty)
        self.model.modelReset.connect(self._update_empty)
        QShortcut(QKeySequence.StandardKey.Delete, self.table, activated=self.remove_selected)

    def _cover_panel(self):
        box = QGroupBox("Cover of selected track")
        l = QVBoxLayout(box)
        row = QHBoxLayout()
        col1, col2 = QVBoxLayout(), QVBoxLayout()
        self.cur_cover = QLabel("—")
        self.cur_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cur_cover.setFixedSize(170, 170)
        self.cur_cover.setFrameShape(QFrame.Shape.StyledPanel)
        self.cur_caption = QLabel("Current")
        self.new_cover = CoverDrop("Drop an image here\nto use it for the\nselected tracks")
        self.new_cover.imageDropped.connect(lambda p: self.assign_cover(p, self.selected_rows()))
        self.new_caption = QLabel("New")
        for c in (self.cur_caption, self.new_caption):
            c.setWordWrap(True)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c.setMaximumWidth(180)
        col1.addWidget(self.cur_cover)
        col1.addWidget(self.cur_caption)
        col1.addStretch(1)
        col2.addWidget(self.new_cover)
        col2.addWidget(self.new_caption)
        col2.addStretch(1)
        row.addLayout(col1)
        row.addLayout(col2)
        l.addLayout(row)
        btns = QHBoxLayout()
        b1 = QPushButton("Choose image…")
        b1.clicked.connect(self.choose_cover_image)
        b2 = QPushButton("Clear chosen cover")
        b2.clicked.connect(self.clear_cover_choice)
        btns.addWidget(b1)
        btns.addWidget(b2)
        l.addLayout(btns)
        self.lookup_note = QPlainTextEdit()
        self.lookup_note.setReadOnly(True)
        self.lookup_note.setMaximumHeight(80)
        self.lookup_note.setPlaceholderText("Online lookup results appear here")
        l.addWidget(self.lookup_note)
        return box

    def _tags_group(self):
        box = QGroupBox("Tags")
        l = QVBoxLayout(box)
        l.addWidget(QLabel("Always kept: TITLE, ARTIST, ALBUM, ALBUMARTIST, DATE, TRACKNUMBER, TOTALTRACKS, "
                           "DISCNUMBER, TOTALDISCS, COPYRIGHT, PUBLISHER", wordWrap=True))
        grid = QHBoxLayout()
        c1, c2 = QVBoxLayout(), QVBoxLayout()
        self.cb_genre = QCheckBox("GENRE")
        self.cb_isrc = QCheckBox("ISRC")
        self.cb_label = QCheckBox("LABEL")
        self.cb_mb = QCheckBox("MusicBrainz IDs")
        self.cb_rg = QCheckBox("ReplayGain / R128")
        self.cb_lyrics = QCheckBox("LYRICS")
        for i, cb in enumerate((self.cb_genre, self.cb_isrc, self.cb_label, self.cb_mb, self.cb_rg, self.cb_lyrics)):
            (c1 if i < 3 else c2).addWidget(cb)
        grid.addWidget(QLabel("Also keep:"))
        grid.addLayout(c1)
        grid.addLayout(c2)
        grid.addStretch(1)
        l.addLayout(grid)
        self.cb_date = QCheckBox("Normalize DATE to YYYY-MM-DD when a full date exists")
        self.cb_disc = QCheckBox("Write DISCNUMBER 1 / TOTALDISCS 1 when missing")
        l.addWidget(self.cb_date)
        l.addWidget(self.cb_disc)
        l.addWidget(QLabel("<small>account_id, purchase_date, iTunSMPB and any value containing an email "
                           "address are always removed.</small>", wordWrap=True))
        return box

    def _name_group(self):
        box = QGroupBox("Filename")
        f = QFormLayout(box)
        self.cb_rename = QCheckBox("Rename files")
        self.ed_template = QLineEdit()
        self.ed_template.setPlaceholderText("{title} - {artist}")
        self.cb_underscore = QCheckBox("Replace spaces with underscores")
        self.name_example = QLabel()
        self.name_example.setWordWrap(True)
        self.name_example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.cb_rename)
        f.addRow("Template:", self.ed_template)
        f.addRow(QLabel("<small>Tokens: " + ", ".join("{" + t + "}" for t in TOKENS)
                        + ", {track:02} pads with zeros</small>", wordWrap=True))
        f.addRow(self.cb_underscore)
        f.addRow("Example:", self.name_example)
        return box

    def _cover_group(self):
        box = QGroupBox("Cover art")
        f = QFormLayout(box)
        self.cb_cover = QCheckBox("Add a cover when missing or low-res")
        self.sp_min = QSpinBox()
        self.sp_min.setRange(100, 3000)
        self.sp_min.setSuffix(" px")
        self.cb_resize = QCheckBox("Resize to")
        self.cmb_size = QComboBox()
        for s in COVER_SIZES:
            self.cmb_size.addItem(f"{s} × {s}", s)
        rs = QHBoxLayout()
        rs.addWidget(self.cb_resize)
        rs.addWidget(self.cmb_size)
        rs.addStretch(1)
        self.cb_norm = QCheckBox("Convert existing covers to square baseline JPEG (quality 90)")
        self.cb_online = QCheckBox("Find HD covers online automatically")
        self.cb_online.setStyleSheet("font-weight: bold;")
        f.addRow(self.cb_cover)
        f.addRow("Low-res if under:", self.sp_min)
        f.addRow(rs)
        f.addRow(self.cb_norm)
        f.addRow(self.cb_online)
        f.addRow(QLabel("<small>Tries Cover Art Archive (exact MusicBrainz release), then iTunes "
                        "(up to 3000 px), then Deezer (1000 px) and embeds the best match. Only artist and "
                        "album names and MusicBrainz IDs are sent; your files never leave your PC. "
                        "Covers are never upscaled.</small>", wordWrap=True))
        return box

    def _audio_group(self):
        box = QGroupBox("Audio")
        l = QVBoxLayout(box)
        self.cb_16 = QCheckBox("Convert 24-bit to 16-bit (TPDF dither)")
        self.cb_44 = QCheckBox("Resample 48 / 88.2 / 96 / 176.4 / 192 kHz to 44.1 kHz")
        self.warn_conv = QLabel(f"<span style='color:{WARN_COLOR.name()}'>⚠ Conversion permanently discards the "
                                "extra bit depth / sample rate. Keep your originals.</span>", wordWrap=True)
        self.cb_seek = QCheckBox("Add SEEKTABLE when missing")
        self.cb_verify = QCheckBox("Verify every output file (decode + MD5 check)")
        for w in (self.cb_16, self.cb_44, self.warn_conv, self.cb_seek, self.cb_verify):
            l.addWidget(w)
        return box

    def _output_group(self):
        box = QGroupBox("Output")
        l = QVBoxLayout(box)
        self.rb_folder = QRadioButton("Write cleaned copies to a folder (originals untouched)")
        self.rb_over = QRadioButton("Overwrite originals")
        grp = QButtonGroup(box)
        grp.addButton(self.rb_folder)
        grp.addButton(self.rb_over)
        row = QHBoxLayout()
        self.ed_out = QLineEdit()
        self.ed_out.setPlaceholderText("Choose an output folder…")
        b = QPushButton("Browse…")
        b.clicked.connect(self.choose_output)
        row.addWidget(self.ed_out, 1)
        row.addWidget(b)
        self.cb_mirror = QCheckBox("Keep sub-folder structure of added folders")
        self.cb_backup = QCheckBox(f"Move originals into a \"{processor.BACKUP_DIR}\" sub-folder first")
        l.addWidget(self.rb_folder)
        l.addLayout(row)
        l.addWidget(self.cb_mirror)
        l.addWidget(self.rb_over)
        l.addWidget(self.cb_backup)
        b2 = QPushButton("Open log file")
        b2.clicked.connect(self.open_log)
        l.addWidget(b2, alignment=Qt.AlignmentFlag.AlignLeft)
        self.rb_over.toggled.connect(self._overwrite_toggled)
        for w in (self.cb_genre, self.cb_isrc, self.cb_label, self.cb_mb, self.cb_rg, self.cb_lyrics, self.cb_date,
                  self.cb_disc, self.cb_rename, self.cb_underscore, self.cb_cover, self.cb_resize, self.cb_norm,
                  self.cb_16, self.cb_44, self.cb_seek, self.cb_verify, self.cb_mirror, self.cb_backup,
                  self.rb_folder):
            w.toggled.connect(self._settings_changed)
        self.cb_online.toggled.connect(self._online_toggled)
        self.ed_template.textChanged.connect(self._settings_changed)
        self.ed_out.textChanged.connect(self._settings_changed)
        self.sp_min.valueChanged.connect(self._settings_changed)
        self.cmb_size.currentIndexChanged.connect(self._settings_changed)
        return box

    # ---------------- settings <-> widgets
    def settings(self) -> Settings:
        return Settings(
            keep_genre=self.cb_genre.isChecked(), keep_isrc=self.cb_isrc.isChecked(),
            keep_label=self.cb_label.isChecked(), keep_musicbrainz=self.cb_mb.isChecked(),
            keep_replaygain=self.cb_rg.isChecked(), keep_lyrics=self.cb_lyrics.isChecked(),
            normalize_date=self.cb_date.isChecked(), fill_disc=self.cb_disc.isChecked(),
            rename=self.cb_rename.isChecked(), template=self.ed_template.text().strip() or "{title} - {artist}",
            spaces_to_underscores=self.cb_underscore.isChecked(),
            cover_enabled=self.cb_cover.isChecked(), cover_min_px=self.sp_min.value(),
            cover_resize=self.cb_resize.isChecked(), cover_size=self.cmb_size.currentData() or 1000,
            cover_normalize_existing=self.cb_norm.isChecked(), online_lookup=self.cb_online.isChecked(),
            convert_16bit=self.cb_16.isChecked(), resample_44k=self.cb_44.isChecked(),
            seektable=self.cb_seek.isChecked(), verify=self.cb_verify.isChecked(),
            overwrite_originals=self.rb_over.isChecked(), backup_originals=self.cb_backup.isChecked(),
            output_dir=self.ed_out.text().strip(), mirror_folders=self.cb_mirror.isChecked())

    def set_settings(self, s: Settings):
        self._loading = True
        try:
            for cb, v in ((self.cb_genre, s.keep_genre), (self.cb_isrc, s.keep_isrc), (self.cb_label, s.keep_label),
                          (self.cb_mb, s.keep_musicbrainz), (self.cb_rg, s.keep_replaygain),
                          (self.cb_lyrics, s.keep_lyrics), (self.cb_date, s.normalize_date),
                          (self.cb_disc, s.fill_disc), (self.cb_rename, s.rename),
                          (self.cb_underscore, s.spaces_to_underscores), (self.cb_cover, s.cover_enabled),
                          (self.cb_resize, s.cover_resize), (self.cb_norm, s.cover_normalize_existing),
                          (self.cb_online, s.online_lookup), (self.cb_16, s.convert_16bit),
                          (self.cb_44, s.resample_44k), (self.cb_seek, s.seektable), (self.cb_verify, s.verify),
                          (self.cb_mirror, s.mirror_folders), (self.cb_backup, s.backup_originals)):
                cb.setChecked(v)
            self.ed_template.setText(s.template)
            self.sp_min.setValue(s.cover_min_px)
            i = self.cmb_size.findData(s.cover_size)
            if i < 0:
                self.cmb_size.addItem(f"{s.cover_size} × {s.cover_size}", s.cover_size)
                i = self.cmb_size.count() - 1
            self.cmb_size.setCurrentIndex(i)
            self.ed_out.setText(s.output_dir)
            (self.rb_over if s.overwrite_originals else self.rb_folder).setChecked(True)
        finally:
            self._loading = False
        self._settings_changed()

    def _settings_changed(self, *_):
        if self._loading:
            return
        s = self.settings()
        self.warn_conv.setVisible(s.convert_16bit or s.resample_44k)
        self.cmb_size.setEnabled(s.cover_resize)
        self.ed_out.setEnabled(not s.overwrite_originals)
        self.cb_mirror.setEnabled(not s.overwrite_originals)
        self.cb_backup.setEnabled(s.overwrite_originals)
        self.ed_template.setEnabled(s.rename)
        self.model.min_px = s.cover_min_px
        self.model.refresh()
        self._update_example(s)
        presets.save_last_settings(s, self.preset_box.currentText() or self.preset_name)

    def _update_example(self, s: Settings):
        rows = self.selected_rows() or ([0] if self.model.items else [])
        it = self.model.items[rows[0]] if rows else None
        tags = clean_tags(it.tags, s.tag_options()).as_dict() if it and not it.error else {
            "TITLE": ["Ginkgo"], "ARTIST": ["Panchiko"], "ALBUM": ["Ginkgo"], "TRACKNUMBER": ["2"], "DATE": ["2025-04-04"]}
        fallback = os.path.splitext(it.name)[0] if it else "Untitled"
        name = (render(s.template, fields_from_tags(tags), s.spaces_to_underscores, fallback) if s.rename
                else fallback) + ".flac"
        self.name_example.setText(f"<code>{html.escape(name)}</code>")

    def _online_toggled(self, on: bool):
        if self._loading:
            return
        if on:
            r = QMessageBox.question(
                self, "Online cover lookup",
                "Turn on online cover lookup?\n\nFor albums with no cover or a low-res cover, the app will send "
                "the artist and album name (and MusicBrainz ID if present) to Cover Art Archive, Apple iTunes and "
                "Deezer, then embed the best matching HD cover.\n\nNo files or other tags are uploaded.")
            if r != QMessageBox.StandardButton.Yes:
                self._loading = True
                self.cb_online.setChecked(False)
                self._loading = False
                return
        self._settings_changed()

    def _overwrite_toggled(self, on: bool):
        if self._loading or not on:
            return
        r = QMessageBox.warning(
            self, "Overwrite originals?",
            "Your original files will be replaced by the cleaned versions.\n\nKeep \"Move originals into a backup "
            "sub-folder first\" switched on unless you have another backup.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if r != QMessageBox.StandardButton.Yes:
            self.rb_folder.setChecked(True)

    # ---------------- presets
    def _refresh_presets(self, select: str):
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItems(presets.list_presets())
        i = self.preset_box.findText(select)
        self.preset_box.setCurrentIndex(max(i, 0))
        self.preset_box.blockSignals(False)
        self.btn_del_preset.setEnabled(self.preset_box.currentText() not in presets.BUILTIN_PRESETS)

    def load_preset(self, name: str):
        try:
            s = presets.load_preset(name, self.settings())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Preset", f"Could not load preset:\n{e}")
            return
        self.preset_name = name
        self.set_settings(s)
        self.btn_del_preset.setEnabled(name not in presets.BUILTIN_PRESETS)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = name.strip()
        if not ok or not name:
            return
        try:
            presets.save_preset(name, self.settings())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Save preset", str(e))
            return
        self.preset_name = name
        self._refresh_presets(name)

    def delete_preset(self):
        name = self.preset_box.currentText()
        if name in presets.BUILTIN_PRESETS:
            return
        if QMessageBox.question(self, "Delete preset", f"Delete preset \"{name}\"?") == QMessageBox.StandardButton.Yes:
            presets.delete_preset(name)
            self._refresh_presets("Ginkgo format")
            self.load_preset("Ginkgo format")

    # ---------------- adding / removing
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        images, others = _split_drop(e)
        if others:
            self.add_paths(others)
        if images:
            self.assign_cover(images[0], self.selected_rows())

    def add_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add FLAC files", "", "FLAC files (*.flac);;All files (*)")
        if files:
            self.add_paths(files)

    def add_folder_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Add folder (scanned recursively)")
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]):
        if self.busy():
            return

        def work(task: Task):
            found = processor.collect_flac_paths(paths)
            batch = []
            for n, (f, root) in enumerate(found):
                if task.is_cancelled():
                    break
                task.progress.emit(n, len(found), f"Reading {os.path.basename(f)}")
                batch.append(processor.scan_file(f, root))
                if len(batch) >= 25:
                    task.partial.emit(batch)
                    batch = []
            if batch:
                task.partial.emit(batch)
            return len(found)

        def done(n):
            self.status.setText(f"{len(self.model.items)} file(s) loaded" if n else "No .flac files found")
            self._settings_changed()

        self.run_task(work, done, partial=self.model.add)

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def remove_selected(self):
        if not self.busy():
            self.model.remove_rows(self.selected_rows())

    def clear_all(self):
        if not self.busy():
            self.model.clear()
            self.update_cover_panel()

    def _update_empty(self, *_):
        self.empty_hint.setVisible(not self.model.items)

    # ---------------- covers
    def _image_dropped_on_table(self, path: str, row: int):
        rows = self.selected_rows()
        if row >= 0 and row not in rows:
            rows = [row]
            self.table.selectRow(row)
        self.assign_cover(path, rows)

    def choose_cover_image(self):
        rows = self.selected_rows()
        if not rows:
            QMessageBox.information(self, "Choose cover", "Select one or more tracks first.")
            return
        p, _ = QFileDialog.getOpenFileName(self, "Choose cover image", "",
                                           "Images (" + " ".join("*" + e for e in IMAGE_EXTS) + ")")
        if p:
            self.assign_cover(p, rows)

    def assign_cover(self, path: str, rows: list[int]):
        if not rows:
            QMessageBox.information(self, "Cover", "Select the tracks that should get this cover first "
                                                   "(or drop the image directly onto a row).")
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            info = image_info(data)
        except Exception as e:
            QMessageBox.warning(self, "Cover", f"Can't read that image:\n{e}")
            return
        for r in rows:
            it = self.model.items[r]
            it.manual_cover, it.manual_cover_name = data, os.path.basename(path)
        self.model.refresh()
        self.update_cover_panel()
        msg = f"Cover {info.width}×{info.height} assigned to {len(rows)} track(s)"
        if info.side < 600:
            msg += f" — warning: smaller than 600 px"
        self.status.setText(msg)

    def clear_cover_choice(self):
        for r in self.selected_rows():
            it = self.model.items[r]
            it.manual_cover, it.manual_cover_name = None, ""
            it.found, it.looked_up, it.lookup_note = None, False, ""
        self.model.refresh()
        self.update_cover_panel()

    def update_cover_panel(self):
        rows = self.selected_rows()
        self._update_example(self.settings())
        if not rows:
            self.cur_cover.clear()
            self.cur_cover.setText("—")
            self.cur_caption.setText("Current")
            self.new_cover.clear()
            self.new_cover.setText("Drop an image here\nto use it for the\nselected tracks")
            self.new_caption.setText("New")
            self.lookup_note.setPlainText("")
            return
        it = self.model.items[rows[0]]
        data = processor.existing_cover_bytes(it.path) if it.cover else None
        pm = pixmap_from_bytes(data, 168)
        if pm:
            self.cur_cover.setPixmap(pm)
            self.cur_caption.setText(f"Current: {it.cover.describe()}")
        else:
            self.cur_cover.clear()
            self.cur_cover.setText("No cover")
            self.cur_caption.setText("Current: none")
        new_data, cap = None, "New: (none chosen)"
        if it.manual_cover:
            new_data = it.manual_cover
            info = image_info(new_data)
            cap = f"New: {info.describe()}\nfrom {it.manual_cover_name}"
        elif it.found:
            new_data = it.found.data
            cap = (f"New: {it.found.width}×{it.found.height}, {len(it.found.data) / 1024:.0f} KB\n"
                   f"{it.found.source}, match {it.found.match * 100:.0f}%")
        s = self.settings()
        if new_data and s.cover_resize and image_info(new_data).side > s.cover_size:
            cap += f"\n(will be resized to {s.cover_size} px)"
        pm = pixmap_from_bytes(new_data, 168)
        if pm:
            self.new_cover.setPixmap(pm)
        else:
            self.new_cover.clear()
            self.new_cover.setText("Drop an image here\nto use it for the\nselected tracks")
        self.new_caption.setText(cap + (f"\n({len(rows)} tracks selected)" if len(rows) > 1 else ""))
        self.lookup_note.setPlainText(it.lookup_note)

    def find_covers(self, then=None):
        s = self.settings()
        if not s.online_lookup:
            self.cb_online.setChecked(True)  # asks for confirmation
            s = self.settings()
            if not s.online_lookup:
                return
        if not s.cover_enabled:
            self.cb_cover.setChecked(True)
            s = self.settings()
        items = list(self.model.items)
        if not items or self.busy():
            return

        def work(task: Task):
            processor.lookup_covers(items, s, task.is_cancelled, task.progress.emit, force=False)

        def done(_):
            self.model.refresh()
            self.update_cover_panel()
            found = sum(1 for i in items if i.found)
            missing = sum(1 for i in items if processor.needs_cover(i, s) and not i.found and not i.manual_cover)
            self.status.setText(f"Cover lookup done: HD cover found for {found} track(s)"
                                + (f", nothing found for {missing}" if missing else ""))
            if callable(then):
                then()

        self.run_task(work, done)

    # ---------------- preview & apply
    def choose_output(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder", self.ed_out.text())
        if d:
            self.ed_out.setText(d)

    def preview(self):
        if self.busy() or not self.model.items:
            if not self.model.items:
                self.status.setText("Add some FLAC files first")
            return
        s = self.settings()
        if not s.overwrite_originals and not s.output_dir:
            self.choose_output()
            s = self.settings()
            if not s.output_dir:
                return
        pending = [i for i in self.model.items if s.online_lookup and processor.needs_cover(i, s)
                   and not i.manual_cover and not i.looked_up and not i.error]
        if pending:
            self.find_covers(then=self._build_preview)
        else:
            self._build_preview()

    def _build_preview(self):
        s = self.settings()
        items = list(self.model.items)

        def work(task: Task):
            return processor.build_plans(items, s, task.is_cancelled, task.progress.emit)

        def done(plans):
            dlg = PreviewDialog(plans, s.overwrite_originals, self)
            if dlg.exec() == PreviewDialog.DialogCode.Accepted:
                self._apply(plans, s)

        self.run_task(work, done)

    def _apply(self, plans, s: Settings):
        if s.overwrite_originals:
            r = QMessageBox.warning(self, "Overwrite originals",
                                    "This will replace your original files"
                                    + (" (originals are moved to a backup sub-folder first)." if s.backup_originals
                                       else ". There will be NO backup.") + "\n\nApply now?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        if any(p.converts and not p.error for p in plans):
            r = QMessageBox.warning(self, "Lossy conversion",
                                    "Some files will be converted (bit depth and/or sample rate). The removed "
                                    "resolution can't be recovered from the output.\n\nKeep your original files. "
                                    "Continue?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        started = _dt.datetime.now().isoformat(timespec="seconds")
        preset = self.preset_box.currentText()

        def work(task: Task):
            entries = []
            for n, p in enumerate(plans):
                if task.is_cancelled():
                    break
                task.progress.emit(n, len(plans), f"Writing {p.item.name}")
                if p.error:
                    e = {"source": p.item.path, "status": "SKIPPED", "error": p.error}
                else:
                    e = processor.apply_plan(p, s)
                entries.append(e)
                task.partial.emit((p, e))
            task.progress.emit(len(plans), len(plans), "Writing log")
            logs = processor.write_log(entries, s, preset, started)
            return entries, logs, task.is_cancelled()

        def on_file(pe):
            p, e = pe
            it = p.item
            if e.get("status") == "OK":
                it.status = "Done → " + os.path.basename(p.dst)
                if s.overwrite_originals:
                    fresh = processor.scan_file(p.dst, it.root)
                    fresh.status = it.status
                    idx = self.model.items.index(it)
                    self.model.items[idx] = fresh
            else:
                it.status = "Error: " + e.get("error", "unknown")
            self.model.refresh()

        def done(res):
            entries, logs, cancelled = res
            ok = sum(1 for e in entries if e.get("status") == "OK")
            bad = len(entries) - ok
            self._last_logs = logs
            msg = f"{ok} file(s) written" + (f", {bad} failed/skipped" if bad else "") + (" (cancelled)" if cancelled else "")
            self.status.setText(msg)
            box = QMessageBox(self)
            box.setWindowTitle("Finished")
            box.setIcon(QMessageBox.Icon.Warning if bad else QMessageBox.Icon.Information)
            box.setText(msg + ".")
            fails = [f"{os.path.basename(e.get('source', ''))}: {e.get('error', '')}" for e in entries
                     if e.get("status") != "OK"]
            if fails:
                box.setDetailedText("\n".join(fails))
            open_btn = None
            if not s.overwrite_originals and s.output_dir:
                open_btn = box.addButton("Open output folder", QMessageBox.ButtonRole.ActionRole)
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if open_btn is not None and box.clickedButton() is open_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(s.output_dir))

        self.run_task(work, done, partial=on_file)

    def open_log(self):
        s = self.settings()
        candidates = []
        if not s.overwrite_originals and s.output_dir:
            candidates.append(os.path.join(s.output_dir, processor.LOG_NAME))
        candidates.append(os.path.join(presets.app_dir(), processor.LOG_NAME))
        for p in candidates:
            if os.path.exists(p):
                QDesktopServices.openUrl(QUrl.fromLocalFile(p))
                return
        QMessageBox.information(self, "Log", "No log yet. It's created the first time you click Apply.")

    # ---------------- task plumbing
    def busy(self) -> bool:
        return self.task is not None and self.task.isRunning()

    def run_task(self, fn, on_result, partial=None):
        if self.busy():
            return
        task = Task(fn, self)
        self.task = task
        self._set_busy(True)
        task.progress.connect(self._on_progress)
        if partial:
            task.partial.connect(partial)
        box: dict = {}
        task.result.connect(lambda r: box.__setitem__("r", r))
        task.failed.connect(lambda msg: QMessageBox.critical(self, "Error", msg))
        task.cancelled.connect(lambda: self.status.setText("Cancelled"))

        def finished():  # run the callback only once the thread is fully done, so it can start another task
            self._set_busy(False)
            self.task = None
            if "r" in box:
                on_result(box["r"])

        task.finished.connect(finished)
        task.start()

    def _on_progress(self, n, total, msg):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(n)
        self.status.setText(msg)

    def cancel_task(self):
        if self.task:
            self.task.cancel()
            self.status.setText("Cancelling…")

    def _set_busy(self, busy: bool):
        for w in (self.btn_add, self.btn_add_dir, self.btn_remove, self.btn_clear, self.btn_find, self.btn_preview,
                  self.preset_box, self.btn_save_preset):
            w.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.btn_cancel.setVisible(busy)
        if busy:
            self.progress.setValue(0)

    def _check_tools(self):
        try:
            audio.find_tool("flac")
            audio.find_tool("metaflac")
        except audio.ToolMissing as e:
            QTimer.singleShot(300, lambda: QMessageBox.critical(self, "Missing component", str(e)))

    def closeEvent(self, e):
        if self.busy():
            if QMessageBox.question(self, "Quit", "A job is running. Cancel it and quit?") != \
                    QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self.task.cancel()
            self.task.wait(15000)
        presets.save_last_settings(self.settings(), self.preset_box.currentText())
        e.accept()
