"""ダイアログ：ロットの開始・ロット設定（F-02）と、設定（Q-03, Q-04, Q-06, Q-08）。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
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
)

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
            ("測定者（社員番号）", self.operator_edit),
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

        for w in (self.name_edit, self.operator_edit, self.lower_edit, self.upper_edit):
            w.textChanged.connect(self._validate)
        self._validate()

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
