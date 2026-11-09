"""画面部品：数値ラベル・区切り線・補助ラベル（INA226_Monitor v1.2.1 の widgets.py から流用）と、番号カード。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..theme import mono_font, ui_font


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


class NumberCard(QFrame):
    """いま記録する番号を大きく出すカード（7 章 3.）。

    - 通常：濃い地に「次に記録する番号」と番号。記録済みの件数も出す
    - 測り直し中：朽葉色の地に「測り直す番号」と、その番号（次の番号は変わらないことも出す）
    - ロットなし：番号は「---」
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("numberCard")
        self.setProperty("mode", "none")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(6)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.caption_label = QLabel("次に記録する番号")
        self.caption_label.setObjectName("numberCaption")
        self.caption_label.setFont(ui_font(14, bold=True))
        self.sub_label = QLabel()
        self.sub_label.setObjectName("numberSub")
        self.sub_label.setFont(ui_font(12))
        # 文字が長くてもカードの幅を広げない（番号の表示を優先する）
        for w in (self.caption_label, self.sub_label):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            w.setMinimumWidth(0)
        left.addStretch(1)
        left.addWidget(self.caption_label)
        left.addWidget(self.sub_label)
        left.addStretch(1)
        lay.addLayout(left, 1)
        self.prefix_label = QLabel("No.")
        self.prefix_label.setObjectName("numberPrefix")
        self.prefix_label.setFont(ui_font(20, bold=True))
        lay.addWidget(self.prefix_label, 0, Qt.AlignBottom)
        self.number_label = FitLabel(mono_font(64, bold=True), min_px=28)
        self.number_label.setObjectName("numberValue")
        self.number_label.setMinimumWidth(120)
        self.number_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        lay.addWidget(self.number_label, 0)
        self.setFixedHeight(96)

    def _set(self, mode: str, caption: str, number: str, sub: str) -> None:
        self.caption_label.setText(caption)
        self.number_label.setText(number)
        self.sub_label.setText(sub)
        if self.property("mode") != mode:
            self.setProperty("mode", mode)
            for w in (self, *self.findChildren(QWidget)):
                w.style().unpolish(w)
                w.style().polish(w)

    def set_next(self, no: int, recorded: int) -> None:
        self._set("next", "次に記録する番号", str(no), f"記録済み {recorded:,} 件")

    def set_remeasure(self, no: int, next_no: int) -> None:
        self._set("remeasure", "測り直す番号", str(no), f"次は No.{next_no} のまま")

    def set_none(self) -> None:
        self._set("none", "次に記録する番号", "---", "ロットを始めてください")

    @property
    def number(self) -> str:
        return self.number_label.text()
