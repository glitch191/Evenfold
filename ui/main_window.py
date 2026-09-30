"""Main window: header with the Simple / Advanced switch, both layouts, the
menu bar, window-state persistence, drag and drop, and the frame loop that
animates visualisations during playback."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from reports.report_export import ReportEntry, export_csv, export_pdf

from . import theme
from .advanced_view import AdvancedView
from .dialogs import AboutDialog, BasicsDialog, DefaultTargetDialog, LoadPresetDialog, ManagePresetsDialog, ask_preset_name
from .onboarding import OnboardingDialog
from .presets import PresetStore
from .session import Session
from .settings import AppSettings
from .simple_view import SimpleView
from .tooltips import tip
from .visualizers import RenderClock, ThemedPlot
from .playback import PlaybackEngine
from .widgets import BIT_DEPTH_LABELS, SegmentedControl, button, wav_paths_from_mime

PANEL_ACTIONS = (("waveform", "Toggle Waveform View"), ("spectrum", "Toggle Spectrum View"),
                 ("comparison", "Toggle Batch Comparison View"))


class MainWindow(QMainWindow):
    def __init__(self, app_settings: AppSettings, hardware_acceleration: bool) -> None:
        super().__init__()
        self.app_settings = app_settings
        self.session = Session(app_settings, self)
        self.presets = PresetStore()
        self.clock = RenderClock(self)
        self.engine = PlaybackEngine(self)
        self._hardware = hardware_acceleration
        self._was_maximized = True

        self.setWindowTitle(theme.APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.setMinimumSize(1120, 720)
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("Root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_header())
        self.stack = QStackedWidget()
        self.simple = SimpleView(self.session, self.engine, app_settings)
        self.advanced = AdvancedView(self.session, self.engine, app_settings, hardware_acceleration)
        self.stack.addWidget(self.simple)
        self.stack.addWidget(self.advanced)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        for view in (self.simple, self.advanced):
            view.browseRequested.connect(self.open_files)
            view.filesDropped.connect(self.session.add_paths)
            view.openOutputRequested.connect(self.open_output_folder)
        self.advanced.exportReportRequested.connect(self.export_report)
        panel = self.advanced.settings_panel
        panel.hardwareToggled.connect(self.set_hardware_acceleration)
        panel.savePresetRequested.connect(self.save_preset)
        panel.loadPresetRequested.connect(self.load_preset)

        self._build_status_bar()
        self._build_menus()

        s = self.session
        s.selectionChanged.connect(lambda _uid: self._sync_player())
        s.trackChanged.connect(self._on_track_changed)
        s.aboutToStart.connect(lambda dry: None if dry else self.engine.release_files())
        s.notice.connect(lambda _level, text: self.statusBar().showMessage(text, 8000))
        s.batchFinished.connect(self._on_batch_finished)
        s.busyChanged.connect(lambda _busy: self._sync_menus())
        s.settingsChanged.connect(self._sync_menus)
        s.tracksChanged.connect(self._sync_menus)
        s.selectionChanged.connect(lambda _uid: self._sync_menus())
        self.engine.stateChanged.connect(self._on_playback_state)
        self.clock.tick.connect(self._on_frame)
        self.clock.rateChanged.connect(lambda _rate: self._update_render_label())

        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._space)
        QShortcut(QKeySequence(Qt.Key.Key_A), self, self._switch_version)
        self.set_mode(app_settings.mode, save=False)
        self._sync_menus()

    # ------------------------------------------------------------------ building
    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(theme.HEADER_HEIGHT)
        h = QHBoxLayout(bar)
        h.setContentsMargins(theme.SPACE["lg"], 0, theme.SPACE["md"], 0)
        h.setSpacing(theme.SPACE["sm"] + 4)
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(QSize(32, 32)))
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel(theme.APP_NAME)
        name.setObjectName("AppName")
        tagline = QLabel(theme.APP_TAGLINE)
        tagline.setObjectName("AppTagline")
        names.addStretch(1)
        names.addWidget(name)
        names.addWidget(tagline)
        names.addStretch(1)
        h.addWidget(logo)
        h.addLayout(names)
        h.addStretch(1)
        self.mode_switch = SegmentedControl([("simple", "Simple"), ("advanced", "Advanced")], "mode")
        self.mode_switch.changed.connect(self.set_mode)
        h.addWidget(self.mode_switch, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addStretch(1)
        basics = button("Mastering Basics", "ghost", "ph.book-open", "basics")
        basics.clicked.connect(self.show_basics)
        help_button = button("", "icon", "ph.question", "help")
        help_button.clicked.connect(self.show_onboarding)
        h.addWidget(basics)
        h.addWidget(help_button)
        return bar

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.render_label = QLabel()
        self.render_label.setToolTip(tip("hardware_acceleration"))
        bar.addPermanentWidget(self.render_label)
        self._update_render_label()

    def _action(self, menu: QMenu, text: str, slot, shortcut=None, icon_name: str | None = None,
                tip_key: str | None = None, checkable: bool = False) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if icon_name:
            action.setIcon(theme.icon(icon_name, "text_muted"))
        if tip_key:
            action.setToolTip(tip(tip_key))
            action.setStatusTip(tip(tip_key).split("<br>", 1)[0].replace("<b>", "").replace("</b>", ""))
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        file_menu.setToolTipsVisible(True)
        self.act_open = self._action(file_menu, "Open Files…", self.open_files, QKeySequence.StandardKey.Open,
                                     "ph.folder-open", "add_files")
        self.act_output = self._action(file_menu, "Open Output Folder", self.open_output_folder, "Ctrl+Shift+O",
                                       "ph.arrow-square-out", "open_output")
        file_menu.addSeparator()
        self.act_report = self._action(file_menu, "Export Report (CSV/PDF)…", self.export_report, "Ctrl+E",
                                       "ph.export", "export_report")
        file_menu.addSeparator()
        self._action(file_menu, "Exit", self.close, QKeySequence.StandardKey.Quit, tip_key="exit")

        edit_menu = bar.addMenu("&Edit")
        edit_menu.setToolTipsVisible(True)
        presets = edit_menu.addMenu(theme.icon("ph.sliders-horizontal", "text_muted"), "Presets")
        presets.setToolTipsVisible(True)
        self.act_save_preset = self._action(presets, "Save Preset…", self.save_preset, "Ctrl+S", "ph.floppy-disk", "save_preset")
        self.act_load_preset = self._action(presets, "Load Preset…", self.load_preset, "Ctrl+L", "ph.download-simple", "load_preset")
        self._action(presets, "Manage Presets…", self.manage_presets, None, "ph.gear", "manage_presets")
        edit_menu.addSeparator()
        self.act_remove = self._action(edit_menu, "Remove Selected Track", self._remove_selected, None, "ph.x", "remove_track")
        self.act_clear = self._action(edit_menu, "Clear Album", self._clear_album, None, "ph.trash", "clear_tracks")

        view_menu = bar.addMenu("&View")
        view_menu.setToolTipsVisible(True)
        modes = QActionGroup(self)
        modes.setExclusive(True)
        self.act_simple = self._action(view_menu, "Simple Mode", lambda: self.set_mode("simple"), "Ctrl+1",
                                       tip_key="mode", checkable=True)
        self.act_advanced = self._action(view_menu, "Advanced Mode", lambda: self.set_mode("advanced"), "Ctrl+2",
                                         tip_key="mode", checkable=True)
        modes.addAction(self.act_simple)
        modes.addAction(self.act_advanced)
        view_menu.addSeparator()
        self.act_panels: dict[str, QAction] = {}
        tips = {"waveform": "waveform", "spectrum": "spectrum", "comparison": "batch_comparison"}
        for name, text in PANEL_ACTIONS:
            self.act_panels[name] = self._action(view_menu, text, lambda checked, n=name: self._set_panel(n, checked),
                                                 tip_key=tips[name], checkable=True)
        view_menu.addSeparator()
        self.act_fullscreen = self._action(view_menu, "Enter Full Screen", self.toggle_fullscreen,
                                           QKeySequence.StandardKey.FullScreen, "ph.arrows-out")
        if self.act_fullscreen.shortcut().isEmpty():
            self.act_fullscreen.setShortcut(QKeySequence("F11"))
        self._action(view_menu, "Reset Layout", self.reset_layout, None, "ph.arrow-counter-clockwise", "reset_layout")

        settings_menu = bar.addMenu("&Settings")
        settings_menu.setToolTipsVisible(True)
        self.act_hardware = self._action(settings_menu, "Hardware Acceleration", self.set_hardware_acceleration,
                                         tip_key="hardware_acceleration", checkable=True)
        self.act_hardware.setChecked(self._hardware)
        self.act_default_target = self._action(settings_menu, "Target LUFS Default…", self.edit_default_target, None,
                                               "ph.gauge", "target_lufs")
        depth_menu = settings_menu.addMenu("Output Bit Depth Override")
        depth_menu.setToolTipsVisible(True)
        depth_group = QActionGroup(self)
        depth_group.setExclusive(True)
        self.act_depths: dict[str | None, QAction] = {}
        for subtype, text in [(None, "Keep Original (Recommended)")] + list(BIT_DEPTH_LABELS.items()):
            action = self._action(depth_menu, text, lambda _c=False, st=subtype: self.set_output_override(st),
                                  tip_key="bit_depth_override", checkable=True)
            depth_group.addAction(action)
            self.act_depths[subtype] = action
            if subtype is None:
                depth_menu.addSeparator()

        help_menu = bar.addMenu("&Help")
        help_menu.setToolTipsVisible(True)
        self._action(help_menu, "Getting Started", self.show_onboarding, None, "ph.rocket-launch", "help")
        self._action(help_menu, "Mastering Basics", self.show_basics, QKeySequence.StandardKey.HelpContents, "ph.book-open", "basics")
        help_menu.addSeparator()
        self._action(help_menu, f"About {theme.APP_NAME}", lambda: AboutDialog(self).exec(), None, "ph.info", "about")

    # ------------------------------------------------------------------ state sync
    def _sync_menus(self) -> None:
        s = self.session
        busy = s.busy
        self.act_open.setEnabled(not busy)
        self.act_output.setEnabled(bool(s.output_dirs))
        self.act_report.setEnabled(bool(s.tracks))
        self.act_remove.setEnabled(not busy and s.selected() is not None)
        self.act_clear.setEnabled(not busy and bool(s.tracks))
        # Settings can't change while a batch runs: its results must match what is shown.
        for action in (self.act_load_preset, self.act_default_target, *self.act_depths.values()):
            action.setEnabled(not busy)
        current = s.settings.output_subtype
        if current in self.act_depths:
            self.act_depths[current].setChecked(True)

    def _sync_panel_actions(self) -> None:
        view = self.current_view()
        for name, action in self.act_panels.items():
            action.setChecked(view.panels.is_visible(name))

    def _update_render_label(self) -> None:
        mode = "GPU rendering" if self._hardware else "Software rendering"
        self.render_label.setText(f"{mode} · {self.clock.rate:.0f} Hz")

    def _update_fullscreen_action(self) -> None:
        full = self.isFullScreen()
        self.act_fullscreen.setText("Exit Full Screen" if full else "Enter Full Screen")
        self.act_fullscreen.setIcon(theme.icon("ph.arrows-in" if full else "ph.arrows-out", "text_muted"))

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_fullscreen_action()
        super().changeEvent(event)

    # ------------------------------------------------------------------ modes and layout
    def current_view(self):
        return self.stack.currentWidget()

    def set_mode(self, mode: str, save: bool = True) -> None:
        mode = mode if mode in ("simple", "advanced") else "simple"
        self.stack.setCurrentWidget(self.advanced if mode == "advanced" else self.simple)
        self.mode_switch.set_current(mode)
        (self.act_advanced if mode == "advanced" else self.act_simple).setChecked(True)
        if save:
            self.app_settings.mode = mode
        self._sync_panel_actions()

    def _set_panel(self, name: str, visible: bool) -> None:
        self.current_view().set_panel_visible(name, visible)

    def reset_layout(self) -> None:
        self.app_settings.reset_layout()
        self.simple.restore_layout()
        self.advanced.restore_layout()
        self._sync_panel_actions()
        self.statusBar().showMessage("Layout reset.", 4000)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        self._update_fullscreen_action()

    # ------------------------------------------------------------------ window lifecycle
    def show_initial(self) -> None:
        geometry = self.app_settings.window_geometry
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = self.app_settings.window_state
        if state == "fullscreen":
            self.showFullScreen()
        elif state == "normal" and geometry is not None:
            self.showNormal()
        else:
            self.showMaximized()
        self.clock.attach(self)
        self._update_render_label()
        self._update_fullscreen_action()
        if not self.app_settings.onboarding_done:
            QTimer.singleShot(350, self._first_run)

    def _first_run(self) -> None:
        self.show_onboarding()
        self.app_settings.onboarding_done = True

    def closeEvent(self, event) -> None:
        if self.session.busy:
            answer = QMessageBox.question(
                self, "Mastering in progress",
                "Mastering is still running. Stop it and quit? Files that were already saved are kept.")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.session.cancel()
            self.session.runner._pool.waitForDone(10000)
        self.app_settings.window_state = ("fullscreen" if self.isFullScreen()
                                          else "maximized" if self.isMaximized() else "normal")
        self.app_settings.window_geometry = self.saveGeometry()
        self.simple.save_layout()
        self.advanced.save_layout()
        self.engine.release_files()
        self.app_settings.sync()
        event.accept()

    # ------------------------------------------------------------------ drag and drop
    def dragEnterEvent(self, event) -> None:
        if wav_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = wav_paths_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.session.add_paths(paths)

    # ------------------------------------------------------------------ playback and animation
    def _sync_player(self) -> None:
        # While files are being written, the player must not hold any of them open.
        if self.session.writing:
            if self.engine.track is not None:
                self.engine.release_files()
        else:
            self.engine.set_track(self.session.selected())

    def _on_track_changed(self, uid: int) -> None:
        if uid == self.session.selected_uid:
            self._sync_player()

    def _on_playback_state(self, playing: bool) -> None:
        if playing:
            self.clock.request("playback")
        else:
            self.clock.release("playback")
            for view in (self.simple, self.advanced):
                view.panels.on_stopped()
                view.loudness.meter.set_live(None)
            self.advanced.player.update_position()

    def _on_frame(self, dt: float) -> None:
        view = self.current_view()
        view.panels.on_frame(dt)
        view.loudness.meter.set_live(self.engine.momentary_lufs())
        if view is self.advanced:
            self.advanced.player.update_position()

    def _switch_version(self) -> None:
        if isinstance(QApplication.focusWidget(), (QLineEdit, QAbstractSpinBox, QComboBox)):
            return
        focus = QApplication.focusWidget()
        if isinstance(focus, QAbstractItemView) and focus.state() == QAbstractItemView.State.EditingState:
            return
        self.engine.toggle_version()

    def _space(self) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QComboBox)):
            return
        if isinstance(focus, QAbstractItemView) and focus.state() == QAbstractItemView.State.EditingState:
            return
        if isinstance(focus, QAbstractButton):
            focus.click()
            return
        if self.current_view() is self.advanced:
            self.engine.toggle()

    # ------------------------------------------------------------------ files
    def open_files(self) -> None:
        if self.session.busy:
            return
        start = self.app_settings.last_folder or QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MusicLocation)
        paths, _ = QFileDialog.getOpenFileNames(self, "Add WAV Files", start, "WAV audio (*.wav *.WAV);;All files (*)")
        if paths:
            self.app_settings.last_folder = str(Path(paths[0]).parent)
            self.session.add_paths(paths)

    def open_output_folder(self) -> None:
        folders = [d for d in self.session.output_dirs if Path(d).exists()]
        if not folders:
            self.statusBar().showMessage("Nothing has been saved yet. Click Master My Album first.", 6000)
            return
        for folder in folders[:4]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _remove_selected(self) -> None:
        if self.session.selected() is not None:
            self.session.remove(self.session.selected_uid)

    def _clear_album(self) -> None:
        if not self.session.tracks:
            return
        answer = QMessageBox.question(self, "Clear Album",
                                      "Remove every track from this list? Files on your disk are not touched.")
        if answer == QMessageBox.StandardButton.Yes:
            self.engine.release_files()
            self.session.clear()

    def _on_batch_finished(self, dry_run: bool, cancelled: bool, summary: str) -> None:
        self.statusBar().showMessage(summary, 10000)
        self._sync_player()
        self._sync_menus()

    def export_report(self) -> None:
        s = self.session
        if not s.tracks:
            self.statusBar().showMessage("Add tracks and master them before exporting a report.", 6000)
            return
        folder = s.output_dirs[0] if s.output_dirs else (self.app_settings.last_folder or "")
        name = f"{s.settings.album or 'Album'} - mastering report.pdf"
        path, chosen = QFileDialog.getSaveFileName(self, "Export Report", str(Path(folder) / name),
                                                   "PDF document (*.pdf);;CSV spreadsheet (*.csv)")
        if not path:
            return
        as_csv = path.lower().endswith(".csv") or (chosen.startswith("CSV") and not path.lower().endswith(".pdf"))
        if as_csv and not path.lower().endswith(".csv"):
            path += ".csv"
        elif not as_csv and not path.lower().endswith(".pdf"):
            path += ".pdf"
        entries = [ReportEntry(t.track_number, t.title, t.path, t.fmt,
                               t.analysis.measurements if t.analysis else None,
                               t.analysis.clipping if t.analysis else None, t.result, t.error)
                   for t in s.tracks]
        used = s.batch_settings if s.has_results and s.batch_settings else s.settings
        try:
            if as_csv:
                export_csv(path, entries, used)
            else:
                export_pdf(path, entries, used, s.last_summary, stale=s.results_stale)
        except OSError as exc:
            QMessageBox.warning(self, "Export Report", f"The report couldn't be saved: {exc.strerror or exc}.")
            return
        self.statusBar().showMessage(f"Report saved to {path}", 8000)

    # ------------------------------------------------------------------ presets and settings
    def save_preset(self) -> None:
        name = ask_preset_name(self, self.presets)
        if name:
            self.presets.save(name, self.session.settings)
            self.statusBar().showMessage(f"Preset “{name}” saved.", 5000)

    def load_preset(self) -> None:
        dialog = LoadPresetDialog(self.presets, self)
        if dialog.exec() and dialog.selected_name():
            name = dialog.selected_name()
            try:
                values = self.presets.load(name)
            except (OSError, ValueError):
                QMessageBox.warning(self, "Load Preset", f"The preset “{name}” couldn't be read.")
                return
            self.session.apply_preset(values)
            self.statusBar().showMessage(f"Preset “{name}” loaded.", 5000)

    def manage_presets(self) -> None:
        ManagePresetsDialog(self.presets, self).exec()

    def set_hardware_acceleration(self, on: bool) -> None:
        on = bool(on)
        self.app_settings.hardware_acceleration = on
        ok = ThemedPlot.set_hardware_acceleration(on)
        self._hardware = on
        self.act_hardware.blockSignals(True)
        self.act_hardware.setChecked(on)
        self.act_hardware.blockSignals(False)
        self.advanced.settings_panel.set_hardware_checked(on)
        self._update_render_label()
        if not ok:
            QMessageBox.information(self, "Hardware Acceleration",
                                    f"The new drawing mode will be used the next time you open {theme.APP_NAME}.")
        else:
            self.statusBar().showMessage("Hardware acceleration " + ("on." if on else "off. Drawing now uses the processor."), 5000)

    def edit_default_target(self) -> None:
        dialog = DefaultTargetDialog(self.app_settings.default_target_lufs, self)
        if dialog.exec():
            value = dialog.value()
            self.app_settings.default_target_lufs = value
            self.session.update_settings(target_lufs=value)

    def set_output_override(self, subtype: str | None) -> None:
        self.app_settings.output_subtype_override = subtype
        self.session.update_settings(output_subtype=subtype)

    # ------------------------------------------------------------------ help
    def show_onboarding(self) -> None:
        OnboardingDialog(self).exec()

    def show_basics(self) -> None:
        BasicsDialog(self).exec()
