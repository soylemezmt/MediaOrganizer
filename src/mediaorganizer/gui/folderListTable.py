from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
)

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
        bold_font = QFont()
        bold_font.setBold(True)

        for row, (label, path) in enumerate(entries):
            item = QTableWidgetItem(label)
            if label in {".", ".."}:
                item.setForeground(QColor("#1a73e8"))
            if label == ".":
                item.setFont(bold_font)
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