"""ダイアログ：ロットの開始・ロット設定（F-02）と、設定（Q-03, Q-04, Q-06, Q-08）。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
)

from ..settings import Settings
from ..theme import mono_font, ui_font
from .widgets import sub_label

LOT_TEXT_MAX_LEN = 30
MAX_VOLTAGE_V = 36.0  # INA226 のバス電圧の範囲（5.1）
_LIMIT_RE = re.compile(r"^\d{1,2}(\.\d{0,3})?$")  # 小数 3 桁まで


@dataclass(frozen=True)
class LotInput:
    lot_name: str
    operator: str
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


def _set_invalid(w, invalid: bool) -> None:
    if bool(w.property("invalid")) != invalid:
        w.setProperty("invalid", invalid)
        w.style().unpolish(w)
        w.style().polish(w)


class LotDialog(QDialog):
    """新しいロット（「開始」）と、ロット設定（「変更」）の入力。

    必須項目が空・下限 ≧ 上限のときは、その欄を赤枠にして「開始」を押せない（F-02）。
    ロット設定では開始番号は変えられない（番号が変わってしまうため）。
    """

    def __init__(self, parent, initial: LotInput, editing: bool = False) -> None:
        super().__init__(parent)
        self.editing = editing
        self.setWindowTitle("ロット設定" if editing else "新しいロット")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 12)
        lay.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.name_edit = QLineEdit(initial.lot_name)
        self.operator_edit = QLineEdit(initial.operator)
        for w in (self.name_edit, self.operator_edit):
            w.setMaxLength(LOT_TEXT_MAX_LEN)
            w.setFont(ui_font(14))
        self.lower_edit = QLineEdit(_limit_text(initial.lower_V))
        self.upper_edit = QLineEdit(_limit_text(initial.upper_V))
        for w in (self.lower_edit, self.upper_edit):
            w.setFont(mono_font(14))
            w.setPlaceholderText("例 3.600")
            w.setMaxLength(6)
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 9_999_999)
        self.start_spin.setValue(initial.start_no)
        self.start_spin.setFont(mono_font(14))
        _style_spin(self.start_spin)
        self.start_spin.setEnabled(not editing)
        self.memo_edit = QPlainTextEdit(initial.memo)
        self.memo_edit.setFont(ui_font(14))
        self.memo_edit.setFixedHeight(QFontMetrics(self.memo_edit.font()).lineSpacing() * 3 + 18)
        self.memo_edit.setTabChangesFocus(True)

        rows = (
            ("ロット名（必須）", self.name_edit),
            ("測定者", self.operator_edit),
            ("下限 [V]（必須）", self.lower_edit),
            ("上限 [V]（必須）", self.upper_edit),
            ("開始番号", self.start_spin),
            ("メモ", self.memo_edit),
        )
        for r, (text, w) in enumerate(rows):
            grid.addWidget(sub_label(text), r, 0, Qt.AlignTop if w is self.memo_edit else Qt.AlignVCenter)
            grid.addWidget(w, r, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

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

        for w in (self.name_edit, self.lower_edit, self.upper_edit):
            w.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> bool:
        name_ok = bool(self.name_edit.text().strip())
        lo = parse_limit(self.lower_edit.text())
        hi = parse_limit(self.upper_edit.text())
        order_ok = lo is not None and hi is not None and lo < hi
        _set_invalid(self.name_edit, not name_ok)
        _set_invalid(self.lower_edit, lo is None or (hi is not None and not order_ok))
        _set_invalid(self.upper_edit, hi is None or (lo is not None and not order_ok))
        if not name_ok:
            msg = "ロット名を入れてください"
        elif lo is None or hi is None:
            msg = "下限・上限を 0〜36 V、小数 3 桁までで入れてください"
        elif not order_ok:
            msg = "下限は上限より小さくしてください"
        else:
            msg = ""
        self.error_label.setText(msg)
        ok = name_ok and order_ok
        self.ok_button.setEnabled(ok)
        return ok

    def _accept(self) -> None:
        if self._validate():
            self.accept()

    def value(self) -> LotInput:
        return LotInput(
            lot_name=self.name_edit.text().strip(),
            operator=self.operator_edit.text().strip(),
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
        self.sound_check = QCheckBox("記録したときに音を鳴らす（OK と NG で違う音）")
        self.sound_check.setChecked(settings.sound)
        lay.addWidget(self.duplicate_check)
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
        s.sound = self.sound_check.isChecked()
