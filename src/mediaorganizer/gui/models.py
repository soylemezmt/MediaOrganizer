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
        "Path",
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
            return QColor("#000000")

        if role == Qt.TextAlignmentRole and col == 6:
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def get_path(self, row_index: int) -> Optional[Path]:
        if 0 <= row_index < len(self.rows):
            return self.rows[row_index].path
        return None

    def find_row_by_path(self, path: Path) -> int:
        for i, row in enumerate(self.rows):
            if row.path == path:
                return i
        return -1

    def remove_paths(self, paths: list[Path]) -> None:
        path_set = set(paths)
        to_remove = [i for i, row in enumerate(self.rows) if row.path in path_set]
        for row_index in reversed(to_remove):
            self.beginRemoveRows(QModelIndex(), row_index, row_index)
            del self.rows[row_index]
            self.endRemoveRows()

    def update_row(self, row_index: int, new_row: MediaRow) -> None:
        if not (0 <= row_index < len(self.rows)):
            return
        self.rows[row_index] = new_row
        left = self.index(row_index, 0)
        right = self.index(row_index, self.columnCount() - 1)
        self.dataChanged.emit(left, right)

    def insert_row_sorted(self, new_row: MediaRow) -> None:
        key = str(new_row.path).lower()
        insert_at = 0
        while insert_at < len(self.rows) and str(self.rows[insert_at].path).lower() < key:
            insert_at += 1
        self.beginInsertRows(QModelIndex(), insert_at, insert_at)
        self.rows.insert(insert_at, new_row)
        self.endInsertRows()

    def sort_rows(self) -> None:
        self.beginResetModel()
        self.rows.sort(key=lambda r: str(r.path).lower())
        self.endResetModel()