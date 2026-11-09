"""記録した電圧の分布グラフ（F-10）：横軸 No、縦軸 電圧 [V] の散布図。上限・下限を破線で表示。

「上下限を調整」を押すと、上限・下限の線をマウスでドラッグして動かせる。
動かしている間は、その上下限で判定したときの OK／NG の色で点を描き直す（まだ記録には反映しない）。
離したら limits_changed を出し、記録の判定し直し・保存はメインウィンドウが行う。

グラフの見た目（背景・目盛線・軸の文字・軸幅の自動調整）は INA226_Monitor v1.2.1 の plot_panel.py と同じ。
"""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..records import RecordBook, round_v
from ..theme import COLORS, mono_font, ui_font

LEFT_AXIS_WIDTH = 62
AXIS_MIN_WIDTH = 24
AXIS_TEXT_GAP = 9
TICK_FONT_PX = 13
POINT_SIZE = 7
LIMIT_MARGIN = 0.2  # 縦軸：下限−(上限−下限)×0.2 〜 上限＋(上限−下限)×0.2（F-10）
MIN_X_SPAN = 20
LIMIT_STEP_V = 0.001  # 上下限は小数 3 桁（F-02）。ドラッグもこの刻みに合わせる
LIMIT_MIN_GAP_V = 0.001  # 下限 < 上限
LIMIT_MAX_V = 36.0  # INA226 のバス電圧の範囲


def snap(v: float) -> float:
    return round(round(v / LIMIT_STEP_V) * LIMIT_STEP_V, 3)


def judge_mask(vs: np.ndarray, lower_V: float, upper_V: float) -> np.ndarray:
    """OK なら True（records.judge と同じく小数 4 桁に丸めて比べる）。"""
    v = np.round(vs, 4)
    return (v >= round_v(lower_V)) & (v <= round_v(upper_V))


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
        pad = span * 0.05  # 端の点が欠けないように
        lo = min(lo, vmin - pad)
        hi = max(hi, vmax + pad)
    return lo, hi


