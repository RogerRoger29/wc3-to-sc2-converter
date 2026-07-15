"""WC3 → SC2 Model Converter — PySide6 GUI Application.
Double-click EXE with premium UX, drag-drop .mdx, auto-conversion.
All features exposed as user-configurable settings.

Usage: python main_window.py [model.mdx] [--silent] [--cli]
"""
from __future__ import annotations
import os, sys, json, time, threading
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QPushButton, QLabel,
    QPlainTextEdit, QProgressBar, QFileDialog, QCheckBox, QLineEdit,
    QGroupBox, QSplitter, QHeaderView, QStyleFactory, QScrollArea, QComboBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QSettings
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QPixmap, QPalette

import mdx as mdxlib, diagnostics, healer, discovery, fuzzy_anims, actor_gen, preview
import auto_updater, blender_manager

# ── Data Model ────────────────────────────────────────────
@dataclass
class ModelJob:
    mdx_path: str = ""; model_name: str = ""; status: str = "queued"; progress: int = 0
    warnings: list = field(default_factory=list); errors: list = field(default_factory=list)
    output_path: str = ""; report_html: str = ""; preview_path: str = ""
    scale: float = 0.05; particle_rate: float = 1.0
    texture_count: int = 0; animation_count: int = 0; start_time: float = 0.0
    def to_dict(self): return {"mdx_path":self.mdx_path,"model_name":self.model_name,"scale":self.scale,"particle_rate":self.particle_rate}
    @staticmethod
    def from_dict(d): return ModelJob(**{k:d.get(k,"") for k in ["mdx_path","model_name","scale","particle_rate"]})

# ── Worker ─────────────────────────────────────────────────
class ConversionSignals(QObject):
    progress=Signal(str,int); log_message=Signal(str,str); status_change=Signal(str,str)
    job_done=Signal(str,bool,str); report_ready=Signal(str,str)

class ConversionWorker(QObject):
    def __init__(self,blender=""): super().__init__(); self.signals=ConversionSignals(); self._blender=blender; self._cancelled=False
    def cancel(self): self._cancelled=True
    def convert(self,job,mdx_data,tex_map,out_dir,build_cfg):
        if self._cancelled: return
        self.signals.status_change.emit(job.mdx_path,"parsing")
        report=diagnostics.run_checks(mdx_data,job.mdx_path); job.warnings=[w.message for w in report.warnings]; job.errors=[e.message for e in report.errors]
        job.report_html=report.to_html(); self.signals.report_ready.emit(job.mdx_path,report.to_html())
        for w in report.warnings: self.signals.log_message.emit("WARNING",w.message)
        for e in report.errors: self.signals.log_message.emit("ERROR",e.message)
        if job.errors: job.status="failed"; self.signals.job_done.emit(job.mdx_path,False,""); return
        if report.auto_fixable_count>0:
            r=healer.apply_all_fixes(mdx_data,mdx_data.get("geosets",[]),mdx_data.get("bones",[]),mdx_data.get("helpers",[]),mdx_data.get("particles",[]))
            for f in r["fixes_applied"]: self.signals.log_message.emit("INFO",f"  ✓ {f}")
            mdx_data=r["data"]
        self.signals.status_change.emit(job.mdx_path,"textures"); self.signals.progress.emit(job.mdx_path,30)
        self.signals.status_change.emit(job.mdx_path,"blender"); self.signals.progress.emit(job.mdx_path,60)
        try:
            import subprocess
            cfg_path=os.path.join(out_dir,"_build_config.json"); json.dump(build_cfg,open(cfg_path,"w",encoding="utf-8"),indent=1)
            blender=self._blender or discovery.find_blender() or blender_manager.get_managed_blender_path() or "blender"
            proc=subprocess.run([blender,"--background","--factory-startup","--python",os.path.join(os.path.dirname(__file__),"build_m3.py"),"--",cfg_path],capture_output=True,text=True,timeout=300)
            self.signals.progress.emit(job.mdx_path,90)
            if "EXPORT_FAILED" in proc.stdout or proc.returncode!=0: self.signals.log_message.emit("ERROR",f"Blender failed:\n{proc.stderr[-800:]}"); job.status="failed"; self.signals.job_done.emit(job.mdx_path,False,""); return
            for l in proc.stdout.splitlines():
                if any(k in l for k in ("anim '","mat[","particle[","EXPORT_OK","baked","BUILD_DONE")): self.signals.log_message.emit("INFO",f"  {l.strip()}")
            self.signals.progress.emit(job.mdx_path,100); job.status="done"; job.output_path=build_cfg["out"]; self.signals.job_done.emit(job.mdx_path,True,build_cfg["out"])
        except Exception as ex: self.signals.log_message.emit("ERROR",str(ex)); job.status="failed"; self.signals.job_done.emit(job.mdx_path,False,"")

