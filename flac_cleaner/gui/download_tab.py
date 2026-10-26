"""'Search & Download' tab: Soulseek search, automatic best-pick download, then FLAC Cleaner."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import secret
from ..slsk import Job, LoginError, SoulseekService, expected_output
from ..tracklist import clean_title
from .import_dialog import ImportDialog
from ..slsk_rank import FolderHit
from .common import ERROR_COLOR, OK_COLOR, WARN_COLOR

RESULT_COLS = ["Score", "Name", "User", "Format", "Tracks", "Size", "Length", "Speed", "Slot", "Notes"]
JOB_COLS = ["Search", "Type", "Source", "Progress", "Status", "Saved as"]


class _Bridge(QObject):
    event = Signal(str, object)


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB"


def _dur(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}" if sec else ""


class DownloadTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._bridge = _Bridge()
        self._bridge.event.connect(self._on_event)
        self.svc = SoulseekService(lambda kind, obj: self._bridge.event.emit(kind, obj))
        self._settings_snapshot = None
        self.svc.settings_provider = lambda: self._settings_snapshot
        self.cfg = self.svc.config
        self.jobs: list[Job] = []
        self.candidates: list = []
        self._results_job: Job | None = None
        self._build()
        self._load_cfg()
        if self.cfg.auto_login and self.cfg.username and self.cfg.password_token:
            QTimer.singleShot(500, self.login)

    # ---------------------------------------------------------------- UI
    def _build(self):
        acct = QGroupBox("Soulseek account")
        al = QHBoxLayout(acct)
        self.ed_user = QLineEdit(placeholderText="Username")
        self.ed_pass = QLineEdit(placeholderText="Password")
        self.ed_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.cb_remember = QCheckBox("Remember password")
        self.cb_auto = QCheckBox("Log in at start")
        self.btn_login = QPushButton("Log in")
        self.btn_logout = QPushButton("Log out")
        self.lbl_status = QLabel("Offline")
        self.lbl_status.setStyleSheet("font-weight: bold;")
        for w in (self.ed_user, self.ed_pass, self.cb_remember, self.cb_auto, self.btn_login, self.btn_logout):
            al.addWidget(w)
        al.addWidget(self.lbl_status, 1)

        search = QGroupBox("Search")
        sl = QHBoxLayout(search)
        self.ed_query = QLineEdit(placeholderText="Artist and song title, or artist and album (e.g. Panchiko Ginkgo)")
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("Song", "song")
        self.cmb_mode.addItem("Album", "album")
        self.btn_go = QPushButton("Search && download")
        self.btn_go.setStyleSheet("font-weight: bold; padding: 5px 14px;")
        self.btn_search = QPushButton("Search only")
        sl.addWidget(self.ed_query, 1)
        sl.addWidget(self.cmb_mode)
        sl.addWidget(self.btn_search)
        sl.addWidget(self.btn_go)
        self.btn_list = QPushButton("Song list / playlist CSV…")
        self.btn_list.setToolTip("Download many songs at once: paste 'Artist - Title' lines or open a playlist "
                                 "CSV exported from Spotify (exportify.app)")
        sl.addWidget(self.btn_list)

        self.results = QTableWidget(0, len(RESULT_COLS))
        self.results.setHorizontalHeaderLabels(RESULT_COLS)
        self._table_defaults(self.results, (50, 330, 110, 140, 50, 70, 55, 80, 70))
        self.results_label = QLabel("Results: the best match is picked automatically. Double-click another one "
                                    "to download it instead.")
        rbox = QWidget()
        rl = QVBoxLayout(rbox)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.results_label)
        rl.addWidget(self.results)

        self.jobs_table = QTableWidget(0, len(JOB_COLS))
        self.jobs_table.setHorizontalHeaderLabels(JOB_COLS)
        self._table_defaults(self.jobs_table, (220, 60, 110, 140, 330))
        jb = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel selected")
        self.btn_clear = QPushButton("Clear finished")
        self.btn_open = QPushButton("Open cleaned folder")
        jb.addWidget(QLabel("Downloads"))
        jb.addStretch(1)
        for b in (self.btn_cancel, self.btn_clear, self.btn_open):
            jb.addWidget(b)
        jbox = QWidget()
        jl = QVBoxLayout(jbox)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.addLayout(jb)
        jl.addWidget(self.jobs_table)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(rbox)
        split.addWidget(jbox)
        split.setSizes([330, 260])

        opts = QGroupBox("Download settings")
        f = QFormLayout(opts)
        self.ed_dl = QLineEdit()
        self.ed_clean = QLineEdit()
        self.cmb_bits = QComboBox()
        self.cmb_bits.addItem("16-bit (CD, like the Ginkgo file)", 16)
        self.cmb_bits.addItem("24-bit (hi-res)", 24)
        self.cmb_bits.addItem("Any", 0)
        self.cb_share = QCheckBox("Share my downloads folder (Soulseek users often block people who share nothing)")
        self.cb_keep = QCheckBox("Keep the original downloads (needed for sharing)")
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65534)
        self.cb_upnp = QCheckBox("Open the port on my router automatically (UPnP)")
        self.sp_giveup = QSpinBox()
        self.sp_giveup.setRange(15, 3600)
        self.sp_giveup.setSuffix(" s")
        self.sp_giveup.setMaximumWidth(110)
        self.sp_giveup.setToolTip("If a user keeps you queued (no data) this long, try the next best source")
        f.addRow("Original downloads:", self._folder_row(self.ed_dl))
        f.addRow("Cleaned files:", self._folder_row(self.ed_clean))
        f.addRow("Prefer:", self.cmb_bits)
        f.addRow(self.cb_share)
        f.addRow(self.cb_keep)
        pr = QHBoxLayout()
        pr.addWidget(self.sp_port)
        pr.addWidget(self.cb_upnp)
        pr.addStretch(1)
        f.addRow("Listening port:", pr)
        f.addRow("Give up on a queued user after:", self.sp_giveup)
        f.addRow(QLabel("<small>Cleaning uses everything on the <b>Clean files</b> tab: preset, tags, filename, "
                        "cover size, online HD cover lookup and bit-depth conversion. If no HD cover is found online, "
                        "the cover image from the uploader's folder is used. Download only music you're allowed to "
                        "share.</small>", wordWrap=True))

        lay = QVBoxLayout(self)
        lay.addWidget(acct)
        lay.addWidget(search)
        lay.addWidget(split, 1)
        lay.addWidget(opts)

        self.btn_login.clicked.connect(self.login)
        self.btn_logout.clicked.connect(self.logout)
        self.ed_pass.returnPressed.connect(self.login)
        self.btn_go.clicked.connect(lambda: self.search(download=True))
        self.btn_search.clicked.connect(lambda: self.search(download=False))
        self.btn_list.clicked.connect(lambda: self.import_list())
        self.ed_query.returnPressed.connect(lambda: self.search(download=True))
        self.results.cellDoubleClicked.connect(self._download_result)
        self.btn_cancel.clicked.connect(self._cancel_selected)
        self.btn_clear.clicked.connect(self._clear_finished)
        self.btn_open.clicked.connect(lambda: self._open(self.ed_clean.text()))
        self.jobs_table.cellDoubleClicked.connect(self._job_details)
        for w in (self.ed_dl, self.ed_clean):
            w.editingFinished.connect(self._save_cfg)
        for w in (self.cb_share, self.cb_keep, self.cb_upnp, self.cb_remember, self.cb_auto):
            w.toggled.connect(self._save_cfg)
        self.cmb_bits.currentIndexChanged.connect(self._save_cfg)
        self.sp_port.valueChanged.connect(self._save_cfg)
        self.sp_giveup.valueChanged.connect(self._save_cfg)
        self.setAcceptDrops(True)
        self._set_online(False)

    @staticmethod
    def _table_defaults(t: QTableWidget, widths):
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(24)
        t.setAlternatingRowColors(True)
        t.setWordWrap(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        t.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate(widths):
            t.setColumnWidth(i, w)

    def _folder_row(self, edit: QLineEdit) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        b = QPushButton("Browse…")

        def pick():
            d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
            if d:
                edit.setText(d)
                self._save_cfg()

        b.clicked.connect(pick)
        h.addWidget(edit, 1)
        h.addWidget(b)
        return w

    # ---------------------------------------------------------------- config
    def _load_cfg(self):
        c = self.cfg
        widgets = (self.ed_user, self.cb_remember, self.cb_auto, self.ed_dl, self.ed_clean, self.cb_share,
                   self.cb_keep, self.cb_upnp, self.sp_port, self.cmb_bits, self.sp_giveup)
        for w in widgets:
            w.blockSignals(True)
        self.ed_user.setText(c.username)
        try:
            self.ed_pass.setText(secret.unprotect(c.password_token) if c.remember else "")
        except Exception:
            self.ed_pass.setText("")
        self.cb_remember.setChecked(c.remember)
        self.cb_auto.setChecked(c.auto_login)
        self.ed_dl.setText(c.download_dir)
        self.ed_clean.setText(c.cleaned_dir)
        self.cb_share.setChecked(c.share_downloads)
        self.cb_keep.setChecked(c.keep_raw)
        self.cb_upnp.setChecked(c.upnp)
        self.sp_port.setValue(c.listen_port)
        self.sp_giveup.setValue(c.give_up_seconds)
        self.cmb_bits.setCurrentIndex(max(self.cmb_bits.findData(c.prefer_bits), 0))
        for w in widgets:
            w.blockSignals(False)

    def _save_cfg(self, *_):
        c = self.cfg
        c.username = self.ed_user.text().strip()
        c.remember = self.cb_remember.isChecked()
        c.auto_login = self.cb_auto.isChecked() and c.remember
        if c.remember and self.ed_pass.text():
            try:
                c.password_token = secret.protect(self.ed_pass.text())
            except Exception:
                c.password_token = ""
        elif not c.remember:
            c.password_token = ""
        c.download_dir = self.ed_dl.text().strip() or c.download_dir
        c.cleaned_dir = self.ed_clean.text().strip() or c.cleaned_dir
        c.share_downloads = self.cb_share.isChecked()
        c.keep_raw = self.cb_keep.isChecked()
        c.upnp = self.cb_upnp.isChecked()
        c.listen_port = self.sp_port.value()
        c.give_up_seconds = self.sp_giveup.value()
        c.prefer_bits = self.cmb_bits.currentData()
        c.save()

    # ---------------------------------------------------------------- login
    def _set_online(self, online: bool):
        for w in (self.ed_query, self.cmb_mode, self.btn_go, self.btn_search, self.btn_list):
            w.setEnabled(online)
        self.btn_logout.setEnabled(online)
        self.btn_login.setEnabled(not online)
        for w in (self.ed_user, self.ed_pass, self.sp_port, self.cb_upnp, self.cb_share, self.ed_dl):
            w.setEnabled(not online)

    def login(self):
        user, pw = self.ed_user.text().strip(), self.ed_pass.text()
        if not user or not pw:
            QMessageBox.information(self, "Soulseek", "Enter your Soulseek username and password.\n\n"
                                    "If the username is new, the Soulseek server creates the account for you.")
            return
        self._save_cfg()
        self.btn_login.setEnabled(False)
        self.lbl_status.setText("Connecting…")
        fut = self.svc.login(user, pw)
        fut.add_done_callback(lambda f: self._bridge.event.emit("login_done", f))

    def logout(self):
        self.svc.logout()
        self._set_online(False)

    # ---------------------------------------------------------------- search / jobs
    def _snapshot_settings(self):
        self._settings_snapshot = self.main.settings()

    def search(self, download: bool):
        q = self.ed_query.text().strip()
        if len(q) < 3:
            return
        self._snapshot_settings()
        self._save_cfg()
        job = self.svc.start_job(q, self.cmb_mode.currentData(), search_only=not download)
        self._results_job = job
        self.results.setRowCount(0)
        self.results_label.setText(f"Searching for “{q}”…")

    def import_list(self, path: str | None = None):
        if not self.svc.online:
            QMessageBox.information(self, "Soulseek", "Log in to Soulseek first.")
            return
        dlg = ImportDialog(self, path)
        if dlg.exec() != ImportDialog.DialogCode.Accepted or not dlg.tracks:
            return
        self._snapshot_settings()
        self._save_cfg()
        settings = self._settings_snapshot
        folder = os.path.join(self.cfg.cleaned_dir, dlg.subfolder) if dlg.subfolder else self.cfg.cleaned_dir
        started = 0
        for t in dlg.tracks:
            q = " ".join(x for x in (t.artists[0] if t.artists else "", clean_title(t.title)) if x)
            skip = expected_output(t, settings, folder) if dlg.skip_existing else ""
            self.svc.start_job(q, "song", track=t, subfolder=dlg.subfolder, skip_path=skip)
            started += 1
        self.results_label.setText(f"Queued {started} song(s) from the list. They download two at a time; "
                                   "each one picks its own best source.")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith((".csv", ".txt"))
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith((".csv", ".txt")):
                self.import_list(u.toLocalFile())
                break

    def _download_result(self, row: int, _col: int):
        if row >= len(self.candidates) or not self._results_job:
            return
        cand = self.candidates[row]
        self._snapshot_settings()
        mode = "album" if isinstance(cand, FolderHit) else "song"
        self.svc.start_job(self._results_job.query, mode, forced=cand)

    def _cancel_selected(self):
        for r in {i.row() for i in self.jobs_table.selectedIndexes()}:
            if r < len(self.jobs) and not self.jobs[r].done:
                self.svc.cancel_job(self.jobs[r])

    def _clear_finished(self):
        self.jobs = [j for j in self.jobs if not j.done]
        self._render_jobs()

    def _job_details(self, row: int, _col: int):
        if row < len(self.jobs):
            j = self.jobs[row]
            text = j.detail or "\n".join(j.attempts) or j.status
            if j.outputs:
                text = "Saved:\n" + "\n".join(j.outputs) + ("\n\n" + j.detail if j.detail else "")
            QMessageBox.information(self, j.query, text)

    @staticmethod
    def _open(path: str):
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---------------------------------------------------------------- events from the Soulseek thread
    def _on_event(self, kind: str, obj):
        if kind == "status":
            self.lbl_status.setText(obj)
        elif kind == "login_done":
            try:
                obj.result()
                self._set_online(True)
            except LoginError as e:
                self._set_online(False)
                self.lbl_status.setText("Offline")
                QMessageBox.warning(self, "Soulseek", str(e))
            except Exception as e:
                self._set_online(False)
                self.lbl_status.setText("Offline")
                QMessageBox.warning(self, "Soulseek", f"Login failed: {e}")
        elif kind == "job":
            if obj not in self.jobs and not obj.search_only:
                self.jobs.insert(0, obj)
            if obj.search_only and obj.done:
                self.results_label.setText(obj.status)
            self._render_jobs()
        elif kind == "results":
            job, cands = obj
            if job is self._results_job:
                self._show_results(job, cands)

    def _show_results(self, job: Job, cands: list):
        self.candidates = cands
        self.results_label.setText(f"{len(cands)} usable result(s) for “{job.query}”. "
                                   + ("The top one is being downloaded; double-click another to use it instead."
                                      if not job.search_only else "Double-click one to download it."))
        self.results.setRowCount(len(cands))
        for r, c in enumerate(cands):
            if isinstance(c, FolderHit):
                vals = [f"{c.score:.0f}", c.name, c.username, c.format_text(), str(len(c.tracks)), _mb(c.size),
                        _dur(sum(t.duration for t in c.tracks)), f"{c.speed / 1e6:.1f} MB/s",
                        "free" if c.free_slot else f"queue {c.queue}", "; ".join(c.notes)]
                tip = c.folder
            else:
                vals = [f"{c.score:.0f}", c.name, c.username, c.format_text(), "1", _mb(c.size), _dur(c.duration),
                        f"{c.speed / 1e6:.1f} MB/s", "free" if c.free_slot else f"queue {c.queue}",
                        "; ".join(c.notes)]
                tip = c.path
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setToolTip(tip if col == 1 else v)
                if r == 0 and not job.search_only:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.results.setItem(r, col, it)

    def _render_jobs(self):
        self.jobs_table.setRowCount(len(self.jobs))
        for r, j in enumerate(self.jobs):
            vals = [j.track.label_text() if j.track else j.query, j.mode.title(), j.source, None, j.status,
                    ", ".join(os.path.basename(o) for o in j.outputs)]
            for col, v in enumerate(vals):
                if col == 3:
                    bar = self.jobs_table.cellWidget(r, 3)
                    if not isinstance(bar, QProgressBar):
                        bar = QProgressBar()
                        bar.setTextVisible(True)
                        self.jobs_table.setCellWidget(r, 3, bar)
                    bar.setValue(int(j.progress * 100))
                    bar.setFormat(f"%p%  {j.speed / 1e6:.1f} MB/s" if j.speed > 0 else "%p%")
                    continue
                it = QTableWidgetItem(v)
                it.setToolTip(j.detail or v)
                if col == 4:
                    if j.done and j.ok:
                        it.setForeground(OK_COLOR)
                    elif j.done:
                        it.setForeground(ERROR_COLOR)
                    elif "trying another" in j.status or "Queued" in j.status:
                        it.setForeground(WARN_COLOR)
                self.jobs_table.setItem(r, col, it)

    # ---------------------------------------------------------------- shutdown
    def confirm_close(self) -> bool:
        running = [j for j in self.jobs if not j.done]
        if not running:
            return True
        return QMessageBox.question(self, "Quit", f"{len(running)} download(s) still running. Quit anyway?") == \
            QMessageBox.StandardButton.Yes

    def shutdown(self):
        self._save_cfg()
        self.svc.shutdown()
