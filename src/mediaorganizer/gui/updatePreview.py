from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout


class UpdatePreviewDialog(QDialog):
    def __init__(self, plan_rows: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preview changes")
        self.resize(1100, 600)

        layout = QVBoxLayout(self)

        info = QLabel(f"{len(plan_rows)} file(s) will be updated.")
        layout.addWidget(info)

        table = QTableWidget(len(plan_rows), 4)
        table.setHorizontalHeaderLabels(["File", "Old value", "New value", "Fields"])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)

        for row, item in enumerate(plan_rows):
            table.setItem(row, 0, QTableWidgetItem(item["file"]))
            table.setItem(row, 1, QTableWidgetItem(item["old"]))
            table.setItem(row, 2, QTableWidgetItem(item["new"]))
            table.setItem(row, 3, QTableWidgetItem(item["fields"]))

        layout.addWidget(table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)