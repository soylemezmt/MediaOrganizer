from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QModelIndex, Qt, QThread, Signal
from PySide6.QtGui import QAction, QFont, QPixmap, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QAbstractItemView,
    QCheckBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .models import MediaTableModel
from .scanner import FolderScanner


class FolderListTable(QTableWidget):
    selection_paths_changed = Signal(list)
    folder_activated = Signal(Path)

    def __init__(self) -> None:
        super().__init__(0, 1)
        self.setHorizontalHeaderLabels(["Folders"])
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.itemSelectionChanged.connect(self._emit_selection_paths)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._row_paths: list[Optional[Path]] = []

    def set_folder_entries(self, current_folder: Path) -> None:
        entries: list[tuple[str, Optional[Path]]] = [(".", current_folder)]
        parent = current_folder.parent if current_folder.parent != current_folder else current_folder
        entries.append(("..", parent))

        subfolders = sorted([p for p in current_folder.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
        for p in subfolders:
            entries.append((p.name, p))

        self.setRowCount(len(entries))
        self._row_paths = []
        for row, (label, path) in enumerate(entries):
            item = QTableWidgetItem(label)
            if label in {".", ".."}:
                item.setForeground(QColor("#1a73e8"))
                item.setFont(QFont("", weight=QFont.Bold))
            self.setItem(row, 0, item)
            self._row_paths.append(path)

        self.clearSelection()

    def selected_folder_paths(self) -> list[Path]:
        paths: list[Path] = []
        for idx in self.selectionModel().selectedRows():
            path = self._row_paths[idx.row()]
            if path is None:
                continue
            label = self.item(idx.row(), 0).text()
            if label == "..":
                continue
            paths.append(path)
        return paths

    def _emit_selection_paths(self) -> None:
        self.selection_paths_changed.emit(self.selected_folder_paths())

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        path = self._row_paths[row]
        if path is not None:
            self.folder_activated.emit(path)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Media Organizer UI")
        self.resize(1500, 900)

        self.current_folder: Optional[Path] = None
        self.scan_thread: Optional[QThread] = None
        self.scanner: Optional[FolderScanner] = None

        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("")

        self.folder_tree = QFileSystemModel()  # placeholder no longer used directly

        self.folder_list = FolderListTable()
        self.folder_list.selection_paths_changed.connect(self.on_folder_selection_changed)
        self.folder_list.folder_activated.connect(self.set_folder)

        self.recursive_checkbox = QCheckBox("Include subfolders")
        self.recursive_checkbox.setChecked(False)
        self.recursive_checkbox.stateChanged.connect(self.refresh_selected_folders)

        self.media_table_model = MediaTableModel()
        self.media_table = QTableView()
        self.media_table.setModel(self.media_table_model)
        self.media_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.media_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.media_table.setAlternatingRowColors(True)
        self.media_table.verticalHeader().setVisible(False)
        self.media_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.media_table.horizontalHeader().setStretchLastSection(True)
        self.media_table.selectionModel().selectionChanged.connect(self.on_media_selection_changed)

        self.preview_label = QLabel("Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(500, 500)
        self.preview_label.setStyleSheet("border: 1px solid #999; background: #fafafa;")

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(self.folder_list, 2)
        left_layout.addWidget(self.recursive_checkbox, 0)
        left_layout.addWidget(self.media_table, 3)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(self.preview_label)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

        self._create_menu()
        self.statusBar().showMessage("Ready")

        # 🎯 Default folder = Pictures
        from PySide6.QtCore import QStandardPaths
        pictures_path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        if pictures_path:
            default_path = Path(pictures_path)
            if default_path.exists():
                self.set_folder(default_path)

    def _create_menu(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        open_folder_action = QAction("Open Folder...", self)
        open_folder_action.triggered.connect(self.choose_folder)
        file_menu.addAction(open_folder_action)

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh_current_folder)
        file_menu.addAction(refresh_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        self.set_folder(Path(folder))

    def set_folder(self, folder: Path) -> None:
        self.current_folder = folder
        self.folder_list.set_folder_entries(folder)

        # 🎯 Auto-select "." row (first row)
        if self.folder_list.rowCount() > 0:
            self.folder_list.selectRow(0)

        self.media_table_model.set_rows([])
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())
        self.statusBar().showMessage(f"Selected folder: {folder}")

    def refresh_current_folder(self) -> None:
        if self.current_folder is not None:
            self.folder_list.set_folder_entries(self.current_folder)
            self.refresh_selected_folders()
        else:
            QMessageBox.information(self, "Refresh", "Please select a folder first.")

    def on_folder_selection_changed(self, selected_paths: list[Path]) -> None:
        self.scan_selected_folders(selected_paths)

    def refresh_selected_folders(self) -> None:
        self.scan_selected_folders(self.folder_list.selected_folder_paths())

    def _cleanup_scan_thread(self) -> None:
        if self.scan_thread is not None:
            self.scan_thread.quit()
            self.scan_thread.wait()
            self.scan_thread = None
            self.scanner = None

    def scan_selected_folders(self, selected_paths: list[Path]) -> None:
        self._cleanup_scan_thread()

        if not selected_paths:
            self.media_table_model.set_rows([])
            self.statusBar().showMessage("No folder selected")
            return

        recursive = self.recursive_checkbox.isChecked()
        folder_text = ", ".join(str(p.name) for p in selected_paths)
        self.statusBar().showMessage(f"Scanning: {folder_text} ...")
        self.preview_label.setText("Scanning...")
        self.preview_label.setPixmap(QPixmap())

        self.scan_thread = QThread(self)
        self.scanner = FolderScanner()
        self.scanner.moveToThread(self.scan_thread)

        self.scan_thread.started.connect(lambda: self.scanner.scan_folders([str(p) for p in selected_paths], recursive))
        self.scanner.scan_finished.connect(self.on_scan_finished)
        self.scanner.scan_failed.connect(self.on_scan_failed)
        self.scanner.scan_finished.connect(self.scan_thread.quit)
        self.scanner.scan_failed.connect(self.scan_thread.quit)
        self.scan_thread.finished.connect(self._on_scan_thread_finished)

        self.scan_thread.start()

    def _on_scan_thread_finished(self) -> None:
        if self.scanner is not None:
            self.scanner.deleteLater()
            self.scanner = None
        if self.scan_thread is not None:
            self.scan_thread.deleteLater()
            self.scan_thread = None

    def on_scan_finished(self, rows) -> None:
        self.media_table_model.set_rows(rows)
        self.statusBar().showMessage(f"Loaded {len(rows)} media files.")
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())

    def on_scan_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)
        self.statusBar().showMessage("Scan failed")
        self.preview_label.setText("Preview")

    def on_media_selection_changed(self) -> None:
        selected = self.media_table.selectionModel().selectedRows()
        if not selected:
            return

        path = self.media_table_model.get_path(selected[0].row())
        if path and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                self.preview_label.setText("Image preview not available")
                self.preview_label.setPixmap(QPixmap())
                return

            self.preview_label.setPixmap(
                pixmap.scaled(500, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.preview_label.setText("")
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview is currently available only for image files.")

    def closeEvent(self, event) -> None:
        self._cleanup_scan_thread()
        super().closeEvent(event)
