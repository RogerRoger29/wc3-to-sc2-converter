"""WC3 → SC2 Model Converter v2.2 — PySide6 GUI Application.

Features:
  - Drag-and-drop .mdx files onto the window or EXE icon
  - Live model preview via Blender headless render
  - Session recovery (auto-saves queue, resumes on restart)
  - Auto-update check on startup
  - One-click Blender setup wizard
  - Zero-config: drag model, click Convert, done

Usage:
    python main_window.py              # Launch GUI
    python main_window.py model.mdx    # GUI with model pre-loaded
    python main_window.py --cli        # CLI mode
"""
from __future__ import annotations
import os, sys, json, time, traceback, threading
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QPushButton, QLabel,
    QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox,
    QCheckBox, QLineEdit, QGroupBox, QSplitter, QHeaderView,
    QStyleFactory, QFrame, QScrollArea, QSizePolicy,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSettings, QSize
from PySide6.QtGui import (
    QColor, QFont, QTextCharFormat, QTextCursor, QPixmap, QPalette,
)

import mdx as mdxlib
import diagnostics, healer, discovery, fuzzy_anims, actor_gen, preview
import auto_updater, blender_manager


# ── Data Models ────────────────────────────────────────────

@dataclass
class ModelJob:
    mdx_path: str = ""
    model_name: str = ""
    status: str = "queued"
    progress: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    output_path: str = ""
    report_html: str = ""
    preview_path: str = ""
    scale: float = 0.05
    particle_rate: float = 1.0
    texture_count: int = 0
    animation_count: int = 0
    start_time: float = 0.0

    def to_dict(self) -> dict:
        return {"mdx_path": self.mdx_path, "model_name": self.model_name,
                "scale": self.scale, "particle_rate": self.particle_rate}

    @staticmethod
    def from_dict(d: dict) -> "ModelJob":
        return ModelJob(mdx_path=d.get("mdx_path", ""), model_name=d.get("model_name", ""),
                        scale=d.get("scale", 0.05), particle_rate=d.get("particle_rate", 1.0))


# ── Worker ─────────────────────────────────────────────────

class ConversionSignals(QObject):
    progress = Signal(str, int)
    log_message = Signal(str, str)
    status_change = Signal(str, str)
    job_done = Signal(str, bool, str)
    report_ready = Signal(str, str)


