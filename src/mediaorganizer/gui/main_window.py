from pathlib import Path
from typing import Optional

from PySide6.QtCore import QModelIndex, Qt, QThread
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .models import MediaTableModel
from .scanner import FolderScanner


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