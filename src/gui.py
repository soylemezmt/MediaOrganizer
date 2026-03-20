from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal, Slot, QThread
from PySide6.QtGui import QAction, QPixmap, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
    QSplitter,
    QFileSystemModel,
    QAbstractItemView,
)

from mediaorganizer.file_types import is_supported_media_file
from mediaorganizer.consistency import get_all_date_sources, normalize_date, analyze_date_consistency
from mediaorganizer.metadata_reader import read_metadata_dates_with_exiftool


@dataclass
class MediaRow:
    path: Path
    file_type: str
    metadata_date: str
    filename_date: str
    folder_date: str
    filesystem_date: str
    size_bytes: int
    is_inconsistent: bool


class MediaTableModel(QAbstractTableModel):
    HEADERS = [
        "Name",
        "Type",
        "Metadata",
        "Filename",
        "Folder",
        "Filesystem",
        "Size",
        "Full Path",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[MediaRow] = []

    def set_rows(self, rows: list[MediaRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            values = [
                row.path.name,
                row.file_type,
                row.metadata_date,
                row.filename_date,
                row.folder_date,
                row.filesystem_date,
                f"{row.size_bytes:,}",
                str(row.path),
            ]
            return values[col]

        if role == Qt.BackgroundRole and row.is_inconsistent:
            return QColor("#fff59d")

        if role == Qt.TextAlignmentRole and col == 6:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def get_path(self, row_index: int) -> Optional[Path]:
        if 0 <= row_index < len(self.rows):
            return self.rows[row_index].path
        return None


class FolderScanner(QObject):
    scan_finished = Signal(list)
    scan_failed = Signal(str)

    @Slot(str)
    def scan_folder(self, folder: str) -> None:
        try:
            root = Path(folder)
            media_files: list[Path] = []
            for current_root, _, filenames in os.walk(root):
                for filename in filenames:
                    p = Path(current_root) / filename
                    if is_supported_media_file(p):
                        media_files.append(p)

            metadata_map = read_metadata_dates_with_exiftool(media_files) if media_files else {}

            rows: list[MediaRow] = []
            checked_sources = ["metadata", "filename", "folder", "filesystem"]

            for p in media_files:
                dates = get_all_date_sources(p, metadata_map.get(p))

                is_inconsistent, *_ = analyze_date_consistency(
                    dates=dates,
                    checked_sources=checked_sources,
                    compare_level="month",
                )

                rows.append(
                    MediaRow(
                        path=p,
                        file_type=p.suffix.lower(),
                        metadata_date=_fmt_year_month(dates.get("metadata")),
                        filename_date=_fmt_year_month(dates.get("filename")),
                        folder_date=_fmt_year_month(dates.get("folder")),
                        filesystem_date=_fmt_year_month(dates.get("filesystem")),
                        size_bytes=p.stat().st_size,
                        is_inconsistent=is_inconsistent,
                    )
                )

            self.scan_finished.emit(rows)
        except Exception as exc:
            self.scan_failed.emit(str(exc))


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

        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.file_model)
        self.folder_tree.setRootIndex(self.file_model.index(str(Path.home())))
        self.folder_tree.setColumnHidden(1, True)
        self.folder_tree.setColumnHidden(2, True)
        self.folder_tree.setColumnHidden(3, True)
        self.folder_tree.clicked.connect(self.on_folder_clicked)

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
        left_layout.addWidget(self.folder_tree, 2)
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
        self.folder_tree.setCurrentIndex(self.file_model.index(str(folder)))
        self.scan_folder(folder)
        self.statusBar().showMessage(f"Selected folder: {folder}")

    def refresh_current_folder(self) -> None:
        if self.current_folder is not None:
            self.scan_folder(self.current_folder)
        else:
            QMessageBox.information(self, "Refresh", "Please select a folder first.")

    def on_folder_clicked(self, index: QModelIndex) -> None:
        path = Path(self.file_model.filePath(index))
        if path.is_dir():
            self.set_folder(path)

    def _cleanup_scan_thread(self) -> None:
        if self.scan_thread is not None:
            self.scan_thread.quit()
            self.scan_thread.wait()
            self.scan_thread = None
            self.scanner = None

    def scan_folder(self, folder: Path) -> None:
        self._cleanup_scan_thread()

        self.statusBar().showMessage(f"Scanning {folder} ...")
        self.preview_label.setText("Scanning...")
        self.preview_label.setPixmap(QPixmap())

        self.scan_thread = QThread(self)
        self.scanner = FolderScanner()
        self.scanner.moveToThread(self.scan_thread)

        self.scan_thread.started.connect(lambda: self.scanner.scan_folder(str(folder)))
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

    def on_scan_finished(self, rows):
        self.media_table_model.set_rows(rows)
        self.statusBar().showMessage(f"Loaded {len(rows)} media files.")
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())

    def on_scan_failed(self, msg):
        QMessageBox.critical(self, "Error", msg)
        self.statusBar().showMessage("Scan failed")
        self.preview_label.setText("Preview")

    def on_media_selection_changed(self):
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
            self.preview_label.setPixmap(pixmap.scaled(500, 500, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview_label.setText("")
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview is currently available only for image files.")

    def closeEvent(self, event):
        self._cleanup_scan_thread()
        super().closeEvent(event)


def _fmt_year_month(dt):
    if dt is None:
        return ""
    try:
        norm = normalize_date(dt, "month")
        if norm is None:
            return ""
        return f"{norm[0]:04d}-{norm[1]:02d}"
    except Exception:
        return ""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
