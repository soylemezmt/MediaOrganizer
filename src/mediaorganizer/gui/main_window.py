from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import Image

from PySide6.QtCore import Qt, QThread, QStandardPaths, Signal, QProcess, QTimer
from PySide6.QtGui import QAction, QPixmap, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
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
    QLineEdit,
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

from send2trash import send2trash

from .folderListTable import FolderListTable
from .models import MediaRow, MediaTableModel
from .scanner import FolderScanner
from .updatePreview import UpdatePreviewDialog
from .utils import fmt_year_month, check_internet_connection
from ..consistency import analyze_date_consistency, get_all_date_sources
from ..metadata_reader import (
    read_metadata_dates_with_exiftool,
    read_location_fields_with_exiftool,
    read_exiftool_date_fields,
)
from ..naming import resolve_destination_path
from .options_dialog import OptionsDialog
from mediaorganizer.location_utils import infer_country_city_from_gps
from .filter import MediaFilterProxyModel, HeaderFilterPopup, FilterHeaderView
from ..exiftool_utils import exiftool_run, exiftool_run_with_files, exiftool_base_cmd
from mediaorganizer.config import SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS
from .duplicate_options_dialog import DuplicateOptionsDialog
from ..duplicates import (
    duplicate_scope_applies,
    resolve_duplicate_for_destination,
)
from .settings import UiOptions

class FileNameEditDelegate(QStyledItemDelegate):
    def __init__(self, owner, parent=None) -> None:
        super().__init__(parent)
        self.owner = owner

    def setModelData(self, editor, model, index) -> None:
        new_name = editor.text().strip()
        self.owner.rename_selected_file_from_editor(index, new_name)


