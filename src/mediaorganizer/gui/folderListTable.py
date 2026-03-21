import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
)


class FolderListTable(QTableWidget):
    selection_paths_changed = Signal(list)
    folder_activated = Signal(Path)
    folder_rename_requested = Signal(Path, str)

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
        self.itemClicked.connect(self._on_item_clicked)
        self.itemChanged.connect(self._on_item_changed)

        self._row_paths: list[Optional[Path]] = []
        self._renaming_row: Optional[int] = None
        self._pre_edit_text: Optional[str] = None
        self._last_clicked_row: Optional[int] = None
        self._last_click_ts: float = 0.0

    def set_folder_entries(self, current_folder: Path) -> None:
        self.blockSignals(True)

        entries: list[tuple[str, Optional[Path]]] = [(".", current_folder)]
        parent = current_folder.parent if current_folder.parent != current_folder else current_folder
        entries.append(("..", parent))

        subfolders = sorted(
            [p for p in current_folder.iterdir() if p.is_dir()],
            key=lambda p: p.name.lower()
        )
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

            editable = label not in {".", ".."}
            flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
            if editable:
                flags |= Qt.ItemIsEditable
            item.setFlags(flags)

            self.setItem(row, 0, item)
            self._row_paths.append(path)

        self.clearSelection()
        self.blockSignals(False)

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

    def _on_item_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        label = item.text()

        if label in {".", ".."}:
            self._last_clicked_row = row
            self._last_click_ts = time.monotonic()
            return

        modifiers = QApplication.keyboardModifiers()
        if modifiers & (Qt.ControlModifier | Qt.ShiftModifier):
            self._last_clicked_row = row
            self._last_click_ts = time.monotonic()
            return

        now = time.monotonic()
        same_row = (self._last_clicked_row == row)
        delta = now - self._last_click_ts

        # Explorer benzeri davranış:
        # ilk tıklama sadece seçer, biraz sonra aynı satıra tekrar tek tıklanırsa edit aç
        if same_row and 0.4 <= delta <= 1.5:
            if self.selectionModel().isRowSelected(row, self.rootIndex()):
                self._renaming_row = row
                self._pre_edit_text = item.text()
                self.editItem(item)

                editor = self.findChild(QLineEdit)
                if editor is not None:
                    editor.selectAll()

        self._last_clicked_row = row
        self._last_click_ts = now

    def _start_rename_if_still_selected(self, row: int) -> None:
        if row < 0 or row >= self.rowCount():
            return
        item = self.item(row, 0)
        if item is None or item.text() in {".", ".."}:
            return
        if not self.selectionModel().isRowSelected(row, self.rootIndex()):
            return
        if self.state() == QAbstractItemView.EditingState:
            return

        self._renaming_row = row
        self._pre_edit_text = item.text()
        self.editItem(item)

        editor = self.findChild(QLineEdit)
        if editor is not None:
            editor.selectAll()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row != self._renaming_row:
            return

        self._renaming_row = None
        old_name = self._pre_edit_text or ""
        self._pre_edit_text = None
        new_name = item.text().strip()

        path = self._row_paths[row]
        if path is None or old_name in {".", ".."}:
            return

        if not new_name or new_name == old_name:
            item.setText(old_name)
            return

        self.folder_rename_requested.emit(path, new_name)

    def revert_row_text(self, path: Path) -> None:
        for row, p in enumerate(self._row_paths):
            if p == path:
                item = self.item(row, 0)
                if item is not None:
                    self.blockSignals(True)
                    item.setText(path.name)
                    self.blockSignals(False)
                return