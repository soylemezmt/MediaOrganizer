from __future__ import annotations

import os
import time
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from send2trash import send2trash

from PySide6.QtCore import Qt, QThread, QStandardPaths, QTimer
from PySide6.QtGui import QAction, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .folderListTable import FolderListTable
from .models import MediaRow, MediaTableModel
from .scanner import FolderScanner
from .updatePreview import UpdatePreviewDialog
from .utils import fmt_year_month
from ..consistency import analyze_date_consistency, get_all_date_sources
from ..metadata_reader import read_metadata_dates_with_exiftool
from ..naming import resolve_destination_path


class FileNameEditDelegate(QStyledItemDelegate):
    def __init__(self, owner, parent=None) -> None:
        super().__init__(parent)
        self.owner = owner

    def setModelData(self, editor, model, index) -> None:
        new_name = editor.text().strip()
        self.owner.rename_selected_file_from_editor(index, new_name)


class MainWindow(QMainWindow):
    DEFAULT_PRIORITY = ["filename", "folder", "metadata", "filesystem", "user_defined"]

    VIDEO_EXTENSIONS = {
        ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm",
        ".mpg", ".mpeg"
    }

    IMAGE_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"
    }

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Media Organizer UI")
        self.resize(1600, 950)

        self.current_folder: Optional[Path] = None
        self.scan_thread: Optional[QThread] = None
        self.scanner: Optional[FolderScanner] = None
        self.current_info_path: Optional[Path] = None
        self.current_preview_video_path: Optional[Path] = None
        self.thumbnail_cache: dict[Path, Optional[Path]] = {}
        
        self._last_media_clicked_row: Optional[int] = None
        self._last_media_click_ts: float = 0.0

        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("")
        self.folder_tree = QFileSystemModel()  # placeholder

        self.current_folder_label = QLabel("")
        self.current_folder_label.setStyleSheet("padding: 4px 8px; border-bottom: 1px solid #ccc;")

        self.folder_list = FolderListTable()
        self.folder_list.selection_paths_changed.connect(self.on_folder_selection_changed)
        self.folder_list.folder_activated.connect(self.set_folder)
        self.folder_list.folder_rename_requested.connect(self.rename_folder)

        self.recursive_checkbox = QCheckBox("Include subfolders")
        self.recursive_checkbox.setChecked(False)
        self.recursive_checkbox.stateChanged.connect(self.refresh_selected_folders)

        self.media_table_model = MediaTableModel()
        self.media_table = QTableView()
        self.media_table.setItemDelegateForColumn(0, FileNameEditDelegate(self, self.media_table))
        self.media_table.setModel(self.media_table_model)
        self.media_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.media_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.media_table.setAlternatingRowColors(True)
        self.media_table.verticalHeader().setVisible(False)
        self.media_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.media_table.horizontalHeader().setStretchLastSection(True)
        self.media_table.selectionModel().selectionChanged.connect(self.on_media_selection_changed)
        self.media_table.clicked.connect(self.on_media_table_clicked)
        
        self.delete_shortcut = QShortcut(QKeySequence.Delete, self.media_table)
        self.delete_shortcut.setContext(Qt.WidgetShortcut)
        self.delete_shortcut.activated.connect(self.delete_selected_files)

        self.preview_label = QLabel("Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(500, 420)
        self.preview_label.setStyleSheet("border: 1px solid #999; background: #fafafa;")

        self.play_overlay_button = QPushButton("▶", self.preview_label)
        self.play_overlay_button.setFixedSize(72, 72)
        self.play_overlay_button.hide()
        self.play_overlay_button.clicked.connect(self.open_current_video)
        self.play_overlay_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 140);
                color: white;
                border: 2px solid white;
                border-radius: 36px;
                font-size: 30px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 180);
            }
        """)

        self.metadata_value_label = QLabel("")
        self.filename_value_label = QLabel("")
        self.folder_value_label = QLabel("")
        self.filesystem_value_label = QLabel("")

        self.metadata_check = QCheckBox("Update Metadata")
        self.filename_check = QCheckBox("Update Filename")
        self.folder_check = QCheckBox("Update Folder")
        self.filesystem_check = QCheckBox("Update Filesystem")
        self.filesystem_check.setChecked(True)

        self.year_combo = QComboBox()
        for year in range(1990, 2101):
            self.year_combo.addItem(str(year))
        self.year_combo.setFixedWidth(90)

        self.month_combo = QComboBox()
        for month in range(1, 13):
            self.month_combo.addItem(f"{month:02d}")
        self.month_combo.setFixedWidth(90)

        self.preview_changes_check = QCheckBox("Preview changes before update")
        self.preview_changes_check.setChecked(True)

        self.priority_list = QListWidget()
        self.priority_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.priority_list.setMinimumHeight(120)

        self.priority_up_button = QPushButton("↑")
        self.priority_down_button = QPushButton("↓")
        self.priority_up_button.setFixedWidth(40)
        self.priority_down_button.setFixedWidth(40)
        self.priority_up_button.clicked.connect(lambda: self.move_priority_item(-1))
        self.priority_down_button.clicked.connect(lambda: self.move_priority_item(+1))
        self._load_default_priority()

        self.selection_count_label = QLabel("You have not selected a file")

        self.update_button = QPushButton("Update")
        self.update_button.clicked.connect(self.update_selected_files)

        self.details_group = QGroupBox("Selected File Details")
        details_layout = QVBoxLayout(self.details_group)

        update_group = QGroupBox("Update Targets")
        update_layout = QFormLayout(update_group)
        update_layout.addRow(self.metadata_check, self.metadata_value_label)
        update_layout.addRow(self.filename_check, self.filename_value_label)
        update_layout.addRow(self.folder_check, self.folder_value_label)
        update_layout.addRow(self.filesystem_check, self.filesystem_value_label)

        priority_group = QGroupBox("Date Source Priority")

        priority_buttons_layout = QVBoxLayout()
        priority_buttons_layout.addWidget(self.priority_up_button)
        priority_buttons_layout.addWidget(self.priority_down_button)
        priority_buttons_layout.addStretch(1)

        priority_inner_layout = QHBoxLayout()
        priority_inner_layout.addWidget(self.priority_list, 1)
        priority_inner_layout.addLayout(priority_buttons_layout)

        priority_group.setLayout(priority_inner_layout)

        top_panels_layout = QHBoxLayout()
        top_panels_layout.addWidget(update_group, 1)
        top_panels_layout.addWidget(priority_group, 1)

        bottom_form = QFormLayout()
        bottom_form.addRow("Year", self.year_combo)
        bottom_form.addRow("Month", self.month_combo)
        bottom_form.addRow(self.preview_changes_check)
        bottom_form.addRow(self.selection_count_label)
        bottom_form.addRow(self.update_button)

        details_layout.addLayout(top_panels_layout)
        details_layout.addLayout(bottom_form)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(self.folder_list, 2)
        left_layout.addWidget(self.recursive_checkbox, 0)
        left_layout.addWidget(self.media_table, 3)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(self.preview_label, 3)
        right_layout.addWidget(self.details_group, 2)

        central_widget = QWidget()
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.current_folder_label, 0)

        splitter = QSplitter()
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        central_layout.addWidget(splitter, 1)
        self.setCentralWidget(central_widget)

        self._create_menu()
        self.statusBar().showMessage("Ready")

        pictures_path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        if pictures_path:
            default_path = Path(pictures_path)
            if default_path.exists():
                self.set_folder(default_path)
            else:
                self.set_folder(Path.home())

    def _create_menu(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("File")

        open_folder_action = QAction("Open Folder...", self)
        open_folder_action.triggered.connect(self.choose_folder)
        file_menu.addAction(open_folder_action)

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self.refresh_current_folder)
        file_menu.addAction(refresh_action)

        select_all_action = QAction("Select All Files", self)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self.media_table.selectAll)
        self.addAction(select_all_action)
        file_menu.addAction(select_all_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _load_default_priority(self) -> None:
        self.priority_list.clear()
        for key in self.DEFAULT_PRIORITY:
            item = QListWidgetItem(self._priority_label(key))
            item.setData(Qt.UserRole, key)
            self.priority_list.addItem(item)
        if self.priority_list.count() > 0:
            self.priority_list.setCurrentRow(0)

    def _priority_label(self, key: str) -> str:
        labels = {
            "metadata": "Metadata",
            "filename": "Filename",
            "folder": "Folder",
            "filesystem": "Filesystem",
            "user_defined": "User Defined",
        }
        return labels.get(key, key)

    def delete_selected_files(self) -> None:
        selected_paths = self.selected_file_paths()
        if not selected_paths:
            return

        selected_rows = sorted(idx.row() for idx in self.media_table.selectionModel().selectedRows())
        preferred_row = selected_rows[0] if selected_rows else 0

        if len(selected_paths) > 1:
            reply = QMessageBox.question(
                self,
                "Delete Files",
                f"Send {len(selected_paths)} selected files to Recycle Bin?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        errors: list[str] = []
        deleted_paths: list[Path] = []

        for path in selected_paths:
            try:
                send2trash(str(path))
                deleted_paths.append(path)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        if deleted_paths:
            if len(deleted_paths) <= 50:
                self.incremental_refresh_files(deleted_paths, [])
            else:
                self.refresh_selected_folders()

            self.clear_details_panel()
            self.preview_label.setText("Preview")
            self.preview_label.setPixmap(QPixmap())

            row_count = self.media_table_model.rowCount()
            if row_count > 0:
                row_to_select = preferred_row if preferred_row < row_count else 0
                self.media_table.selectRow(row_to_select)

        if errors:
            QMessageBox.warning(
                self,
                "Delete completed with errors",
                "\n".join(errors[:20]),
            )
        elif deleted_paths:
            self.statusBar().showMessage(f"{len(deleted_paths)} file(s) sent to Recycle Bin.")


    def move_priority_item(self, delta: int) -> None:
        row = self.priority_list.currentRow()
        if row < 0:
            return

        new_row = row + delta
        if new_row < 0 or new_row >= self.priority_list.count():
            return

        item = self.priority_list.takeItem(row)
        self.priority_list.insertItem(new_row, item)
        self.priority_list.setCurrentRow(new_row)

    def get_priority_order(self) -> list[str]:
        order: list[str] = []
        for i in range(self.priority_list.count()):
            item = self.priority_list.item(i)
            order.append(item.data(Qt.UserRole))
        return order

    def get_user_defined_date(self) -> datetime:
        year = int(self.year_combo.currentText())
        month = int(self.month_combo.currentText())
        return datetime(year, month, 1, 12, 0, 0)

    def choose_date_by_priority(self, dates: dict) -> tuple[Optional[datetime], Optional[str]]:
        candidates = dict(dates)
        candidates["user_defined"] = self.get_user_defined_date()

        for key in self.get_priority_order():
            dt = candidates.get(key)
            if dt is not None:
                return dt, key
        return None, None

    def _to_display_path(self, path: Path) -> Path:
        if self.current_folder is not None:
            try:
                return path.relative_to(self.current_folder)
            except Exception:
                pass
        return path

    def _is_path_in_current_view(self, path: Path) -> bool:
        selected_paths = self.folder_list.selected_folder_paths()
        if not selected_paths:
            return False

        recursive = self.recursive_checkbox.isChecked()

        if recursive and self.current_folder is not None and self.current_folder in selected_paths:
            selected_paths = [self.current_folder]

        try:
            path_resolved = path.resolve()
        except Exception:
            path_resolved = path

        for folder in selected_paths:
            try:
                folder_resolved = folder.resolve()
            except Exception:
                folder_resolved = folder

            if recursive:
                try:
                    path_resolved.relative_to(folder_resolved)
                    return True
                except Exception:
                    pass
            else:
                if path_resolved.parent == folder_resolved:
                    return True

        return False

    def _build_media_row_for_path(self, path: Path) -> Optional[MediaRow]:
        if not path.exists() or not path.is_file():
            return None

        metadata_map = read_metadata_dates_with_exiftool([path])
        dates = get_all_date_sources(path, metadata_map.get(path))
        is_inconsistent, *_ = analyze_date_consistency(
            dates=dates,
            checked_sources=["metadata", "filename", "folder", "filesystem"],
            compare_level="month",
        )

        display_path = self._to_display_path(path)

        return MediaRow(
            path=display_path,
            file_type=path.suffix.lower(),
            metadata_date=fmt_year_month(dates.get("metadata")),
            filename_date=fmt_year_month(dates.get("filename")),
            folder_date=fmt_year_month(dates.get("folder")),
            filesystem_date=fmt_year_month(dates.get("filesystem")),
            size_bytes=path.stat().st_size,
            is_inconsistent=is_inconsistent,
        )

    def incremental_refresh_files(self, old_paths: list[Path], new_paths: list[Path]) -> None:
        old_display_paths = [self._to_display_path(p) for p in old_paths]
        self.media_table_model.remove_paths(old_display_paths)

        for path in new_paths:
            if not self._is_path_in_current_view(path):
                continue

            row = self._build_media_row_for_path(path)
            if row is None:
                continue

            existing_index = self.media_table_model.find_row_by_path(row.path)
            if existing_index >= 0:
                self.media_table_model.update_row(existing_index, row)
            else:
                self.media_table_model.insert_row_sorted(row)

        self.statusBar().showMessage(f"Updated {len(new_paths)} file(s) incrementally.")

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        self.set_folder(Path(folder))

    def set_folder(self, folder: Path) -> None:
        self.current_folder = folder
        self.thumbnail_cache.clear()
        self.current_preview_video_path = None
        self.show_play_overlay(False)

        self.current_folder_label.setText(str(folder))
        self.folder_list.set_folder_entries(folder)
        if self.folder_list.rowCount() > 0:
            self.folder_list.selectRow(0)
        self.media_table_model.set_rows([])
        self.clear_details_panel()
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())
        self.statusBar().showMessage(f"Selected folder: {folder}")

    def clear_details_panel(self) -> None:
        self.current_info_path = None
        self.current_preview_video_path = None
        self.show_play_overlay(False)

        self.metadata_value_label.setText("")
        self.filename_value_label.setText("")
        self.folder_value_label.setText("")
        self.filesystem_value_label.setText("")
        self.selection_count_label.setText("You have not selected a file")
        self.metadata_check.setChecked(False)
        self.filename_check.setChecked(False)
        self.folder_check.setChecked(False)
        self.filesystem_check.setChecked(True)

    def estimate_selected_file_count(self, selected_paths: list[Path], recursive: bool) -> int:
        count = 0
        exts = {
            ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif",
            ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm",
            ".mpg", ".mpeg"
        }

        for folder in selected_paths:
            if recursive:
                for root, _, filenames in os.walk(folder):
                    for name in filenames:
                        if Path(name).suffix.lower() in exts:
                            count += 1
            else:
                for p in folder.iterdir():
                    if p.is_file() and p.suffix.lower() in exts:
                        count += 1
        return count

    def refresh_current_folder(self) -> None:
        if self.current_folder is not None:
            self.folder_list.set_folder_entries(self.current_folder)
            if self.folder_list.rowCount() > 0:
                self.folder_list.selectRow(0)
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

    def on_scan_progress(self, current: int, total: int) -> None:
        if total > 0:
            self.statusBar().showMessage(f"Scanning... {current}/{total}")
        else:
            self.statusBar().showMessage(f"Scanning... {current}")

    def scan_selected_folders(self, selected_paths: list[Path]) -> None:
        self._cleanup_scan_thread()

        if not selected_paths:
            self.media_table_model.set_rows([])
            self.clear_details_panel()
            self.statusBar().showMessage("No folder selected")
            return

        recursive = self.recursive_checkbox.isChecked()
        if recursive and self.current_folder is not None and self.current_folder in selected_paths:
            selected_paths = [self.current_folder]

        try:
            estimated_count = self.estimate_selected_file_count(selected_paths, recursive)
        except Exception:
            estimated_count = -1

        scan_limit = None
        if estimated_count > 100:
            reply = QMessageBox.question(
                self,
                "Large file list",
                (
                    f"About {estimated_count} files will be listed.\n\n"
                    "Do you want to continue and list all of them?\n"
                    "If you choose No, only the first 100 files will be listed."
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                scan_limit = 100

        folder_text = ", ".join(str(p.name) for p in selected_paths)
        self.statusBar().showMessage(f"Scanning: {folder_text} ...")
        self.preview_label.setText("Scanning...")
        self.preview_label.setPixmap(QPixmap())
        self.show_play_overlay(False)
        self.current_preview_video_path = None

        self.scan_thread = QThread(self)
        scanner = FolderScanner()
        self.scanner = scanner
        scanner.moveToThread(self.scan_thread)
        scanner.progress_changed.connect(self.on_scan_progress)

        paths_str = [str(p) for p in selected_paths]
        self.scan_thread.started.connect(
            lambda scanner=scanner, paths=paths_str, recursive=recursive, scan_limit=scan_limit:
                scanner.scan_folders(paths, recursive, scan_limit)
        )

        scanner.scan_finished.connect(self.on_scan_finished)
        scanner.scan_failed.connect(self.on_scan_failed)
        scanner.scan_finished.connect(self.scan_thread.quit)
        scanner.scan_failed.connect(self.scan_thread.quit)
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
        if self.current_folder is not None:
            for row in rows:
                try:
                    rel = row.path.relative_to(self.current_folder)
                    row.path = rel
                except Exception:
                    pass
        self.media_table_model.set_rows(rows)
        self.statusBar().showMessage(f"Loaded {len(rows)} media files.")
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())
        self.show_play_overlay(False)
        self.current_preview_video_path = None
        self.clear_details_panel()
        if len(rows) > 0:
            self.media_table.selectRow(0)

    def on_scan_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)
        self.statusBar().showMessage("Scan failed")
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())
        self.show_play_overlay(False)
        self.current_preview_video_path = None
        self.clear_details_panel()

    def selected_file_paths(self) -> list[Path]:
        rows = self.media_table.selectionModel().selectedRows()
        paths: list[Path] = []
        for row in rows:
            rel_path = self.media_table_model.get_path(row.row())
            if rel_path is not None:
                if self.current_folder is not None and not rel_path.is_absolute():
                    paths.append(self.current_folder / rel_path)
                else:
                    paths.append(rel_path)
        return paths

    def on_media_selection_changed(self) -> None:
        selected_paths = self.selected_file_paths()
        count = len(selected_paths)
        if count == 0:
            self.clear_details_panel()
            self.preview_label.setText("Preview")
            self.preview_label.setPixmap(QPixmap())
            return

        self.selection_count_label.setText(f"{count} files are selected.")
        first_path = selected_paths[0]
        self.current_info_path = first_path
        self.show_preview(first_path)
        self.populate_details_panel(first_path)

    def on_media_table_clicked(self, index) -> None:
        if not index.isValid() or index.column() != 0:
            return

        modifiers = QApplication.keyboardModifiers()
        row = index.row()
        now = time.monotonic()

        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            self._last_media_clicked_row = row
            self._last_media_click_ts = now
            return

        same_row = (self._last_media_clicked_row == row)
        delta = now - self._last_media_click_ts

        if same_row and 0.4 <= delta <= 1.5:
            if self.media_table.selectionModel().isRowSelected(row, self.media_table.rootIndex()):
                if self.media_table.state() != QAbstractItemView.EditingState:
                    self.media_table.edit(index)

        self._last_media_clicked_row = row
        self._last_media_click_ts = now
        

    def rename_folder(self, old_path: Path, new_name: str) -> None:
        new_name = new_name.strip()
        if not new_name:
            self.folder_list.revert_row_text(old_path)
            return

        if new_name in {".", ".."}:
            self.folder_list.revert_row_text(old_path)
            return

        try:
            target_path = old_path.with_name(new_name)
            if target_path.exists():
                raise FileExistsError(f"Folder already exists: {target_path.name}")

            new_path = old_path.rename(target_path)

            if self.current_folder is not None:
                self.folder_list.set_folder_entries(self.current_folder)

            for row in range(self.folder_list.rowCount()):
                item = self.folder_list.item(row, 0)
                if item is not None and item.text() == new_path.name:
                    self.folder_list.selectRow(row)
                    break

            self.statusBar().showMessage(f"Folder renamed to: {new_path.name}")
        except Exception as exc:
            self.folder_list.revert_row_text(old_path)
            QMessageBox.warning(self, "Rename Folder", f"Could not rename folder:\n{exc}")

    def rename_selected_file_from_editor(self, index, new_name: str) -> None:
        if not index.isValid():
            return

        row = index.row()
        rel_path = self.media_table_model.get_path(row)
        if rel_path is None:
            return

        old_path = self.current_folder / rel_path if self.current_folder is not None and not rel_path.is_absolute() else rel_path
        new_name = new_name.strip()

        if not new_name or new_name == old_path.name:
            self.refresh_selected_folders()
            return

        try:
            target_path = old_path.with_name(new_name)
            if target_path.exists():
                raise FileExistsError(f"File already exists: {target_path.name}")

            new_path = old_path.rename(target_path)

            if len(self.selected_file_paths()) <= 50:
                self.incremental_refresh_files([old_path], [new_path])
            else:
                self.refresh_selected_folders()

            self.statusBar().showMessage(f"File renamed to: {new_path.name}")
        except Exception as exc:
            QMessageBox.warning(self, "Rename File", f"Could not rename file:\n{exc}")
            self.refresh_selected_folders()

    def position_play_overlay(self) -> None:
        btn = self.play_overlay_button
        parent = self.preview_label
        x = (parent.width() - btn.width()) // 2
        y = (parent.height() - btn.height()) // 2
        btn.move(max(0, x), max(0, y))

    def show_play_overlay(self, visible: bool) -> None:
        if visible:
            self.position_play_overlay()
            self.play_overlay_button.show()
            self.play_overlay_button.raise_()
        else:
            self.play_overlay_button.hide()

    def open_current_video(self) -> None:
        if self.current_preview_video_path is None:
            return

        if not self.current_preview_video_path.exists():
            QMessageBox.warning(self, "Video", "Video file not found.")
            return

        try:
            os.startfile(str(self.current_preview_video_path))
        except Exception as exc:
            QMessageBox.warning(self, "Video", f"Could not open video: {exc}")

    def extract_video_thumbnail(self, path: Path) -> Optional[Path]:
        cached = self.thumbnail_cache.get(path)
        if cached is not None and cached.exists():
            return cached

        thumb = self._extract_video_thumbnail_with_exiftool(path)
        if thumb is None:
            thumb = self._extract_video_frame_with_ffmpeg(path)

        self.thumbnail_cache[path] = thumb
        return thumb

    def _extract_video_thumbnail_with_exiftool(self, path: Path) -> Optional[Path]:
        try:
            safe_name = f"media_thumb_{abs(hash(str(path)))}.jpg"
            thumb_path = Path(tempfile.gettempdir()) / safe_name

            cmd = [
                "exiftool",
                "-b",
                "-ThumbnailImage",
                str(path),
            ]

            with open(thumb_path, "wb") as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)

            if result.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
                return thumb_path
        except Exception:
            pass

        return None

    def _extract_video_frame_with_ffmpeg(self, path: Path) -> Optional[Path]:
        try:
            safe_name = f"media_frame_{abs(hash(str(path)))}.jpg"
            thumb_path = Path(tempfile.gettempdir()) / safe_name

            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                "00:00:01",
                "-i",
                str(path),
                "-vframes",
                "1",
                str(thumb_path),
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if result.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
                return thumb_path
        except Exception:
            pass

        return None

    def show_preview(self, path: Path) -> None:
        ext = path.suffix.lower()
        self.current_preview_video_path = None
        self.show_play_overlay(False)

        if ext in self.IMAGE_EXTENSIONS:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                self.preview_label.setText("Image preview not available")
                self.preview_label.setPixmap(QPixmap())
                return

            self.preview_label.setPixmap(
                pixmap.scaled(500, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.preview_label.setText("")
            return

        if ext in self.VIDEO_EXTENSIONS:
            thumb_path = self.extract_video_thumbnail(path)

            if thumb_path is not None:
                pixmap = QPixmap(str(thumb_path))
                if not pixmap.isNull():
                    self.preview_label.setPixmap(
                        pixmap.scaled(500, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    )
                    self.preview_label.setText("")
                    self.current_preview_video_path = path
                    self.show_play_overlay(True)
                    return

            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Video preview not available")
            self.current_preview_video_path = path
            self.show_play_overlay(True)
            return

        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Preview is currently available only for image and video files.")

    def populate_details_panel(self, path: Path) -> None:
        dates = self.get_dates_for_path(path)
        self.metadata_value_label.setText(self._fmt_year_month(dates.get("metadata")))
        self.filename_value_label.setText(self._fmt_year_month(dates.get("filename")))
        self.folder_value_label.setText(self._fmt_year_month(dates.get("folder")))
        self.filesystem_value_label.setText(self._fmt_year_month(dates.get("filesystem")))

        default_dt = self.choose_default_date(dates)
        if default_dt is not None:
            self.year_combo.setCurrentText(f"{default_dt.year:04d}")
            self.month_combo.setCurrentText(f"{default_dt.month:02d}")

    def get_dates_for_path(self, path: Path) -> dict:
        metadata_map = read_metadata_dates_with_exiftool([path])
        return get_all_date_sources(path, metadata_map.get(path))

    def choose_default_date(self, dates: dict) -> Optional[datetime]:
        dt, _ = self.choose_date_by_priority(dates)
        return dt

    def build_update_plan(
        self,
        selected_paths: list[Path],
        year: int,
        month: int,
        do_metadata: bool,
        do_filename: bool,
        do_folder: bool,
        do_filesystem: bool,
    ) -> list[dict]:
        plan = []

        for source_path in selected_paths:
            old_dates = self.get_dates_for_path(source_path)
            target_dt, source_key = self.choose_date_by_priority(old_dates)

            if target_dt is None:
                continue

            old_value = (
                f"metadata={self._fmt_year_month(old_dates.get('metadata'))}, "
                f"filename={self._fmt_year_month(old_dates.get('filename'))}, "
                f"folder={self._fmt_year_month(old_dates.get('folder'))}, "
                f"filesystem={self._fmt_year_month(old_dates.get('filesystem'))}, "
                f"user_defined={self._fmt_year_month(self.get_user_defined_date())}"
            )

            new_name = source_path.name
            if do_filename:
                new_name = self.build_updated_filename(source_path.name, target_dt)

            new_folder = str(source_path.parent)
            if do_folder and self.current_folder is not None:
                new_folder = str(self.current_folder / f"{target_dt.year:04d}" / f"{target_dt.month:02d}")

            fields = []
            if do_metadata:
                fields.append("metadata")
            if do_filename:
                fields.append("filename")
            if do_folder:
                fields.append("folder")
            if do_filesystem:
                fields.append("filesystem")

            new_value = (
                f"date={target_dt.year:04d}-{target_dt.month:02d}, "
                f"source={self._priority_label(source_key)}, "
                f"name={new_name}, folder={new_folder}"
            )

            plan.append(
                {
                    "file": str(source_path),
                    "old": old_value,
                    "new": new_value,
                    "fields": ", ".join(fields),
                }
            )

        return plan

    def update_selected_files(self) -> None:
        selected_paths = self.selected_file_paths()
        if not selected_paths:
            QMessageBox.information(self, "Update", "You have not selected a file")
            return

        year = int(self.year_combo.currentText())
        month = int(self.month_combo.currentText())

        do_metadata = self.metadata_check.isChecked()
        do_filename = self.filename_check.isChecked()
        do_folder = self.folder_check.isChecked()
        do_filesystem = self.filesystem_check.isChecked()

        if not any([do_metadata, do_filename, do_folder, do_filesystem]):
            QMessageBox.information(self, "Update", "Please check at least one field to update.")
            return

        if self.preview_changes_check.isChecked():
            plan = self.build_update_plan(
                selected_paths, year, month, do_metadata, do_filename, do_folder, do_filesystem
            )
            dialog = UpdatePreviewDialog(plan, self)
            if dialog.exec() != QDialog.Accepted:
                return

        errors: list[str] = []
        old_paths: list[Path] = []
        new_paths: list[Path] = []

        for source_path in selected_paths:
            old_paths.append(source_path)
            try:
                current_path = source_path
                dates = self.get_dates_for_path(source_path)
                target_dt, _source_key = self.choose_date_by_priority(dates)

                if target_dt is None:
                    errors.append(f"{source_path.name}: no usable date found")
                    continue

                if do_filename:
                    new_name = self.build_updated_filename(current_path.name, target_dt)
                    if new_name != current_path.name:
                        dest_path, _ = resolve_destination_path(
                            current_path.parent,
                            new_name,
                            current_path.stat().st_size,
                        )
                        if dest_path is not None and dest_path != current_path:
                            current_path = current_path.rename(dest_path)

                if do_folder and self.current_folder is not None:
                    target_folder = self.current_folder / f"{target_dt.year:04d}" / f"{target_dt.month:02d}"
                    target_folder.mkdir(parents=True, exist_ok=True)
                    dest_path, _ = resolve_destination_path(
                        target_folder,
                        current_path.name,
                        current_path.stat().st_size,
                    )
                    if dest_path is not None and dest_path != current_path:
                        current_path = Path(shutil.move(str(current_path), str(dest_path)))

                if do_metadata:
                    self.write_metadata(current_path, target_dt)

                if do_filesystem:
                    self.write_filesystem_time(current_path, target_dt)

                new_paths.append(current_path)

            except Exception as exc:
                errors.append(f"{source_path.name}: {exc}")

        if len(old_paths) <= 50:
            self.incremental_refresh_files(old_paths, new_paths)
        else:
            self.refresh_selected_folders()

        if errors:
            QMessageBox.warning(self, "Update completed with errors", "\n".join(errors[:20]))
        else:
            QMessageBox.information(self, "Update", "Selected files updated successfully.")

    def build_updated_filename(self, original_name: str, target_dt: datetime) -> str:
        import re

        p = Path(original_name)
        stem = p.stem
        ext = p.suffix
        y = f"{target_dt.year:04d}"
        m = f"{target_dt.month:02d}"
        d = "01"

        patterns = [
            (r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", f"{y}{m}{d}"),
            (r"(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)", f"{y}-{m}-{d}"),
            (r"(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)", f"{y}-{m}-{d}"),
            (r"(?<!\d)(20\d{2})[-_.](\d{2})(?!\d)", f"{y}-{m}"),
        ]

        for pattern, replacement in patterns:
            new_stem, count = re.subn(pattern, replacement, stem, count=1)
            if count > 0:
                return new_stem + ext
        return original_name

    def write_metadata(self, path: Path, dt: datetime) -> None:
        dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")
        cmd = [
            "exiftool",
            "-overwrite_original",
            f"-DateTimeOriginal={dt_str}",
            f"-CreateDate={dt_str}",
            f"-ModifyDate={dt_str}",
            f"-MediaCreateDate={dt_str}",
            f"-TrackCreateDate={dt_str}",
            str(path),
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Failed to update metadata")

    def write_filesystem_time(self, path: Path, dt: datetime) -> None:
        ts = dt.timestamp()
        os.utime(path, (ts, ts))

        if sys.platform.startswith("win"):
            import ctypes
            import ctypes.wintypes as wintypes

            FILE_WRITE_ATTRIBUTES = 0x0100
            OPEN_EXISTING = 3

            handle = ctypes.windll.kernel32.CreateFileW(
                str(path),
                FILE_WRITE_ATTRIBUTES,
                0,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
            if handle == -1 or handle == 0:
                raise RuntimeError("CreateFileW failed while updating creation time")

            epoch = datetime(1601, 1, 1)
            delta = dt - epoch
            filetime = int(delta.total_seconds() * 10**7)

            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

            ft = FILETIME(filetime & 0xFFFFFFFF, filetime >> 32)
            ok = ctypes.windll.kernel32.SetFileTime(
                handle,
                ctypes.byref(ft),
                ctypes.byref(ft),
                ctypes.byref(ft),
            )
            ctypes.windll.kernel32.CloseHandle(handle)
            if not ok:
                raise RuntimeError("SetFileTime failed while updating creation time")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.position_play_overlay()

    def closeEvent(self, event) -> None:
        self._cleanup_scan_thread()
        super().closeEvent(event)

    @staticmethod
    def _fmt_year_month(dt: Optional[datetime]) -> str:
        if dt is None:
            return ""
        return f"{dt.year:04d}-{dt.month:02d}"