class MainWindow(QMainWindow):
    request_scan = Signal(list, bool, object, object)
    DEFAULT_PRIORITY = ["filename", "folder", "metadata", "filesystem", "user_defined"]

    IMAGE_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS
    VIDEO_EXTENSIONS = SUPPORTED_VIDEO_EXTENSIONS

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Media Organizer UI")
        self.resize(1600, 950)
        self.internet_available = False

        self.all_media_rows = []
        
        self.rotate_process: Optional[QProcess] = None
        self.rotate_target_path: Optional[Path] = None
        self.rotate_temp_path: Optional[Path] = None
        self.rotate_backup_path: Optional[Path] = None
        self.rotate_saved_info: Optional[dict] = None
        self.rotate_duration_ms: Optional[int] = None
        self.rotate_stderr_buffer = ""
        
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
        
        self.show_conflicts_only_checkbox = QCheckBox("Show only conflicting rows")
        self.show_conflicts_only_checkbox.setChecked(False)
        self.show_conflicts_only_checkbox.toggled.connect(self.apply_row_filters)

        self.media_table_model = MediaTableModel()

        self.media_proxy_model = MediaFilterProxyModel(self)
        self.media_proxy_model.setSourceModel(self.media_table_model)

        self.media_table = QTableView()
        self.media_table.setItemDelegateForColumn(0, FileNameEditDelegate(self, self.media_table))
        self.media_table.setModel(self.media_proxy_model)

        self.filter_header = FilterHeaderView(Qt.Horizontal, self.media_table)
        self.media_table.setHorizontalHeader(self.filter_header)
        self.filter_header.filter_changed.connect(self.on_column_filter_changed)
        
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
        
        self.info_panel_visible = True
        
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel("File Information")

        self.info_close_button = QPushButton("✕")
        self.info_close_button.setFixedSize(20, 20)
        self.info_close_button.setStyleSheet("""
            QPushButton {
                border: none;
                font-weight: bold;
            }
            QPushButton:hover {
                color: red;
            }
        """)
        self.info_close_button.setText("✖")
        self.info_close_button.clicked.connect(self._close_info_panel)

        header_layout.addWidget(title_label)
        header_layout.addStretch()
        header_layout.addWidget(self.info_close_button)

        self.info_group = QGroupBox()
        self.info_group.setMinimumWidth(320)

        self.info_form = QFormLayout()

        self.info_name_label = QLabel("")
        self.info_name_label.setWordWrap(True)

        self.info_full_path_label = QLabel("")
        self.info_full_path_label.setWordWrap(True)

        self.info_size_label = QLabel("")
        self.info_type_label = QLabel("")

        self.info_metadata_date_label = QLabel("")
        self.info_filename_date_label = QLabel("")
        self.info_folder_date_label = QLabel("")
        self.info_filesystem_date_label = QLabel("")
        
        self.info_exif_datetimeoriginal_label = QLabel("")
        self.info_exif_createdate_label = QLabel("")
        self.info_exif_mediacreatedate_label = QLabel("")
        self.info_exif_trackcreatedate_label = QLabel("")
        self.info_exif_creationdate_label = QLabel("")
        self.info_exif_modifydate_label = QLabel("")
        self.info_exif_filemodifydate_label = QLabel("")        

        self.info_country_label = QLabel("")
        self.info_city_label = QLabel("")
        self.info_latitude_label = QLabel("")
        self.info_longitude_label = QLabel("")

        self.info_created_label = QLabel("")
        self.info_modified_label = QLabel("")
        self.info_accessed_label = QLabel("")

        self.info_form.addRow("Name", self.info_name_label)
        self.info_form.addRow("Full path", self.info_full_path_label)
        self.info_form.addRow("Type", self.info_type_label)
        self.info_form.addRow("Size", self.info_size_label)
        self.info_form.addRow("Metadata date", self.info_metadata_date_label)
        self.info_form.addRow("Filename date", self.info_filename_date_label)
        self.info_form.addRow("DateTimeOriginal", self.info_exif_datetimeoriginal_label)
        self.info_form.addRow("CreateDate", self.info_exif_createdate_label)
        self.info_form.addRow("MediaCreateDate", self.info_exif_mediacreatedate_label)
        self.info_form.addRow("TrackCreateDate", self.info_exif_trackcreatedate_label)
        self.info_form.addRow("CreationDate", self.info_exif_creationdate_label)
        self.info_form.addRow("ModifyDate", self.info_exif_modifydate_label)
        self.info_form.addRow("FileModifyDate", self.info_exif_filemodifydate_label)        
        self.info_form.addRow("Folder date", self.info_folder_date_label)
        self.info_form.addRow("Filesystem date", self.info_filesystem_date_label)
        self.info_form.addRow("Country", self.info_country_label)
        self.info_form.addRow("City", self.info_city_label)
        self.info_form.addRow("Latitude", self.info_latitude_label)
        self.info_form.addRow("Longitude", self.info_longitude_label)
        self.info_form.addRow("Created", self.info_created_label)
        self.info_form.addRow("Modified", self.info_modified_label)
        self.info_form.addRow("Accessed", self.info_accessed_label)
        
        info_layout = QVBoxLayout(self.info_group)
        info_layout.addLayout(header_layout)
        info_layout.addLayout(self.info_form)

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
        
        self.rotate_ccw_button = QPushButton("↶", self.preview_label)
        self.rotate_ccw_button.setToolTip("Rotate 90° counterclockwise")
        self.rotate_ccw_button.setFixedSize(40, 34)
        self.rotate_ccw_button.clicked.connect(lambda: self.rotate_selected_media(-90))

        self.rotate_cw_button = QPushButton("↷", self.preview_label)
        self.rotate_cw_button.setToolTip("Rotate 90° clockwise")
        self.rotate_cw_button.setFixedSize(40, 34)
        self.rotate_cw_button.clicked.connect(lambda: self.rotate_selected_media(+90))
        
        style = """
        QPushButton {
            background-color: rgba(0, 0, 0, 120);
            color: white;
            border: 1px solid white;
            border-radius: 8px;
            font-size: 16px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: rgba(0, 0, 0, 170);
        }
        """

        self.rotate_ccw_button.setStyleSheet(style)
        self.rotate_cw_button.setStyleSheet(style)

        self.info_toggle_button = QPushButton("ℹ", self.preview_label)
        self.info_toggle_button.setFixedSize(34, 34)
        self.info_toggle_button.setToolTip("Show File Information")
        self.info_toggle_button.clicked.connect(self._open_info_panel)
        self.info_toggle_button.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 120);
                color: white;
                border: 1px solid white;
                border-radius: 17px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 170);
            }
        """)
        self.info_toggle_button.hide()
        
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

        pictures_path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        default_target_folder = pictures_path if pictures_path else str(Path.home())

        self.target_folder_check = QCheckBox("Target folder")
        self.target_folder_check.setChecked(False)
        self.target_folder_check.toggled.connect(self.on_target_folder_mode_changed)

        self.target_folder_edit = QLineEdit(default_target_folder)
        self.target_folder_edit.setEnabled(False)

        self.target_folder_button = QPushButton("...")
        self.target_folder_button.setFixedWidth(36)
        self.target_folder_button.setEnabled(False)
        self.target_folder_button.clicked.connect(self.choose_target_folder)

        self.preview_changes_check = QCheckBox("Preview changes before update")
        self.preview_changes_check.setChecked(False)

        self.priority_list = QListWidget()
        self.priority_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.priority_list.setMinimumHeight(120)
        self.priority_list.setMinimumWidth(130)

        self.priority_up_button = QPushButton("↑")
        self.priority_down_button = QPushButton("↓")
        self.priority_up_button.setFixedWidth(40)
        self.priority_down_button.setFixedWidth(40)
        self.priority_up_button.clicked.connect(lambda: self.move_priority_item(-1))
        self.priority_down_button.clicked.connect(lambda: self.move_priority_item(+1))
        self._load_default_priority()

        self.ui_options = UiOptions()
        self.apply_column_settings()

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

        user_date_group = QGroupBox("User Defined Date")
        user_date_group.setMinimumWidth(170)
        user_date_layout = QFormLayout(user_date_group)
        user_date_layout.addRow("Year", self.year_combo)
        user_date_layout.addRow("Month", self.month_combo)

        top_panels_layout = QHBoxLayout()
        top_panels_layout.addWidget(update_group, 1)
        top_panels_layout.addWidget(priority_group, 1)
        top_panels_layout.addWidget(user_date_group, 0)

        bottom_form = QFormLayout()

        target_folder_widget = QWidget()
        target_folder_layout = QHBoxLayout(target_folder_widget)
        target_folder_layout.setContentsMargins(0, 0, 0, 0)
        target_folder_layout.addWidget(self.target_folder_edit, 1)
        target_folder_layout.addWidget(self.target_folder_button, 0)
        bottom_form.addRow(self.target_folder_check, target_folder_widget)

        bottom_form.addRow(self.preview_changes_check)
        bottom_form.addRow(self.selection_count_label)
        bottom_form.addRow(self.update_button)

        details_layout.addLayout(top_panels_layout)
        details_layout.addLayout(bottom_form)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(self.folder_list, 2)
        
        checkbox_layout = QHBoxLayout()
        checkbox_layout.addWidget(self.recursive_checkbox)
        checkbox_layout.addWidget(self.show_conflicts_only_checkbox)
        checkbox_layout.addStretch()  # sağa boşluk bırakır (opsiyonel)

        left_layout.addLayout(checkbox_layout)
        
        left_layout.addWidget(self.media_table, 3)

        preview_container = QWidget()
        preview_container_layout = QVBoxLayout(preview_container)
        preview_container_layout.setContentsMargins(0, 0, 0, 0)

        preview_container_layout.addWidget(self.preview_label)


        self.preview_info_splitter = QSplitter(Qt.Horizontal)
        self.preview_info_splitter.addWidget(preview_container)
        self.preview_info_splitter.addWidget(self.info_group)
        self.preview_info_splitter.setStretchFactor(0, 4)
        self.preview_info_splitter.setStretchFactor(1, 2)
        
        self.info_group.show()

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(self.preview_info_splitter, 3)
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

        if pictures_path:
            default_path = Path(pictures_path)
            if default_path.exists():
                self.set_folder(default_path)
            else:
                self.set_folder(Path.home())
                
        self.internet_available = check_internet_connection()

        if not self.internet_available:
            self.statusBar().showMessage("No internet connection. Location lookup disabled.")
        
        self._update_info_toggle_button()

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
        
        options_menu = menu_bar.addMenu("Options")

        display_options_action = QAction("Display and Date Options...", self)
        display_options_action.triggered.connect(self.show_options_dialog)
        options_menu.addAction(display_options_action)
        
        duplicate_options_action = QAction("Duplicate Files...", self)
        duplicate_options_action.triggered.connect(self.show_duplicate_options_dialog)
        options_menu.addAction(duplicate_options_action)
        
        view_menu = menu_bar.addMenu("View")

        self.toggle_info_panel_action = QAction("Show Info Panel", self)
        self.toggle_info_panel_action.setCheckable(True)
        self.toggle_info_panel_action.setChecked(True)
        self.toggle_info_panel_action.triggered.connect(self.toggle_info_panel)

        view_menu.addAction(self.toggle_info_panel_action)

    def get_location_for_path(self, path: Path) -> dict:
        location_map = read_location_fields_with_exiftool([path])
        return location_map.get(path, {})

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

    def _display_relative_dir(self, path: Path) -> str:
        parent = path.parent
        if self.current_folder is not None:
            try:
                rel = parent.relative_to(self.current_folder)
                text = str(rel).replace("/", "\\")
                return "" if text == "." else text
            except Exception:
                pass
        return str(parent)

    def _set_rotate_buttons_enabled(self, enabled: bool) -> None:
        self.rotate_ccw_button.setEnabled(enabled)
        self.rotate_cw_button.setEnabled(enabled)

    def _position_overlay_buttons(self) -> None:
        self._position_play_overlay_button()
        self._position_rotate_buttons()
        self._update_info_toggle_button()

    def _position_play_overlay_button(self) -> None:
        btn = self.play_overlay_button
        parent = self.preview_label
        x = max(0, (parent.width() - btn.width()) // 2)
        y = max(0, (parent.height() - btn.height()) // 2)
        btn.move(x, y)

    def _update_info_toggle_button(self) -> None:
        if self.info_panel_visible:
            self.info_toggle_button.hide()
            return

        parent = self.preview_label
        btn = self.info_toggle_button

        margin = 10
        x = max(0, parent.width() - btn.width() - margin)
        y = margin
        btn.move(x, y)
        btn.show()
        btn.raise_()    

    def _close_info_panel(self) -> None:
        self.info_panel_visible = False
        self.info_group.hide()

        if hasattr(self, "toggle_info_panel_action"):
            self.toggle_info_panel_action.setChecked(False)

        self.preview_info_splitter.setSizes([1000, 0])
        self._update_info_toggle_button()
        self._position_overlay_buttons()

    def _open_info_panel(self) -> None:
        self.info_panel_visible = True
        self.info_group.show()

        if hasattr(self, "toggle_info_panel_action"):
            self.toggle_info_panel_action.setChecked(True)

        self.preview_info_splitter.setSizes([700, 320])

        if self.current_info_path is not None:
            self.populate_info_panel(self.current_info_path)

        self._update_info_toggle_button()
        self._position_overlay_buttons()

    def _set_info_row_visible(self, form_layout: QFormLayout, label_widget: QWidget, visible: bool) -> None:
        for i in range(form_layout.rowCount()):
            item = form_layout.itemAt(i, QFormLayout.FieldRole)
            if item and item.widget() is label_widget:
                label_item = form_layout.itemAt(i, QFormLayout.LabelRole)

                if label_item and label_item.widget():
                    label_item.widget().setVisible(visible)

                label_widget.setVisible(visible)
                break

    def _is_video_file(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".3gp", ".mts", ".m2ts"
        }

    def _is_image_file(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp", ".bmp"
        }   

    def _is_rotatable_image(self, path: Path) -> bool:
        return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

    def _is_rotatable_video(self, path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv", ".3gp"}

    def _position_rotate_buttons(self) -> None:
        parent = self.preview_label

        margin = 10
        spacing = 6

        btn_w = self.rotate_ccw_button.width()
        btn_h = self.rotate_ccw_button.height()

        # sol alt
        x = margin
        y_bottom = parent.height() - margin - btn_h

        # alt buton (clockwise)
        self.rotate_cw_button.move(x, y_bottom)

        # üst buton (counter-clockwise)
        self.rotate_ccw_button.move(x, y_bottom - btn_h - spacing)

        self.rotate_ccw_button.raise_()
        self.rotate_cw_button.raise_()

    def _handle_duplicate_decision_for_rename(self, decision, source_path: Path):
        """
        duplicate policy kararı geldiğinde rename işlemi için GUI seviyesinde ne yapılacağını belirler.
        Geri dönüş:
            ("skip", None)
            ("rename", target_path)
            ("normal", None)
        """
        if decision is None:
            return "normal", None

        if decision.action == "skip":
            return "skip", None

        if decision.action == "rename_copy_or_move":
            return "rename", decision.target_path

        if decision.action == "replace_with_incoming":
            return "rename", decision.target_path

        if decision.action == "keep_existing_best":
            return "skip", None

        if decision.action == "ask":
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Question)
            msg.setWindowTitle("Duplicate file found")
            msg.setText(f"A duplicate file was found for:\n{source_path.name}")
            msg.setInformativeText(
                "What would you like to do?\n\n"
                "Yes = Keep both (rename this file)\n"
                "No = Skip renaming this file\n"
                "Cancel = Cancel this file"
            )
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            msg.setDefaultButton(QMessageBox.Yes)

            result = msg.exec()

            if result == QMessageBox.Yes:
                return "rename", decision.target_path
            if result == QMessageBox.No:
                return "skip", None
            return "skip", None

        return "normal", None

    def _handle_duplicate_decision_for_move(self, decision, source_path: Path):
        """
        duplicate policy kararı geldiğinde move işlemi için GUI seviyesinde ne yapılacağını belirler.
        Geri dönüş:
            ("skip", None)
            ("move", target_path)
            ("normal", None)
        """
        if decision is None:
            return "normal", None

        if decision.action == "skip":
            return "skip", None

        if decision.action == "rename_copy_or_move":
            return "move", decision.target_path

        if decision.action == "replace_with_incoming":
            return "move", decision.target_path

        if decision.action == "keep_existing_best":
            return "skip", None

        if decision.action == "ask":
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Question)
            msg.setWindowTitle("Duplicate file found")
            msg.setText(f"A duplicate file was found for:\n{source_path.name}")
            msg.setInformativeText(
                "What would you like to do?\n\n"
                "Yes = Keep both (rename moved file)\n"
                "No = Skip moving this file\n"
                "Cancel = Cancel this file"
            )
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            msg.setDefaultButton(QMessageBox.Yes)

            result = msg.exec()

            if result == QMessageBox.Yes:
                return "move", decision.target_path
            if result == QMessageBox.No:
                return "skip", None
            return "skip", None

        return "normal", None

    def _handle_duplicate_decision_for_copy(self, decision, source_path: Path):
        """
        duplicate policy kararı geldiğinde copy işlemi için GUI seviyesinde ne yapılacağını belirler.
        Geri dönüş:
            ("skip", None)
            ("copy", target_path)
            ("normal", None)   # duplicate policy karar üretmedi / normal akışa dön
        """
        if decision is None:
            return "normal", None

        if decision.action == "skip":
            return "skip", None

        if decision.action == "rename_copy_or_move":
            return "copy", decision.target_path

        if decision.action == "replace_with_incoming":
            return "copy", decision.target_path

        if decision.action == "keep_existing_best":
            return "skip", None

        if decision.action == "ask":
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Question)
            msg.setWindowTitle("Duplicate file found")
            msg.setText(f"A duplicate file was found for:\n{source_path.name}")
            msg.setInformativeText(
                "What would you like to do?\n\n"
                "Yes = Keep both (rename incoming copy)\n"
                "No = Skip incoming file\n"
                "Cancel = Cancel this file"
            )
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            msg.setDefaultButton(QMessageBox.Yes)

            result = msg.exec()

            if result == QMessageBox.Yes:
                return "copy", decision.target_path
            if result == QMessageBox.No:
                return "skip", None
            return "skip", None

        return "normal", None


    def _capture_all_date_info(self, path: Path) -> dict:
        info = {
            "filesystem": None,
            "exif_dates": {},
        }

        dates = self.get_dates_for_path(path)
        info["filesystem"] = dates.get("filesystem")

        exif_map = read_exiftool_date_fields([path])
        info["exif_dates"] = exif_map.get(path, {}) or {}

        return info

    def _restore_exif_dates(self, path: Path, exif_dates: dict) -> None:
        if not exif_dates:
            return

        cmd = exiftool_base_cmd("-overwrite_original")

        has_any_tag = False
        for tag in [
            "DateTimeOriginal",
            "CreateDate",
            "MediaCreateDate",
            "TrackCreateDate",
            "CreationDate",
            "ModifyDate",
            "FileModifyDate",
        ]:
            value = (exif_dates.get(tag) or "").strip()
            if value:
                cmd.append(f"-{tag}={value}")
                has_any_tag = True
                
        if not has_any_tag:
            return

        cmd.append(str(path))

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="cp1254",
            errors="replace",
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ExifTool date restore failed")
        
    def _restore_all_date_info(self, path: Path, saved_info: dict) -> None:
        exif_dates = saved_info.get("exif_dates") or {}
        filesystem_dt = saved_info.get("filesystem")

        self._restore_exif_dates(path, exif_dates)

        if filesystem_dt is not None:
            self.write_filesystem_time(path, filesystem_dt)
            
    def _rotate_image_file(self, path: Path, angle: int) -> None:
        direction = "clockwise" if angle > 0 else "counterclockwise"
        self.statusBar().showMessage(f"Rotating image {direction}: {path.name} ...")
        QApplication.processEvents()

        saved_info = self._capture_all_date_info(path)

        suffix = path.suffix
        tmp_fd, tmp_name = tempfile.mkstemp(
            suffix=suffix,
            prefix=path.stem + "_tmp_",
            dir=str(path.parent),
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        try:
            with Image.open(path) as img:
                exif = img.info.get("exif")
                rotated = img.rotate(-angle, expand=True)

                save_kwargs = {}
                if exif:
                    save_kwargs["exif"] = exif

                rotated.save(tmp_path, **save_kwargs)

            tmp_path.replace(path)
            self._restore_all_date_info(path, saved_info)

            self.statusBar().showMessage(f"Image rotated: {path.name}", 5000)

        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            self.statusBar().showMessage(f"Image rotation failed: {path.name}", 5000)
            raise

    def _rotate_video_file(self, path: Path, angle: int) -> None:
        saved_info = self._capture_all_date_info(path)

        suffix = path.suffix
        tmp_fd, tmp_name = tempfile.mkstemp(
            suffix=suffix,
            prefix=path.stem + "_tmp_",
            dir=str(path.parent),
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        old_path = path.with_suffix(path.suffix + ".old_rotate_backup")

        try:
            transpose_value = "1" if angle == 90 else "2"

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-vf",
                f"transpose={transpose_value}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "copy",
                str(tmp_path),
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="cp1254",
                errors="replace",
            )

            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "ffmpeg rotate failed")

            if old_path.exists():
                old_path.unlink()

            path.replace(old_path)
            tmp_path.replace(path)

            self._restore_all_date_info(path, saved_info)

            if old_path.exists():
                old_path.unlink()

        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

            if old_path.exists() and not path.exists():
                try:
                    old_path.replace(path)
                except Exception:
                    pass

            raise

    def _hhmmss_to_ms(self, text: str) -> int:
        # örn: 00:01:23.45
        hh, mm, ss = text.split(":")
        total_seconds = int(hh) * 3600 + int(mm) * 60 + float(ss)
        return int(total_seconds * 1000)

    def _start_video_rotation(self, path: Path, angle: int) -> None:
        if self.rotate_process is not None:
            QMessageBox.information(self, "Rotate", "Another video rotation is already running.")
            return

        self._set_rotate_buttons_enabled(False)

        saved_info = self._capture_all_date_info(path)

        suffix = path.suffix
        tmp_fd, tmp_name = tempfile.mkstemp(
            suffix=suffix,
            prefix=path.stem + "_tmp_",
            dir=str(path.parent),
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        old_path = path.with_suffix(path.suffix + ".old_rotate_backup")
        if old_path.exists():
            try:
                old_path.unlink()
            except Exception:
                pass

        transpose_value = "1" if angle == 90 else "2"
        direction = "clockwise" if angle > 0 else "counterclockwise"

        self.rotate_target_path = path
        self.rotate_temp_path = tmp_path
        self.rotate_backup_path = old_path
        self.rotate_saved_info = saved_info
        self.rotate_duration_ms = None
        self.rotate_stderr_buffer = ""

        self.statusBar().showMessage(f"Rotating video {direction}: {path.name} ... 0%")

        process = QProcess(self)
        self.rotate_process = process

        process.setProgram("ffmpeg")
        process.setArguments([
            "-y",
            "-i", str(path),
            "-progress", "pipe:2",
            "-nostats",
            "-vf", f"transpose={transpose_value}",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "copy",
            str(tmp_path),
        ])

        process.readyReadStandardError.connect(self._on_rotate_process_stderr)
        process.finished.connect(self._on_rotate_process_finished)

        process.start()

    def _on_rotate_process_finished(self, exit_code: int, exit_status) -> None:
        process = self.rotate_process
        path = self.rotate_target_path
        tmp_path = self.rotate_temp_path
        old_path = self.rotate_backup_path
        saved_info = self.rotate_saved_info

        self.rotate_process = None
        self.rotate_target_path = None
        self.rotate_temp_path = None
        self.rotate_backup_path = None
        self.rotate_saved_info = None
        self.rotate_duration_ms = None
        self.rotate_stderr_buffer = ""

        try:
            if process is None or path is None or tmp_path is None or old_path is None or saved_info is None:
                self.statusBar().showMessage("Video rotation failed.", 5000)
                return

            if exit_code != 0:
                err = ""
                try:
                    err = bytes(process.readAllStandardError()).decode("cp1254", errors="ignore")
                except Exception:
                    pass

                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

                self.statusBar().showMessage(f"Video rotation failed: {path.name}", 5000)
                QMessageBox.warning(
                    self,
                    "Rotate",
                    f"Could not rotate video:\n{err or 'ffmpeg failed.'}"
                )
                return

            if old_path.exists():
                try:
                    old_path.unlink()
                except Exception:
                    pass

            path.replace(old_path)
            tmp_path.replace(path)

            self._restore_all_date_info(path, saved_info)

            if old_path.exists():
                try:
                    old_path.unlink()
                except Exception:
                    pass

            self.statusBar().showMessage(f"Video rotated: {path.name}", 5000)

            self.extract_video_thumbnail(path)
            self.show_preview(path)
            self.populate_details_panel(path)
            if self.info_panel_visible:
                self.populate_info_panel(path)

        except Exception as exc:
            # mümkünse geri alma
            try:
                if old_path is not None and old_path.exists() and path is not None and not path.exists():
                    old_path.replace(path)
            except Exception:
                pass

            self.statusBar().showMessage("Video rotation failed.", 5000)
            QMessageBox.warning(
                self,
                "Rotate",
                f"Could not finalize rotated video:\n{exc}"
            )

        finally:
            self._set_rotate_buttons_enabled(True)
            if process is not None:
                process.deleteLater()

    def rotate_selected_media(self, angle: int) -> None:
        selected_paths = self.selected_file_paths()
        if len(selected_paths) != 1:
            QMessageBox.information(
                self,
                "Rotate",
                "Please select exactly one image or video file."
            )
            return

        path = selected_paths[0]

        try:
            if self._is_rotatable_image(path):
                self._set_rotate_buttons_enabled(False)
                try:
                    self._rotate_image_file(path, angle)
                    self.thumbnail_cache.pop(path, None)
                    self.show_preview(path)
                    self.populate_details_panel(path)
                    if self.info_panel_visible:
                        self.populate_info_panel(path)
                finally:
                    self._set_rotate_buttons_enabled(True)

            elif self._is_rotatable_video(path):
                self._start_video_rotation(path, angle)

            else:
                QMessageBox.information(
                    self,
                    "Rotate",
                    "Selected file type is not supported for rotation."
                )
                return

        except Exception as exc:
            QMessageBox.warning(
                self,
                "Rotate",
                f"Could not rotate file:\n{exc}"
            )

    def _on_rotate_process_stderr(self) -> None:
        if self.rotate_process is None:
            return

        data = bytes(self.rotate_process.readAllStandardError()).decode("cp1254", errors="ignore")
        if not data:
            return

        self.rotate_stderr_buffer += data

        while "\n" in self.rotate_stderr_buffer:
            line, self.rotate_stderr_buffer = self.rotate_stderr_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue

            # Duration: 00:01:23.45
            if "Duration:" in line and self.rotate_duration_ms is None:
                try:
                    part = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
                    self.rotate_duration_ms = self._hhmmss_to_ms(part)
                except Exception:
                    pass

            # ffmpeg -progress çıktısı: out_time_ms=...
            elif line.startswith("out_time_ms="):
                try:
                    out_time_ms = int(line.split("=", 1)[1].strip())
                    if self.rotate_duration_ms and self.rotate_duration_ms > 0:
                        percent = max(0, min(100, int(out_time_ms * 100 / self.rotate_duration_ms)))
                        if self.rotate_target_path is not None:
                            self.statusBar().showMessage(
                                f"Rotating video: {self.rotate_target_path.name} ... {percent}%"
                            )
                    else:
                        if self.rotate_target_path is not None:
                            self.statusBar().showMessage(
                                f"Rotating video: {self.rotate_target_path.name} ..."
                            )
                except Exception:
                    pass

            elif line.startswith("progress=end"):
                if self.rotate_target_path is not None:
                    self.statusBar().showMessage(
                        f"Finalizing video rotation: {self.rotate_target_path.name} ..."
                    )

    def apply_row_filters(self) -> None:
        self.media_table_model.set_rows(list(self.all_media_rows))
        self.media_proxy_model.set_show_conflicts_only(
            self.show_conflicts_only_checkbox.isChecked()
        )

        if self.media_proxy_model.rowCount() > 0:
            self.media_table.selectRow(0)
        else:
            self.preview_label.setText("Preview")
            self.preview_label.setPixmap(QPixmap())
            self.show_play_overlay(False)
            self.current_preview_video_path = None
            self.clear_details_panel()

    def apply_column_settings(self) -> None:
        cols = ["name"]

        c = self.ui_options.columns
        if c.show_type:
            cols.append("type")
        if c.show_metadata:
            cols.append("metadata")
        if c.show_filename:
            cols.append("filename")
        if c.show_folder:
            cols.append("folder")
        if c.show_filesystem:
            cols.append("filesystem")
        if c.show_size:
            cols.append("size")
        if c.show_path:
            cols.append("path")
        if c.show_full_path:
            cols.append("full_path")
        if c.show_country:
            cols.append("country")
        if c.show_city:
            cols.append("city")

        self.media_table_model.set_visible_columns(cols)

    def toggle_info_panel(self, checked: bool) -> None:
        self.info_panel_visible = checked
        self.info_group.setVisible(checked)

        if checked:
            self.preview_info_splitter.setSizes([700, 320])
            if self.current_info_path is not None:
                self.populate_info_panel(self.current_info_path)
        else:
            self.preview_info_splitter.setSizes([1000, 0])

        self._update_info_toggle_button()
        self._position_overlay_buttons()

    def populate_info_panel(self, path: Path) -> None:
        self.info_name_label.setText(path.name)
        self.info_full_path_label.setText(str(path.resolve()))
        self.info_type_label.setText(path.suffix.lower())

        try:
            size_bytes = path.stat().st_size
            self.info_size_label.setText(f"{size_bytes:,} bytes")
        except Exception:
            self.info_size_label.setText("")

        exif_dates = read_exiftool_date_fields([path]).get(path, {})

        self.info_exif_datetimeoriginal_label.setText(exif_dates.get("DateTimeOriginal") or "")
        self.info_exif_createdate_label.setText(exif_dates.get("CreateDate") or "")
        self.info_exif_mediacreatedate_label.setText(exif_dates.get("MediaCreateDate") or "")
        self.info_exif_trackcreatedate_label.setText(exif_dates.get("TrackCreateDate") or "")
        self.info_exif_creationdate_label.setText(exif_dates.get("CreationDate") or "")
        self.info_exif_modifydate_label.setText(exif_dates.get("ModifyDate") or "")
        self.info_exif_filemodifydate_label.setText(exif_dates.get("FileModifyDate") or "")
        
        is_image = self._is_image_file(path)
        is_video = self._is_video_file(path)

        # Resimlerde daha anlamlı olan alanlar
        self._set_info_row_visible(self.info_form, self.info_exif_datetimeoriginal_label, is_image)
        self._set_info_row_visible(self.info_form, self.info_exif_creationdate_label, is_image or is_video)

        # Videolarda daha sık görülen alanlar
        self._set_info_row_visible(self.info_form, self.info_exif_mediacreatedate_label, is_video)
        self._set_info_row_visible(self.info_form, self.info_exif_trackcreatedate_label, is_video)

        # Her iki türde de görülebilecek genel alanlar
        self._set_info_row_visible(self.info_form, self.info_exif_createdate_label, True)
        self._set_info_row_visible(self.info_form, self.info_exif_modifydate_label, True)
        self._set_info_row_visible(self.info_form, self.info_exif_filemodifydate_label, True)
        
        dates = self.get_dates_for_path(path)
        self.info_metadata_date_label.setText(self._fmt_year_month(dates.get("metadata")))
        self.info_filename_date_label.setText(self._fmt_year_month(dates.get("filename")))
        self.info_folder_date_label.setText(self._fmt_year_month(dates.get("folder")))
        self.info_filesystem_date_label.setText(self._fmt_year_month(dates.get("filesystem")))

        try:
            stat = path.stat()
            self.info_created_label.setText(datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"))
            self.info_modified_label.setText(datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"))
            self.info_accessed_label.setText(datetime.fromtimestamp(stat.st_atime).strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            self.info_created_label.setText("")
            self.info_modified_label.setText("")
            self.info_accessed_label.setText("")

        location = self.get_location_for_path(path)

        country = str(location.get("country") or "")
        city = str(location.get("city") or "")
        gps_lat = str(location.get("gps_lat") or "")
        gps_lon = str(location.get("gps_lon") or "")

        if (not country or not city) and gps_lat and gps_lon and self.internet_available:
            inferred_country, inferred_city = infer_country_city_from_gps(gps_lat, gps_lon)
            if not country:
                country = inferred_country
            if not city:
                city = inferred_city

        self.info_country_label.setText(country)
        self.info_city_label.setText(city)
        self.info_latitude_label.setText(gps_lat)
        self.info_longitude_label.setText(gps_lon)

    def show_options_dialog(self) -> None:
        dlg = OptionsDialog(self.ui_options, self)
        if dlg.exec():
            self.ui_options = dlg.build_options()
            self.apply_column_settings()
            self.refresh_selected_folders()


    def show_duplicate_options_dialog(self) -> None:
        dialog = DuplicateOptionsDialog(self.ui_options.duplicate_files, self)
        if dialog.exec() == QDialog.Accepted:
            self.ui_options.duplicate_files = dialog.build_options()
            self.statusBar().showMessage("Duplicate file options updated.", 3000)

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

    def on_column_filter_changed(self, section: int, text: str) -> None:
        self.media_proxy_model.set_column_filter(section, text)
        self.filter_header.set_filter_text(section, text)

        if self.media_proxy_model.rowCount() > 0:
            self.media_table.selectRow(0)
        else:
            self.preview_label.setText("Preview")
            self.preview_label.setPixmap(QPixmap())
            self.show_play_overlay(False)
            self.current_preview_video_path = None
            self.clear_details_panel()


    def _source_row_from_proxy_index(self, index) -> int:
        if not index.isValid():
            return -1
        source_index = self.media_proxy_model.mapToSource(index)
        return source_index.row() if source_index.isValid() else -1


    def _source_index_from_proxy_index(self, index):
        if not index.isValid():
            return index
        return self.media_proxy_model.mapToSource(index)

    def on_target_folder_mode_changed(self, checked: bool) -> None:
        self.target_folder_edit.setEnabled(checked)
        self.target_folder_button.setEnabled(checked)
        self.update_button.setText("Copy" if checked else "Update")

    def choose_target_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Target Folder",
            self.target_folder_edit.text().strip() or str(Path.home()),
        )
        if folder:
            self.target_folder_edit.setText(folder)

    def get_target_folder_path(self) -> Path:
        text = self.target_folder_edit.text().strip()
        if text:
            return Path(text)
        pictures_path = QStandardPaths.writableLocation(QStandardPaths.PicturesLocation)
        return Path(pictures_path) if pictures_path else Path.home()

    def get_relative_parent_under_current_folder(self, source_path: Path) -> Path:
        if self.current_folder is not None:
            try:
                rel_parent = source_path.parent.relative_to(self.current_folder)
                return rel_parent
            except Exception:
                pass
        return Path(".")

    def get_copy_target_dir(self, source_path: Path, target_dt: datetime, do_folder: bool, target_root: Path) -> Path:
        if do_folder:
            return target_root / f"{target_dt.year:04d}" / f"{target_dt.month:02d}"
        rel_parent = self.get_relative_parent_under_current_folder(source_path)
        return target_root / rel_parent

    def get_copy_preview_path(
        self,
        source_path: Path,
        target_dt: datetime,
        do_filename: bool,
        do_folder: bool,
        target_root: Path,
    ) -> Path:
        dest_dir = self.get_copy_target_dir(source_path, target_dt, do_folder, target_root)
        dest_name = source_path.name
        if do_filename:
            dest_name = self.build_updated_filename(dest_name, target_dt)
        return dest_dir / dest_name

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
            full_path=str(path.resolve()),
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
        if self.rotate_process is not None:
            QMessageBox.information(self, "Rotate", "Please wait until the current video rotation finishes.")
            return
        
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
        
        self.info_name_label.setText("")
        self.info_full_path_label.setText("")
        self.info_size_label.setText("")
        self.info_type_label.setText("")
        self.info_metadata_date_label.setText("")
        self.info_filename_date_label.setText("")
        self.info_folder_date_label.setText("")
        self.info_filesystem_date_label.setText("")
        self.info_country_label.setText("")
        self.info_city_label.setText("")
        self.info_latitude_label.setText("")
        self.info_longitude_label.setText("")
        self.info_created_label.setText("")
        self.info_modified_label.setText("")
        self.info_accessed_label.setText("")

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
        old_thread = self.scan_thread
        old_scanner = self.scanner

        self.scan_thread = None
        self.scanner = None

        if old_thread is not None:
            old_thread.quit()
            old_thread.wait()

        if old_scanner is not None:
            old_scanner.deleteLater()

        if old_thread is not None:
            old_thread.deleteLater()

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

        if estimated_count > 0:
            shown_total = scan_limit if scan_limit is not None else estimated_count
            self.statusBar().showMessage(f"Scanning 0 out of {shown_total} files.")
        else:
            self.statusBar().showMessage("Scanning...")
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
        try:
            self.request_scan.disconnect()
        except Exception:
            pass

        self.request_scan.connect(scanner.scan_folders)

        scanner.scan_finished.connect(self.on_scan_finished)
        scanner.scan_failed.connect(self.on_scan_failed)
        scanner.scan_finished.connect(self.scan_thread.quit)
        scanner.scan_failed.connect(self.scan_thread.quit)
        
        thread = self.scan_thread
        worker = scanner
        thread.finished.connect(lambda: self._on_scan_thread_finished(thread, worker))

        self.scan_thread.start()
        self.request_scan.emit(paths_str, recursive, scan_limit, self.ui_options)

    def _on_scan_thread_finished(self, thread: QThread, worker) -> None:
        if worker is self.scanner:
            self.scanner = None

        if thread is self.scan_thread:
            self.scan_thread = None

        if worker is not None:
            worker.deleteLater()

        if thread is not None:
            thread.deleteLater()

    def on_scan_finished(self, rows) -> None:
        if self.current_folder is not None:
            for row in rows:
                try:
                    abs_path = row.path
                    rel = abs_path.relative_to(self.current_folder)
                    row.path = rel
                except Exception:
                    pass

                full_path = self.current_folder / row.path if not row.path.is_absolute() else row.path
                row.relative_dir = self._display_relative_dir(full_path)

        self.all_media_rows = rows
        self.apply_row_filters()

        self.statusBar().showMessage(f"Loaded {len(rows)} media files.")
        self.preview_label.setText("Preview")
        self.preview_label.setPixmap(QPixmap())
        self.show_play_overlay(False)
        self.current_preview_video_path = None
        self.clear_details_panel()

        if self.media_proxy_model.rowCount() > 0:
            self.media_table.clearSelection()
            self.media_table.selectRow(0)
        else:
            self.selection_count_label.setText("You have not selected a file")
            
            

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

        for proxy_index in rows:
            source_index = self.media_proxy_model.mapToSource(proxy_index)
            if not source_index.isValid():
                continue

            rel_path = self.media_table_model.get_path(source_index.row())
            if rel_path is not None:
                if self.current_folder is not None and not rel_path.is_absolute():
                    paths.append(self.current_folder / rel_path)
                else:
                    paths.append(rel_path)

        return paths

    def on_media_selection_changed(self, selected=None, deselected=None) -> None:
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

        if self.info_panel_visible:
            self.populate_info_panel(first_path)

    def on_media_table_clicked(self, index) -> None:
        if not index.isValid() or index.column() != 0:
            return

        source_index = self.media_proxy_model.mapToSource(index)
        if not source_index.isValid():
            return

        modifiers = QApplication.keyboardModifiers()
        row = source_index.row()
        now = time.monotonic()

        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            self._last_media_clicked_row = row
            self._last_media_click_ts = now
            return

        same_row = (self._last_media_clicked_row == row)
        delta = now - self._last_media_click_ts

        if same_row and 0.4 <= delta <= 1.5:
            if self.media_table.selectionModel().isRowSelected(index.row(), self.media_table.rootIndex()):
                if self.media_table.state() != QAbstractItemView.EditingState:
                    self.media_table.edit(index)

        self._last_media_clicked_row = row
        self._last_media_click_ts = now

    def rename_folder(self, old_path: Path, new_name: str) -> None:
        new_name = new_name.strip()
        if not new_name:
            self.folder_list.revert_row_text(old_path)
            return
        
        if new_name == old_path.name:
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

        source_index = self.media_proxy_model.mapToSource(index)
        if not source_index.isValid():
            return

        row = source_index.row()
        rel_path = self.media_table_model.get_path(row)
        if rel_path is None:
            return

        old_path = self.current_folder / rel_path if self.current_folder is not None and not rel_path.is_absolute() else rel_path
        new_name = new_name.strip()

        if not new_name or new_name == old_path.name:
            return

        try:
            dest_dir = old_path.parent
            target_path = None

            dup_options = self.ui_options.duplicate_files
            if duplicate_scope_applies(dup_options, "rename", old_path):
                decision = resolve_duplicate_for_destination(
                    source_path=old_path,
                    dest_dir=dest_dir,
                    preferred_name=new_name,
                    options=dup_options,
                )

                rename_action, duplicate_target_path = self._handle_duplicate_decision_for_rename(
                    decision,
                    old_path,
                )

                if rename_action == "skip":
                    self.statusBar().showMessage(f"Rename skipped for: {old_path.name}")
                    self.refresh_selected_folders()
                    return

                if rename_action == "rename" and duplicate_target_path is not None:
                    target_path = duplicate_target_path

            if target_path is None:
                target_path, collision_action = resolve_destination_path(
                    dest_dir,
                    new_name,
                    old_path.stat().st_size,
                )

                if target_path is None:
                    self.statusBar().showMessage(f"Rename skipped for: {old_path.name}")
                    self.refresh_selected_folders()
                    return

            same_path = False
            try:
                same_path = old_path.resolve() == target_path.resolve()
            except Exception:
                pass

            if same_path:
                self.refresh_selected_folders()
                return

            new_path = old_path.rename(target_path)

            if len(self.selected_file_paths()) <= 50:
                self.incremental_refresh_files([old_path], [new_path])
            else:
                self.refresh_selected_folders()

            self.statusBar().showMessage(f"File renamed to: {new_path.name}")

        except Exception as exc:
            QMessageBox.warning(self, "Rename File", f"Could not rename file:\n{exc}")
            self.refresh_selected_folders()

    def _map_proxy_row_to_source_row(self, proxy_row: int) -> int:
        proxy_index = self.media_proxy_model.index(proxy_row, 0)
        if not proxy_index.isValid():
            return -1

        source_index = self.media_proxy_model.mapToSource(proxy_index)
        if not source_index.isValid():
            return -1

        return source_index.row()


    def _map_proxy_index_to_source_index(self, proxy_index):
        if not proxy_index.isValid():
            return proxy_index
        return self.media_proxy_model.mapToSource(proxy_index)

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

            row_count = self.media_proxy_model.rowCount()
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

    def position_play_overlay(self) -> None:
        btn = self.play_overlay_button
        parent = self.preview_label
        x = (parent.width() - btn.width()) // 2
        y = (parent.height() - btn.height()) // 2
        btn.move(max(0, x), max(0, y))

    def show_play_overlay(self, visible: bool) -> None:
        if visible:
            self._position_play_overlay_button()
            self.play_overlay_button.show()
            self.play_overlay_button.raise_()
        else:
            self.play_overlay_button.hide()

        self._update_info_toggle_button()

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

        # Önce gerçek videodan yeni kare al
        thumb = self._extract_video_frame_with_ffmpeg(path)

        # Olmazsa gömülü thumbnail'e düş
        if thumb is None:
            thumb = self._extract_video_thumbnail_with_exiftool(path)

        self.thumbnail_cache[path] = thumb
        return thumb

    def _extract_video_thumbnail_with_exiftool(self, path: Path) -> Optional[Path]:
        try:
            safe_name = f"media_thumb_{abs(hash(str(path)))}.jpg"
            thumb_path = Path(tempfile.gettempdir()) / safe_name

            cmd = exiftool_base_cmd("-b", "-ThumbnailImage", str(path))

            with open(thumb_path, "wb") as f:
                result = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, check=False)

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
                "-frames:v",
                "1",
                str(thumb_path),
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="cp1254",
                errors="replace",
            )

            if result.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
                return thumb_path
        except Exception:
            pass

        return None

    def show_preview(self, path: Path) -> None:
        self.rotate_ccw_button.show()
        self.rotate_cw_button.show()
        self._position_rotate_buttons()    
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
            self.rotate_ccw_button.show()
            self.rotate_cw_button.show()            
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
                    self.rotate_ccw_button.show()
                    self.rotate_cw_button.show()                    
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
        copy_mode = self.target_folder_check.isChecked()
        target_root = self.get_target_folder_path() if copy_mode else None

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

            if copy_mode and target_root is not None:
                preview_dest = self.get_copy_preview_path(
                    source_path,
                    target_dt,
                    do_filename,
                    do_folder,
                    target_root,
                )
                new_name = preview_dest.name
                new_folder = str(preview_dest.parent)
                action_label = "copy"
            else:
                new_name = source_path.name
                if do_filename:
                    new_name = self.build_updated_filename(source_path.name, target_dt)

                new_folder = str(source_path.parent)
                if do_folder and self.current_folder is not None:
                    new_folder = str(self.current_folder / f"{target_dt.year:04d}" / f"{target_dt.month:02d}")
                action_label = "update"

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
                f"action={action_label}, "
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

    def copy_selected_files(self) -> None:
        selected_paths = self.selected_file_paths()
        if not selected_paths:
            QMessageBox.information(self, "Copy", "You have not selected a file")
            return

        target_root = self.get_target_folder_path()

        do_metadata = self.metadata_check.isChecked()
        do_filename = self.filename_check.isChecked()
        do_folder = self.folder_check.isChecked()
        do_filesystem = self.filesystem_check.isChecked()

        if not target_root.exists():
            try:
                target_root.mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                QMessageBox.warning(self, "Copy", f"Could not create target folder:\n{exc}")
                return

        if self.preview_changes_check.isChecked():
            plan = self.build_update_plan(
                selected_paths,
                int(self.year_combo.currentText()),
                int(self.month_combo.currentText()),
                do_metadata,
                do_filename,
                do_folder,
                do_filesystem,
            )
            dialog = UpdatePreviewDialog(plan, self)
            if dialog.exec() != QDialog.Accepted:
                return

        errors: list[str] = []
        copied_count = 0
        skipped_count = 0

        for source_path in selected_paths:
            try:
                dates = self.get_dates_for_path(source_path)
                target_dt, _source_key = self.choose_date_by_priority(dates)

                if target_dt is None:
                    errors.append(f"{source_path.name}: no usable date found")
                    continue

                dest_dir = self.get_copy_target_dir(source_path, target_dt, do_folder, target_root)
                dest_dir.mkdir(parents=True, exist_ok=True)

                dest_name = source_path.name
                if do_filename:
                    dest_name = self.build_updated_filename(dest_name, target_dt)

                dest_path = None

                dup_options = self.ui_options.duplicate_files
                if duplicate_scope_applies(dup_options, "copy", source_path):
                    decision = resolve_duplicate_for_destination(
                        source_path=source_path,
                        dest_dir=dest_dir,
                        preferred_name=dest_name,
                        options=dup_options,
                    )

                    copy_action, duplicate_target_path = self._handle_duplicate_decision_for_copy(
                        decision,
                        source_path,
                    )

                    if copy_action == "skip":
                        skipped_count += 1
                        continue

                    if copy_action == "copy" and duplicate_target_path is not None:
                        dest_path = duplicate_target_path

                if dest_path is None:
                    dest_path, collision_action = resolve_destination_path(
                        dest_dir,
                        dest_name,
                        source_path.stat().st_size,
                    )

                    if dest_path is None:
                        skipped_count += 1
                        continue

                shutil.copy2(str(source_path), str(dest_path))

                if do_metadata:
                    self.write_metadata(dest_path, target_dt)

                if do_filesystem:
                    self.write_filesystem_time(dest_path, target_dt)

                copied_count += 1

            except Exception as exc:
                errors.append(f"{source_path.name}: {exc}")

        if errors:
            QMessageBox.warning(self, "Copy completed with errors", "\n".join(errors[:20]))
        else:
            QMessageBox.information(
                self,
                "Copy",
                f"{copied_count} file(s) copied successfully.\n"
                f"{skipped_count} file(s) skipped."
            )

        self.statusBar().showMessage(
            f"{copied_count} file(s) copied to {target_root}, {skipped_count} skipped."
        )

    def update_selected_files(self) -> None:
        selected_paths = self.selected_file_paths()
        if not selected_paths:
            QMessageBox.information(self, "Update", "You have not selected a file")
            return

        if self.target_folder_check.isChecked():
            self.copy_selected_files()
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
        updated_count = 0
        skipped_count = 0

        for source_path in selected_paths:
            old_paths.append(source_path)

            try:
                current_path = source_path
                target_dt = datetime(year, month, 1, 12, 0, 0)

                target_name = current_path.name
                if do_filename:
                    target_name = self.build_updated_filename(target_name, target_dt)

                target_folder = current_path.parent
                if do_folder:
                    target_folder = self.current_folder / f"{target_dt.year:04d}" / f"{target_dt.month:02d}"

                target_path = current_path

                # move veya rename gerekiyorsa önce hedef yolu çöz
                if do_folder or do_filename:
                    resolved_target_path = None

                    dup_options = self.ui_options.duplicate_files

                    if do_folder and duplicate_scope_applies(dup_options, "move", current_path):
                        decision = resolve_duplicate_for_destination(
                            source_path=current_path,
                            dest_dir=target_folder,
                            preferred_name=target_name,
                            options=dup_options,
                        )

                        move_action, duplicate_target_path = self._handle_duplicate_decision_for_move(
                            decision,
                            current_path,
                        )

                        if move_action == "skip":
                            skipped_count += 1
                            new_paths.append(current_path)
                            continue

                        if move_action == "move" and duplicate_target_path is not None:
                            resolved_target_path = duplicate_target_path

                    if resolved_target_path is None:
                        resolved_target_path, collision_action = resolve_destination_path(
                            target_folder,
                            target_name,
                            current_path.stat().st_size,
                        )

                        if resolved_target_path is None:
                            skipped_count += 1
                            new_paths.append(current_path)
                            continue

                    same_path = False
                    try:
                        same_path = current_path.resolve() == resolved_target_path.resolve()
                    except Exception:
                        pass

                    if not same_path:
                        if resolved_target_path.parent != current_path.parent:
                            resolved_target_path.parent.mkdir(parents=True, exist_ok=True)
                            current_path = Path(shutil.move(str(current_path), str(resolved_target_path)))
                        else:
                            current_path = current_path.rename(resolved_target_path)

                if do_metadata:
                    self.write_metadata(current_path, target_dt)

                if do_filesystem:
                    self.write_filesystem_time(current_path, target_dt)

                new_paths.append(current_path)
                updated_count += 1

            except Exception as exc:
                errors.append(f"{source_path.name}: {exc}")
                new_paths.append(source_path)

        if len(old_paths) <= 50:
            self.incremental_refresh_files(old_paths, new_paths)
        else:
            self.refresh_selected_folders()

        if errors:
            QMessageBox.warning(self, "Update completed with errors", "\n".join(errors[:20]))
        else:
            QMessageBox.information(
                self,
                "Update",
                f"{updated_count} file(s) updated successfully.\n"
                f"{skipped_count} file(s) skipped."
            )
            
        self.statusBar().showMessage(
            f"{updated_count} file(s) updated, {skipped_count} skipped."
        )

    def build_updated_filename(self, original_name: str, target_dt: datetime) -> str:
        import re

        p = Path(original_name)
        stem = p.stem
        ext = p.suffix

        y = f"{target_dt.year:04d}"
        m = f"{target_dt.month:02d}"
        d = f"{target_dt.day:02d}"

        year_re = r"(1[89]\d{2}|20\d{2})"

        # ❗ ÖNEMLİ: sadece yıl içeren isimleri değiştirme
        if re.match(rf"^{year_re}_", stem):
            return original_name

        if re.match(r"^((?:1[89]\d|20\d))x_", stem):
            return original_name

        patterns = [
            # YYYY_MM_DD
            (rf"(?<!\d){year_re}_(\d{{2}})_(\d{{2}})(?!\d)", f"{y}_{m}_{d}"),

            # YYYY_MM_
            (rf"(?<!\d){year_re}_(\d{{2}})_(?!\d)", f"{y}_{m}_"),

            # YYYYMMDD
            (rf"(?<!\d){year_re}(\d{{2}})(\d{{2}})(?!\d)", f"{y}{m}{d}"),

            # YYYY-MM-DD / YYYY.MM.DD / YYYY_MM_DD
            (rf"(?<!\d){year_re}[-_.](\d{{2}})[-_.](\d{{2}})(?!\d)", f"{y}-{m}-{d}"),

            # YYYY-M-D
            (rf"(?<!\d){year_re}[-_.](\d{{1,2}})[-_.](\d{{1,2}})(?!\d)", f"{y}-{m}-{d}"),

            # YYYY-MM
            (rf"(?<!\d){year_re}[-_.](\d{{2}})(?!\d)", f"{y}-{m}"),
        ]

        for pattern, replacement in patterns:
            new_stem, count = re.subn(pattern, replacement, stem, count=1)
            if count > 0:
                return new_stem + ext

        return original_name

    def _is_image_file(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif", ".webp"
        }

    def _is_video_file(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm", ".mpg", ".mpeg"
        }

    def write_metadata(self, path: Path, dt: datetime) -> None:
        dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")

        cmd = exiftool_base_cmd("-overwrite_original")


        if self._is_image_file(path):
            cmd += [
                f"-DateTimeOriginal={dt_str}",
                f"-CreateDate={dt_str}",
                f"-ModifyDate={dt_str}",
            ]
        elif self._is_video_file(path):
            cmd += [
                f"-CreateDate={dt_str}",
                f"-ModifyDate={dt_str}",
                f"-MediaCreateDate={dt_str}",
                f"-TrackCreateDate={dt_str}",
            ]
        else:
            cmd += [
                f"-CreateDate={dt_str}",
                f"-ModifyDate={dt_str}",
            ]

        cmd.append(str(path))

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="cp1254",
            errors="replace",
        )

        stderr_text = (result.stderr or "").strip()
        stdout_text = (result.stdout or "").strip()

        # ExifTool bazen warning verip yine de işe yarar sonuç üretebilir.
        # Gerçek hata durumunda exception atalım.
        if result.returncode != 0:
            message = stderr_text or stdout_text or "Failed to update metadata"
            raise RuntimeError(message)

    def write_filesystem_time(self, path: Path, dt: datetime) -> None:
        # Aware datetime gelirse önce local naive datetime'a çevir.
        # Böylece Windows FILETIME hesabında naive/aware çakışması olmaz.
        if dt.tzinfo is not None and dt.utcoffset() is not None:
            dt = dt.astimezone().replace(tzinfo=None)

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
                raise RuntimeError("CreateFileW failed while updating filesystem time")

            epoch = datetime(1601, 1, 1)
            delta = dt - epoch
            filetime = int(delta.total_seconds() * 10**7)

            class FILETIME(ctypes.Structure):
                _fields_ = [
                    ("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD),
                ]

            ft = FILETIME(filetime & 0xFFFFFFFF, filetime >> 32)

            ok = ctypes.windll.kernel32.SetFileTime(
                handle,
                ctypes.byref(ft),  # creation
                ctypes.byref(ft),  # access
                ctypes.byref(ft),  # write
            )
            ctypes.windll.kernel32.CloseHandle(handle)

            if not ok:
                raise RuntimeError("SetFileTime failed while updating filesystem time")

        else:
            ts = dt.timestamp()
            os.utime(path, (ts, ts))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_overlay_buttons()

    def closeEvent(self, event) -> None:
        self._cleanup_scan_thread()
        super().closeEvent(event)

    @staticmethod
    def _fmt_year_month(dt: Optional[datetime]) -> str:
        if dt is None:
            return ""
        return f"{dt.year:04d}-{dt.month:02d}"