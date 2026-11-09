"""メインウィンドウ（仕様 6・7 章）。接続 → ロット開始 → 記録（5 回測定の中央値）→ 出力 の流れをまとめる。

ヘッダー・左パネル・ステータスバーの組み立て方は INA226_Monitor v1.2.1 の main_window.py にならう。
"""

from __future__ import annotations

import logging
import queue
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__, sound
from ..export import Conditions, ExportError, default_base, export_lot, resolve_base
from ..measure import BatteryMonitor, BatteryState, CollectResult, SampleCollector
from ..paths import AppPaths, default_save_dir
from ..protocol import (
    CHECKER_INTERVAL_MS,
    DEFAULT_INTERVAL_MS,
    LineKind,
    parse_firmware_banner,
    parse_interval_ack,
)
from ..records import (
    DATETIME_FMT,
    Measurement,
    LotInfo,
    RecordBook,
    compute_stats,
    judge,
    make_record,
    summary_text,
)
from ..serial_io import PortOpenError, SerialReader, list_ports, open_port
from ..settings import Settings
from ..theme import app_icon, mono_font, ui_font
from ..workfile import WorkFile, cleanup_finished, find_unfinished, finish_lot, load_lot, new_lot_id, read_records
from .dialogs import ExportDialog, LotDialog, LotInput, SettingsDialog
from .plot_panel import DistributionPlot
from .record_table import RecordTablePanel
from .widgets import ElideLabel, FitLabel, NumberCard, divider, sub_label

log = logging.getLogger(__name__)

POLL_INTERVAL_MS = 20  # 受信キューを見る間隔（記録の応答を速くするため短く）
DISPLAY_INTERVAL_MS = 200  # 電圧表示の更新間隔（F-03）
NO_DATA_TIMEOUT_S = 5.0
LEFT_PANEL_WIDTH = 360

MSG_NO_PORT = "Arduino が見つかりません。USB の接続を確認してください"
MSG_NOT_CONNECTED = "Arduino に接続してください（ポートを選んで「接続」）"
MSG_NO_LOT = "ロットを始めてください（「新しいロット」）"
MSG_NO_BATTERY = "バッテリが接続されていません"
MSG_NO_DATA = "データが届いていません"
MSG_DISCONNECTED = "Arduino との接続が切れました"
MSG_OLD_SKETCH = "Arduino のスケッチが周期変更に対応していません（50 ms で記録します）"
MSG_DUPLICATE = "前回と同じバッテリのようです。もう一度記録しますか？"
MSG_WORKFILE_ERROR = "作業ファイルに書き込めません"
RECORD_TEXT = "● 記録（Space）"
CONTINUOUS_TEXT = "● 連続測定中（自動で記録）"

STATE_TEXT = {
    BatteryState.NO_DATA: ("データ待ち", "none"),
    BatteryState.NONE: ("バッテリ未接続", "none"),
    BatteryState.UNSTABLE: ("変動中", "unstable"),
    BatteryState.STABLE: ("安定", "stable"),
}

