"""記録した電圧の分布グラフ（F-10）：横軸 No、縦軸 電圧 [V] の散布図。上限・下限を破線で表示。

グラフの見た目（背景・目盛線・軸の文字・軸幅の自動調整）は INA226_Monitor v1.2.1 の plot_panel.py と同じ。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QGraphicsView, QHBoxLayout, QLabel, QVBoxLayout

from ..records import RecordBook
from ..theme import COLORS, mono_font, ui_font

LEFT_AXIS_WIDTH = 62
AXIS_MIN_WIDTH = 24
AXIS_TEXT_GAP = 9
TICK_FONT_PX = 13
POINT_SIZE = 7
LIMIT_MARGIN = 0.2  # 縦軸：下限−(上限−下限)×0.2 〜 上限＋(上限−下限)×0.2（F-10）
MIN_X_SPAN = 20


def _style_axis(ax: pg.AxisItem, text_color: str) -> None:
    ax.setPen(pg.mkPen(COLORS["graph_grid"]))
    ax.setTextPen(pg.mkPen(text_color))
    ax.setStyle(tickFont=mono_font(TICK_FONT_PX), tickLength=0, maxTickLevel=0)  # 主目盛りだけ
    ax.setTickDensity(0.6)


def _make_plot() -> pg.PlotWidget:
    pw = pg.PlotWidget(background=COLORS["panel_bg"])
    pw.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
    pi = pw.getPlotItem()
    pi.hideButtons()
    pi.setMenuEnabled(False)
    pi.layout.setContentsMargins(0, 0, 0, 0)
    vb = pi.getViewBox()
    vb.setBackgroundColor(COLORS["graph_bg"])
    vb.setBorder(pg.mkPen(COLORS["graph_border"]))
    vb.setMouseEnabled(x=False, y=False)
    for name in ("left", "bottom"):
        _style_axis(pi.getAxis(name), COLORS["graph_axis"])
    pi.getAxis("left").setWidth(LEFT_AXIS_WIDTH)
    pi.showGrid(x=False, y=True, alpha=1.0)
    return pw


def y_range(lower_V: float, upper_V: float, values) -> tuple[float, float]:
    """縦軸の範囲：上下限の前後に (上限−下限)×0.2 の余白。記録値がはみ出すときはそこまで広げる。"""
    span = max(upper_V - lower_V, 0.001)
    lo, hi = lower_V - span * LIMIT_MARGIN, upper_V + span * LIMIT_MARGIN
    if len(values):
        vmin, vmax = float(np.min(values)), float(np.max(values))
        pad = span * 0.05
        if vmin < lo:
            lo = vmin - pad
        if vmax > hi:
            hi = vmax + pad
    return lo, hi


class DistributionPlot(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        pg.setConfigOptions(antialias=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        head = QHBoxLayout()
        self.title_label = QLabel("分布（横軸 No、縦軸 電圧 V）")
        self.title_label.setObjectName("voltageAxisLabel")
        self.title_label.setFont(ui_font(13, bold=True))
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.legend_label = QLabel(
            f"<span style='color:{COLORS['plot_ok']}'>●</span> OK　"
            f"<span style='color:{COLORS['plot_ng']}'>●</span> NG　"
            f"<span style='color:{COLORS['input_border']}'>┄</span> 上下限"
        )
        self.legend_label.setFont(ui_font(12))
        self.legend_label.setTextFormat(Qt.RichText)
        head.addWidget(self.legend_label)
        lay.addLayout(head)

        self.pw = _make_plot()
        pi = self.pw.getPlotItem()
        self.ok_item = pg.ScatterPlotItem(
            size=POINT_SIZE, pen=None, brush=pg.mkBrush(COLORS["plot_ok"]), symbol="o", pxMode=True
        )
        self.ng_item = pg.ScatterPlotItem(
            size=POINT_SIZE + 1, pen=None, brush=pg.mkBrush(COLORS["plot_ng"]), symbol="o", pxMode=True
        )
        pi.addItem(self.ok_item)
        pi.addItem(self.ng_item)
        limit_pen = pg.mkPen(COLORS["limit_line"], width=1.5, style=Qt.DashLine)
        self.lower_line = pg.InfiniteLine(angle=0, movable=False, pen=limit_pen)
        self.upper_line = pg.InfiniteLine(angle=0, movable=False, pen=limit_pen)
        for line in (self.lower_line, self.upper_line):
            line.setVisible(False)
            line.setZValue(5)  # 目盛線より手前
            pi.addItem(line)
        self.ok_item.setZValue(10)
        self.ng_item.setZValue(11)  # NG の点は OK の点より手前
        lay.addWidget(self.pw, 1)
        self._limits: tuple[float, float] | None = None

    def set_limits(self, lower_V: float | None, upper_V: float | None) -> None:
        if lower_V is None or upper_V is None:
            self._limits = None
            self.lower_line.setVisible(False)
            self.upper_line.setVisible(False)
            return
        self._limits = (lower_V, upper_V)
        self.lower_line.setValue(lower_V)
        self.upper_line.setValue(upper_V)
        self.lower_line.setVisible(True)
        self.upper_line.setVisible(True)

    def refresh(self, book: RecordBook | None) -> None:
        recs = book.records if book else []
        n = len(recs)
        nos = np.fromiter((r.no for r in recs), dtype=float, count=n)
        vs = np.fromiter((r.voltage_V for r in recs), dtype=float, count=n)
        ok = np.fromiter((r.judge.is_ok for r in recs), dtype=bool, count=n)
        self.ok_item.setData(x=nos[ok], y=vs[ok])
        self.ng_item.setData(x=nos[~ok], y=vs[~ok])
        vb = self.pw.getPlotItem().getViewBox()
        start = book.start_no if book else 1
        last = max(book.next_no if book else 1, start + MIN_X_SPAN)
        vb.setXRange(start - 0.5, last + 0.5, padding=0.02)
        if self._limits:
            vb.setYRange(*y_range(*self._limits, vs), padding=0.0)
        elif n:
            vb.setYRange(float(vs.min()) - 0.05, float(vs.max()) + 0.05, padding=0.0)
        self._fit_axis_width()

    def _fit_axis_width(self) -> None:
        ax = self.pw.getPlotItem().getAxis("left")
        vb = self.pw.getPlotItem().getViewBox()
        lo, hi = vb.viewRange()[1]
        levels = ax.tickValues(lo, hi, max(vb.height(), 1.0))
        text_w = 0
        if levels:
            spacing, values = levels[0]
            fm = QFontMetrics(ax.style["tickFont"])
            text_w = max((fm.horizontalAdvance(t) for t in ax.tickStrings(values, ax.scale, spacing)), default=0)
        w = max(AXIS_MIN_WIDTH, text_w + AXIS_TEXT_GAP)
        if ax.width() != w:
            ax.setWidth(w)
        if self.title_label.contentsMargins().left() != w:
            self.title_label.setContentsMargins(w, 0, 0, 0)
