import fnmatch

from PySide6.QtCore import Qt, QThread, QStandardPaths, Signal, QSortFilterProxyModel, QRect, QPoint
from PySide6.QtGui import QAction, QPixmap, QKeySequence, QShortcut, QPainter
from PySide6.QtWidgets import (
    QHeaderView,
    QLineEdit,
    QStyle,
    QStyleOptionHeader,
    QVBoxLayout,
    QFrame,
)

class MediaFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.column_filters: dict[int, str] = {}
        self.show_conflicts_only = False

    def set_column_filter(self, column: int, pattern: str) -> None:
        self.column_filters[column] = pattern
        self.invalidateFilter()

    def get_column_filter(self, column: int) -> str:
        return self.column_filters.get(column, "*")

    def set_show_conflicts_only(self, enabled: bool) -> None:
        self.show_conflicts_only = enabled
        self.invalidateFilter()

    def _matches_pattern(self, value: str, pattern: str) -> bool:
        value = (value or "").strip()
        pattern = (pattern or "").strip()

        # Boş filtre: sadece boş hücreler
        if pattern == "":
            return value == ""

        # Tek başına * : hepsi
        if pattern == "*":
            return True

        if not pattern.startswith("*"):
            pattern = "*" + pattern
        if not pattern.endswith("*"):
            pattern = pattern + "*"

        return fnmatch.fnmatchcase(value.lower(), pattern.lower())

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        model = self.sourceModel()
        if model is None:
            return True

        if self.show_conflicts_only:
            row_obj = model.rows[source_row]
            if not row_obj.is_inconsistent:
                return False

        for col, pattern in self.column_filters.items():
            index = model.index(source_row, col, source_parent)
            value = str(model.data(index, Qt.DisplayRole) or "")
            if not self._matches_pattern(value, pattern):
                return False

        return True


class HeaderFilterPopup(QFrame):
    def __init__(self, header, section: int, initial_text: str) -> None:
        super().__init__(header.window(), Qt.Popup | Qt.FramelessWindowHint)
        self.header = header
        self.section = section

        self.setFrameShape(QFrame.StyledPanel)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText('Filtre: boş=yalnız boşlar, *=hepsi, örn: 2024*, *.jpg, ?5')
        self.edit.setText(initial_text)
        layout.addWidget(self.edit)

        self.edit.returnPressed.connect(self._apply_and_close)
        self.edit.editingFinished.connect(self._apply_and_close)

        self.resize(320, self.sizeHint().height())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.edit.setFocus()
        self.edit.selectAll()

    def _apply_and_close(self) -> None:
        self.header.filter_changed.emit(self.section, self.edit.text())
        self.close()


class FilterHeaderView(QHeaderView):
    filter_changed = Signal(int, str)

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self.setSectionsClickable(True)
        self._active_filters: dict[int, str] = {}

    def _section_rect(self, section: int) -> QRect:
        x = self.sectionViewportPosition(section)
        w = self.sectionSize(section)
        return QRect(x, 0, w, self.height())

    def set_filter_text(self, section: int, text: str) -> None:
        self._active_filters[section] = text
        self.viewport().update()

    def filter_text(self, section: int) -> str:
        return self._active_filters.get(section, "*")

    def is_filter_active(self, section: int) -> bool:
        text = self._active_filters.get(section, "*")
        return text != "*"

    def mousePressEvent(self, event) -> None:
        section = self.logicalIndexAt(event.pos())
        if section < 0:
            super().mousePressEvent(event)
            return

        rect = self._section_rect(section)
        icon_rect = QRect(rect.right() - 18, rect.center().y() - 7, 14, 14)

        if icon_rect.contains(event.pos()):
            self._show_filter_popup(section)
            return

        super().mousePressEvent(event)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int) -> None:
        if not rect.isValid():
            return

        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        opt.section = logicalIndex

        header_text = str(
            self.model().headerData(logicalIndex, self.orientation(), Qt.DisplayRole) or ""
        )

        # Arka plan ve normal header görünümü
        # Metni biz çizeceğimiz için opt.text'i boş bırakıyoruz
        opt.text = ""
        self.style().drawControl(QStyle.CE_Header, opt, painter, self)

        painter.save()

        # Başlık metni için alan bırak: sağda filtre ikonu olacak
        text_rect = rect.adjusted(6, 0, -22, 0)

        font = painter.font()
        if self.is_filter_active(logicalIndex):
            font.setBold(True)
        painter.setFont(font)

        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, header_text)

        # Filtre ikonu
        icon_text = "▽" if not self.is_filter_active(logicalIndex) else "🔻"
        icon_rect = QRect(rect.right() - 18, rect.top(), 16, rect.height())
        painter.drawText(icon_rect, Qt.AlignCenter, icon_text)

        painter.restore()

    def _show_filter_popup(self, section: int) -> None:
        rect = self._section_rect(section)
        global_pos = self.viewport().mapToGlobal(QPoint(rect.left(), rect.bottom()))
        popup = HeaderFilterPopup(self, section, self.filter_text(section))
        popup.move(global_pos)
        popup.show()