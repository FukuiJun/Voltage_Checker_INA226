"""画面部品：数値ラベル・区切り線・補助ラベル（INA226_Monitor v1.2.1 の widgets.py から流用）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy

from ..theme import ui_font


class FitLabel(QLabel):
    """等幅・右寄せの数値ラベル。桁が増えて入りきらないときは文字を小さくする。"""

    def __init__(self, base_font: QFont, min_px: int = 10, parent=None) -> None:
        super().__init__(parent)
        self._base = QFont(base_font)
        self._min = min_px
        self.setFont(self._base)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt の名前に合わせる)
        super().setText(text)
        self._fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        avail = self.contentsRect().width()
        f = QFont(self._base)
        px = f.pixelSize()
        while px > self._min and QFontMetrics(f).horizontalAdvance(self.text()) > avail > 0:
            px -= 1
            f.setPixelSize(px)
        if f != self.font():
            self.setFont(f)


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


def sub_label(text: str, px: int = 13) -> QLabel:
    """補助の文字色（項目名・単位）のラベル。"""
    label = QLabel(text)
    label.setProperty("role", "sub")
    label.setFont(ui_font(px))
    return label


class ElideLabel(QLabel):
    """入りきらないときは末尾を「…」にするラベル（ヘッダーのロット表示。全文はツールチップ）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setToolTip(text)
        self.update()

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(QFontMetrics(self.font()).horizontalAdvance(self.text()) + 4)
        return hint

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = QFontMetrics(self.font()).elidedText(self.text(), Qt.ElideRight, rect.width())
        painter.drawText(rect, int(Qt.AlignLeft | Qt.AlignVCenter), text)