class DistributionPlot(QFrame):
    limits_dragging = Signal(float, float, int)  # ドラッグ中の (下限, 上限, その上下限での NG 件数)
    limits_changed = Signal(float, float)  # ドラッグを終えた（上下限が変わったときだけ）

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
        self.hint_label = QLabel("線をドラッグして上下限を変えられます")
        self.hint_label.setObjectName("adjustHint")
        self.hint_label.setFont(ui_font(12, bold=True))
        self.hint_label.setVisible(False)
        head.addWidget(self.hint_label)
        head.addSpacing(8)
        self.legend_label = QLabel(
            f"<span style='color:{COLORS['plot_ok']}'>●</span> OK　"
            f"<span style='color:{COLORS['plot_ng']}'>●</span> NG　"
            f"<span style='color:{COLORS['input_border']}'>┄</span> 上下限"
        )
        self.legend_label.setFont(ui_font(12))
        self.legend_label.setTextFormat(Qt.RichText)
        head.addWidget(self.legend_label)
        head.addSpacing(8)
        self.adjust_button = QPushButton("上下限を調整")
        self.adjust_button.setObjectName("adjustButton")
        self.adjust_button.setCheckable(True)
        self.adjust_button.setFont(ui_font(12))
        self.adjust_button.setFocusPolicy(Qt.NoFocus)
        self.adjust_button.setToolTip("オンにすると、グラフの上限・下限の線をドラッグして動かせます")
        self.adjust_button.toggled.connect(self.set_adjustable)
        head.addWidget(self.adjust_button)
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
        self._pen_fixed = pg.mkPen(COLORS["limit_line"], width=1.5, style=Qt.DashLine)
        self._pen_adjust = pg.mkPen(COLORS["limit_line"], width=3)
        self._pen_hover = pg.mkPen(COLORS["header_text"], width=4)
        label_opts = {
            "position": 0.98,  # 右端
            "anchors": [(1, 1), (1, 1)],
            "color": COLORS["limit_line"],
            "fill": pg.mkBrush(COLORS["graph_bg"] + "C0"),
        }
        self.lower_line = pg.InfiniteLine(
            angle=0, movable=False, pen=self._pen_fixed, hoverPen=self._pen_hover,
            label="下限 {value:.3f} V", labelOpts=dict(label_opts, anchors=[(1, 0), (1, 0)]),
        )
        self.upper_line = pg.InfiniteLine(
            angle=0, movable=False, pen=self._pen_fixed, hoverPen=self._pen_hover,
            label="上限 {value:.3f} V", labelOpts=label_opts,
        )
        for line in (self.lower_line, self.upper_line):
            line.setVisible(False)
            line.setZValue(20)  # 点より手前（つかみやすいように）
            line.label.setFont(mono_font(12, bold=True))
            line.sigPositionChanged.connect(self._on_line_moved)
            line.sigPositionChangeFinished.connect(self._on_line_released)
            pi.addItem(line)
        self.ok_item.setZValue(10)
        self.ng_item.setZValue(11)  # NG の点は OK の点より手前
        lay.addWidget(self.pw, 1)
        self._limits: tuple[float, float] | None = None  # 確定している上下限
        self._nos = np.zeros(0)
        self._vs = np.zeros(0)
        self._ok = np.zeros(0, dtype=bool)
        self._setting = False  # set_limits() で線を動かしている（ドラッグではない）
        self.adjust_button.setEnabled(False)

    # ---------------------------------------------------------------- 上下限
    def set_limits(self, lower_V: float | None, upper_V: float | None) -> None:
        """確定した上下限を表示する（ドラッグ中の仮の位置は捨てる）。"""
        if lower_V is None or upper_V is None:
            self._limits = None
            self.lower_line.setVisible(False)
            self.upper_line.setVisible(False)
            self.set_adjust_enabled(False)
            return
        self._limits = (lower_V, upper_V)
        # 線が見えていないとラベルの数値が更新されない（pyqtgraph の仕様）ので、先に表示する
        self.lower_line.setVisible(True)
        self.upper_line.setVisible(True)
        self._setting = True
        try:
            self.lower_line.setBounds((None, None))
            self.upper_line.setBounds((None, None))
            self.lower_line.setValue(lower_V)
            self.upper_line.setValue(upper_V)
            self._update_bounds()
        finally:
            self._setting = False
        for line in (self.lower_line, self.upper_line):
            line.label.valueChanged()

    @property
    def dragged_limits(self) -> tuple[float, float]:
        return snap(self.lower_line.value()), snap(self.upper_line.value())

    def set_adjust_enabled(self, enabled: bool) -> None:
        """ロットがあり、測定中でないときだけ調整できる。"""
        enabled = enabled and self._limits is not None
        if not enabled and self.adjust_button.isChecked():
            self.adjust_button.setChecked(False)
        self.adjust_button.setEnabled(enabled)

    def set_adjustable(self, on: bool) -> None:
        if self.adjust_button.isChecked() != on:
            self.adjust_button.setChecked(on)  # toggled からもう一度呼ばれる
            return
        for line in (self.lower_line, self.upper_line):
            line.setMovable(on)
            line.setPen(self._pen_adjust if on else self._pen_fixed)
        self.hint_label.setVisible(on)
        if not on and self._limits is not None and self.dragged_limits != self._limits:
            self.set_limits(*self._limits)  # 確定していない位置は元に戻す
            self._recolor(self._ok)

    @property
    def adjustable(self) -> bool:
        return self.adjust_button.isChecked()

    def _update_bounds(self) -> None:
        lo, hi = self.lower_line.value(), self.upper_line.value()
        self.lower_line.setBounds((0.0, hi - LIMIT_MIN_GAP_V))
        self.upper_line.setBounds((lo + LIMIT_MIN_GAP_V, LIMIT_MAX_V))

    def _on_line_moved(self, line) -> None:
        if self._setting:
            return
        v = snap(line.value())
        if abs(v - line.value()) > 1e-9:
            self._setting = True
            try:
                line.setValue(v)  # 0.001 V 刻みに合わせる
            finally:
                self._setting = False
        self._update_bounds()
        lo, hi = self.dragged_limits
        ok = judge_mask(self._vs, lo, hi)
        self._recolor(ok)
        self.limits_dragging.emit(lo, hi, int((~ok).sum()))

    def _on_line_released(self, _line) -> None:
        if self._setting or self._limits is None:
            return
        lo, hi = self.dragged_limits
        if (lo, hi) != (round(self._limits[0], 3), round(self._limits[1], 3)):
            self.limits_changed.emit(lo, hi)

    def _recolor(self, ok: np.ndarray) -> None:
        self.ok_item.setData(x=self._nos[ok], y=self._vs[ok])
        self.ng_item.setData(x=self._nos[~ok], y=self._vs[~ok])

    def refresh(self, book: RecordBook | None) -> None:
        recs = book.records if book else []
        n = len(recs)
        nos = np.fromiter((r.no for r in recs), dtype=float, count=n)
        vs = np.fromiter((r.voltage_V for r in recs), dtype=float, count=n)
        ok = np.fromiter((r.judge.is_ok for r in recs), dtype=bool, count=n)
        self._nos, self._vs, self._ok = nos, vs, ok
        self._recolor(ok)
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
