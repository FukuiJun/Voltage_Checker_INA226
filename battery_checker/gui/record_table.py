"""記録の一覧（F-10）：No／電圧 [V]／判定／記録日時／備考。新しい記録が一番上（降順）。"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QItemSelectionModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QFrame, QHeaderView, QTableView, QVBoxLayout

from ..records import Record, RecordBook
from ..theme import COLORS, mono_font, ui_font

COLUMNS = ("No", "電圧 [V]", "判定", "記録日時", "備考")
COL_NO, COL_V, COL_JUDGE, COL_AT, COL_NOTE = range(len(COLUMNS))


class RecordTableModel(QAbstractTableModel):
    """RecordBook をそのまま見せる（行 0 が最後の記録）。2000 件でも軽いよう、表示する分だけ作る。"""

    def __init__(self, book: RecordBook | None = None) -> None:
        super().__init__()
        self.book = book or RecordBook()
        self._ng_brush = QBrush(QColor(COLORS["ng_bg"]))
        self._ng_text = QBrush(QColor(COLORS["ng_text"]))
        self._mono = mono_font(13)
        self._ui = ui_font(13)
        self._ng_font = ui_font(13, bold=True)

    # ---- 中身の入れ替え
    def set_book(self, book: RecordBook) -> None:
        self.beginResetModel()
        self.book = book
        self.endResetModel()

    def appended(self) -> None:
        """最後に 1 件足した（一番上に 1 行増える）。"""
        self.beginInsertRows(QModelIndex(), 0, 0)
        self.endInsertRows()

    def removed_last(self) -> None:
        """最後の 1 件を取り消した（呼ぶ前に book から消しておく）。"""
        self.beginRemoveRows(QModelIndex(), 0, 0)
        self.endRemoveRows()

    def changed(self, no: int | None = None) -> None:
        if no is None:
            if self.rowCount():
                self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, len(COLUMNS) - 1))
            return
        row = self.row_of(no)
        if row is not None:
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    # ---- 行と番号の対応
    def record_at(self, row: int) -> Record | None:
        n = len(self.book.records)
        return self.book.records[n - 1 - row] if 0 <= row < n else None

    def row_of(self, no: int) -> int | None:
        i = no - self.book.start_no
        n = len(self.book.records)
        return n - 1 - i if 0 <= i < n else None

    # ---- QAbstractTableModel
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.book.records)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        r = self.record_at(index.row())
        if r is None:
            return None
        col = index.column()
        if role == Qt.DisplayRole:
            if col == COL_NO:
                return str(r.no)
            if col == COL_V:
                return f"{r.voltage_V:.4f}"
            if col == COL_JUDGE:
                return r.judge.label
            if col == COL_AT:
                return r.measured_at
            if col == COL_NOTE:
                parts = [f"⚠ {r.note}"] if r.note else []
                if r.remeasured:
                    parts.append(f"測り直し {r.remeasured} 回")
                return "／".join(parts)
        elif role == Qt.TextAlignmentRole:
            if col in (COL_NO, COL_V):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if col == COL_JUDGE:
                return int(Qt.AlignCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        elif role == Qt.FontRole:
            if col in (COL_NO, COL_V, COL_AT):
                return self._mono
            if col == COL_JUDGE and not r.judge.is_ok:
                return self._ng_font
            return self._ui
        elif role == Qt.BackgroundRole and col == COL_JUDGE and not r.judge.is_ok:
            return self._ng_brush
        elif role == Qt.ForegroundRole and col == COL_JUDGE and not r.judge.is_ok:
            return self._ng_text
        return None


class RecordTablePanel(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.model = RecordTableModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(26)
        self.view.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        hh = self.view.horizontalHeader()
        hh.setFont(ui_font(13, bold=True))
        hh.setHighlightSections(False)
        for col, w in ((COL_NO, 70), (COL_V, 100), (COL_JUDGE, 124), (COL_AT, 172)):
            hh.setSectionResizeMode(col, QHeaderView.Fixed)
            self.view.setColumnWidth(col, w)
        hh.setSectionResizeMode(COL_NOTE, QHeaderView.Stretch)
        self.view.setFocusPolicy(Qt.ClickFocus)
        lay.addWidget(self.view)

    def selected_no(self) -> int | None:
        rows = self.view.selectionModel().selectedRows()
        if not rows:
            return None
        r = self.model.record_at(rows[0].row())
        return r.no if r else None

    def select_no(self, no: int | None) -> None:
        sm = self.view.selectionModel()
        row = self.model.row_of(no) if no is not None else None
        if row is None:
            sm.clearSelection()
            return
        idx = self.model.index(row, 0)
        sm.select(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        sm.setCurrentIndex(idx, QItemSelectionModel.NoUpdate)
        self.view.scrollTo(idx)