_TEXT_INPUTS = (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox)
_RECORD_KEYS = (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter)


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths, mock: bool = False, mock_speed: float = 1.0) -> None:
        super().__init__()
        self.paths = paths
        self.mock = mock
        self.mock_speed = mock_speed
        self.settings = Settings.load(paths.settings_file)
        if not self.settings.save_dir:
            self.settings.save_dir = str(default_save_dir())

        # 接続
        self.reader: SerialReader | None = None
        self.rx_queue: queue.Queue = queue.Queue()
        self._device = ""
        self.interval_ack: tuple[int, int] | None = None
        self.interval_no_ack = False
        self.firmware_version: int | None = None
        self.invalid_count = 0
        self._last_data_mono = 0.0
        self._no_data_shown = False
        self._last_display = 0.0
        # 測定
        self.monitor = BatteryMonitor(self.settings.connect_threshold_V, self.settings.stable_width_mV / 1000)
        self.collector: SampleCollector | None = None
        self._pending: tuple[Measurement, datetime] | None = None  # NG の確認待ちの測定結果
        self.continuous = False  # 連続測定モード
        self._pressed_mono = 0.0
        self.last_latency_s: float | None = None  # 記録ボタンから結果表示まで（AC-15 の確認用）
        self.last_collect_s: float | None = None  # そのうち 5 個を集めるのにかかった時間
        self.last_save_s: float | None = None  # そのうち作業ファイルの保存にかかった時間
        self.remeasure_no: int | None = None
        # ロット
        self.lot: LotInfo | None = None
        self.book: RecordBook | None = None
        self.workfile: WorkFile | None = None
        self._saved_folder: Path | None = None

        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1280, 800)
        self.setMinimumSize(1024, 700)
        self._build_ui()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(self._poll)

        QShortcut(QKeySequence.Undo, self, activated=self.on_undo)  # Ctrl+Z
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.cancel_remeasure)
        QApplication.instance().installEventFilter(self)

        self.refresh_ports()
        self._show_lot()
        self._update_display(force=True)
        self.set_message("準備完了")

    # ================================================================ UI
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 8)
        body.setSpacing(12)
        body.addWidget(self._build_left())
        right = QVBoxLayout()
        right.setSpacing(12)
        self.table = RecordTablePanel()
        self.table.view.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        self.plot = DistributionPlot()
        self.plot.limits_dragging.connect(self._on_limits_dragging)
        self.plot.limits_changed.connect(self.on_limits_dragged)
        right.addWidget(self.table, 55)
        right.addWidget(self.plot, 45)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.new_lot_button = self._button("新しいロット", "newLotButton", self.on_new_lot, 15)
        self.export_button = self._button("出力（CSV・Excel）", "exportButton", self.on_export, 15)
        self.end_lot_button = self._button("ロット終了", "endLotButton", self.on_end_lot, 15)
        for b in (self.new_lot_button, self.export_button, self.end_lot_button):
            b.setMinimumWidth(130)
            actions.addWidget(b)
        right.addLayout(actions)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("summaryLabel")
        self.summary_label.setFont(mono_font(14, bold=True))
        self.summary_label.setFixedHeight(30)
        self.summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.summary_label)
        self.setCentralWidget(central)
        self._build_status_bar()

    def _button(self, text: str, name: str, slot, px: int = 14, bold: bool = True) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName(name)
        b.setFont(ui_font(px, bold=bold))
        b.setFocusPolicy(Qt.NoFocus)  # Space／Enter は記録に使うので、ボタンにフォーカスを持たせない
        b.clicked.connect(slot)
        return b

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("header")
        header.setFixedHeight(56)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 8, 16, 12)
        lay.setSpacing(10)
        decor = QHBoxLayout()
        decor.setSpacing(3)
        for _ in range(4):  # 端子台を模した飾り（INA226_Monitor と同じ）
            bar = QFrame()
            bar.setObjectName("headerDecor")
            bar.setFixedSize(5, 20)
            decor.addWidget(bar)
        lay.addLayout(decor)
        name = QLabel(APP_NAME)
        name.setObjectName("appName")
        name.setFont(ui_font(19, bold=True))
        lay.addWidget(name)
        lay.addSpacing(12)
        self.lot_label = ElideLabel()
        self.lot_label.setObjectName("lotLabel")
        self.lot_label.setFont(ui_font(14, bold=True))
        lay.addWidget(self.lot_label, 1)
        self.settings_button = self._button("設定", "headerButton", self.on_settings, 13, bold=False)
        lay.addWidget(self.settings_button)
        lay.addSpacing(6)
        port_label = QLabel("ポート")
        port_label.setObjectName("portLabel")
        port_label.setFont(ui_font(13))
        lay.addWidget(port_label)
        self.port_combo = QComboBox()
        self.port_combo.setFont(ui_font(14))
        self.port_combo.setMinimumWidth(180)
        self.port_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.port_combo.setMinimumContentsLength(18)
        self.port_combo.setFocusPolicy(Qt.NoFocus)
        self.port_combo.currentIndexChanged.connect(lambda *_: self._update_buttons())
        lay.addWidget(self.port_combo)
        self.refresh_button = self._button("更新", "refreshButton", self.refresh_ports, 14, bold=False)
        lay.addWidget(self.refresh_button)
        self.connect_button = self._button("接続", "refreshButton", self.on_connect_toggle, 14)
        self.connect_button.setMinimumWidth(72)
        lay.addWidget(self.connect_button)
        return header

    def _build_left(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # 0. いま記録する番号（いちばん目立つ所に大きく）
        self.number_card = NumberCard()
        lay.addWidget(self.number_card)

        # 1. 「電圧」見出しと接続状態
        row = QHBoxLayout()
        heading = QLabel("電圧")
        heading.setFont(ui_font(13, bold=True))
        row.addWidget(heading)
        row.addStretch(1)
        self.state_label = QLabel()
        self.state_label.setObjectName("batteryState")
        self.state_label.setFont(ui_font(14, bold=True))
        row.addWidget(self.state_label)
        lay.addLayout(row)

        # 2. 電圧の数値バッジ
        badge = QFrame()
        badge.setObjectName("voltageBadge")
        bl = QHBoxLayout(badge)
        bl.setContentsMargins(14, 4, 14, 4)
        bl.setSpacing(8)
        self.voltage_label = FitLabel(mono_font(46, bold=True), min_px=24)
        bl.addWidget(self.voltage_label, 1)
        unit = QLabel("V")
        unit.setFont(ui_font(20, bold=True))
        bl.addWidget(unit, 0, Qt.AlignBottom)
        lay.addWidget(badge)

        # 4. 連続測定モードの切り替えと記録ボタン
        self.continuous_button = self._button(
            "連続測定モード：オフ", "continuousButton", lambda: self.set_continuous(not self.continuous), 13, bold=False
        )
        self.continuous_button.setCheckable(True)
        self.continuous_button.setToolTip("オンにすると、電圧が安定したら自動で記録します。バッテリを外して 0 V になると次を記録できます")
        self.continuous_button.setMinimumHeight(34)
        lay.addWidget(self.continuous_button)
        self.record_button = self._button(RECORD_TEXT, "recordButton", self.on_record, 22)
        for b in (self.record_button, self.continuous_button):
            # 文字の長さで左パネル（幅固定）を広げない
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumWidth(0)
        self.record_button.setFixedHeight(72)
        lay.addWidget(self.record_button)

        # 6. 取り消し・測り直し
        row = QHBoxLayout()
        row.setSpacing(8)
        self.undo_button = self._button("取り消す（Ctrl+Z）", "undoButton", self.on_undo, 13, bold=False)
        self.remeasure_button = self._button("この番号を測り直す", "remeasureButton", self.on_remeasure, 13, bold=False)
        for b in (self.undo_button, self.remeasure_button):
            b.setMinimumHeight(38)
            # 文字の長さで左パネルの幅を広げない（左パネルは幅固定）
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            b.setMinimumWidth(0)
            row.addWidget(b, 1)
        lay.addLayout(row)

        # 7. 下限・上限
        lay.addWidget(divider())
        row = QHBoxLayout()
        self.limits_label = QLabel()
        self.limits_label.setFont(mono_font(14, bold=True))
        row.addWidget(sub_label("判定"))
        row.addWidget(self.limits_label, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.lot_detail_label = sub_label("", 12)
        self.lot_detail_label.setWordWrap(True)
        row.addWidget(self.lot_detail_label, 1)
        self.lot_settings_button = self._button("ロット設定…", "lotSettingsButton", self.on_lot_settings, 13, bold=False)
        row.addWidget(self.lot_settings_button, 0, Qt.AlignTop)
        lay.addLayout(row)
        lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("leftScroll")
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(LEFT_PANEL_WIDTH)
        scroll.setFocusPolicy(Qt.NoFocus)
        self.left_scroll = scroll
        return scroll

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        sb.setSizeGripEnabled(False)
        self.conn_label = QLabel()
        self.interval_label = QLabel()
        self.invalid_label = QLabel()
        self.saved_label = QLabel()
        self.msg_label = QLabel()
        self.msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.open_folder_button = QPushButton("フォルダを開く")
        self.open_folder_button.setFocusPolicy(Qt.NoFocus)
        self.open_folder_button.setVisible(False)
        self.open_folder_button.clicked.connect(self._open_saved_folder)
        for w in (self.conn_label, self.interval_label, self.invalid_label, self.saved_label, self.msg_label,
                  self.open_folder_button):
            w.setFont(ui_font(12))
        for w in (self.conn_label, self.interval_label, self.invalid_label, self.saved_label):
            sb.addWidget(w)
        sb.addPermanentWidget(self.msg_label)
        sb.addPermanentWidget(self.open_folder_button)
        self.setStatusBar(sb)

    # ================================================================ メッセージ（テストで差し替えられるよう 1 か所に）
    def set_message(self, text: str, error: bool = False) -> None:
        self.msg_label.setText(text)
        if bool(self.msg_label.property("error")) != error:
            self.msg_label.setProperty("error", error)
            self.msg_label.style().unpolish(self.msg_label)
            self.msg_label.style().polish(self.msg_label)
        if text:
            self.open_folder_button.setVisible(False)

    def _warn(self, text: str) -> None:
        QMessageBox.warning(self, APP_NAME, text)

    def _info(self, text: str) -> None:
        QMessageBox.information(self, APP_NAME, text)

    def _ask(self, text: str, choices: list[tuple[str, str]], escape: str) -> str:
        """ボタンを選ばせる。choices は (キー, ボタンの文字)。閉じたら escape のキー。"""
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Question)
        box.setText(text)
        buttons = {box.addButton(label, QMessageBox.ActionRole): key for key, label in choices}
        box.setDefaultButton(next(iter(buttons)))
        for b, key in buttons.items():
            if key == escape:
                box.setEscapeButton(b)
        box.exec()
        return buttons.get(box.clickedButton(), escape)

    def _ask_export(self, default: str) -> tuple[str, str] | None:
        """出力先フォルダとファイル名（基名）を聞く。キャンセルなら None。"""
        lot = self.lot
        dlg = ExportDialog(self, self.settings.save_dir, default, lambda folder, base: resolve_base(folder, lot, base))
        if dlg.exec() != ExportDialog.Accepted:
            return None
        return dlg.folder, dlg.base

    def _ask_lot(self, initial: LotInput, editing: bool) -> LotInput | None:
        dlg = LotDialog(self, initial, editing)
        return dlg.value() if dlg.exec() == LotDialog.Accepted else None

    # ================================================================ 表示更新
    @property
    def busy(self) -> bool:
        """測定中（5 個を集めている）か、NG の確認待ち。この間はほかの操作をさせない。"""
        return self.collector is not None or self._pending is not None

    def _update_buttons(self) -> None:
        connected = self.reader is not None
        measuring = self.busy
        has_lot = self.lot is not None and self.book is not None
        n = len(self.book) if self.book else 0
        battery = self.monitor.connected
        selected = self.table.selected_no() is not None
        self.record_button.setEnabled(connected and has_lot and battery and not measuring)
        self.undo_button.setEnabled(has_lot and n > 0 and not measuring)
        # 測り直しを始めるのは接続中だけ。やめるのはいつでもできる
        self.remeasure_button.setEnabled(
            has_lot and not measuring and (self.remeasure_no is not None or (connected and selected))
        )
        self.export_button.setEnabled(has_lot and n > 0 and not measuring)
        self.new_lot_button.setEnabled(not measuring)
        self.end_lot_button.setEnabled(has_lot and not measuring)
        self.lot_settings_button.setEnabled(has_lot and not measuring)
        self.plot.set_adjust_enabled(has_lot and not measuring)
        self.settings_button.setEnabled(not measuring)
        self.port_combo.setEnabled(not connected and not measuring)
        self.refresh_button.setEnabled(not connected and not measuring)
        self.connect_button.setEnabled(not measuring and (connected or bool(self.port_combo.currentData())))
        self.connect_button.setText("切断" if connected else "接続")
        self.continuous_button.setEnabled(connected and has_lot and not measuring)
        if self.continuous_button.isChecked() != self.continuous:
            self.continuous_button.setChecked(self.continuous)
        self.continuous_button.setText(f"連続測定モード：{'オン' if self.continuous else 'オフ'}")
        self._update_number()
        if self.remeasure_no is not None:
            self.record_button.setText(f"● No.{self.remeasure_no} を測り直す（Space）")
            self.remeasure_button.setText("測り直しをやめる")
        else:
            self.record_button.setText(CONTINUOUS_TEXT if self.continuous else RECORD_TEXT)
            self.remeasure_button.setText("この番号を測り直す")

    def _update_number(self) -> None:
        book = self.book
        if book is None:
            self.number_card.set_none()
        elif self.remeasure_no is not None:
            self.number_card.set_remeasure(self.remeasure_no, book.next_no)
        else:
            self.number_card.set_next(book.next_no, len(book))

    def _update_display(self, force: bool = False) -> None:
        """電圧表示・接続状態・ステータス欄（200 ms ごと）。"""
        now = time.monotonic()
        if not force and now - self._last_display < DISPLAY_INTERVAL_MS / 1000:
            return
        self._last_display = now
        if self.reader is None:
            self.voltage_label.setText("---")
            text, state = "Arduino 未接続", "none"
        else:
            v = self.monitor.last_V
            self.voltage_label.setText("---" if v is None else f"{v:.4f}")
            text, state = STATE_TEXT[self.monitor.state]
        if self.collector is not None:
            text = "測定中…"
        elif self._pending is not None:
            text = "NG の確認中"
        if self.state_label.text() != text or self.state_label.property("state") != state:
            self.state_label.setText(text)
            self.state_label.setProperty("state", state)
            self.state_label.style().unpolish(self.state_label)
            self.state_label.style().polish(self.state_label)
        self._update_status()
        self._update_buttons()

    def _update_status(self) -> None:
        self.conn_label.setText(f"接続中：{self._device}" if self.reader else "未接続")
        if self.reader is None:
            interval = "周期 --"
        elif self.interval_ack is not None:
            interval = f"周期 {self.interval_ack[0]} ms"
        elif self.interval_no_ack:
            interval = f"周期 {DEFAULT_INTERVAL_MS} ms（スケッチ未対応）"
        else:
            interval = "周期 確認中"
        self.interval_label.setText(interval)
        self.invalid_label.setText(f"不正行：{self.invalid_count:,}")
        t = self.workfile.last_saved if self.workfile else None
        saved = time.strftime("%H:%M:%S", time.localtime(t)) if t else "--:--:--"
        mark = "（未保存あり）" if self.workfile and self.workfile.dirty else ""
        self.saved_label.setText(f"作業ファイル保存：{saved}{mark}")

    def _show_lot(self) -> None:
        """ロットが変わったとき：ヘッダー・判定・一覧・グラフ・集計を描き直す。"""
        lot, book = self.lot, self.book
        if lot is None or book is None:
            self.lot_label.setText("ロットなし")
            self.limits_label.setText("---")
            self.lot_detail_label.setText("「新しいロット」で始めます")
            self.table.model.set_book(RecordBook())
            self.plot.set_limits(None, None)
        else:
            self.lot_label.setText(f"{lot.lot_name} ／ {lot.limits_text()}")
            self.limits_label.setText(f"{lot.lower_V:.3f} 〜 {lot.upper_V:.3f} V")
            who = f"測定者（社員番号）{lot.operator}　" if lot.operator else ""
            self.lot_detail_label.setText(f"{who}開始 {lot.started_at}")
            self.table.model.set_book(book)
            self.plot.set_limits(lot.lower_V, lot.upper_V)
            self.table.select_no(book.last.no if book.last else None)
        self._refresh_records()
        self._update_buttons()
        self._update_status()

    def _refresh_records(self) -> None:
        book = self.book
        self._update_number()
        self.plot.refresh(book)
        self.summary_label.setText(summary_text(compute_stats(book.records)) if book else "")

    # ================================================================ F-01 ポート・接続
    def refresh_ports(self) -> None:
        current = self.port_combo.currentData() or self.settings.last_port
        self.port_combo.clear()
        ports = list_ports(include_mock=self.mock)
        for p in ports:
            self.port_combo.addItem(p.label, p.device)
        if not ports:
            self.port_combo.addItem("（ポートがありません）", "")
            self.set_message(MSG_NO_PORT)
        else:
            idx = self.port_combo.findData(current)
            self.port_combo.setCurrentIndex(max(idx, 0))
            if self.msg_label.text() == MSG_NO_PORT:
                self.set_message("")
        self._update_buttons()

    def select_port(self, device: str) -> None:
        self.port_combo.setCurrentIndex(self.port_combo.findData(device))

    def on_connect_toggle(self) -> None:
        if self.reader is not None:
            self.disconnect_port()
        else:
            self.connect_port()

    def connect_port(self) -> bool:
        if self.reader is not None or self.busy:
            return False
        device = self.port_combo.currentData()
        if not device:
            self.set_message(MSG_NO_PORT)
            return False
        try:
            port = open_port(device, self.mock_speed)
        except PortOpenError as e:
            self.set_message(str(e), error=True)
            self._warn(str(e))
            return False
        self._device = device
        self.interval_ack = None
        self.interval_no_ack = False
        self.firmware_version = None
        self.invalid_count = 0
        self._last_data_mono = time.monotonic()
        self._no_data_shown = False
        self.monitor.reset()
        self.rx_queue = queue.Queue()
        self.reader = SerialReader(port, self.rx_queue, interval_ms=CHECKER_INTERVAL_MS)
        self.reader.start()
        self.poll_timer.start()
        self.settings.last_port = device
        self.settings.save(self.paths.settings_file)
        self.set_message(f"{device} に接続しました")
        self._update_display(force=True)
        return True

    def disconnect_port(self, message: str = "切断しました") -> None:
        self._cancel_collect()
        self.continuous = False
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self.poll_timer.stop()
        self.monitor.reset()
        self.set_message(message)
        self._update_display(force=True)

    def _poll(self) -> None:
        """受信キューを空にする（20 ms ごと）。データ行は監視と 5 回測定に渡す。"""
        if self.reader is None:
            return
        disconnected = None
        got_data = False
        status_changed = False  # 周期の表示が変わった（すぐ描き直す）
        was_connected = self.monitor.connected
        while True:
            try:
                ev = self.rx_queue.get_nowait()
            except queue.Empty:
                break
            kind = ev[0]
            if kind == "line":
                parsed, pc_time = ev[1], ev[2]
                if parsed.kind is LineKind.DATA:
                    v = parsed.row.bus_V
                    self.monitor.feed(v, pc_time)
                    got_data = True
                    if self.collector is not None:
                        res = self.collector.feed(v, pc_time)
                        if res is not None:
                            self._finish_collect(res)
                elif parsed.kind is LineKind.COMMENT:
                    self._on_comment(parsed.text)
                    status_changed = True
                elif parsed.kind is LineKind.HEADER:
                    status_changed = True
                    self.interval_ack = None  # Arduino がリセットされた。受信スレッドが周期変更を送り直す
                    self.interval_no_ack = False
                elif parsed.kind is LineKind.INVALID:
                    self.invalid_count += 1
            elif kind == "interval_no_ack":
                self.interval_no_ack = True
                status_changed = True
                log.warning("%s（%s, fw=%s）", MSG_OLD_SKETCH, self._device, self.firmware_version)
                self.set_message(MSG_OLD_SKETCH)
            elif kind == "disconnected":
                disconnected = ev[1]
        if self.collector is not None:
            res = self.collector.poll(time.time())
            if res is not None:
                self._finish_collect(res)
        now = time.monotonic()
        if got_data:
            self._last_data_mono = now
            if self._no_data_shown:
                self._no_data_shown = False
                self.set_message("")
        elif now - self._last_data_mono > NO_DATA_TIMEOUT_S and not self._no_data_shown:
            self._no_data_shown = True
            log.warning("データが届いていません（%s）", self._device)
            self.set_message(MSG_NO_DATA)
        # バッテリの付け外しは記録ボタンの有効・無効に関わるので、すぐ描き直す
        if self.continuous and self.monitor.connected != was_connected:
            self._show_continuous_hint()
        self._update_display(force=status_changed or self.monitor.connected != was_connected)
        self._try_auto_record()
        if disconnected is not None:
            log.warning("%s: %s", MSG_DISCONNECTED, disconnected)
            self.disconnect_port(MSG_DISCONNECTED)
            self.set_message(MSG_DISCONNECTED, error=True)
            self._warn(f"{MSG_DISCONNECTED}。記録済みのデータは作業ファイルに残っています。USB を繋ぎ直して「接続」してください")

    def _on_comment(self, text: str) -> None:
        fw = parse_firmware_banner(text)
        if fw is not None:
            self.firmware_version = fw
        ack = parse_interval_ack(text)
        if ack is not None:
            self.interval_ack = ack
            self.interval_no_ack = False
            self.set_message(f"送信周期 {ack[0]} ms（INA226 平均 {ack[1]} 回）で受信しています")
            return
        if text.startswith("# error"):
            log.warning("Arduino からのエラー: %s", text)
        self.set_message(text)

    def effective_interval_ms(self) -> int | None:
        if self.interval_ack is not None:
            return self.interval_ack[0]
        if self.interval_no_ack:
            return DEFAULT_INTERVAL_MS
        return None

    # ================================================================ F-04 記録
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        """Space／Enter で記録する（文字の入力欄にカーソルがあるときは入力を優先する）。"""
        if ev.type() != QEvent.KeyPress or ev.key() not in _RECORD_KEYS:
            return False
        if ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        if not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        if isinstance(QApplication.focusWidget(), _TEXT_INPUTS):
            return False
        if not ev.isAutoRepeat():  # 押しっぱなしでは続けて記録しない
            self.on_record()
        return True

    def on_record(self) -> None:
        if self.busy:
            return  # 集めている間・NG の確認待ちの再入力は無視する
        if self.reader is None:
            self.set_message(MSG_NOT_CONNECTED, error=True)
            return
        if self.lot is None or self.book is None:
            self.set_message(MSG_NO_LOT, error=True)
            return
        if not self.monitor.connected:
            self.set_message(f"{MSG_NO_BATTERY}。バッテリを繋いでから記録してください", error=True)
            return
        if (
            self.remeasure_no is None
            and self.settings.confirm_duplicate
            and len(self.book) > 0
            and not self.monitor.detached_since_mark
        ):
            ans = self._ask(MSG_DUPLICATE, [("record", "記録する"), ("cancel", "やめる")], escape="cancel")
            if ans != "record":
                self.set_message("記録をやめました")
                return
            if self.reader is None or not self.monitor.connected:
                self.set_message(MSG_NO_BATTERY, error=True)
                return
        self._start_collect()

    def _start_collect(self) -> None:
        """記録ボタンの後の 5 回測定を始める（手動・自動・もう一度測定で共用）。"""
        self._pressed_mono = time.monotonic()
        self.collector = SampleCollector(time.time(), self.monitor.threshold_V)
        self._update_display(force=True)

    # ---- 連続測定モード
    def set_continuous(self, on: bool) -> None:
        """連続測定モード：電圧が安定したら自動で記録し、0 V（接続判定電圧未満）になったら次を待つ。"""
        on = bool(on) and self.reader is not None and self.lot is not None and not self.busy
        self.continuous = on
        if on:
            self.set_message("連続測定モード：安定したら自動で記録します（バッテリを外すと次を記録できます）")
            self._show_continuous_hint()
        else:
            self.set_message("連続測定モードを終わりました")
        self._update_buttons()

    def _show_continuous_hint(self) -> None:
        """連続測定モードで、次にすることをステータス欄に出す。"""
        if self.busy or self.remeasure_no is not None:
            return
        if self.monitor.connected and not self.monitor.detached_since_mark:
            hint = "バッテリを外してください"
        elif self.monitor.connected:
            hint = "安定したら自動で記録します"
        else:
            hint = "次のバッテリを繋いでください"
        self.set_message(f"連続測定モード：{hint}")

    def _try_auto_record(self) -> None:
        if not (self.continuous and self.reader is not None and self.lot is not None and not self.busy):
            return
        if QApplication.activeModalWidget() is not None:
            return  # ほかの確認ウィンドウが出ている間は待つ
        # 前回の記録の後に一度 0 V（接続判定電圧未満）になり、いま安定している
        if self.monitor.detached_since_mark and self.monitor.state is BatteryState.STABLE:
            self._start_collect()

    def _cancel_collect(self) -> None:
        if self.collector is not None:
            self.collector = None

    def _finish_collect(self, res: CollectResult) -> None:
        self.collector = None
        if not res.ok:
            msg = res.failure.value
            self.set_message(f"記録していません：{msg}", error=True)
            self._update_display(force=True)
            return
        lot = self.lot
        m, when = res.measurement, datetime.now()
        rec = make_record(0, m, lot.lower_V, lot.upper_V, self.settings.stable_width_mV / 1000, when)
        if not rec.judge.is_ok and self.settings.confirm_ng:
            # NG：記録するか、もう一度測定するかを聞く。受信の処理（_poll）の中でウィンドウを出さないよう後で出す
            self._pending = (m, when)
            self.set_message(f"NG　{m.median_V:.4f} V（{rec.judge.label}）：記録するか選んでください", error=True)
            self._update_display(force=True)
            QTimer.singleShot(0, self._ask_ng)
            return
        self._commit(m, when)

    def _ask_ng(self) -> None:
        """NG の注意ウィンドウ：「記録する」（既定。Enter）か「もう一度測定」（Esc）。"""
        if self._pending is None or self.lot is None or self.book is None:
            self._pending = None
            return
        m, when = self._pending
        lot = self.lot
        rec = make_record(0, m, lot.lower_V, lot.upper_V, self.settings.stable_width_mV / 1000, when)
        no = self.remeasure_no if self.remeasure_no is not None else self.book.next_no
        if rec.judge.value == "NG_LOW":
            why = f"下限未満。下限 {lot.lower_V:.3f} V"
        else:
            why = f"上限超え。上限 {lot.upper_V:.3f} V"
        if self.settings.sound:
            sound.play(False)
        ans = self._ask(
            f"NG です：No.{no}　{m.median_V:.4f} V（{why}）\n"
            "このまま記録しますか？　それとも、もう一度測定しますか？",
            [("record", "記録する"), ("retry", "もう一度測定")],
            escape="retry",
        )
        self._pending = None
        if ans == "record":
            self._commit(m, when, sounded=True)
            return
        # もう一度測定：記録せずに、繋いでいるバッテリをすぐ測り直す
        if self.reader is None or not self.monitor.connected:
            self.set_message(f"記録していません：{MSG_NO_BATTERY}", error=True)
            self._update_display(force=True)
            return
        self.set_message(f"No.{no} をもう一度測定します")
        self._start_collect()

    def _commit(self, m: Measurement, when: datetime, sounded: bool = False) -> None:
        """測定結果を記録として確定する（追加、または測り直しの置き換え）→ 作業ファイルに保存 → 表示。"""
        lot, book = self.lot, self.book
        width = self.settings.stable_width_mV / 1000
        if self.remeasure_no is not None and book.get(self.remeasure_no) is not None:
            no = self.remeasure_no
            rec = book.replace(no, make_record(no, m, lot.lower_V, lot.upper_V, width, when))
            self.remeasure_no = None
            self.table.model.changed(no)
            appended = False
            sub = f"測り直しました（{rec.remeasured} 回目）"
        else:
            self.remeasure_no = None
            rec = book.append(make_record(book.next_no, m, lot.lower_V, lot.upper_V, width, when))
            self.table.model.appended()
            appended = True
            sub = ""
        lot.revision += 1
        lot.port = self._device
        lot.interval_ms = self.effective_interval_ms()
        lot.firmware = self.firmware_version
        self.last_collect_s = time.monotonic() - self._pressed_mono
        t_save = time.monotonic()
        saved = self.workfile.save(lot, book, appended=appended)
        self.last_save_s = time.monotonic() - t_save
        self.monitor.mark_recorded()
        self.table.select_no(rec.no)
        self._refresh_records()
        if saved:
            warn = f"　⚠ {rec.note}" if rec.note else ""
            msg = f"No.{rec.no}：{sub}" if sub else f"No.{rec.no} を記録しました"
            msg += f"（{rec.voltage_V:.4f} V　{rec.judge.label}）{warn}"
            if self.continuous and not sub:
                msg += "　バッテリを外してください"
            self.set_message(msg, error=not rec.judge.is_ok)  # NG は赤字
        else:
            self.set_message(f"{MSG_WORKFILE_ERROR}（{self.workfile.csv_path}）", error=True)
        self._update_display(force=True)
        self.last_latency_s = time.monotonic() - self._pressed_mono
        if self.settings.sound and not sounded:
            sound.play(rec.judge.is_ok)

    def _save_workfile(self, appended: bool = False) -> None:
        if self.workfile is None or self.lot is None or self.book is None:
            return
        if not self.workfile.save(self.lot, self.book, appended=appended):
            self.set_message(f"{MSG_WORKFILE_ERROR}（{self.workfile.csv_path}）", error=True)

    # ================================================================ F-06 取り消し
    def on_undo(self) -> None:
        if self.busy or self.book is None or not len(self.book):
            return
        if QApplication.activeModalWidget() is not None:
            return
        last = self.book.last
        ans = self._ask(
            f"No.{last.no}（{last.voltage_V:.4f} V）を取り消しますか？", [("yes", "はい"), ("no", "いいえ")], escape="no"
        )
        if ans != "yes":
            return
        self.book.undo_last()
        self.table.model.removed_last()
        if self.remeasure_no is not None and self.book.get(self.remeasure_no) is None:
            self.remeasure_no = None
        self.lot.revision += 1
        self._save_workfile()
        # 取り消した後は、同じバッテリをもう一度記録してよい（二重記録の確認を出さない）
        self.monitor.detached_since_mark = True
        self.table.select_no(self.book.last.no if self.book.last else None)
        self._refresh_records()
        if not (self.workfile and self.workfile.dirty):
            self.set_message(f"No.{last.no} を取り消しました（次は No.{self.book.next_no}）")
        self._update_display(force=True)

    # ================================================================ F-07 測り直し
    def on_remeasure(self) -> None:
        if self.busy or self.book is None:
            return
        if self.remeasure_no is not None:
            self.cancel_remeasure()
            return
        no = self.table.selected_no()
        if no is None:
            return
        self.start_remeasure(no)

    def start_remeasure(self, no: int) -> None:
        if self.book is None or self.book.get(no) is None:
            return
        self.remeasure_no = no
        self.set_message(f"No.{no} を測り直します：そのバッテリを繋いで「記録」（やめるときは Esc）")
        self._update_buttons()

    def cancel_remeasure(self) -> None:
        if self.remeasure_no is None or self.busy:
            return
        no = self.remeasure_no
        self.remeasure_no = None
        self.set_message(f"No.{no} の測り直しをやめました")
        self._update_buttons()

    # ================================================================ F-02 ロット
    def on_new_lot(self) -> None:
        if self.busy:
            return
        if self.lot is not None:
            ans = self._ask(
                f"現在のロット（{self.lot.lot_name}、{len(self.book)} 件）を終了して、新しいロットを始めますか？",
                [("end", "終了して新しいロット"), ("keep", "途中のまま残す"), ("cancel", "キャンセル")],
                escape="cancel",
            )
            if ans == "cancel":
                return
            if ans == "end" and not self.on_end_lot(confirm=self.lot.unexported and len(self.book) > 0):
                return
        s = self.settings
        initial = LotInput(s.lot_name, s.operator, s.lower_V, s.upper_V, 1, "")
        value = self._ask_lot(initial, editing=False)
        if value is None:
            return
        self.start_lot(value)

    def start_lot(self, value: LotInput) -> bool:
        now = datetime.now().replace(microsecond=0)
        try:
            self.paths.lots_dir.mkdir(parents=True, exist_ok=True)
            lot_id = new_lot_id(self.paths.lots_dir, now)
            info = LotInfo(
                lot_id=lot_id,
                lot_name=value.lot_name,
                lower_V=value.lower_V,
                upper_V=value.upper_V,
                start_no=value.start_no,
                operator=value.operator,
                memo=value.memo,
                started_at=now.strftime(DATETIME_FMT),
            )
            book = RecordBook(value.start_no)
            wf = WorkFile.create(self.paths.lots_dir, info, book)
        except OSError:
            log.warning("作業ファイルを作れません", exc_info=True)
            msg = f"作業ファイルを作れません（{self.paths.lots_dir}）"
            self.set_message(msg, error=True)
            self._warn(msg)
            return False
        s = self.settings
        s.lot_name, s.operator, s.lower_V, s.upper_V = value.lot_name, value.operator, value.lower_V, value.upper_V
        s.save(self.paths.settings_file)
        self._open_lot(info, book, wf)
        self.set_message(f"ロット {info.lot_name} を始めました")
        return True

    def _open_lot(self, info: LotInfo, book: RecordBook, wf: WorkFile) -> None:
        self.lot, self.book, self.workfile = info, book, wf
        self.remeasure_no = None
        self.monitor.detached_since_mark = True
        self._show_lot()

    def _close_lot(self) -> None:
        self.lot = self.book = self.workfile = None
        self.remeasure_no = None
        self.continuous = False
        self._show_lot()

    def on_lot_settings(self) -> None:
        if self.lot is None or self.busy:
            return
        lot = self.lot
        initial = LotInput(lot.lot_name, lot.operator, lot.lower_V, lot.upper_V, lot.start_no, lot.memo)
        value = self._ask_lot(initial, editing=True)
        if value is None:
            return
        self.apply_lot_settings(value)

    def apply_lot_settings(self, value: LotInput) -> None:
        lot = self.lot
        limits_changed = (value.lower_V, value.upper_V) != (lot.lower_V, lot.upper_V)
        lot.lot_name, lot.operator, lot.memo = value.lot_name, value.operator, value.memo
        lot.lower_V, lot.upper_V = value.lower_V, value.upper_V
        changed = self.book.rejudge(lot.lower_V, lot.upper_V) if limits_changed else 0
        lot.revision += 1
        self._save_workfile()
        sel = self.table.selected_no()
        self._show_lot()
        self.table.select_no(sel)
        if limits_changed:
            # 次のロットの初期値にも使う（F-02：前回の値を初期表示）
            self.settings.lower_V, self.settings.upper_V = lot.lower_V, lot.upper_V
            self.settings.save(self.paths.settings_file)
        msg = "ロット設定を変えました"
        if limits_changed:
            msg += f"（{lot.limits_text()}。判定をやり直しました。変わった記録 {changed} 件）"
        self.set_message(msg)

    # ---- グラフの線をドラッグして上下限を変える
    def _on_limits_dragging(self, lower_V: float, upper_V: float, ng: int) -> None:
        now = compute_stats(self.book.records).ng if self.book else 0
        self.set_message(f"下限 {lower_V:.3f} V・上限 {upper_V:.3f} V にすると NG {ng:,} 件（いま {now:,} 件）")

    def on_limits_dragged(self, lower_V: float, upper_V: float) -> None:
        """グラフの上下限の線を離した。判定が変わる記録があれば確認してから変える。"""
        lot, book = self.lot, self.book
        if lot is None or book is None or self.busy:
            if lot is not None:
                self.plot.set_limits(lot.lower_V, lot.upper_V)
                self.plot.refresh(book)
            return
        changed = sum(1 for r in book.records if judge(r.voltage_V, lower_V, upper_V) is not r.judge)
        if changed:
            ans = self._ask(
                f"上下限を 下限 {lower_V:.3f} V・上限 {upper_V:.3f} V に変えます。\n"
                f"判定が変わる記録が {changed:,} 件あります。変更しますか？",
                [("yes", "変更する"), ("no", "やめる")],
                escape="no",
            )
            if ans != "yes":
                self.plot.set_limits(lot.lower_V, lot.upper_V)
                self.plot.refresh(book)
                self.set_message("上下限の変更をやめました")
                return
        self.apply_lot_settings(LotInput(lot.lot_name, lot.operator, lower_V, upper_V, lot.start_no, lot.memo))

    def on_end_lot(self, confirm: bool = True) -> bool:
        """「ロット終了」。終了したら True。"""
        if self.lot is None or self.busy:
            return False
        if confirm:
            if self.lot.unexported and len(self.book) > 0:
                text = f"出力していない記録があります。ロット（{self.lot.lot_name}）を終了しますか？"
            else:
                text = f"ロット（{self.lot.lot_name}）を終了しますか？"
            if self._ask(text, [("end", "終了する"), ("cancel", "やめる")], escape="cancel") != "end":
                return False
        name = self.lot.lot_name
        if not finish_lot(self.workfile, self.lot):
            self.set_message(f"{MSG_WORKFILE_ERROR}（{self.workfile.json_path}）", error=True)
        self._close_lot()
        self.set_message(f"ロット {name} を終了しました")
        return True

    # ================================================================ F-09 出力
    def on_export(self) -> None:
        if self.lot is None or not self.book or self.busy:
            return
        lot = self.lot
        answer = self._ask_export(default_base(lot, self.book.records))
        if not answer:
            return
        folder, base = answer
        cond = Conditions(
            port=lot.port or self._device,
            interval_ms=lot.interval_ms if lot.interval_ms is not None else self.effective_interval_ms(),
            firmware=lot.firmware if lot.firmware is not None else self.firmware_version,
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = export_lot(Path(folder), lot, self.book.records, cond, base=base)
        except ExportError as e:
            QApplication.restoreOverrideCursor()
            self.set_message(str(e), error=True)
            self._warn(str(e))
            return
        QApplication.restoreOverrideCursor()
        lot.exported_revision = lot.revision
        lot.last_export_dir = str(folder)
        lot.last_export_base = res.base
        if self.workfile:
            self.workfile.save_info(lot)
        self.settings.save_dir = str(folder)
        self.settings.save(self.paths.settings_file)
        self._saved_folder = Path(folder)
        self.set_message(f"保存しました：{folder}（{res.base}.csv / .xlsx）")
        self.open_folder_button.setVisible(True)
        self.last_export = res
        ans = self._ask("出力しました。ロットを終了しますか？", [("end", "終了する"), ("continue", "続ける")], escape="continue")
        if ans == "end":
            self.on_end_lot(confirm=False)
            self.set_message(f"保存しました：{folder}（ロットを終了しました）")
            self.open_folder_button.setVisible(True)

    def _open_saved_folder(self) -> None:
        if self._saved_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._saved_folder)))

    # ================================================================ F-08 再開
    def check_resume(self) -> None:
        """起動時：終了していないロットがあれば、続けるか聞く。"""
        try:
            removed = cleanup_finished(self.paths.lots_dir)
            if removed:
                log.warning("終了から 30 日を過ぎた作業ファイルを消しました: %s", ", ".join(removed))
        except OSError:
            log.warning("古い作業ファイルの整理に失敗", exc_info=True)
        for entry in find_unfinished(self.paths.lots_dir):
            try:
                records, _dropped, _w = read_records(entry.csv_path, entry.info.start_no)
            except (OSError, ValueError):
                log.warning("作業ファイルを読めません: %s", entry.csv_path, exc_info=True)
                continue
            last = f"、最後は No.{records[-1].no}" if records else ""
            ans = self._ask(
                f"前回のロット（{entry.info.lot_name}、{len(records)} 件{last}）が途中です。続けますか？",
                [("resume", "続ける"), ("new", "新しいロット"), ("later", "あとで")],
                escape="later",
            )
            if ans == "resume":
                self.resume_lot(entry)
            elif ans == "new":
                self.on_new_lot()
            return

    def resume_lot(self, entry) -> bool:
        try:
            state, wf = load_lot(entry)
        except (OSError, ValueError):
            log.warning("作業ファイルから再開できません: %s", entry.csv_path, exc_info=True)
            msg = f"作業ファイルを読めません（{entry.csv_path}）"
            self.set_message(msg, error=True)
            self._warn(msg)
            return False
        self._open_lot(state.info, state.book, wf)
        msg = f"ロット {state.info.lot_name} を再開しました（{len(state.book)} 件、次は No.{state.book.next_no}）"
        notes = []
        if state.dropped_no is not None:
            notes.append(f"最後の 1 件（No.{state.dropped_no}）が壊れていたため除きました")
        notes += state.warnings
        if notes:
            self.set_message("　".join(notes), error=True)
            self._info(msg + "\n" + "\n".join(notes))
        else:
            self.set_message(msg)
        return True

    # ================================================================ 設定
    def on_settings(self) -> None:
        if self.busy:
            return
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() != SettingsDialog.Accepted:
            return
        dlg.apply_to(self.settings)
        self.apply_settings()

    def apply_settings(self) -> None:
        self.monitor.threshold_V = self.settings.connect_threshold_V
        self.monitor.stable_width_V = self.settings.stable_width_mV / 1000
        self.settings.save(self.paths.settings_file)
        self.set_message("設定を保存しました")
        self._update_display(force=True)

    # ================================================================ 終了
    def closeEvent(self, event) -> None:  # noqa: N802
        # 記録はそのつど作業ファイルに保存しているので、確認せずに閉じる（次回起動時に続けられる）
        self._cancel_collect()
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self.poll_timer.stop()
        QApplication.instance().removeEventFilter(self)
        self.settings.save(self.paths.settings_file)
        event.accept()
