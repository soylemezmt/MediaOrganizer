
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .settings import UiOptions, ColumnSettings, DateSourceSettings

class OptionsDialog(QDialog):
    def __init__(self, options: UiOptions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.resize(420, 420)

        self._options = options

        layout = QVBoxLayout(self)

        columns_group = QGroupBox("Visible Columns")
        columns_layout = QFormLayout(columns_group)

        self.chk_type = QCheckBox()
        self.chk_metadata = QCheckBox()
        self.chk_filename = QCheckBox()
        self.chk_folder = QCheckBox()
        self.chk_filesystem = QCheckBox()
        self.chk_size = QCheckBox()
        self.chk_path = QCheckBox()
        self.chk_full_path = QCheckBox()
        self.chk_country = QCheckBox()
        self.chk_city = QCheckBox()

        c = options.columns
        self.chk_type.setChecked(c.show_type)
        self.chk_metadata.setChecked(c.show_metadata)
        self.chk_filename.setChecked(c.show_filename)
        self.chk_folder.setChecked(c.show_folder)
        self.chk_filesystem.setChecked(c.show_filesystem)
        self.chk_size.setChecked(c.show_size)
        self.chk_path.setChecked(c.show_path)
        self.chk_full_path.setChecked(c.show_full_path)
        self.chk_country.setChecked(c.show_country)
        self.chk_city.setChecked(c.show_city)

        columns_layout.addRow("Name", QLabel("Always visible"))
        columns_layout.addRow("Type", self.chk_type)
        columns_layout.addRow("Metadata", self.chk_metadata)
        columns_layout.addRow("Filename", self.chk_filename)
        columns_layout.addRow("Folder", self.chk_folder)
        columns_layout.addRow("Filesystem", self.chk_filesystem)
        columns_layout.addRow("Size", self.chk_size)
        columns_layout.addRow("Path", self.chk_path)
        columns_layout.addRow("Full Path", self.chk_full_path)
        columns_layout.addRow("Country/Longitude", self.chk_country)
        columns_layout.addRow("City/Latitude", self.chk_city)

        date_group = QGroupBox("Date Source Options")
        date_layout = QFormLayout(date_group)

        self.metadata_combo = QComboBox()
        self.metadata_combo.addItems([
            "DateTimeOriginal",
            "CreateDate",
            "MediaCreateDate",
            "TrackCreateDate",
            "CreationDate",
            "ModifyDate",
            "FileModifyDate",
        ])
        self.metadata_combo.setCurrentText(options.date_sources.metadata_tag)

        self.filesystem_combo = QComboBox()
        self.filesystem_combo.addItems(["ctime", "mtime"])
        self.filesystem_combo.setCurrentText(options.date_sources.filesystem_time)

        date_layout.addRow("Metadata date tag", self.metadata_combo)
        date_layout.addRow("Filesystem date", self.filesystem_combo)

        buttons = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)

        layout.addWidget(columns_group)
        layout.addWidget(date_group)
        layout.addLayout(buttons)

    def build_options(self) -> UiOptions:
        return UiOptions(
            columns=ColumnSettings(
                show_type=self.chk_type.isChecked(),
                show_metadata=self.chk_metadata.isChecked(),
                show_filename=self.chk_filename.isChecked(),
                show_folder=self.chk_folder.isChecked(),
                show_filesystem=self.chk_filesystem.isChecked(),
                show_size=self.chk_size.isChecked(),
                show_path=self.chk_path.isChecked(),
                show_full_path=self.chk_full_path.isChecked(),
                show_country=self.chk_country.isChecked(),
                show_city=self.chk_city.isChecked(),
            ),
            date_sources=DateSourceSettings(
                metadata_tag=self.metadata_combo.currentText(),
                filesystem_time=self.filesystem_combo.currentText(),
            ),
        )