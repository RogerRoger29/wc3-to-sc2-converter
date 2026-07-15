"""WC3 → SC2 Model Converter v2.0 — PySide6 GUI Application.

Double-click executable with premium UX: drag-and-drop model queue, batch conversion,
live color-coded log, per-model diagnostics, settings persistence, and dark theme.

Usage:
    python main_window.py          # Launch the GUI
    python main_window.py --cli    # Fall back to CLI mode
"""
from __future__ import annotations
import os, sys, json, time, traceback
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QPushButton, QLabel,
    QPlainTextEdit, QProgressBar, QFileDialog, QMessageBox,
    QCheckBox, QLineEdit, QGroupBox, QSplitter, QHeaderView,
    QStyleFactory, QComboBox,
)
from PySide6.QtCore import (
    Qt, QThread, Signal, QObject, QTimer, QSettings, QUrl,
)
from PySide6.QtGui import (
    QColor, QFont, QTextCharFormat, QTextCursor, QIcon, QDragEnterEvent,
    QDropEvent, QPalette, QAction,
)

# Our engine modules
import mdx as mdxlib
import diagnostics
import healer
import discovery
import fuzzy_anims
import actor_gen
import convert as converter


# ──────────────────────────── Data Models ────────────────────────────

@dataclass
class ModelJob:
    """Represents one model in the conversion queue."""
    mdx_path: str
    model_name: str = ""
    status: str = "queued"  # queued, parsing, textures, blender, done, failed
    progress: int = 0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    output_path: str = ""
    report_html: str = ""
    scale: float = 0.05
    particle_rate: float = 1.0
    texture_count: int = 0
    animation_count: int = 0
    start_time: float = 0.0

    @property
    def status_icon(self) -> str:
        return {"queued": "⏳", "parsing": "📖", "textures": "🎨",
                "blender": "🔧", "done": "✅", "failed": "❌"}.get(self.status, "❓")


# ──────────────────────────── Worker Thread ────────────────────────────

class ConversionSignals(QObject):
    """Signals emitted by the conversion worker to update the UI."""
    progress = Signal(str, int)       # model_name, pct 0-100
    log_message = Signal(str, str)    # level, message
    status_change = Signal(str, str)  # model_name, new_status
    job_done = Signal(str, bool, str) # model_name, success, output_path
    report_ready = Signal(str, str)   # model_name, html_report


