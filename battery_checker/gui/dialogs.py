"""ダイアログ：ロットの開始・ロット設定（F-02）と、設定（Q-03, Q-04, Q-06, Q-08）。"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..colors import COLORS
from ..settings import Settings
from ..theme import mono_font, ui_font
from .widgets import sub_label

LOT_TEXT_MAX_LEN = 30
EMPLOYEE_ID_MAX_LEN = 20
_EMPLOYEE_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]*$")  # 半角英数字とハイフン
MAX_VOLTAGE_V = 36.0  # INA226 のバス電圧の範囲（5.1）
_LIMIT_RE = re.compile(r"^\d{1,2}(\.\d{0,3})?$")  # 小数 3 桁まで


@dataclass(frozen=True)
class LotInput:
    lot_name: str
    operator: str  # 測定者の社員番号（名前ではない）
    lower_V: float
    upper_V: float
    start_no: int
    memo: str


def parse_limit(text: str) -> float | None:
    t = text.strip()
    if not _LIMIT_RE.match(t):
        return None
    v = float(t)
    return v if 0.0 <= v <= MAX_VOLTAGE_V else None


def normalize_employee_id(text: str) -> str:
    """全角の英数字・ハイフンを半角にし、前後の空白を除く（例：「０１２３４５」→「012345」）。"""
    t = unicodedata.normalize("NFKC", text).strip()
    return t.replace("ー", "-").replace("−", "-").replace("‐", "-")


def is_valid_employee_id(text: str) -> bool:
    """空（未入力）か、半角英数字とハイフンだけ（20 文字まで）なら True。"""
    t = normalize_employee_id(text)
    return not t or (len(t) <= EMPLOYEE_ID_MAX_LEN and bool(_EMPLOYEE_ID_RE.match(t)))


def _latin_only(w) -> None:
    """日本語入力（IME）を使わない欄にする（社員番号・電圧は半角で打てばよいので）。"""
    w.setAttribute(Qt.WA_InputMethodEnabled, False)
    w.setInputMethodHints(Qt.ImhLatinOnly | Qt.ImhNoPredictiveText)


def _set_invalid(w, invalid: bool) -> None:
    if bool(w.property("invalid")) != invalid:
        w.setProperty("invalid", invalid)
        w.style().unpolish(w)
        w.style().polish(w)


class LimitRangeBar(QWidget):
    """下限・上限を 1 本のバーの 2 つのつまみで調整する（F-02）。

    - 左のつまみ＝下限、右のつまみ＝上限。バーは薄いベージュ、つまみの間（OK の範囲）は濃い朽葉色
    - ドラッグは 0.01 V 刻み。矢印キーで最後に触ったつまみを 0.001 V（Shift で 0.01 V）ずつ動かす
    - バーの範囲は下限・上限の前後 0.5 V 以上（0.5 V 単位。例 3.600〜4.200 V → 3.0〜5.0 V）。
      つまみを端まで動かして離すと、範囲を広げる（さらに先へ動かせる）。値が無いときは 0〜5 V
    - 下限は上限より 0.001 V 以上小さく保つ
    """

    valuesChanged = Signal(float, float)

    DRAG_STEP_V = 0.01
    KEY_STEP_V = 0.001
    MIN_GAP_V = 0.001
    DEFAULT_RANGE = (0.0, 5.0)
    PAD_V = 0.5
    MARGIN_PX = 14  # つまみが端で切れないよう、バーの両端に空ける幅
    HANDLE_R = 9

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lo = 3.0
        self._hi = 4.2
        self._vmin, self._vmax = self.DEFAULT_RANGE
        self._has_values = False  # 下限・上限が入っていない間は、つまみを薄く描く
        self._drag: str | None = None
        self._active = "lo"
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(54)  # ダイアログを縦に広げても、バーの行は広げない
        self.setToolTip("つまみをドラッグして下限・上限を変えます（矢印キーで 0.001 V、Shift＋矢印で 0.01 V）")

    # ---- 値
    def values(self) -> tuple[float, float]:
        return self._lo, self._hi

    def set_values(self, lo: float, hi: float) -> None:
        """入力欄から値を受け取る（valuesChanged は出さない）。"""
        self._lo, self._hi = round(lo, 3), round(hi, 3)
        self._has_values = True
        self._fit_range()
        self.update()

    def visible_range(self) -> tuple[float, float]:
        return self._vmin, self._vmax

    def _fit_range(self) -> None:
        """下限・上限の前後に余白を取った範囲（0.5 V 単位）にする。"""
        pad = max(self.PAD_V, (self._hi - self._lo) * 0.5)
        self._vmin = max(0.0, math.floor((self._lo - pad) * 2) / 2)
        self._vmax = min(MAX_VOLTAGE_V, math.ceil((self._hi + pad) * 2) / 2)

    def _set_from_user(self, which: str, v: float) -> None:
        v = round(min(max(v, 0.0), MAX_VOLTAGE_V), 3)
        if which == "lo":
            self._lo = min(v, round(self._hi - self.MIN_GAP_V, 3))
        else:
            self._hi = max(v, round(self._lo + self.MIN_GAP_V, 3))
        self._has_values = True
        self._active = which
        self.update()
        self.valuesChanged.emit(self._lo, self._hi)

    # ---- 座標
    def _track(self) -> QRectF:
        r = self.contentsRect()
        return QRectF(r.left() + self.MARGIN_PX, r.top() + 15, r.width() - 2 * self.MARGIN_PX, 6)

    def _x_of(self, v: float) -> float:
        t = self._track()
        return t.left() + t.width() * (v - self._vmin) / (self._vmax - self._vmin)

    def _v_of(self, x: float) -> float:
        t = self._track()
        v = self._vmin + (x - t.left()) / max(t.width(), 1.0) * (self._vmax - self._vmin)
        v = min(max(v, self._vmin), self._vmax)
        return round(round(v / self.DRAG_STEP_V) * self.DRAG_STEP_V, 3)

    def handle_center(self, which: str) -> QPointF:
        t = self._track()
        return QPointF(self._x_of(self._lo if which == "lo" else self._hi), t.center().y())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 54)

    # ---- 描画
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = self._track()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["divider"]))  # バー全体：薄いベージュ
        p.drawRoundedRect(t, 3, 3)
        x_lo, x_hi = self._x_of(self._lo), self._x_of(self._hi)
        if self._has_values:
            p.setBrush(QColor(COLORS["badge_bg"]))  # つまみの間（OK の範囲）：濃い朽葉色
            p.drawRect(QRectF(x_lo, t.top(), max(x_hi - x_lo, 1.0), t.height()))
        # 目盛り（範囲に合わせて 0.5 / 1 / 5 V ごと）
        p.setFont(mono_font(11))
        p.setPen(QColor(COLORS["text_sub"]))
        span = self._vmax - self._vmin
        step = 0.5 if span <= 3 else 1.0 if span <= 10 else 5.0
        fm = QFontMetrics(p.font())
        ticks = []
        v = math.ceil(self._vmin / step) * step
        while v <= self._vmax + 1e-9:
            ticks.append(round(v, 3))
            v += step
        for i, v in enumerate(ticks):
            x = self._x_of(v)
            p.drawLine(QPointF(x, t.bottom() + 2), QPointF(x, t.bottom() + 6))
            label = f"{v:g}" + (" V" if i == len(ticks) - 1 else "")
            w = fm.horizontalAdvance(label)
            # 両端の文字が部品の外にはみ出さないよう、内側に寄せる
            lx = min(max(x - w / 2, self.contentsRect().left()), self.contentsRect().right() - w)
            p.drawText(QPointF(lx, t.bottom() + 6 + fm.ascent()), label)
        # つまみ
        for which in ("lo", "hi"):
            c = self.handle_center(which)
            # つまみ：クリーム色に朽葉色の縁。いま操作しているつまみは縁を濃く
            active = self.hasFocus() and which == self._active
            if self._has_values:
                ring = QColor(COLORS["text"]) if active else QColor(COLORS["badge_bg"])
            else:
                ring = QColor(COLORS["input_border"])
            p.setPen(QPen(ring, 2.5))
            p.setBrush(QColor(COLORS["input_bg"]))
            p.drawEllipse(c, self.HANDLE_R, self.HANDLE_R)
        p.end()

    # ---- 操作
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        x = event.position().x()
        d_lo = abs(x - self._x_of(self._lo))
        d_hi = abs(x - self._x_of(self._hi))
        if d_lo == d_hi:  # 重なっているときは、押した側に動かせる方
            which = "lo" if x < self._x_of(self._lo) else "hi"
        else:
            which = "lo" if d_lo < d_hi else "hi"
        self._drag = which
        self.setFocus()
        self._set_from_user(which, self._v_of(x))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag:
            self._set_from_user(self._drag, self._v_of(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag and self._has_values:
            # 端まで動かしていたら、範囲を広げてさらに先へ動かせるようにする
            edge = (self._vmax - self._vmin) * 0.03
            if self._lo <= self._vmin + edge or self._hi >= self._vmax - edge:
                self._fit_range()
                self.update()
        self._drag = None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        step = self.DRAG_STEP_V if event.modifiers() & Qt.ShiftModifier else self.KEY_STEP_V
        if event.key() in (Qt.Key_Left, Qt.Key_Down):
            self._set_from_user(self._active, (self._lo if self._active == "lo" else self._hi) - step)
        elif event.key() in (Qt.Key_Right, Qt.Key_Up):
            self._set_from_user(self._active, (self._lo if self._active == "lo" else self._hi) + step)
        else:
            super().keyPressEvent(event)
            return
        if not (self._vmin <= self._lo and self._hi <= self._vmax):
            self._fit_range()
            self.update()


class LotDialog(QDialog):
    """新しいロット（「開始」）と、ロット設定（「変更」）の入力。

    必須項目が空・下限 ≧ 上限のときは、その欄を赤枠にして「開始」を押せない（F-02）。
    ロット設定では開始番号は変えられない（番号が変わってしまうため）。
    """

    def __init__(self, parent, initial: LotInput, editing: bool = False) -> None:
        super().__init__(parent)
        self.editing = editing
        self.setWindowTitle("ロット設定" if editing else "新しいロット")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 12)
        lay.setSpacing(10)
        lay.setSizeConstraint(QVBoxLayout.SetFixedSize)  # 中身の大きさに合わせる（メモ欄が間延びしない）

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.name_edit = QLineEdit(initial.lot_name)
        self.name_edit.setMaxLength(LOT_TEXT_MAX_LEN)
        self.name_edit.setFont(ui_font(14))
        # 測定者は名前ではなく社員番号
        self.operator_edit = QLineEdit(initial.operator)
        self.operator_edit.setMaxLength(EMPLOYEE_ID_MAX_LEN)
        self.operator_edit.setFont(mono_font(14))
        self.operator_edit.setPlaceholderText("例 123456")
        self.operator_edit.setToolTip("測定者の社員番号（半角の英数字。名前は入れません）")
        _latin_only(self.operator_edit)
        self.lower_edit = QLineEdit(_limit_text(initial.lower_V))
        self.upper_edit = QLineEdit(_limit_text(initial.upper_V))
        for w in (self.lower_edit, self.upper_edit):
            w.setFont(mono_font(14))
            w.setPlaceholderText("例 3.600")
            w.setMaxLength(6)
            _latin_only(w)
        self.range_bar = LimitRangeBar()
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 9_999_999)
        self.start_spin.setValue(initial.start_no)
        self.start_spin.setFont(mono_font(14))
        _style_spin(self.start_spin)
        self.start_spin.setEnabled(not editing)
        self.memo_edit = QPlainTextEdit(initial.memo)
        self.memo_edit.setFont(ui_font(14))
        self.memo_edit.setFixedHeight(QFontMetrics(self.memo_edit.font()).lineSpacing() * 3 + 18)  # 3 行
        self.memo_edit.setTabChangesFocus(True)

        # 並び：ロット名 → 開始番号 → 測定者 → 電圧（下限・上限・バー） → メモ
        rows = (
            ("ロット名（必須）", self.name_edit),
            ("開始番号", self.start_spin),
            ("測定者（社員番号）", self.operator_edit),
            ("下限 [V]（必須）", self.lower_edit),
            ("上限 [V]（必須）", self.upper_edit),
            ("", self.range_bar),
            ("メモ", self.memo_edit),
        )
        for r, (text, w) in enumerate(rows):
            if text:
                grid.addWidget(sub_label(text), r, 0, Qt.AlignTop if w is self.memo_edit else Qt.AlignVCenter)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        # Tab キーで移る順も、上の並びと同じにする
        order = [w for _, w in rows]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)

        self.error_label = QLabel()
        self.error_label.setProperty("role", "sub")
        self.error_label.setFont(ui_font(12))
        lay.addWidget(self.error_label)
        if editing:
            lay.addWidget(sub_label("上下限を変えると、記録済みの判定もやり直します", 12))

        self.buttons = QDialogButtonBox()
        self.ok_button = self.buttons.addButton("変更" if editing else "開始", QDialogButtonBox.AcceptRole)
        self.buttons.addButton("キャンセル", QDialogButtonBox.RejectRole)
        self.ok_button.setDefault(True)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        for w in (self.name_edit, self.operator_edit, self.lower_edit, self.upper_edit):
            w.textChanged.connect(self._validate)
        # バーと入力欄を連動させる
        self.range_bar.valuesChanged.connect(self._on_bar_moved)
        for w in (self.lower_edit, self.upper_edit):
            w.textChanged.connect(self._on_limit_typed)
        self._on_limit_typed()
        self._validate()

    def _on_bar_moved(self, lo: float, hi: float) -> None:
        """バーを動かした → 入力欄を書き換える（入力欄からバーへは戻さない）。"""
        self._syncing = True
        try:
            self.lower_edit.setText(f"{lo:.3f}")
            self.upper_edit.setText(f"{hi:.3f}")
        finally:
            self._syncing = False

    def _on_limit_typed(self, *_args) -> None:
        """入力欄を書き換えた → 正しい値ならバーのつまみを動かす。"""
        if getattr(self, "_syncing", False):
            return
        lo = parse_limit(self.lower_edit.text())
        hi = parse_limit(self.upper_edit.text())
        if lo is not None and hi is not None and lo < hi:
            self.range_bar.set_values(lo, hi)

    def _validate(self) -> bool:
        name_ok = bool(self.name_edit.text().strip())
        id_ok = is_valid_employee_id(self.operator_edit.text())
        lo = parse_limit(self.lower_edit.text())
        hi = parse_limit(self.upper_edit.text())
        order_ok = lo is not None and hi is not None and lo < hi
        _set_invalid(self.name_edit, not name_ok)
        _set_invalid(self.operator_edit, not id_ok)
        _set_invalid(self.lower_edit, lo is None or (hi is not None and not order_ok))
        _set_invalid(self.upper_edit, hi is None or (lo is not None and not order_ok))
        if not name_ok:
            msg = "ロット名を入れてください"
        elif not id_ok:
            msg = "測定者は社員番号（半角の英数字）で入れてください。名前は入れません"
        elif lo is None or hi is None:
            msg = "下限・上限を 0〜36 V、小数 3 桁までで入れてください"
        elif not order_ok:
            msg = "下限は上限より小さくしてください"
        else:
            msg = ""
        self.error_label.setText(msg)
        ok = name_ok and id_ok and order_ok
        self.ok_button.setEnabled(ok)
        return ok

    def _accept(self) -> None:
        if self._validate():
            self.accept()

    def value(self) -> LotInput:
        return LotInput(
            lot_name=self.name_edit.text().strip(),
            operator=normalize_employee_id(self.operator_edit.text()),
            lower_V=parse_limit(self.lower_edit.text()) or 0.0,
            upper_V=parse_limit(self.upper_edit.text()) or 0.0,
            start_no=self.start_spin.value(),
            memo=self.memo_edit.toPlainText(),
        )


def _style_spin(w) -> None:
    """数値の入力欄の文字の位置を、ほかの入力欄（左余白 8px）にそろえる。

    注意：setButtonSymbols(NoButtons) は使わない。スタイルシートと組み合わせると、
    Qt が中の文字欄を幅 1px につぶして数値が見えなくなる（矢印は app.qss で幅 0 にして隠す）。
    """
    w.lineEdit().setTextMargins(7, 0, 0, 0)


def _limit_text(v: float) -> str:
    return f"{v:.3f}" if v > 0 else ""


class SettingsDialog(QDialog):
    """接続判定電圧・安定幅・二重記録の確認・記録時の音。"""

    def __init__(self, parent, settings: Settings) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 12)
        lay.setSpacing(10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.05, 10.0)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setSingleStep(0.1)
        self.threshold_spin.setSuffix(" V")
        self.threshold_spin.setValue(settings.connect_threshold_V)
        self.stable_spin = QDoubleSpinBox()
        self.stable_spin.setRange(0.5, 500.0)
        self.stable_spin.setDecimals(1)
        self.stable_spin.setSingleStep(1.0)
        self.stable_spin.setSuffix(" mV")
        self.stable_spin.setValue(settings.stable_width_mV)
        for w in (self.threshold_spin, self.stable_spin):
            w.setFont(mono_font(14))
            _style_spin(w)
        grid.addWidget(sub_label("接続判定電圧"), 0, 0)
        grid.addWidget(self.threshold_spin, 0, 1)
        grid.addWidget(sub_label("これ未満は「バッテリ未接続」", 11), 0, 2)
        grid.addWidget(sub_label("安定幅"), 1, 0)
        grid.addWidget(self.stable_spin, 1, 1)
        grid.addWidget(sub_label("「安定」と「ばらつき大」の基準", 11), 1, 2)
        lay.addLayout(grid)
        self.duplicate_check = QCheckBox("バッテリを外さずに 2 回記録しようとしたら確認する")
        self.duplicate_check.setChecked(settings.confirm_duplicate)
        self.ng_check = QCheckBox("NG のときに「記録する／もう一度測定」を確認する")
        self.ng_check.setChecked(settings.confirm_ng)
        self.sound_check = QCheckBox("記録したときに音を鳴らす（OK と NG で違う音）")
        self.sound_check.setChecked(settings.sound)
        lay.addWidget(self.duplicate_check)
        lay.addWidget(self.ng_check)
        lay.addWidget(self.sound_check)
        buttons = QDialogButtonBox()
        ok = buttons.addButton("OK", QDialogButtonBox.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.RejectRole)
        ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def apply_to(self, s: Settings) -> None:
        s.connect_threshold_V = round(self.threshold_spin.value(), 2)
        s.stable_width_mV = round(self.stable_spin.value(), 1)
        s.confirm_duplicate = self.duplicate_check.isChecked()
        s.confirm_ng = self.ng_check.isChecked()
        s.sound = self.sound_check.isChecked()


class ExportDialog(QDialog):
    """出力（F-09）：保存先フォルダとファイル名。

    ファイル名の初期値は `<ロット名>_<番号始め>-<番号終わり>_電圧記録_<日付>`。変えることもできる。
    実際に保存する名前（使えない文字は _、同じ名前があれば _2 …）と、上書きになるかを下に出す。
    resolve(folder, base) は (実際の基名, 前回の出力を上書きするか) を返す。
    """

    def __init__(self, parent, folder: str, default_base: str, resolve: Callable[[Path, str], tuple[str, bool]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("出力（CSV・Excel）")
        self.setMinimumWidth(620)
        self._default = default_base
        self._resolve = resolve
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 12)
        lay.setSpacing(8)

        lay.addWidget(sub_label("保存先フォルダ"))
        row = QHBoxLayout()
        self.folder_edit = QLineEdit(folder)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setFont(ui_font(13))
        browse = QPushButton("参照…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(browse)
        lay.addLayout(row)

        lay.addWidget(sub_label("ファイル名（.csv と .xlsx の 2 つを保存します）"))
        row = QHBoxLayout()
        self.name_edit = QLineEdit(default_base)
        self.name_edit.setFont(mono_font(13))
        self.name_edit.setMaxLength(120)
        self.reset_button = QPushButton("初期値に戻す")
        self.reset_button.setToolTip("ロット名_番号始め-番号終わり_電圧記録_日付")
        self.reset_button.clicked.connect(lambda: self.name_edit.setText(self._default))
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.reset_button)
        lay.addLayout(row)

        self.preview_label = QLabel()
        self.preview_label.setFont(ui_font(12))
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.preview_label)

        buttons = QDialogButtonBox()
        self.ok_button = buttons.addButton("保存", QDialogButtonBox.AcceptRole)
        buttons.addButton("キャンセル", QDialogButtonBox.RejectRole)
        self.ok_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.name_edit.textChanged.connect(self._update_preview)
        self._update_preview()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "保存先フォルダ", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self._update_preview()

    def _update_preview(self) -> None:
        from ..filename import sanitize

        name = sanitize(self.name_edit.text().strip())
        self.ok_button.setEnabled(bool(name) and bool(self.folder_edit.text()))
        if not name:
            self.preview_label.setText("ファイル名を入れてください")
            return
        final, overwrite = self._resolve(Path(self.folder_edit.text()), name)
        text = f"保存するファイル：{final}.csv ／ {final}.xlsx"
        if overwrite:
            text += "\n（このロットの前回の出力を上書きします）"
        elif final != name:
            text += f"\n（同じ名前のファイルがあるため「{final}」にします）"
        elif name != self.name_edit.text().strip():
            text += "\n（ファイル名に使えない文字・空白は _ にします）"
        self.preview_label.setText(text)

    @property
    def folder(self) -> str:
        return self.folder_edit.text()

    @property
    def base(self) -> str:
        return self.name_edit.text().strip()