class ConversionWorker(QObject):
    def __init__(self, blender_path: str = ""):
        super().__init__()
        self.signals = ConversionSignals()
        self._blender = blender_path
        self._cancelled = False

    def cancel(self): self._cancelled = True

    def convert(self, job: ModelJob, mdx_data: dict, tex_map: dict, out_dir: str, build_cfg: dict):
        if self._cancelled: return
        self.signals.status_change.emit(job.mdx_path, "parsing")
        self.signals.log_message.emit("INFO", f"Running checks on {job.model_name}...")
        report = diagnostics.run_checks(mdx_data, job.mdx_path)
        job.warnings = [w.message for w in report.warnings]
        job.errors = [e.message for e in report.errors]
        job.report_html = report.to_html()
        self.signals.report_ready.emit(job.mdx_path, report.to_html())
        for w in report.warnings: self.signals.log_message.emit("WARNING", w.message)
        for e in report.errors: self.signals.log_message.emit("ERROR", e.message)
        if job.errors:
            job.status = "failed"
            self.signals.job_done.emit(job.mdx_path, False, "")
            return
        if report.auto_fixable_count > 0:
            result = healer.apply_all_fixes(mdx_data, mdx_data.get("geosets", []),
                                            mdx_data.get("bones", []), mdx_data.get("helpers", []),
                                            mdx_data.get("particles", []))
            for fix in result["fixes_applied"]: self.signals.log_message.emit("INFO", f"  ✓ {fix}")
            mdx_data = result["data"]
        self.signals.status_change.emit(job.mdx_path, "textures")
        self.signals.progress.emit(job.mdx_path, 30)
        self.signals.status_change.emit(job.mdx_path, "blender")
        self.signals.log_message.emit("INFO", "Launching Blender...")
        self.signals.progress.emit(job.mdx_path, 60)
        try:
            import subprocess
            cfg_path = os.path.join(out_dir, "_build_config.json")
            json.dump(build_cfg, open(cfg_path, "w", encoding="utf-8"), indent=1)
            blender = self._blender or discovery.find_blender() or blender_manager.get_managed_blender_path() or "blender"
            cmd = [blender, "--background", "--factory-startup",
                   "--python", os.path.join(os.path.dirname(__file__), "build_m3.py"), "--", cfg_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            self.signals.progress.emit(job.mdx_path, 90)
            if "EXPORT_FAILED" in proc.stdout or proc.returncode != 0:
                self.signals.log_message.emit("ERROR", f"Blender failed:\n{proc.stderr[-800:]}")
                job.status = "failed"
                self.signals.job_done.emit(job.mdx_path, False, "")
                return
            for line in proc.stdout.splitlines():
                if any(k in line for k in ("anim '","mat[","particle[","EXPORT_OK","baked","BUILD_DONE")):
                    self.signals.log_message.emit("INFO", f"  {line.strip()}")
            self.signals.progress.emit(job.mdx_path, 100)
            job.status = "done"
            job.output_path = build_cfg["out"]
            self.signals.job_done.emit(job.mdx_path, True, build_cfg["out"])
        except subprocess.TimeoutExpired:
            self.signals.log_message.emit("ERROR", "Blender timed out (5 min)")
            job.status = "failed"
            self.signals.job_done.emit(job.mdx_path, False, "")
        except Exception as e:
            self.signals.log_message.emit("ERROR", f"Failed: {e}")
            job.status = "failed"
            self.signals.job_done.emit(job.mdx_path, False, "")


# ── Main Window ────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, auto_load_files: List[str] = None):
        super().__init__()
        self.setWindowTitle("WC3 → SC2 Model Converter")
        self.resize(1280, 850)
        self.setMinimumSize(960, 620)

        self.jobs: Dict[str, ModelJob] = {}
        self.settings = QSettings("wc3toSC2", "Converter")
        self.worker: Optional[ConversionWorker] = None
        self.worker_thread: Optional[QThread] = None
        self._load_settings()

        self._setup_theme()
        self._setup_ui()
        self._restore_session()
        self._check_updates()
        if auto_load_files:
            for f in auto_load_files:
                if os.path.isdir(f): self._add_folder_path(f)
                elif f.lower().endswith(".mdx"): self._add_model_path(f)
            if self.jobs:
                self.statusBar().showMessage(f"Loaded {len(self.jobs)} model(s). Click Convert All to begin.")

    # ── Theme ────────────────────────────────────────────

    def _setup_theme(self):
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        p = QPalette()
        p.setColor(QPalette.Window, QColor(30, 30, 46))
        p.setColor(QPalette.WindowText, QColor(205, 214, 244))
        p.setColor(QPalette.Base, QColor(24, 24, 37))
        p.setColor(QPalette.AlternateBase, QColor(40, 40, 58))
        p.setColor(QPalette.Text, QColor(205, 214, 244))
        p.setColor(QPalette.Button, QColor(49, 50, 68))
        p.setColor(QPalette.ButtonText, QColor(205, 214, 244))
        p.setColor(QPalette.Highlight, QColor(137, 180, 250))
        p.setColor(QPalette.HighlightedText, QColor(30, 30, 46))
        app.setPalette(p)
        app.setStyleSheet("QTabWidget::pane{border:1px solid #313244} QTabBar::tab{padding:8px 18px}"
                          "QTabBar::tab:selected{background:#45475a} QGroupBox{font-weight:bold;"
                          "border:1px solid #45475a;border-radius:6px;margin-top:10px;padding-top:14px}"
                          "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 5px}"
                          "QTreeWidget::item{padding:3px 0} QToolTip{background:#313244;color:#cdd6f4;border:1px solid #45475a}")

    # ── UI ───────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central); layout.setContentsMargins(8, 8, 8, 8)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_convert_tab(), "🔄 Convert")
        self.tabs.addTab(self._create_settings_tab(), "⚙ Settings")
        self.tabs.addTab(self._create_about_tab(), "ℹ About")
        layout.addWidget(self.tabs)
        self.statusBar().showMessage("Drag .mdx files here or click '+ Add Models' to begin")

    def _create_convert_tab(self) -> QWidget:
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        # LEFT: queue + buttons
        left = QWidget()
        ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 4, 0)

        self.welcome = QLabel(
            "<div style='color:#a6adc8;padding:30px;text-align:center'>"
            "<h2 style='color:#89b4fa'>WC3 → SC2 Model Converter</h2>"
            "<p>Drag and drop <b>.mdx</b> files here to begin</p>"
            "<p style='font-size:12px'>— or click <b>+ Add Models</b> below —</p></div>")
        self.welcome.setAlignment(Qt.AlignCenter)
        self.welcome.setVisible(True)
        ll.addWidget(self.welcome)

        self.queue_tree = QTreeWidget()
        self.queue_tree.setHeaderLabels(["Model", "Status", "Progress", "Warnings"])
        self.queue_tree.setAlternatingRowColors(True)
        self.queue_tree.setDragDropMode(self.queue_tree.DragDropMode.DropOnly)
        self.queue_tree.setAcceptDrops(True)
        self.queue_tree.dragEnterEvent = lambda e: e.acceptProposedAction() if e.mimeData().hasUrls() else None
        self.queue_tree.dropEvent = self._drop_event
        self.queue_tree.itemSelectionChanged.connect(self._on_queue_select)
        h = self.queue_tree.header()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3): h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.queue_tree.setVisible(False)
        ll.addWidget(self.queue_tree)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Models")
        add_btn.setToolTip("Select .mdx files to convert")
        add_btn.clicked.connect(self._add_models)
        folder_btn = QPushButton("📁 Folder")
        folder_btn.setToolTip("Add all .mdx files from a folder")
        folder_btn.clicked.connect(self._add_folder)
        clear_btn = QPushButton("Clear Done")
        clear_btn.clicked.connect(self._clear_done)
        btn_row.addWidget(add_btn); btn_row.addWidget(folder_btn); btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        self.convert_btn = QPushButton("▶ Convert All")
        self.convert_btn.setStyleSheet("background:#a6e3a1;color:#1e1e2e;font-weight:bold;padding:6px 20px")
        self.convert_btn.clicked.connect(self._start_conversion)
        self.convert_btn.setToolTip("Start converting all queued models")
        self.cancel_btn = QPushButton("■ Stop"); self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_conversion)
        btn_row.addWidget(self.convert_btn); btn_row.addWidget(self.cancel_btn)
        ll.addLayout(btn_row)
        splitter.addWidget(left)

        # RIGHT: preview + log
        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(4, 0, 0, 0)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(200)
        self.preview_label.setStyleSheet("background:#1e1e2e;border:1px solid #313244;border-radius:4px")
        self.preview_label.setText("<span style='color:#6c7086'>Model preview</span>")
        rl.addWidget(self.preview_label, 2)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumBlockCount(5000)
        rl.addWidget(self.log_view, 3)

        self.overall_progress = QProgressBar(); self.overall_progress.setVisible(False)
        rl.addWidget(self.overall_progress)

        splitter.addWidget(right)
        splitter.setSizes([550, 650])

        ml = QVBoxLayout(tab); ml.addWidget(splitter)
        return tab

    def _create_settings_tab(self) -> QWidget:
        tab = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        w = QWidget(); layout = QVBoxLayout(w)

        # Blender
        gb = QGroupBox("Blender (Required)")
        gl = QVBoxLayout(gb)
        hb = QHBoxLayout()
        self.blender_edit = QLineEdit(self._blender_path)
        self.blender_edit.setPlaceholderText("Auto-detect")
        hb.addWidget(QLabel("Path:")); hb.addWidget(self.blender_edit)
        auto_btn = QPushButton("Auto-Detect"); auto_btn.clicked.connect(self._auto_detect_blender)
        hb.addWidget(auto_btn)
        browse_btn = QPushButton("Browse..."); browse_btn.clicked.connect(lambda: self._browse_file(
            self.blender_edit, "Select blender.exe", "Blender (blender.exe);;All Files (*)"))
        hb.addWidget(browse_btn)
        gl.addLayout(hb)

        self.oneclick_btn = QPushButton("🔧 One-Click Setup (Download Blender + Addon)")
        self.oneclick_btn.setStyleSheet("background:#f9e2af;color:#1e1e2e;font-weight:bold;padding:8px")
        self.oneclick_btn.clicked.connect(self._oneclick_setup)
        self.oneclick_btn.setToolTip("Automatically downloads Blender 4.4.3 and installs the m3studio addon")
        gl.addWidget(self.oneclick_btn)

        self.blender_status = QLabel("Not checked"); gl.addWidget(self.blender_status)
        layout.addWidget(gb)

        # Defaults
        gb2 = QGroupBox("Defaults")
        gl2 = QVBoxLayout(gb2)
        for label, attr, default in [("Scale:", "scale_edit", "0.05"),
                                      ("Particle rate:", "particle_rate_edit", "1.0"),
                                      ("Output dir:", "output_edit", "./out")]:
            hd = QHBoxLayout(); hd.addWidget(QLabel(label))
            edit = QLineEdit(str(getattr(self, f"_{attr.replace('_edit','')}", default)))
            setattr(self, attr, edit)
            hd.addWidget(edit); gl2.addLayout(hd)
        layout.addWidget(gb2)

        # Toggles
        gb3 = QGroupBox("Auto-Fix")
        gl3 = QVBoxLayout(gb3)
        for label, attr in [("Auto-detect inverted alpha", "auto_alpha_cb"),
                            ("Auto-estimate scale", "auto_scale_cb"),
                            ("Fuzzy-match animations", "fuzzy_anims_cb"),
                            ("Generate actor XML", "gen_actor_cb"),
                            ("Generate HTML report", "gen_report_cb")]:
            cb = QCheckBox(label); cb.setChecked(getattr(self, f"_{attr.replace('_cb','')}", True))
            setattr(self, attr, cb); gl3.addWidget(cb)
        layout.addWidget(gb3)

        save_btn = QPushButton("💾 Save Settings"); save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn); layout.addStretch()
        scroll.setWidget(w); tl = QVBoxLayout(tab); tl.addWidget(scroll)
        return tab

    def _create_about_tab(self) -> QWidget:
        tab = QWidget(); layout = QVBoxLayout(tab)
        layout.setAlignment(Qt.AlignCenter)
        layout.addWidget(QLabel("<h1 style='color:#89b4fa'>WC3 → SC2 Model Converter</h1>"))
        layout.addWidget(QLabel("<p>Version 2.2.0</p>"))
        layout.addWidget(QLabel("<p>Convert Warcraft 3 .mdx models to StarCraft 2 .m3</p>"))
        layout.addWidget(QLabel("<p><a href='https://github.com/RogerRoger29/wc3-to-sc2-converter'"
                                "style='color:#89b4fa'>github.com/RogerRoger29/wc3-to-sc2-converter</a></p>"))
        layout.addWidget(QLabel("<p style='color:#a6adc8;font-size:11px'>Powered by m3studio (Solstice245) | MIT License</p>"))
        return tab

    # ── Drag & Drop ──────────────────────────────────────

    def _drop_event(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path): self._add_folder_path(path)
            elif path.lower().endswith(".mdx"): self._add_model_path(path)
        event.acceptProposedAction()

    # ── Model Management ──────────────────────────────────

    def _add_models(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select MDX models", "", "MDX Files (*.mdx);;All (*)")
        for f in files: self._add_model_path(f)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if folder: self._add_folder_path(folder)

    def _add_model_path(self, path: str, silent: bool = False):
        if path in self.jobs: return
        name = os.path.splitext(os.path.basename(path))[0]
        job = ModelJob(mdx_path=path, model_name=name, scale=self._scale,
                       particle_rate=self._particle_rate)
        try:
            m = mdxlib.parse(path)
            job.texture_count = len(m.get("textures", []))
            job.animation_count = len(m.get("sequences", []))
        except Exception: pass
        self.jobs[path] = job
        self._add_queue_item(job)
        if not silent: self._log("INFO", f"Added: {name}")
        self._update_queue_visibility()
        self._save_session()

    def _add_folder_path(self, folder: str):
        count = 0
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".mdx"):
                    self._add_model_path(os.path.join(root, f), silent=True)
                    count += 1
        self._log("INFO", f"Added {count} model(s) from {folder}")
        self._save_session()

    def _add_queue_item(self, job: ModelJob):
        item = QTreeWidgetItem(self.queue_tree)
        item.setText(0, job.model_name)
        item.setText(1, "⏳ Queued"); item.setText(2, "—"); item.setText(3, "—")
        item.setData(0, Qt.UserRole, job.mdx_path)

    def _update_queue_visibility(self):
        has = self.queue_tree.topLevelItemCount() > 0
        self.welcome.setVisible(not has)
        self.queue_tree.setVisible(has)

    def _on_queue_select(self):
        items = self.queue_tree.selectedItems()
        if not items: return
        path = items[0].data(0, Qt.UserRole)
        job = self.jobs.get(path)
        if job and job.preview_path and os.path.exists(job.preview_path):
            pix = QPixmap(job.preview_path).scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview_label.setPixmap(pix)
        elif job:
            self.preview_label.setText(f"<span style='color:#6c7086'>{job.model_name}<br>{job.texture_count} textures, {job.animation_count} anims</span>")

    def _clear_done(self):
        for i in range(self.queue_tree.topLevelItemCount() - 1, -1, -1):
            item = self.queue_tree.topLevelItem(i)
            path = item.data(0, Qt.UserRole)
            if path in self.jobs and self.jobs[path].status in ("done", "failed"):
                self.queue_tree.takeTopLevelItem(i)
        self._update_queue_visibility()

    # ── Conversion ────────────────────────────────────────

    def _start_conversion(self):
        queued = [j for j in self.jobs.values() if j.status == "queued"]
        if not queued:
            self._log("WARNING", "No models queued. Add models first.")
            return
        self.convert_btn.setEnabled(False); self.cancel_btn.setEnabled(True)
        self.overall_progress.setVisible(True); self.overall_progress.setMaximum(len(queued))
        self._process_next()

    def _process_next(self):
        queued = [j for j in self.jobs.values() if j.status == "queued"]
        if not queued: self._conversion_finished(); return
        job = queued[0]; job.start_time = time.time()
        self._log("INFO", f"▶ Starting: {job.model_name}")
        try: mdx_data = mdxlib.parse(job.mdx_path)
        except Exception as e:
            self._log("ERROR", f"Parse failed: {e}"); job.status = "failed"; job.errors.append(str(e))
            self._update_queue_item(job); self._process_next(); return
        if self._auto_scale:
            est, conf = discovery.estimate_scale(mdx_data); job.scale = est
            self._log("INFO", f"Scale: {est} ({conf})")
        out_dir = self._output_dir or os.path.join(os.path.dirname(job.mdx_path), "out")
        os.makedirs(out_dir, exist_ok=True)
        build_cfg = {"mdx": job.mdx_path, "out": os.path.join(out_dir, job.model_name + ".m3"),
                     "model_name": job.model_name, "scale": job.scale,
                     "asset_texture_dir": "Assets\\Textures\\", "textures": {},
                     "anim_names": {}, "features": {"animations": True, "attachments": True,
                     "particles": True, "hittest": True, "camera": True},
                     "particle_rate_scale": job.particle_rate, "team_color": True}
        if self._fuzzy_anims and mdx_data.get("sequences"):
            build_cfg["anim_names"] = fuzzy_anims.build_anim_map(
                [s["name"] for s in mdx_data["sequences"]])
        tex_map = {}
        for i, t in enumerate(mdx_data.get("textures", [])):
            if t.get("replaceableId") in (1, 2) or not t.get("path"): continue
            tex_map[i] = os.path.splitext(os.path.basename(t["path"]))[0] + ".dds"
        build_cfg["textures"] = {str(k): v for k, v in tex_map.items()}
        # Render preview
        blender = self._get_blender_path()
        if blender and os.path.exists(blender):
            def _preview_thread():
                p = preview.render_preview(job.mdx_path, blender)
                if p: job.preview_path = p
            threading.Thread(target=_preview_thread, daemon=True).start()
        self.worker = ConversionWorker(blender)
        self.worker_thread = QThread(); self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(lambda: self.worker.convert(job, mdx_data, tex_map, out_dir, build_cfg))
        self.worker.signals.progress.connect(self._on_progress)
        self.worker.signals.log_message.connect(self._log)
        self.worker.signals.status_change.connect(self._on_status_change)
        self.worker.signals.job_done.connect(self._on_job_done)
        self.worker.signals.report_ready.connect(self._on_report_ready)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def _cancel_conversion(self):
        if self.worker: self.worker.cancel()
        self._log("WARNING", "Stopping after current model..."); self.cancel_btn.setEnabled(False)

    def _conversion_finished(self):
        self.convert_btn.setEnabled(True); self.cancel_btn.setEnabled(False)
        self.overall_progress.setVisible(False)
        d = sum(1 for j in self.jobs.values() if j.status == "done")
        f = sum(1 for j in self.jobs.values() if j.status == "failed")
        self._log("SUCCESS", f"✓ Done! {d} succeeded, {f} failed.")
        self.statusBar().showMessage(f"Complete: {d} ok, {f} failed", 8000)
        self._save_session()

    # ── Signals ───────────────────────────────────────────

    def _on_progress(self, path, pct):
        if path in self.jobs: self.jobs[path].progress = pct; self._update_queue_item(self.jobs[path])
        self.overall_progress.setValue(sum(1 for j in self.jobs.values() if j.status == "done"))

    def _on_status_change(self, path, status):
        if path in self.jobs: self.jobs[path].status = status; self._update_queue_item(self.jobs[path])

    def _on_job_done(self, path, success, out_path):
        job = self.jobs.get(path)
        if job:
            dur = time.time() - job.start_time if job.start_time else 0
            self._log("SUCCESS" if success else "ERROR", f"{'✓' if success else '✗'} {job.model_name} ({dur:.1f}s)")
            if success and self._gen_actor:
                try:
                    xml = actor_gen.generate_actor_xml(job.model_name, f"Assets\\Textures\\{job.model_name}.m3", scale=job.scale)
                    ap = os.path.join(os.path.dirname(out_path), job.model_name + "_actor.xml")
                    with open(ap, "w") as fh: fh.write(xml)
                except Exception: pass
            if self._gen_report and job.report_html:
                try:
                    with open(os.path.join(os.path.dirname(out_path or job.mdx_path), job.model_name + "_report.html"), "w") as fh:
                        fh.write(job.report_html)
                except Exception: pass
            self._update_queue_item(job)
        self.worker_thread = None; self.worker = None
        self._process_next()

    def _on_report_ready(self, path, html):
        if path in self.jobs: self.jobs[path].report_html = html

    # ── Logging ───────────────────────────────────────────

    def _log(self, level: str, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        fmt = QTextCharFormat()
        fmt.setForeground({"ERROR": QColor("#e74c3c"), "WARNING": QColor("#f39c12"),
                           "SUCCESS": QColor("#a6e3a1"), "INFO": QColor("#cdd6f4")}.get(level, QColor("#a6adc8")))
        c = self.log_view.textCursor(); c.movePosition(QTextCursor.End)
        c.insertText(f"{ts}  {level:<7} {msg}\n", fmt)
        self.log_view.setTextCursor(c); self.log_view.ensureCursorVisible()

    def _update_queue_item(self, job: ModelJob):
        item = None
        for i in range(self.queue_tree.topLevelItemCount()):
            if self.queue_tree.topLevelItem(i).data(0, Qt.UserRole) == job.mdx_path:
                item = self.queue_tree.topLevelItem(i); break
        if not item: return
        icons = {"queued": "⏳", "parsing": "📖", "textures": "🎨", "blender": "🔧", "done": "✅", "failed": "❌"}
        item.setText(1, f"{icons.get(job.status, '?')} {job.status.title()}")
        item.setText(2, f"{job.progress}%" if job.progress else "—")
        item.setText(3, f"⚠ {len(job.warnings)}" if job.warnings else "—")

    # ── Session Recovery ──────────────────────────────────

    def _save_session(self):
        data = [j.to_dict() for j in self.jobs.values() if j.status == "queued"]
        self.settings.setValue("session", json.dumps(data))

    def _restore_session(self):
        try:
            data = json.loads(self.settings.value("session", "[]"))
            for d in data:
                if os.path.exists(d.get("mdx_path", "")):
                    self._add_model_path(d["mdx_path"], silent=True)
            if self.jobs:
                self._log("INFO", f"Restored {len(self.jobs)} model(s) from previous session.")
        except (json.JSONDecodeError, TypeError):
            pass

    # ── Updates ───────────────────────────────────────────

    def _check_updates(self):
        def _check():
            info = auto_updater.check_for_update()
            if info:
                self._log("INFO", f"🔔 Update available: {info['version']} — {info['url']}")
        threading.Thread(target=_check, daemon=True).start()

    # ── Settings ──────────────────────────────────────────

    def _load_settings(self):
        self._blender_path = self.settings.value("blender_path", "")
        self._scale = float(self.settings.value("default_scale", "0.05"))
        self._particle_rate = float(self.settings.value("default_particle_rate", "1.0"))
        self._output_dir = self.settings.value("output_dir", "./out")
        self._auto_alpha = self.settings.value("auto_alpha", "true") == "true"
        self._auto_scale = self.settings.value("auto_scale", "true") == "true"
        self._fuzzy_anims = self.settings.value("fuzzy_anims", "true") == "true"
        self._gen_actor = self.settings.value("gen_actor", "true") == "true"
        self._gen_report = self.settings.value("gen_report", "true") == "true"

    def _save_settings(self):
        for key, attr in [("blender_path", "blender_edit"), ("default_scale", "scale_edit"),
                          ("default_particle_rate", "particle_rate_edit"), ("output_dir", "output_edit")]:
            self.settings.setValue(key, getattr(self, attr).text().strip())
        for key, attr in [("auto_alpha", "auto_alpha_cb"), ("auto_scale", "auto_scale_cb"),
                          ("fuzzy_anims", "fuzzy_anims_cb"), ("gen_actor", "gen_actor_cb"),
                          ("gen_report", "gen_report_cb")]:
            self.settings.setValue(key, str(getattr(self, attr).isChecked()).lower())
        self._load_settings()
        self._log("INFO", "Settings saved."); self.statusBar().showMessage("Settings saved", 3000)

    def _auto_detect_blender(self):
        path = discovery.find_blender() or blender_manager.get_managed_blender_path()
        if path and os.path.exists(path):
            self.blender_edit.setText(path); self.blender_status.setText(f"✅ Found: {path}")
        else:
            self.blender_status.setText("❌ Not found — use One-Click Setup")

    def _browse_file(self, edit: QLineEdit, title: str, filter_str: str):
        path, _ = QFileDialog.getOpenFileName(self, title, "", filter_str)
        if path: edit.setText(path)

    def _oneclick_setup(self):
        self._log("INFO", "Starting one-click Blender setup...")
        self.oneclick_btn.setEnabled(False); self.oneclick_btn.setText("Downloading...")
        self.blender_status.setText("⏳ Downloading Blender 4.4.3 (~300MB)...")
        QApplication.processEvents()

        def _setup():
            exe = blender_manager.ensure_blender_ready(
                lambda stage, cur, tot: None)
            return exe
        def _done(future):
            exe = future.result() if hasattr(future, 'result') else None
            if exe:
                self.blender_edit.setText(str(exe))
                self.blender_status.setText(f"✅ Ready: {exe}")
                self._log("SUCCESS", "Blender + m3studio installed successfully!")
            else:
                self.blender_status.setText("❌ Setup failed — check internet or install manually")
                self._log("ERROR", "One-click setup failed.")
            self.oneclick_btn.setEnabled(True); self.oneclick_btn.setText("🔧 One-Click Setup (Download Blender + Addon)")

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_setup)
            fut.add_done_callback(_done)

    def _get_blender_path(self) -> str:
        return (self.blender_edit.text().strip() or discovery.find_blender()
                or blender_manager.get_managed_blender_path() or "blender")


# ── Entry Point ───────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("wc3toSC2")

    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        import convert; convert.main()
        return

    # Check for files passed via command line (drag-drop onto EXE)
    auto_files = [a for a in sys.argv[1:] if not a.startswith("-")]

    window = MainWindow(auto_load_files=auto_files if auto_files else None)
    window.show()

    # If files were passed and --silent flag, auto-start conversion
    if auto_files and "--silent" in sys.argv and window.jobs:
        QTimer.singleShot(500, window._start_conversion)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
