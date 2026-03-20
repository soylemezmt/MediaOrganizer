from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor


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
            return QColor("#ffcc80")

        if role == Qt.ForegroundRole and row.is_inconsistent:
            return QColor("#000000")  # siyah yazı

        if role == Qt.TextAlignmentRole and col == 6:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def get_path(self, row_index: int) -> Optional[Path]:
        if 0 <= row_index < len(self.rows):
            return self.rows[row_index].path
        return None