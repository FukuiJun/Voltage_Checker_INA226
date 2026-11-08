"""COM ポートの一覧・接続と、受信スレッド（仕様 F-01）。INA226_Monitor v1.2.1 の serial_io.py を流用。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

from .mock_serial import MockProfile, MockSerial
from .protocol import (
    STARTUP_DISCARD_S,
    LineKind,
    LineSplitter,
    interval_command,
    parse_interval_ack,
    parse_line,
)

log = logging.getLogger(__name__)

BAUDRATE = 115200

INTERVAL_RETRY_S = 1.0  # 周期変更の応答を待つ時間（これを過ぎたら送り直す）
INTERVAL_MAX_TRIES = 3
NO_HEADER_SEND_S = 2.5  # 開いてからこの時間ヘッダ行が来ずにデータだけ届いたら、リセットされないボードとみなす

# 模擬シリアル（--mock 起動時だけ一覧に出す）
MOCK_PORTS: dict[str, tuple[str, MockProfile]] = {
    "MOCK1": ("模擬データ（バッテリの付け外し）", MockProfile()),
    "MOCK2": ("模擬データ（付け外し・ときどきばらつき大）", MockProfile(noisy_every=4)),
    "MOCK3": ("模擬データ（バッテリを繋いだまま）", MockProfile(battery_cycle=None)),
    "MOCK4": ("模擬データ（20 秒後に USB 抜け）", MockProfile(disconnect_after_s=20.0)),
    "MOCK5": ("模擬データ（周期を変えられない古いスケッチ）", MockProfile(ignore_commands=True)),
    "MOCK6": ("模擬データ（ポートを開いてもリセットされないボード）", MockProfile(reset_on_open=False)),
}


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str

    @property
    def label(self) -> str:
        return f"{self.device} - {self.description}" if self.description else self.device


class PortOpenError(Exception):
    def __init__(self, device: str) -> None:
        super().__init__(f"{device} を開けません。シリアルモニタなど他のソフトを閉じてください")
        self.device = device


def _clean_description(device: str, desc: str | None) -> str:
    desc = (desc or "").strip()
    if desc.lower() == "n/a" or desc == device:
        return ""
    # Windows では「Arduino Uno (COM7)」のようにポート名が付いている
    suffix = f"({device})"
    if desc.endswith(suffix):
        desc = desc[: -len(suffix)].strip()
    return desc


def list_ports(include_mock: bool = False) -> list[PortInfo]:
    ports: list[PortInfo] = []
    try:
        from serial.tools import list_ports as lp

        for p in sorted(lp.comports(), key=lambda p: p.device):
            ports.append(PortInfo(p.device, _clean_description(p.device, p.description)))
    except Exception:
        log.warning("COM ポート一覧を取得できません", exc_info=True)
    if include_mock:
        ports.extend(PortInfo(dev, desc) for dev, (desc, _) in MOCK_PORTS.items())
    return ports


def open_port(device: str, mock_speed: float = 1.0):
    """ポートを開く。開けなければ PortOpenError。"""
    if device in MOCK_PORTS:
        return MockSerial(MOCK_PORTS[device][1], speed=mock_speed)
    try:
        import serial

        ser = serial.Serial(
            device,
            BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        ser.reset_input_buffer()
        return ser
    except Exception as e:
        log.warning("ポートを開けません: %s", device, exc_info=True)
        raise PortOpenError(device) from e


class SerialReader(threading.Thread):
    """受信・行判定を行い、結果をキューに入れる。

    interval_ms を指定すると、周期変更コマンドを送る（仕様 5.1・F-01）。
      - ヘッダ行（Arduino の起動・リセット直後に出る）を受けるたびに送る
      - ヘッダ行が来ないままデータが届く（ポートを開いてもリセットされないボード）ときも送る
      - 応答が無ければ INTERVAL_RETRY_S ごとに送り直し、INTERVAL_MAX_TRIES 回で諦める

    キューに入るもの：
      ("line", ParsedLine, pc_time)  … 判定済みの 1 行（pc_time は受信時の PC 時刻）
      ("interval_no_ack",)           … 何度送っても周期変更の応答が無かった
      ("disconnected", message)      … 読み込みで例外（USB 抜けなど）。スレッドは終わる
    """

    def __init__(self, port, out: queue.Queue, interval_ms: int | None = None, clock=time.monotonic) -> None:
        super().__init__(name="serial-reader", daemon=True)
        self.port = port
        self.out = out
        self.interval_ms = interval_ms
        self._clock = clock
        self._stop_event = threading.Event()
        # 周期変更コマンドの送信状態
        self._tries = 0
        self._last_send = 0.0
        self._acked = False
        self._gave_up = False
        self._header_seen = False
        self._data_seen = False

    def run(self) -> None:
        splitter = LineSplitter()
        t_open = self._clock()
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = self.port.read(max(1, self.port.in_waiting))
                except Exception as e:  # SerialException は OSError の派生だが念のため広く拾う
                    if not self._stop_event.is_set():
                        log.warning("受信中に接続が切れました: %s", e)
                        self.out.put(("disconnected", str(e)))
                    return
                if chunk:
                    pc_time = time.time()
                    for line in splitter.feed(chunk):
                        parsed = parse_line(line)
                        # 開いた直後の行は Arduino のリセット前の残りかもしれないので捨てる
                        if parsed.kind is LineKind.DATA and self._clock() - t_open < STARTUP_DISCARD_S:
                            continue
                        self.out.put(("line", parsed, pc_time))
                        self._on_line(parsed)
                if self.interval_ms is not None:
                    self._interval_step(self._clock() - t_open)
        finally:
            try:
                self.port.close()
            except Exception:
                pass

    def _on_line(self, parsed) -> None:
        if parsed.kind is LineKind.HEADER:
            # 起動・リセットのたびに既定の周期に戻るので、最初から送り直す
            self._header_seen = True
            self._acked = False
            self._gave_up = False
            self._tries = 0
            if self.interval_ms is not None:
                self._send_interval()
        elif parsed.kind is LineKind.COMMENT and parse_interval_ack(parsed.text):
            self._acked = True
        elif parsed.kind is LineKind.DATA:
            self._data_seen = True

    def _interval_step(self, since_open: float) -> None:
        if self._acked or self._gave_up:
            return
        now = self._clock()
        if self._tries == 0:
            # ヘッダ行が来ないままデータが届いている＝リセットされないボード
            if not self._header_seen and self._data_seen and since_open >= NO_HEADER_SEND_S:
                self._send_interval()
        elif now - self._last_send >= INTERVAL_RETRY_S:
            if self._tries < INTERVAL_MAX_TRIES:
                self._send_interval()
            else:
                self._gave_up = True
                log.warning("周期変更の応答がありません（%d ms、%d 回送信）", self.interval_ms, self._tries)
                self.out.put(("interval_no_ack",))

    def _send_interval(self) -> None:
        self._tries += 1
        self._last_send = self._clock()
        try:
            self.port.write(interval_command(self.interval_ms))
        except Exception as e:  # 送れなくても受信は続ける（周期は既定のまま）
            log.warning("周期変更コマンドを送れません: %s", e)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        self.join(timeout)
