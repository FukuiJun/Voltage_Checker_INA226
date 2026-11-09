"""画面部品：数値ラベル・区切り線・補助ラベル（INA226_Monitor v1.2.1 の widgets.py から流用）と、番号カード。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..colors import COLORS
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


class NumberValue(QWidget):
    """「No.」と番号を 1 つの部品として描く（右寄せ・同じベースライン・間はいつも少しだけ）。

    番号が長くて入りきらないときは、番号の文字を小さくする（「No.」の大きさは変えない）。
    """

    GAP_PX = 6
    PREFIX = "No."

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._prefix_font = ui_font(22, bold=True)
        self._number_font = mono_font(64, bold=True)
        self._min_px = 28
        self._text = "---"
        self._prefix_color = QColor(COLORS["limit_line"])
        self._number_color = QColor(COLORS["header_text"])
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def text(self) -> str:
        return self._text

    def set_value(self, text: str, prefix_color: str, number_color: str) -> None:
        self._text = text
        self._prefix_color = QColor(prefix_color)
        self._number_color = QColor(number_color)
        self.updateGeometry()
        self.update()

    def _fonts_for(self, width: int) -> tuple[QFont, QFont]:
        """幅に入るよう番号のフォントを縮める。"""
        pf = QFont(self._prefix_font)
        nf = QFont(self._number_font)
        prefix_w = QFontMetrics(pf).horizontalAdvance(self.PREFIX)
        px = nf.pixelSize()
        while px > self._min_px and prefix_w + self.GAP_PX + QFontMetrics(nf).horizontalAdvance(self._text) > width:
            px -= 1
            nf.setPixelSize(px)
        return pf, nf

    def sizeHint(self) -> QSize:  # noqa: N802
        pfm, nfm = QFontMetrics(self._prefix_font), QFontMetrics(self._number_font)
        w = pfm.horizontalAdvance(self.PREFIX) + self.GAP_PX + nfm.horizontalAdvance(self._text)
        return QSize(w, nfm.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(QFontMetrics(self._prefix_font).horizontalAdvance(self.PREFIX) + 40, 40)

    def paintEvent(self, event) -> None:  # noqa: N802
        rect = self.contentsRect()
        pf, nf = self._fonts_for(rect.width())
        pfm, nfm = QFontMetrics(pf), QFontMetrics(nf)
        prefix_w = pfm.horizontalAdvance(self.PREFIX)
        number_w = nfm.horizontalAdvance(self._text)
        x = rect.right() + 1 - (prefix_w + self.GAP_PX + number_w)  # 右寄せ
        # 番号の文字を縦の中央に置き、「No.」は番号と同じベースラインにそろえる
        baseline = rect.top() + (rect.height() + nfm.ascent() - nfm.descent()) // 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(pf)
        painter.setPen(self._prefix_color)
        painter.drawText(x, baseline, self.PREFIX)
        painter.setFont(nf)
        painter.setPen(self._number_color)
        painter.drawText(x + prefix_w + self.GAP_PX, baseline, self._text)
        painter.end()


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
        self.number_value = NumberValue()
        # 左の「次に記録する番号」を隠さないよう、番号の欄は 180 px まで（長い番号は文字が小さくなる）
        self.number_value.setMaximumWidth(180)
        lay.addWidget(self.number_value, 0)
        self.setFixedHeight(96)

    def _set(self, mode: str, caption: str, number: str, sub: str) -> None:
        self.caption_label.setText(caption)
        prefix, value = {
            "next": (COLORS["limit_line"], COLORS["header_text"]),
            "remeasure": (COLORS["header_text"], COLORS["header_text"]),
            "none": (COLORS["text_sub"], COLORS["text_sub"]),
        }[mode]
        self.number_value.set_value(number, prefix, value)
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
        return self.number_value.text()