class ConversionWorker(QObject):
    """Runs model conversion on a QThread. Processes one model's stages."""

    def __init__(self, blender_path: str = ""):
        super().__init__()
        self.signals = ConversionSignals()
        self._blender = blender_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def log(self, level: str, msg: str):
        self.signals.log_message.emit(level, msg)

    def convert(self, job: ModelJob, mdx_data: Dict, tex_map: Dict,
                out_dir: str, build_cfg: Dict):
        """Execute the full conversion pipeline for one model."""
        if self._cancelled:
            return

        # Stage 1: Pre-flight diagnostics
        self.signals.status_change.emit(job.mdx_path, "parsing")
        self.log("INFO", f"Running pre-flight checks on {job.model_name}...")
        report = diagnostics.run_checks(mdx_data, job.mdx_path)
        job.warnings = [w.message for w in report.warnings]
        job.errors = [e.message for e in report.errors]
        job.report_html = report.to_html()
        self.signals.report_ready.emit(job.mdx_path, report.to_html())
        for w in report.warnings:
            self.log("WARNING", w.message)
        for e in report.errors:
            self.log("ERROR", e.message)

        if job.errors:
            job.status = "failed"
            self.signals.job_done.emit(job.mdx_path, False, "")
            return

        # Stage 2: Apply self-healing
        if report.auto_fixable_count > 0:
            self.log("INFO", f"Applying {report.auto_fixable_count} auto-fix(es)...")
            result = healer.apply_all_fixes(
                mdx_data, mdx_data.get("geosets", []),
                mdx_data.get("bones", []), mdx_data.get("helpers", []),
                mdx_data.get("particles", []))
            for fix in result["fixes_applied"]:
                self.log("INFO", f"  ✓ {fix}")
            mdx_data = result["data"]

        # Stage 3: Convert textures
        self.signals.status_change.emit(job.mdx_path, "textures")
        self.log("INFO", f"Converting {job.texture_count} texture(s)...")
        self.signals.progress.emit(job.mdx_path, 25)

        # (Texture conversion is done externally; we assume tex_map is ready)
        self.signals.progress.emit(job.mdx_path, 50)

        # Stage 4: Write build config + launch Blender
        self.signals.status_change.emit(job.mdx_path, "blender")
        self.log("INFO", "Launching Blender for M3 export...")
        self.signals.progress.emit(job.mdx_path, 60)

        try:
            # Write build config
            cfg_path = os.path.join(out_dir, "_build_config.json")
            json.dump(build_cfg, open(cfg_path, "w", encoding="utf-8"), indent=1)

            # Launch Blender subprocess
            import subprocess
            blender = self._blender or discovery.find_blender() or "blender"
            cmd = [blender, "--background", "--factory-startup",
                   "--python", os.path.join(os.path.dirname(__file__), "build_m3.py"),
                   "--", cfg_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            self.signals.progress.emit(job.mdx_path, 90)

            if "EXPORT_FAILED" in proc.stdout or proc.returncode != 0:
                self.log("ERROR", f"Blender export failed:\n{proc.stderr[-1000:]}")
                job.status = "failed"
                job.errors.append("Blender export failed")
                self.signals.job_done.emit(job.mdx_path, False, "")
                return

            for line in proc.stdout.splitlines():
                if any(k in line for k in ("anim '", "mat[", "particle[", "EXPORT_OK",
                                            "baked", "BUILD_DONE")):
                    self.log("INFO", f"  {line.strip()}")

            self.signals.progress.emit(job.mdx_path, 100)
            job.status = "done"
            job.output_path = build_cfg["out"]
            self.signals.job_done.emit(job.mdx_path, True, build_cfg["out"])
            self.log("SUCCESS", f"{job.model_name}.m3 exported successfully!")

        except subprocess.TimeoutExpired:
            self.log("ERROR", "Blender timed out (5 min limit)")
            job.status = "failed"
            self.signals.job_done.emit(job.mdx_path, False, "")
        except Exception as e:
            self.log("ERROR", f"Conversion failed: {e}")
            job.status = "failed"
            self.signals.job_done.emit(job.mdx_path, False, "")


# ──────────────────────────── Main Window ────────────────────────────

class MainWindow(QMainWindow):
    """Main application window with tabbed interface."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("WC3 → SC2 Model Converter v2.0")
        self.resize(1200, 800)
        self.setMinimumSize(900, 600)

        # State
        self.jobs: Dict[str, ModelJob] = {}
        self.settings = QSettings("wc3toSC2", "Converter")
        self.worker: Optional[ConversionWorker] = None
        self.worker_thread: Optional[QThread] = None
        self.blender_path = self.settings.value("blender_path", "")
        self.default_scale = float(self.settings.value("default_scale", "0.05"))
        self.default_particle_rate = float(self.settings.value("default_particle_rate", "1.0"))
        self.output_dir = self.settings.value("output_dir", "./out")
        self.auto_alpha = self.settings.value("auto_alpha", "true") == "true"
        self.auto_scale = self.settings.value("auto_scale", "true") == "true"
        self.fuzzy_anims = self.settings.value("fuzzy_anims", "true") == "true"
        self.gen_actor = self.settings.value("gen_actor", "true") == "true"
        self.gen_report = self.settings.value("gen_report", "true") == "true"

        self._setup_theme()
        self._setup_ui()
        self._restore_state()

    # ── Theme ────────────────────────────────────────────────

    def _setup_theme(self):
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(30, 30, 46))
        palette.setColor(QPalette.WindowText, QColor(205, 214, 244))
        palette.setColor(QPalette.Base, QColor(24, 24, 37))
        palette.setColor(QPalette.AlternateBase, QColor(40, 40, 58))
        palette.setColor(QPalette.ToolTipBase, QColor(49, 50, 68))
        palette.setColor(QPalette.Text, QColor(205, 214, 244))
        palette.setColor(QPalette.Button, QColor(49, 50, 68))
        palette.setColor(QPalette.ButtonText, QColor(205, 214, 244))
        palette.setColor(QPalette.BrightText, QColor(255, 100, 100))
        palette.setColor(QPalette.Highlight, QColor(137, 180, 250))
        palette.setColor(QPalette.HighlightedText, QColor(30, 30, 46))
        app.setPalette(palette)
        app.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #313244; }
            QTabBar::tab { padding: 8px 20px; }
            QTabBar::tab:selected { background: #45475a; color: #cdd6f4; }
            QGroupBox { font-weight: bold; border: 1px solid #45475a; border-radius: 6px; margin-top: 12px; padding-top: 16px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
            QTreeWidget { alternate-background-color: #313244; }
        """)

    # ── UI Setup ─────────────────────────────────────────────

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_convert_tab(), "Convert")
        self.tabs.addTab(self._create_batch_tab(), "Batch")
        self.tabs.addTab(self._create_history_tab(), "History")
        self.tabs.addTab(self._create_settings_tab(), "Settings")
        layout.addWidget(self.tabs)

        # Status bar
        self.statusBar().showMessage("Ready — drag .mdx files here to begin")

    def _create_convert_tab(self) -> QWidget:
        tab = QWidget()
        splitter = QSplitter(Qt.Horizontal)

        # Left: model queue
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        self.queue_tree = QTreeWidget()
        self.queue_tree.setHeaderLabels(["Name", "Status", "Progress", "Warnings"])
        self.queue_tree.setAlternatingRowColors(True)
        self.queue_tree.setDragDropMode(self.queue_tree.DragDropMode.DropOnly)
        self.queue_tree.setAcceptDrops(True)
        self.queue_tree.dragEnterEvent = self._drag_enter
        self.queue_tree.dropEvent = self._drop_event
        header = self.queue_tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        ll.addWidget(self.queue_tree)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Models")
        add_btn.clicked.connect(self._add_models)
        folder_btn = QPushButton("📁 Add Folder")
        folder_btn.clicked.connect(self._add_folder)
        clear_btn = QPushButton("Clear Done")
        clear_btn.clicked.connect(self._clear_done)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(folder_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        self.convert_btn = QPushButton("▶ Convert All")
        self.convert_btn.setStyleSheet("background:#a6e3a1;color:#1e1e2e;font-weight:bold;padding:6px 20px;")
        self.convert_btn.clicked.connect(self._start_conversion)
        self.cancel_btn = QPushButton("■ Stop")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_conversion)
        btn_row.addWidget(self.convert_btn)
        btn_row.addWidget(self.cancel_btn)
        ll.addLayout(btn_row)

        splitter.addWidget(left)

        # Right: log + progress
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumBlockCount(5000)
        rl.addWidget(self.log_view, 3)

        self.overall_progress = QProgressBar()
        self.overall_progress.setVisible(False)
        rl.addWidget(self.overall_progress)

        splitter.addWidget(right)
        splitter.setSizes([600, 500])

        main_layout = QVBoxLayout(tab)
        main_layout.addWidget(splitter)
        return tab

    def _create_batch_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        gb = QGroupBox("Batch Source")
        gl = QVBoxLayout(gb)

        h1 = QHBoxLayout()
        self.batch_folder_edit = QLineEdit()
        self.batch_folder_edit.setPlaceholderText("C:\\My Models\\Human\\")
        h1.addWidget(QLabel("Folder:"))
        h1.addWidget(self.batch_folder_edit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_batch_folder)
        h1.addWidget(browse_btn)
        gl.addLayout(h1)

        self.batch_recursive = QCheckBox("Include subdirectories")
        self.batch_recursive.setChecked(True)
        gl.addWidget(self.batch_recursive)

        h2 = QHBoxLayout()
        self.batch_out_edit = QLineEdit("./out")
        h2.addWidget(QLabel("Output:"))
        h2.addWidget(self.batch_out_edit)
        gl.addLayout(h2)

        self.batch_count_label = QLabel("")
        gl.addWidget(self.batch_count_label)

        layout.addWidget(gb)

        scan_btn = QPushButton("🔍 Scan for Models")
        scan_btn.clicked.connect(self._scan_batch)
        layout.addWidget(scan_btn)

        self.batch_convert_btn = QPushButton("▶ Convert All Found Models")
        self.batch_convert_btn.setStyleSheet("background:#a6e3a1;color:#1e1e2e;font-weight:bold;padding:8px;")
        self.batch_convert_btn.clicked.connect(self._batch_convert)
        self.batch_convert_btn.setEnabled(False)
        layout.addWidget(self.batch_convert_btn)
        layout.addStretch()
        return tab

    def _create_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.history_tree = QTreeWidget()
        self.history_tree.setHeaderLabels(["Date", "Model", "Duration", "Result", "Output"])
        self.history_tree.setAlternatingRowColors(True)
        layout.addWidget(self.history_tree)
        clear_btn = QPushButton("Clear History")
        clear_btn.clicked.connect(self._clear_history)
        layout.addWidget(clear_btn)
        self._load_history()
        return tab

    def _create_settings_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Blender
        gb1 = QGroupBox("Blender")
        gl1 = QVBoxLayout(gb1)
        hb = QHBoxLayout()
        self.blender_edit = QLineEdit(self.blender_path)
        self.blender_edit.setPlaceholderText("Auto-detect (leave empty)")
        hb.addWidget(QLabel("Path:"))
        hb.addWidget(self.blender_edit)
        auto_btn = QPushButton("Auto-Detect")
        auto_btn.clicked.connect(self._auto_detect_blender)
        hb.addWidget(auto_btn)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_blender)
        hb.addWidget(browse_btn)
        gl1.addLayout(hb)
        self.blender_status = QLabel("Not checked")
        gl1.addWidget(self.blender_status)
        layout.addWidget(gb1)

        # Defaults
        gb2 = QGroupBox("Defaults")
        gl2 = QVBoxLayout(gb2)
        hd1 = QHBoxLayout()
        self.scale_edit = QLineEdit(str(self.default_scale))
        hd1.addWidget(QLabel("Default scale:"))
        hd1.addWidget(self.scale_edit)
        self.particle_rate_edit = QLineEdit(str(self.default_particle_rate))
        hd1.addWidget(QLabel("Particle rate:"))
        hd1.addWidget(self.particle_rate_edit)
        gl2.addLayout(hd1)
        hd2 = QHBoxLayout()
        self.output_edit = QLineEdit(self.output_dir)
        hd2.addWidget(QLabel("Output dir:"))
        hd2.addWidget(self.output_edit)
        gl2.addLayout(hd2)
        layout.addWidget(gb2)

        # Auto-fix toggles
        gb3 = QGroupBox("Auto-Fix Features")
        gl3 = QVBoxLayout(gb3)
        self.auto_alpha_cb = QCheckBox("Auto-detect inverted alpha")
        self.auto_alpha_cb.setChecked(self.auto_alpha)
        gl3.addWidget(self.auto_alpha_cb)
        self.auto_scale_cb = QCheckBox("Auto-estimate scale")
        self.auto_scale_cb.setChecked(self.auto_scale)
        gl3.addWidget(self.auto_scale_cb)
        self.fuzzy_anims_cb = QCheckBox("Fuzzy-match animation names")
        self.fuzzy_anims_cb.setChecked(self.fuzzy_anims)
        gl3.addWidget(self.fuzzy_anims_cb)
        self.gen_actor_cb = QCheckBox("Generate SC2 actor XML")
        self.gen_actor_cb.setChecked(self.gen_actor)
        gl3.addWidget(self.gen_actor_cb)
        self.gen_report_cb = QCheckBox("Generate HTML conversion report")
        self.gen_report_cb.setChecked(self.gen_report)
        gl3.addWidget(self.gen_report_cb)
        layout.addWidget(gb3)

        save_btn = QPushButton("💾 Save Settings")
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)
        layout.addStretch()
        return tab

    # ── Drag & Drop ──────────────────────────────────────────

    def _drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop_event(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                self._add_folder_path(path)
            elif path.lower().endswith(".mdx"):
                self._add_model_path(path)
        event.acceptProposedAction()

    # ── Model Management ─────────────────────────────────────

    def _add_models(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select MDX models", "", "MDX Files (*.mdx);;All Files (*)")
        for f in files:
            self._add_model_path(f)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with .mdx models")
        if folder:
            self._add_folder_path(folder)

    def _add_model_path(self, path: str):
        if path in self.jobs:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        job = ModelJob(mdx_path=path, model_name=name, scale=self.default_scale,
                       particle_rate=self.default_particle_rate)

        # Quick parse to populate stats
        try:
            m = mdxlib.parse(path)
            job.texture_count = len(m.get("textures", []))
            job.animation_count = len(m.get("sequences", []))
        except Exception:
            pass

        self.jobs[path] = job
        self._add_queue_item(job)

    def _add_folder_path(self, folder: str):
        count = 0
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".mdx"):
                    self._add_model_path(os.path.join(root, f))
                    count += 1
        self._log("INFO", f"Added {count} model(s) from {folder}")

    def _add_queue_item(self, job: ModelJob):
        item = QTreeWidgetItem(self.queue_tree)
        item.setText(0, job.model_name)
        item.setText(1, job.status_icon + " Queued")
        item.setText(2, "—")
        item.setText(3, "—")
        item.setData(0, Qt.UserRole, job.mdx_path)

    def _get_item_by_path(self, path: str) -> Optional[QTreeWidgetItem]:
        for i in range(self.queue_tree.topLevelItemCount()):
            item = self.queue_tree.topLevelItem(i)
            if item.data(0, Qt.UserRole) == path:
                return item
        return None

    def _clear_done(self):
        for i in range(self.queue_tree.topLevelItemCount() - 1, -1, -1):
            item = self.queue_tree.topLevelItem(i)
            path = item.data(0, Qt.UserRole)
            if path in self.jobs and self.jobs[path].status in ("done", "failed"):
                self.queue_tree.takeTopLevelItem(i)

    # ── Conversion Engine ────────────────────────────────────

    def _start_conversion(self):
        queued = [j for j in self.jobs.values() if j.status == "queued"]
        if not queued:
            self._log("WARNING", "No models queued for conversion.")
            return

        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.overall_progress.setVisible(True)
        self.overall_progress.setMaximum(len(queued))

        self._process_next()

    def _process_next(self):
        queued = [j for j in self.jobs.values() if j.status == "queued"]
        if not queued:
            self._conversion_finished()
            return

        job = queued[0]
        job.start_time = time.time()
        self._log("INFO", f"Starting: {job.model_name}")

        # Parse MDX
        try:
            mdx_data = mdxlib.parse(job.mdx_path)
        except Exception as e:
            self._log("ERROR", f"Failed to parse {job.model_name}: {e}")
            job.status = "failed"
            job.errors.append(str(e))
            self._update_queue_item(job)
            self._process_next()
            return

        # Auto-scale
        if self.auto_scale:
            est_scale, conf = discovery.estimate_scale(mdx_data)
            job.scale = est_scale
            self._log("INFO", f"Auto-scale: {est_scale} (confidence: {conf})")

        # Build config
        out_dir = self.output_dir or os.path.join(os.path.dirname(job.mdx_path), "out")
        os.makedirs(out_dir, exist_ok=True)
        build_cfg = {
            "mdx": job.mdx_path,
            "out": os.path.join(out_dir, job.model_name + ".m3"),
            "model_name": job.model_name,
            "scale": job.scale,
            "asset_texture_dir": "Assets\\Textures\\",
            "textures": {},
            "anim_names": {},
            "features": {"animations": True, "attachments": True, "particles": True,
                         "hittest": True, "camera": True},
            "particle_rate_scale": job.particle_rate,
            "team_color": True,
        }

        # Fuzzy anim names
        if self.fuzzy_anims and mdx_data.get("sequences"):
            names = [s["name"] for s in mdx_data["sequences"]]
            anim_map = fuzzy_anims.build_anim_map(names)
            build_cfg["anim_names"] = anim_map

        # Resolve textures
        tex_map = {}
        particle_ids = {e["textureId"] for e in mdx_data.get("particles", [])}
        for i, t in enumerate(mdx_data.get("textures", [])):
            if t.get("replaceableId") in (1, 2) or not t.get("path"):
                continue
            dds_name = os.path.splitext(os.path.basename(t["path"]))[0] + ".dds"
            tex_map[i] = dds_name
        build_cfg["textures"] = {str(k): v for k, v in tex_map.items()}

        # Start worker thread
        self.worker = ConversionWorker(self._get_blender_path())
        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(
            lambda: self.worker.convert(job, mdx_data, tex_map, out_dir, build_cfg))
        self.worker.signals.progress.connect(self._on_progress)
        self.worker.signals.log_message.connect(self._log)
        self.worker.signals.status_change.connect(self._on_status_change)
        self.worker.signals.job_done.connect(self._on_job_done)
        self.worker.signals.report_ready.connect(self._on_report_ready)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def _cancel_conversion(self):
        if self.worker:
            self.worker.cancel()
        self._log("WARNING", "Cancelling — finishing current model...")
        self.cancel_btn.setEnabled(False)

    def _conversion_finished(self):
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.overall_progress.setVisible(False)
        self.statusBar().showMessage("Conversion complete")

        done = sum(1 for j in self.jobs.values() if j.status == "done")
        failed = sum(1 for j in self.jobs.values() if j.status == "failed")
        self._log("SUCCESS", f"Done! {done} succeeded, {failed} failed.")
        self._save_history()

    # ── Signal Handlers ──────────────────────────────────────

    def _on_progress(self, model_path: str, pct: int):
        if model_path in self.jobs:
            self.jobs[model_path].progress = pct
            self._update_queue_item(self.jobs[model_path])
        self.overall_progress.setValue(
            sum(1 for j in self.jobs.values() if j.status == "done") + (1 if pct == 100 else 0))

    def _on_status_change(self, model_path: str, status: str):
        if model_path in self.jobs:
            self.jobs[model_path].status = status
            self._update_queue_item(self.jobs[model_path])

    def _on_job_done(self, model_path: str, success: bool, output_path: str):
        job = self.jobs.get(model_path)
        if not job:
            return
        duration = time.time() - job.start_time if job.start_time else 0
        self._log("SUCCESS" if success else "ERROR",
                  f"{job.model_name}: {'✓' if success else '✗'} ({duration:.1f}s)"
                  + (f" → {output_path}" if success else ""))

        # Generate actor XML
        if success and self.gen_actor:
            try:
                xml = actor_gen.generate_actor_xml(
                    job.model_name, f"Assets\\Textures\\{job.model_name}.m3",
                    scale=job.scale)
                actor_path = os.path.join(os.path.dirname(output_path),
                                          job.model_name + "_actor.xml")
                with open(actor_path, "w", encoding="utf-8") as f:
                    f.write(xml)
                self._log("INFO", f"Generated actor XML: {actor_path}")
            except Exception as e:
                self._log("WARNING", f"Could not generate actor XML: {e}")

        # Generate HTML report
        if self.gen_report and job.report_html:
            try:
                report_path = os.path.join(os.path.dirname(output_path or job.mdx_path),
                                           job.model_name + "_report.html")
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(job.report_html)
            except Exception:
                pass

        self._update_queue_item(job)
        self.worker_thread = None
        self.worker = None
        self._process_next()

    def _on_report_ready(self, model_path: str, html: str):
        if model_path in self.jobs:
            self.jobs[model_path].report_html = html

    # ── Logging ──────────────────────────────────────────────

    def _log(self, level: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        fmt = QTextCharFormat()
        if level == "ERROR":
            fmt.setForeground(QColor("#e74c3c"))
        elif level == "WARNING":
            fmt.setForeground(QColor("#f39c12"))
        elif level == "SUCCESS":
            fmt.setForeground(QColor("#a6e3a1"))
        elif level == "INFO":
            fmt.setForeground(QColor("#cdd6f4"))
        else:
            fmt.setForeground(QColor("#a6adc8"))
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(f"{ts}  {level:<7} {message}\n", fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    # ── Queue Updates ────────────────────────────────────────

    def _update_queue_item(self, job: ModelJob):
        item = self._get_item_by_path(job.mdx_path)
        if not item:
            return
        icons = {"queued": "⏳", "parsing": "📖", "textures": "🎨",
                 "blender": "🔧", "done": "✅", "failed": "❌"}
        item.setText(1, f"{icons.get(job.status, '?')} {job.status.title()}")
        item.setText(2, f"{job.progress}%" if job.progress > 0 else "—")
        w = len(job.warnings)
        item.setText(3, f"⚠ {w}" if w > 0 else "—")

    # ── Batch Tab ────────────────────────────────────────────

    def _browse_batch_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder with .mdx models")
        if folder:
            self.batch_folder_edit.setText(folder)

    def _scan_batch(self):
        folder = self.batch_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            self._log("ERROR", "Invalid folder path.")
            return
        pattern = "**/*.mdx" if self.batch_recursive.isChecked() else "*.mdx"
        import glob
        files = glob.glob(os.path.join(folder, pattern), recursive=self.batch_recursive.isChecked())
        self.batch_count_label.setText(f"Found: {len(files)} .mdx files")
        self.batch_convert_btn.setEnabled(len(files) > 0)

    def _batch_convert(self):
        folder = self.batch_folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            return
        pattern = "**/*.mdx" if self.batch_recursive.isChecked() else "*.mdx"
        import glob
        files = glob.glob(os.path.join(folder, pattern), recursive=self.batch_recursive.isChecked())
        for f in files:
            self._add_model_path(f)
        self._log("INFO", f"Added {len(files)} model(s) from batch folder.")
        self.tabs.setCurrentIndex(0)  # Switch to Convert tab
        self._start_conversion()

    # ── History ──────────────────────────────────────────────

    def _save_history(self):
        history = []
        for job in self.jobs.values():
            if job.status in ("done", "failed"):
                history.append({
                    "model": job.model_name,
                    "path": job.mdx_path,
                    "date": datetime.now().isoformat(),
                    "status": job.status,
                    "output": job.output_path,
                    "warnings": len(job.warnings),
                })
        self.settings.setValue("history", json.dumps(history[-100:]))
        self._load_history()

    def _load_history(self):
        self.history_tree.clear()
        try:
            data = json.loads(self.settings.value("history", "[]"))
            for entry in reversed(data):
                item = QTreeWidgetItem(self.history_tree)
                item.setText(0, entry.get("date", "")[:19])
                item.setText(1, entry.get("model", ""))
                item.setText(2, "")  # duration not stored
                item.setText(3, entry.get("status", "").upper())
                item.setText(4, entry.get("output", ""))
        except (json.JSONDecodeError, TypeError):
            pass

    def _clear_history(self):
        self.settings.setValue("history", "[]")
        self.history_tree.clear()

    # ── Settings ─────────────────────────────────────────────

    def _auto_detect_blender(self):
        path = discovery.find_blender()
        if path:
            self.blender_edit.setText(path)
            self.blender_status.setText(f"✅ Found: {path}")
        else:
            self.blender_status.setText("❌ Not found — please browse manually")

    def _browse_blender(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select blender.exe", "",
            "Blender (blender.exe);;All Files (*)")
        if path:
            self.blender_edit.setText(path)
            self.blender_status.setText(f"✅ Set to: {path}")

    def _get_blender_path(self) -> str:
        return self.blender_edit.text().strip() or discovery.find_blender() or "blender"

    def _save_settings(self):
        self.settings.setValue("blender_path", self.blender_edit.text().strip())
        self.settings.setValue("default_scale", self.scale_edit.text())
        self.settings.setValue("default_particle_rate", self.particle_rate_edit.text())
        self.settings.setValue("output_dir", self.output_edit.text())
        self.settings.setValue("auto_alpha", str(self.auto_alpha_cb.isChecked()).lower())
        self.settings.setValue("auto_scale", str(self.auto_scale_cb.isChecked()).lower())
        self.settings.setValue("fuzzy_anims", str(self.fuzzy_anims_cb.isChecked()).lower())
        self.settings.setValue("gen_actor", str(self.gen_actor_cb.isChecked()).lower())
        self.settings.setValue("gen_report", str(self.gen_report_cb.isChecked()).lower())
        self.default_scale = float(self.scale_edit.text() or "0.05")
        self.default_particle_rate = float(self.particle_rate_edit.text() or "1.0")
        self.output_dir = self.output_edit.text()
        self._log("INFO", "Settings saved.")
        self.statusBar().showMessage("Settings saved", 3000)

    def _restore_state(self):
        self.blender_edit.setText(self.blender_path)
        self.scale_edit.setText(str(self.default_scale))
        self.particle_rate_edit.setText(str(self.default_particle_rate))
        self.output_edit.setText(self.output_dir)
        self._auto_detect_blender()


# ──────────────────────────── Entry Point ────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("wc3toSC2")
    app.setOrganizationName("wc3toSC2")

    if "--cli" in sys.argv:
        # Fall back to CLI mode
        sys.argv.remove("--cli")
        import convert
        convert.main()
    else:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