# ── Main Window ────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self,auto_files=None):
        super().__init__(); self.setWindowTitle("WC3 → SC2 Model Converter v3.1"); self.resize(1280,850); self.setMinimumSize(960,620)
        self.jobs:Dict[str,ModelJob]={}; self.settings=QSettings("wc3toSC2","Converter"); self.worker=None; self.worker_thread=None
        self._load_all_settings(); self._setup_theme(); self._setup_ui(); self._restore_session(); self._check_updates()
        if auto_files:
            for f in auto_files:
                if os.path.isdir(f): self._add_folder_path(f)
                elif f.lower().endswith(".mdx"): self._add_model_path(f)
            if self.jobs: self.statusBar().showMessage(f"Loaded {len(self.jobs)} model(s). Click Convert All.")

    def _setup_theme(self):
        app=QApplication.instance(); app.setStyle(QStyleFactory.create("Fusion"))
        p=QPalette(); p.setColor(QPalette.Window,QColor(30,30,46)); p.setColor(QPalette.WindowText,QColor(205,214,244))
        p.setColor(QPalette.Base,QColor(24,24,37)); p.setColor(QPalette.Text,QColor(205,214,244))
        p.setColor(QPalette.Button,QColor(49,50,68)); p.setColor(QPalette.ButtonText,QColor(205,214,244))
        p.setColor(QPalette.Highlight,QColor(137,180,250)); p.setColor(QPalette.HighlightedText,QColor(30,30,46))
        app.setPalette(p)

    def _setup_ui(self):
        c=QWidget(); self.setCentralWidget(c); l=QVBoxLayout(c); l.setContentsMargins(8,8,8,8)
        self.tabs=QTabWidget(); self.tabs.addTab(self._convert_tab(),"🔄 Convert"); self.tabs.addTab(self._settings_tab(),"⚙ Settings"); self.tabs.addTab(self._about_tab(),"ℹ About")
        l.addWidget(self.tabs); self.statusBar().showMessage("Drag .mdx files here or click '+ Add Models'")

    def _convert_tab(self):
        tab=QWidget(); sp=QSplitter(Qt.Horizontal)
        left=QWidget(); ll=QVBoxLayout(left); ll.setContentsMargins(0,0,4,0)
        self.welcome=QLabel("<div style='color:#a6adc8;padding:30px;text-align:center'><h2 style='color:#89b4fa'>WC3 → SC2 Converter</h2><p>Drag <b>.mdx</b> files here to begin</p><p style='font-size:12px'>or click <b>+ Add Models</b></p></div>")
        self.welcome.setAlignment(Qt.AlignCenter); self.welcome.setVisible(True); ll.addWidget(self.welcome)
        self.queue_tree=QTreeWidget(); self.queue_tree.setHeaderLabels(["Model","Status","Progress","Warnings"])
        self.queue_tree.setAlternatingRowColors(True); self.queue_tree.setDragDropMode(self.queue_tree.DragDropMode.DropOnly); self.queue_tree.setAcceptDrops(True)
        self.queue_tree.dragEnterEvent=lambda e: e.acceptProposedAction() if e.mimeData().hasUrls() else None
        self.queue_tree.dropEvent=self._drop; self.queue_tree.itemSelectionChanged.connect(self._on_select)
        for i,c in enumerate([QHeaderView.Stretch,QHeaderView.ResizeToContents,QHeaderView.ResizeToContents,QHeaderView.ResizeToContents]):
            self.queue_tree.header().setSectionResizeMode(i,c)
        self.queue_tree.setVisible(False); ll.addWidget(self.queue_tree)
        br=QHBoxLayout(); add=QPushButton("+ Add Models"); add.clicked.connect(self._add_models); br.addWidget(add)
        fd=QPushButton("📁 Folder"); fd.clicked.connect(self._add_folder); br.addWidget(fd)
        cl=QPushButton("Clear Done"); cl.clicked.connect(self._clear_done); br.addWidget(cl); br.addStretch()
        self.cvt_btn=QPushButton("▶ Convert All"); self.cvt_btn.setStyleSheet("background:#a6e3a1;color:#1e1e2e;font-weight:bold;padding:6px 20px"); self.cvt_btn.clicked.connect(self._start); br.addWidget(self.cvt_btn)
        self.stp_btn=QPushButton("■ Stop"); self.stp_btn.setEnabled(False); self.stp_btn.clicked.connect(self._cancel); br.addWidget(self.stp_btn); ll.addLayout(br); sp.addWidget(left)
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(4,0,0,0)
        self.preview_lbl=QLabel(); self.preview_lbl.setAlignment(Qt.AlignCenter); self.preview_lbl.setMinimumHeight(200)
        self.preview_lbl.setStyleSheet("background:#1e1e2e;border:1px solid #313244;border-radius:4px")
        self.preview_lbl.setText("<span style='color:#6c7086'>Model preview</span>"); rl.addWidget(self.preview_lbl,2)
        self.log_view=QPlainTextEdit(); self.log_view.setReadOnly(True); self.log_view.setFont(QFont("Consolas",10)); self.log_view.setMaximumBlockCount(5000); rl.addWidget(self.log_view,3)
        self.ov_prog=QProgressBar(); self.ov_prog.setVisible(False); rl.addWidget(self.ov_prog); sp.addWidget(right); sp.setSizes([550,650])
        ml=QVBoxLayout(tab); ml.addWidget(sp); return tab

    def _settings_tab(self):
        tab=QWidget(); sc=QScrollArea(); sc.setWidgetResizable(True); w=QWidget(); lo=QVBoxLayout(w)

        # ── Blender ──
        gb=QGroupBox("🔧 Blender"); gl=QVBoxLayout(gb)
        hb=QHBoxLayout(); self._be=QLineEdit(self._bp); self._be.setPlaceholderText("Auto-detect"); hb.addWidget(QLabel("Path:")); hb.addWidget(self._be)
        a=QPushButton("Auto-Detect"); a.clicked.connect(self._auto_blender); hb.addWidget(a)
        b=QPushButton("Browse..."); b.clicked.connect(lambda: self._browse(self._be,"blender.exe","Blender (*.exe);;All (*)")); hb.addWidget(b); gl.addLayout(hb)
        self._1clk=QPushButton("🔧 One-Click Setup (Download Blender + Addon)"); self._1clk.setStyleSheet("background:#f9e2af;color:#1e1e2e;font-weight:bold;padding:8px"); self._1clk.clicked.connect(self._oneclick); gl.addWidget(self._1clk)
        self._bs=QLabel("Not checked"); gl.addWidget(self._bs); lo.addWidget(gb)

        # ── Scale & Output ──
        gb2=QGroupBox("📐 Scale & Output"); gl2=QVBoxLayout(gb2)
        for lbl,attr,defv in [("Scale:","_se","0.05"),("Particle rate:","_pe","1.0"),("Particle size:","_pse","1.0"),("Output dir:","_oe","./out")]:
            hd=QHBoxLayout(); hd.addWidget(QLabel(lbl)); e=QLineEdit(str(getattr(self,attr.replace("_e",""),defv))); setattr(self,attr,e); hd.addWidget(e); gl2.addLayout(hd)
        lo.addWidget(gb2)

        # ── Animation Quality ──
        gb3=QGroupBox("🎬 Animation Quality"); gl3=QVBoxLayout(gb3)
        hf=QHBoxLayout(); hf.addWidget(QLabel("FPS mode:")); self._fps_cb=QComboBox(); self._fps_cb.addItems(["Auto-detect","30","60","15","10"]); self._fps_cb.setCurrentText(self._fps_mode); hf.addWidget(self._fps_cb); gl3.addLayout(hf)
        self._squad_cb=QCheckBox("Squad quaternion interpolation (higher quality rotations)"); self._squad_cb.setChecked(self._squad); gl3.addWidget(self._squad_cb)
        self._kf_cb=QCheckBox("Keyframe reduction (smaller M3 files)"); self._kf_cb.setChecked(self._kf_reduce); gl3.addWidget(self._kf_cb)
        lo.addWidget(gb3)

        # ── Mesh & LOD ──
        gb4=QGroupBox("🔺 Mesh & LOD"); gl4=QVBoxLayout(gb4)
        self._lod_cb=QComboBox(); self._lod_cb.addItems(["No LOD","LOD1 (50% tris)","LOD1+LOD2 (50%+25% tris)"]); self._lod_cb.setCurrentIndex(self._lod_level); gl4.addWidget(self._lod_cb)
        lo.addWidget(gb4)

        # ── Team Color ──
        gb5=QGroupBox("🎨 Team Color"); gl5=QVBoxLayout(gb5)
        self._tc_cb=QComboBox(); self._tc_cb.addItems(["TEAMEMIS (diffuse mask)","UV Mask (accurate)","Off"]); self._tc_cb.setCurrentIndex(self._tc_mode); gl5.addWidget(self._tc_cb)
        lo.addWidget(gb5)

        # ── PBR / Normals ──
        gb6=QGroupBox("✨ PBR & Normals"); gl6=QVBoxLayout(gb6)
        self._nm_cb=QCheckBox("Generate normal maps from diffuse"); self._nm_cb.setChecked(self._normals); gl6.addWidget(self._nm_cb)
        hd2=QHBoxLayout(); hd2.addWidget(QLabel("Normal strength:")); self._ns_e=QLineEdit(str(self._nm_strength)); hd2.addWidget(self._ns_e); gl6.addLayout(hd2)
        lo.addWidget(gb6)

        # ── Pipeline ──
        gb7=QGroupBox("⚡ Pipeline"); gl7=QVBoxLayout(gb7)
        self._mt_cb=QCheckBox("Multi-threaded texture conversion"); self._mt_cb.setChecked(self._multi_tex); gl7.addWidget(self._mt_cb)
        self._cache_cb=QCheckBox("MDX parse cache (faster re-conversions)"); self._cache_cb.setChecked(self._mdx_cache); gl7.addWidget(self._cache_cb)
        self._aa_cb=QCheckBox("Auto-detect inverted alpha"); self._aa_cb.setChecked(self._auto_alpha); gl7.addWidget(self._aa_cb)
        self._as_cb=QCheckBox("Auto-estimate scale"); self._as_cb.setChecked(self._auto_scale); gl7.addWidget(self._as_cb)
        self._fa_cb=QCheckBox("Fuzzy-match animation names"); self._fa_cb.setChecked(self._fuzzy_anims); gl7.addWidget(self._fa_cb)
        lo.addWidget(gb7)

        # ── Output ──
        gb8=QGroupBox("📄 Output"); gl8=QVBoxLayout(gb8)
        self._ax_cb=QCheckBox("Generate SC2 actor XML"); self._ax_cb.setChecked(self._gen_actor); gl8.addWidget(self._ax_cb)
        self._gr_cb=QCheckBox("Generate HTML conversion report"); self._gr_cb.setChecked(self._gen_report); gl8.addWidget(self._gr_cb)
        lo.addWidget(gb8)

        sv=QPushButton("💾 Save Settings"); sv.clicked.connect(self._save_all); lo.addWidget(sv); lo.addStretch()
        sc.setWidget(w); tl=QVBoxLayout(tab); tl.addWidget(sc); return tab

    def _about_tab(self):
        t=QWidget(); l=QVBoxLayout(t); l.setAlignment(Qt.AlignCenter)
        l.addWidget(QLabel("<h1 style='color:#89b4fa'>WC3 → SC2 Model Converter</h1>"))
        l.addWidget(QLabel("<p>Version 3.1.0</p>"))
        l.addWidget(QLabel("<p><a href='https://github.com/RogerRoger29/wc3-to-sc2-converter' style='color:#89b4fa'>GitHub</a></p>"))
        return t

    # ── Drag & Drop ──
    def _drop(self,e):
        for u in e.mimeData().urls():
            p=u.toLocalFile()
            if os.path.isdir(p): self._add_folder_path(p)
            elif p.lower().endswith(".mdx"): self._add_model_path(p)
        e.acceptProposedAction()

    # ── Model Management ──
    def _add_models(self):
        fs,_=QFileDialog.getOpenFileNames(self,"Select MDX","","MDX (*.mdx);;All (*)")
        for f in fs: self._add_model_path(f)

    def _add_folder(self):
        d=QFileDialog.getExistingDirectory(self,"Select folder")
        if d: self._add_folder_path(d)

    def _add_model_path(self,p,silent=False):
        if p in self.jobs: return
        n=os.path.splitext(os.path.basename(p))[0]; j=ModelJob(mdx_path=p,model_name=n,scale=self._scale,particle_rate=self._prate)
        try:
            m=mdxlib.parse(p); j.texture_count=len(m.get("textures",[])); j.animation_count=len(m.get("sequences",[]))
        except: pass
        self.jobs[p]=j; self._add_item(j)
        if not silent: self._log("INFO",f"Added: {n}")
        self._vis(); self._save_session()

    def _add_folder_path(self,d):
        c=0
        for r,_,fs in os.walk(d):
            for f in fs:
                if f.lower().endswith(".mdx"): self._add_model_path(os.path.join(r,f),silent=True); c+=1
        self._log("INFO",f"Added {c} model(s)"); self._save_session()

    def _add_item(self,j):
        it=QTreeWidgetItem(self.queue_tree); it.setText(0,j.model_name); it.setText(1,"⏳ Queued"); it.setText(2,"—"); it.setText(3,"—"); it.setData(0,Qt.UserRole,j.mdx_path)

    def _vis(self):
        h=self.queue_tree.topLevelItemCount()>0; self.welcome.setVisible(not h); self.queue_tree.setVisible(h)

    def _on_select(self):
        its=self.queue_tree.selectedItems()
        if not its: return
        p=its[0].data(0,Qt.UserRole); j=self.jobs.get(p)
        if j and j.preview_path and os.path.exists(j.preview_path):
            self.preview_lbl.setPixmap(QPixmap(j.preview_path).scaled(480,480,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        elif j: self.preview_lbl.setText(f"<span style='color:#6c7086'>{j.model_name}<br>{j.texture_count} textures, {j.animation_count} anims</span>")

    def _clear_done(self):
        for i in range(self.queue_tree.topLevelItemCount()-1,-1,-1):
            it=self.queue_tree.topLevelItem(i); p=it.data(0,Qt.UserRole)
            if p in self.jobs and self.jobs[p].status in ("done","failed"): self.queue_tree.takeTopLevelItem(i)
        self._vis()

    # ── Conversion ──
    def _start(self):
        q=[j for j in self.jobs.values() if j.status=="queued"]
        if not q: self._log("WARNING","No models queued."); return
        self.cvt_btn.setEnabled(False); self.stp_btn.setEnabled(True); self.ov_prog.setVisible(True); self.ov_prog.setMaximum(len(q)); self._next()

    def _next(self):
        q=[j for j in self.jobs.values() if j.status=="queued"]
        if not q: self._done(); return
        j=q[0]; j.start_time=time.time(); self._log("INFO",f"▶ {j.model_name}")
        try: md=mdxlib.parse(j.mdx_path)
        except Exception as e: self._log("ERROR",str(e)); j.status="failed"; self._upd(j); self._next(); return
        if self._auto_scale: s,c=discovery.estimate_scale(md); j.scale=s; self._log("INFO",f"Scale: {s} ({c})")
        od=self._od or os.path.join(os.path.dirname(j.mdx_path),"out"); os.makedirs(od,exist_ok=True)
        bc={"mdx":j.mdx_path,"out":os.path.join(od,j.model_name+".m3"),"model_name":j.model_name,"scale":j.scale,
            "asset_texture_dir":"Assets\\Textures\\","textures":{},"anim_names":{},
            "features":{"animations":True,"attachments":True,"particles":True,"hittest":True,"camera":True},
            "particle_rate_scale":j.particle_rate,"team_color":self._tc_mode!=2,
            # v3.1 settings passed through
            "fps_mode":self._fps_mode,"squad":self._squad,"keyframe_reduce":self._kf_reduce,
            "lod_level":self._lod_level,"tc_mask":self._tc_mode==1,"gen_normals":self._normals,
            "normal_strength":self._nm_strength}
        if self._fuzzy_anims and md.get("sequences"): bc["anim_names"]=fuzzy_anims.build_anim_map([s["name"] for s in md["sequences"]])
        tm={}
        for i,t in enumerate(md.get("textures",[])):
            if t.get("replaceableId") in (1,2) or not t.get("path"): continue
            tm[i]=os.path.splitext(os.path.basename(t["path"]))[0]+".dds"
        bc["textures"]={str(k):v for k,v in tm.items()}
        bl=self._get_blender()
        if bl and os.path.exists(bl): threading.Thread(target=lambda: setattr(j,"preview_path",preview.render_preview(j.mdx_path,bl) or ""),daemon=True).start()
        self.worker=ConversionWorker(bl); self.worker_thread=QThread(); self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(lambda: self.worker.convert(j,md,tm,od,bc))
        self.worker.signals.progress.connect(self._op); self.worker.signals.log_message.connect(self._log)
        self.worker.signals.status_change.connect(self._os); self.worker.signals.job_done.connect(self._oj)
        self.worker.signals.report_ready.connect(self._or); self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def _cancel(self):
        if self.worker: self.worker.cancel()
        self._log("WARNING","Stopping..."); self.stp_btn.setEnabled(False)

    def _done(self):
        self.cvt_btn.setEnabled(True); self.stp_btn.setEnabled(False); self.ov_prog.setVisible(False)
        d=sum(1 for j in self.jobs.values() if j.status=="done"); f=sum(1 for j in self.jobs.values() if j.status=="failed")
        self._log("SUCCESS",f"✓ {d} ok, {f} failed"); self._save_session()

    def _op(self,p,pc):
        if p in self.jobs: self.jobs[p].progress=pc; self._upd(self.jobs[p])
        self.ov_prog.setValue(sum(1 for j in self.jobs.values() if j.status=="done"))

    def _os(self,p,s):
        if p in self.jobs: self.jobs[p].status=s; self._upd(self.jobs[p])

    def _oj(self,p,ok,out):
        j=self.jobs.get(p)
        if j:
            d=time.time()-j.start_time if j.start_time else 0; self._log("SUCCESS" if ok else "ERROR",f"{'✓' if ok else '✗'} {j.model_name} ({d:.1f}s)")
            if ok and self._gen_actor:
                try:
                    x=actor_gen.generate_actor_xml(j.model_name,f"Assets\\Textures\\{j.model_name}.m3",scale=j.scale)
                    with open(os.path.join(os.path.dirname(out),j.model_name+"_actor.xml"),"w") as fh: fh.write(x)
                except: pass
            if self._gen_report and j.report_html:
                try: open(os.path.join(os.path.dirname(out or j.mdx_path),j.model_name+"_report.html"),"w").write(j.report_html)
                except: pass
            self._upd(j)
        self.worker_thread=None; self.worker=None; self._next()

    def _or(self,p,html):
        if p in self.jobs: self.jobs[p].report_html=html

    # ── Logging ──
    def _log(self,lv,msg):
        ts=datetime.now().strftime("%H:%M:%S")
        f=QTextCharFormat(); f.setForeground({"ERROR":QColor("#e74c3c"),"WARNING":QColor("#f39c12"),"SUCCESS":QColor("#a6e3a1"),"INFO":QColor("#cdd6f4")}.get(lv,QColor("#a6adc8")))
        c=self.log_view.textCursor(); c.movePosition(QTextCursor.End); c.insertText(f"{ts}  {lv:<7} {msg}\n",f); self.log_view.setTextCursor(c); self.log_view.ensureCursorVisible()

    def _upd(self,j):
        for i in range(self.queue_tree.topLevelItemCount()):
            it=self.queue_tree.topLevelItem(i)
            if it.data(0,Qt.UserRole)==j.mdx_path:
                ic={"queued":"⏳","parsing":"📖","textures":"🎨","blender":"🔧","done":"✅","failed":"❌"}
                it.setText(1,f"{ic.get(j.status,'?')} {j.status.title()}"); it.setText(2,f"{j.progress}%" if j.progress else "—")
                it.setText(3,f"⚠ {len(j.warnings)}" if j.warnings else "—"); break

    # ── Session ──
    def _save_session(self):
        self.settings.setValue("session",json.dumps([j.to_dict() for j in self.jobs.values() if j.status=="queued"]))

    def _restore_session(self):
        try:
            d=json.loads(self.settings.value("session","[]"))
            for x in d:
                if os.path.exists(x.get("mdx_path","")): self._add_model_path(x["mdx_path"],silent=True)
            if self.jobs: self._log("INFO",f"Restored {len(self.jobs)} model(s).")
        except: pass

    def _check_updates(self):
        threading.Thread(target=lambda: (lambda u: self._log("INFO",f"🔔 Update: {u['version']} — {u['url']}") if u else None)(auto_updater.check_for_update()),daemon=True).start()

    # ── Settings ──
    def _load_all_settings(self):
        s=self.settings
        self._bp=s.value("blender_path",""); self._scale=float(s.value("default_scale","0.05"))
        self._prate=float(s.value("default_particle_rate","1.0")); self._psize=float(s.value("default_particle_size","1.0"))
        self._od=s.value("output_dir","./out"); self._fps_mode=s.value("fps_mode","Auto-detect")
        self._squad=s.value("squad","true")=="true"; self._kf_reduce=s.value("keyframe_reduce","true")=="true"
        self._lod_level=int(s.value("lod_level","0")); self._tc_mode=int(s.value("tc_mode","0"))
        self._normals=s.value("gen_normals","false")=="true"; self._nm_strength=float(s.value("normal_strength","1.0"))
        self._multi_tex=s.value("multi_tex","true")=="true"; self._mdx_cache=s.value("mdx_cache","true")=="true"
        self._auto_alpha=s.value("auto_alpha","true")=="true"; self._auto_scale=s.value("auto_scale","true")=="true"
        self._fuzzy_anims=s.value("fuzzy_anims","true")=="true"; self._gen_actor=s.value("gen_actor","true")=="true"
        self._gen_report=s.value("gen_report","true")=="true"

    def _save_all(self):
        s=self.settings
        for k,a in [("blender_path","_be"),("default_scale","_se"),("default_particle_rate","_pe"),
                     ("default_particle_size","_pse"),("output_dir","_oe")]: s.setValue(k,getattr(self,a).text().strip())
        s.setValue("fps_mode",self._fps_cb.currentText()); s.setValue("squad",str(self._squad_cb.isChecked()).lower())
        s.setValue("keyframe_reduce",str(self._kf_cb.isChecked()).lower()); s.setValue("lod_level",str(self._lod_cb.currentIndex()))
        s.setValue("tc_mode",str(self._tc_cb.currentIndex())); s.setValue("gen_normals",str(self._nm_cb.isChecked()).lower())
        s.setValue("normal_strength",self._ns_e.text().strip())
        for k,a in [("multi_tex","_mt_cb"),("mdx_cache","_cache_cb"),("auto_alpha","_aa_cb"),
                     ("auto_scale","_as_cb"),("fuzzy_anims","_fa_cb"),("gen_actor","_ax_cb"),("gen_report","_gr_cb")]:
            s.setValue(k,str(getattr(self,a).isChecked()).lower())
        self._load_all_settings(); self._log("INFO","✅ Settings saved — all features configured"); self.statusBar().showMessage("Settings saved",3000)

    def _auto_blender(self):
        p=discovery.find_blender() or blender_manager.get_managed_blender_path()
        if p and os.path.exists(p): self._be.setText(p); self._bs.setText(f"✅ {p}")
        else: self._bs.setText("❌ Not found — use One-Click Setup")

    def _browse(self,ed,title,filt):
        p,_=QFileDialog.getOpenFileName(self,title,"",filt)
        if p: ed.setText(p)

    def _oneclick(self):
        self._log("INFO","One-click setup..."); self._1clk.setEnabled(False); self._1clk.setText("Downloading...")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            ex.submit(lambda: blender_manager.ensure_blender_ready()).add_done_callback(
                lambda f: (self._be.setText(str(f.result() or "")),
                           self._bs.setText(f"✅ {f.result()}" if f.result() else "❌ Failed"),
                           self._log("SUCCESS" if f.result() else "ERROR","Blender setup complete." if f.result() else "Failed."),
                           setattr(self._1clk,"enabled",True),
                           self._1clk.setText("🔧 One-Click Setup")))

    def _get_blender(self): return self._be.text().strip() or discovery.find_blender() or blender_manager.get_managed_blender_path() or "blender"

# ── Entry Point ──
def main():
    app=QApplication(sys.argv); app.setApplicationName("wc3toSC2")
    if "--cli" in sys.argv: sys.argv.remove("--cli"); import convert; convert.main(); return
    af=[a for a in sys.argv[1:] if not a.startswith("-")]
    w=MainWindow(auto_load_files=af if af else None); w.show()
    if af and "--silent" in sys.argv and w.jobs: QTimer.singleShot(500,w._start)
    sys.exit(app.exec())

if __name__=="__main__": main()
