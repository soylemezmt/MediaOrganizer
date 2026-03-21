from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QStandardPaths
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .models import MediaTableModel, MediaRow
from .utils import fmt_year_month
from ..consistency import get_all_date_sources, analyze_date_consistency
from .scanner import FolderScanner
from ..metadata_reader import read_metadata_dates_with_exiftool
from ..naming import resolve_destination_path
from PySide6.QtWidgets import QDialog
from .updatePreview import UpdatePreviewDialog
from .folderListTable import FolderListTable



class MainWindow(QMainWindow):
    DEFAULT_PRIORITY = ["folder", "filename", "metadata", "filesystem"]

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Media Organizer UI")
        self.resize(1600, 950)

        self.current_folder: Optional[Path] = None
        self.scan_thread: Optional[QThread] = None
        self.scanner: Optional[FolderScanner] = None
        self.current_info_path: Optional[Path] = None

        self.file_model = QFileSystemModel()
        self.file_model.setRootPath("")
        self.folder_tree = QFileSystemModel()  # placeholder

        self.current_folder_label = QLabel("")
        self.current_folder_label.setStyleSheet("padding: 4px 8px; border-bottom: 1px solid #ccc;")

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
        self.media_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.media_table.setAlternatingRowColors(True)
        self.media_table.verticalHeader().setVisible(False)
        self.media_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.media_table.horizontalHeader().setStretchLastSection(True)
        self.media_table.selectionModel().selectionChanged.connect(self.on_media_selection_changed)

        self.preview_label = QLabel("Preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(500, 420)
        self.preview_label.setStyleSheet("border: 1px solid #999; background: #fafafa;")

        self.metadata_value_label = QLabel("")
        self.filename_value_label = QLabel("")
        self.folder_value_label = QLabel("")
        self.filesystem_value_label = QLabel("")

        self.metadata_check = QCheckBox("Update Metadata")
        self.filename_check = QCheckBox("Update Filename")
        self.folder_check = QCheckBox("Update Folder")
        self.filesystem_check = QCheckBox("Update Filesystem")

        self.year_combo = QComboBox()
        for year in range(1990, 2101):
            self.year_combo.addItem(str(year))

        self.month_combo = QComboBox()
        for month in range(1, 13):
            self.month_combo.addItem(f"{month:02d}")

        self.preview_changes_check = QCheckBox("Preview changes before update")
        self.preview_changes_check.setChecked(True)

        self.selection_count_label = QLabel("You have not selected a file")

        self.update_button = QPushButton("Update")
        self.update_button.clicked.connect(self.update_selected_files)

        self.details_group = QGroupBox("Selected File Details")
        details_layout = QFormLayout(self.details_group)
        details_layout.addRow(self.metadata_check, self.metadata_value_label)
        details_layout.addRow(self.filename_check, self.filename_value_label)
        details_layout.addRow(self.folder_check, self.folder_value_label)
        details_layout.addRow(self.filesystem_check, self.filesystem_value_label)
        details_layout.addRow("Year", self.year_combo)
        details_layout.addRow("Month", self.month_combo)
        details_layout.addRow(self.preview_changes_check)
        details_layout.addRow(self.selection_count_label)
        details_layout.addRow(self.update_button)

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

        # Önce eski satırları kaldır
        self.media_table_model.remove_paths(old_display_paths)

        # Sonra halen görünümde olması gereken yeni dosyaları tekrar ekle
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
        self.metadata_value_label.setText("")
        self.filename_value_label.setText("")
        self.folder_value_label.setText("")
        self.filesystem_value_label.setText("")
        self.selection_count_label.setText("You have not selected a file")
        for cb in [self.metadata_check, self.filename_check, self.folder_check, self.filesystem_check]:
            cb.setChecked(False)

    def estimate_selected_file_count(self, selected_paths: list[Path], recursive: bool) -> int:
        count = 0
        exts = {
            ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif",
            ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm"
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
        # If "." is selected and recursive mode is on, ignore other selected subfolders
        if recursive and self.current_folder is not None:
            if self.current_folder in selected_paths:
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
        self.clear_details_panel()
        if len(rows) > 0:
            self.media_table.selectRow(0)

    def on_scan_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "Error", msg)
        self.statusBar().showMessage("Scan failed")
        self.preview_label.setText("Preview")
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
            return

        self.selection_count_label.setText(f"{count} files are selected.")
        first_path = selected_paths[0]
        self.current_info_path = first_path
        self.show_preview(first_path)
        self.populate_details_panel(first_path)

    def show_preview(self, path: Path) -> None:
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                self.preview_label.setText("Image preview not available")
                self.preview_label.setPixmap(QPixmap())
                return
            self.preview_label.setPixmap(
                pixmap.scaled(500, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self.preview_label.setText("")
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview is currently available only for image files.")

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
        for key in self.DEFAULT_PRIORITY:
            dt = dates.get(key)
            if dt is not None:
                return dt
        return None

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
        target_dt = datetime(year, month, 1, 12, 0, 0)
        plan = []

        for source_path in selected_paths:
            old_dates = self.get_dates_for_path(source_path)

            old_value = (
                f"metadata={self._fmt_year_month(old_dates.get('metadata'))}, "
                f"filename={self._fmt_year_month(old_dates.get('filename'))}, "
                f"folder={self._fmt_year_month(old_dates.get('folder'))}, "
                f"filesystem={self._fmt_year_month(old_dates.get('filesystem'))}"
            )

            new_name = source_path.name
            if do_filename:
                new_name = self.build_updated_filename(source_path.name, target_dt)

            new_folder = str(source_path.parent)
            if do_folder and self.current_folder is not None:
                new_folder = str(self.current_folder / f"{year:04d}" / f"{month:02d}")

            fields = []
            if do_metadata:
                fields.append("metadata")
            if do_filename:
                fields.append("filename")
            if do_folder:
                fields.append("folder")
            if do_filesystem:
                fields.append("filesystem")

            new_value = f"date={year:04d}-{month:02d}, name={new_name}, folder={new_folder}"

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
        target_dt = datetime(year, month, 1, 12, 0, 0)

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

        for source_path in selected_paths:
            try:
                current_path = source_path

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

            except Exception as exc:
                errors.append(f"{source_path.name}: {exc}")

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
            (r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', f"{y}{m}{d}"),
            (r'(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)', f"{y}-{m}-{d}"),
            (r'(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)', f"{y}-{m}-{d}"),
            (r'(?<!\d)(20\d{2})[-_.](\d{2})(?!\d)', f"{y}-{m}"),
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

    def closeEvent(self, event) -> None:
        self._cleanup_scan_thread()
        super().closeEvent(event)

    @staticmethod
    def _fmt_year_month(dt: Optional[datetime]) -> str:
        if dt is None:
            return ""
        return f"{dt.year:04d}-{dt.month:02d}"
