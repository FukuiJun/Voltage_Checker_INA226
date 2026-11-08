"""Arduino から届く行の分割と判定（仕様 5.1）。INA226_Monitor v1.2.1 の protocol.py と同じ。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

HEADER_LINE = "time_ms,bus_V,shunt_uV,current_mA"

_INT_RE = re.compile(r"^\d+$")
_REAL_RE = re.compile(r"^[+-]?\d+(\.\d*)?$")


class LineKind(Enum):
    HEADER = "header"
    COMMENT = "comment"
    DATA = "data"
    INVALID = "invalid"


@dataclass(frozen=True)
class DataRow:
    time_ms: int
    bus_V: float
    shunt_uV: float
    current_mA: float  # 生データ（ゼロ点補正前）


@dataclass(frozen=True)
class ParsedLine:
    kind: LineKind
    text: str
    row: DataRow | None = None


def parse_line(line: str) -> ParsedLine:
    """1 行（改行を除いた文字列）を判定する。"""
    text = line.strip("\r\n")
    if text == HEADER_LINE:
        return ParsedLine(LineKind.HEADER, text)
    if text.startswith("#"):
        return ParsedLine(LineKind.COMMENT, text)
    cols = text.split(",")
    if (
        len(cols) == 4
        and _INT_RE.match(cols[0])
        and all(_REAL_RE.match(c) for c in cols[1:])
    ):
        row = DataRow(int(cols[0]), float(cols[1]), float(cols[2]), float(cols[3]))
        return ParsedLine(LineKind.DATA, text, row)
    return ParsedLine(LineKind.INVALID, text)


class LineSplitter:
    """受信バイト列を行に分ける。改行が来るまでは次の受信とつなげる。"""

    MAX_PENDING = 4096  # 改行が来ないまま溜まり続けた場合の上限

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[str]:
        if not data:
            return []
        self._buf.extend(data)
        lines: list[str] = []
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            raw = bytes(self._buf[:idx])
            del self._buf[: idx + 1]
            lines.append(raw.decode("ascii", errors="replace").rstrip("\r"))
        if len(self._buf) > self.MAX_PENDING:
            lines.append(self._buf.decode("ascii", errors="replace"))
            self._buf.clear()
        return lines

    def clear(self) -> None:
        self._buf.clear()


# ---- サンプリング周期の設定 ----
DEFAULT_INTERVAL_MS = 50  # Arduino がリセット直後に使う周期
CHECKER_INTERVAL_MS = 40  # このアプリが接続時に頼む周期（仕様 5.1）
# ポートを開いた直後のデータ行は Arduino のリセット前の残りかもしれないので捨てる（INA226_Monitor の zero_cal.py から移した）
STARTUP_DISCARD_S = 0.3
INTERVAL_CHOICES_MS = (10, 20, 50, 100, 200, 500, 1000)
MIN_INTERVAL_MS = 10
MAX_INTERVAL_MS = 1000

_ACK_RE = re.compile(r"^#\s*interval_ms=(\d+)\s+avg=(\d+)\s*$")


def interval_command(ms: int) -> bytes:
    """Arduino に送る周期変更コマンド。"""
    return f"INTERVAL {int(ms)}\n".encode("ascii")


def parse_interval_ack(text: str) -> tuple[int, int] | None:
    """Arduino の応答 `# interval_ms=10 avg=4` を (周期 ms, 平均回数) にする。応答でなければ None。"""
    m = _ACK_RE.match(text.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def interval_label(ms: int) -> str:
    hz = 1000 / ms
    return f"{ms} ms（{hz:g} Hz）"


# スケッチが起動時に出す名乗り（周期変更に対応した版だけが出す）
FIRMWARE_VERSION = 2
_BANNER_RE = re.compile(r"^#\s*ina226_logger\s+fw=(\d+)\b")


def firmware_banner() -> str:
    return f"# ina226_logger fw={FIRMWARE_VERSION}"


def parse_firmware_banner(text: str) -> int | None:
    """`# ina226_logger fw=2` から版を取り出す。名乗りでなければ None。"""
    m = _BANNER_RE.match(text.strip())
    return int(m.group(1)) if m else None
