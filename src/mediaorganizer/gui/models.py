from dataclasses import dataclass, replace
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
    country: str = ""
    city: str = ""
    relative_dir: str = ""
    full_path: str = ""


class MediaTableModel(QAbstractTableModel):
    COLUMN_DEFS = [
        ("name", "Name"),
        ("type", "Type"),
        ("metadata", "Metadata"),
        ("filename", "Filename"),
        ("folder", "Folder"),
        ("filesystem", "Filesystem"),
        ("size", "Size"),
        ("path", "Path"),
        ("full_path", "Full Path"),
        ("country", "Country/Longitude"),
        ("city", "City/Latitude"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[MediaRow] = []
        self.visible_columns = [
            "name", "metadata", "filename", "folder", "filesystem", "size", "path"
        ]

    def set_visible_columns(self, columns: list[str]) -> None:
        self.beginResetModel()
        self.visible_columns = columns
        self.endResetModel()

    def set_rows(self, rows: list[MediaRow]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.visible_columns)

    def _header_for_key(self, key: str) -> str:
        for k, title in self.COLUMN_DEFS:
            if k == key:
                return title
        return key

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            if 0 <= section < len(self.visible_columns):
                return self._header_for_key(self.visible_columns[section])
        return str(section + 1)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        row = self.rows[index.row()]
        key = self.visible_columns[index.column()]

        if role in (Qt.DisplayRole, Qt.EditRole):
            values = {
                "name": row.path.name,
                "type": row.file_type,
                "metadata": row.metadata_date,
                "filename": row.filename_date,
                "folder": row.folder_date,
                "filesystem": row.filesystem_date,
                "size": f"{row.size_bytes:,}",
                "path": row.relative_dir,
                "full_path": row.full_path,
                "country": row.country,
                "city": row.city,
            }
            return values.get(key, "")

        if role == Qt.BackgroundRole and row.is_inconsistent:
            return QColor("#ffcc80")

        if role == Qt.ForegroundRole and row.is_inconsistent:
            return QColor("#000000")

        if role == Qt.TextAlignmentRole and key == "size":
            return int(Qt.AlignRight | Qt.AlignVCenter)

        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags

        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if self.visible_columns[index.column()] == "name":
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index: QModelIndex, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or index.column() != 0:
            return False

        new_name = str(value).strip()
        if not new_name:
            return False

        row_index = index.row()
        row = self.rows[row_index]
        if new_name == row.path.name:
            return False

        new_path = row.path.with_name(new_name)
        self.rows[row_index] = replace(row, path=new_path)

        left = self.index(row_index, 0)
        right = self.index(row_index, self.columnCount() - 1)
        self.dataChanged.emit(left, right)
        return True

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