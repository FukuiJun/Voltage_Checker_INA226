"""サンプリング周期の変更（仕様 5.1・F-01）：コマンド・応答、模擬シリアル、受信スレッド。"""

import queue
import time

import pytest

from battery_checker.mock_serial import MockArduinoState, MockProfile, MockSerial
from battery_checker.protocol import (
    INTERVAL_CHOICES_MS,
    LineKind,
    LineSplitter,
    interval_command,
    interval_label,
    parse_interval_ack,
    parse_line,
)
from battery_checker.serial_io import SerialReader


def test_command_and_ack():
    assert interval_command(10) == b"INTERVAL 10\n"
    assert parse_interval_ack("# interval_ms=10 avg=4") == (10, 4)
    assert parse_interval_ack("# interval_ms=1000 avg=256\r") == (1000, 256)
    assert parse_interval_ack("# I2C scan...") is None
    assert parse_interval_ack("# error: interval out of range") is None
    # 応答はコメント行として扱われる（データ・不正行に数えない）
    assert parse_line("# interval_ms=10 avg=4").kind is LineKind.COMMENT
    assert interval_label(10) == "10 ms（100 Hz）"
    assert interval_label(1000) == "1000 ms（1 Hz）"


@pytest.mark.parametrize(
    "ms, avg", [(10, 4), (20, 4), (50, 16), (100, 16), (200, 64), (500, 128), (1000, 256)]
)
def test_avg_rule_matches_sketch(ms, avg):
    assert ms in INTERVAL_CHOICES_MS
    assert MockArduinoState.avg_for(ms) == avg


def test_mock_rejects_out_of_range():
    st = MockArduinoState()
    assert st.handle_command("INTERVAL 5") == "# error: interval out of range"
    assert st.handle_command("FOO") == "# error: unknown command"
    assert st.interval_ms == 50


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def read_lines(port, clock, seconds):
    # 読み込みの区切りで行が途中で切れることがあるので、分割器はポートごとに 1 つ使い続ける
    splitter = port.__dict__.setdefault("_test_splitter", LineSplitter())
    lines = []
    end = clock() + seconds
    while clock() < end:
        lines += splitter.feed(port.read(max(1, port.in_waiting)))
    return lines


def data_times(lines):
    return [parse_line(x).row.time_ms for x in lines if parse_line(x).kind is LineKind.DATA]


def test_mock_changes_interval_and_resets_to_default():
    clock = FakeClock()
    port = MockSerial(MockProfile(reset_every_s=6.0, invalid_every_s=None), clock=clock, sleep=clock.sleep)
    lines = read_lines(port, clock, 1.5)  # ヘッダまで
    assert "time_ms,bus_V,shunt_uV,current_mA" in lines
    port.write(b"INTERVAL 10\n")
    lines = read_lines(port, clock, 2.0)
    assert "# interval_ms=10 avg=4" in lines
    t = data_times(lines)[3:]
    assert {b - a for a, b in zip(t, t[1:])} <= {10, 11}
    lines = read_lines(port, clock, 5.0)  # 6 秒でリセット → 既定の 50 ms に戻る
    t = data_times(lines[lines.index("time_ms,bus_V,shunt_uV,current_mA"):])
    assert {b - a for a, b in zip(t, t[1:])} <= {50, 51}


def test_old_sketch_ignores_command():
    clock = FakeClock()
    port = MockSerial(MockProfile(ignore_commands=True, invalid_every_s=None), clock=clock, sleep=clock.sleep)
    read_lines(port, clock, 1.5)
    port.write(b"INTERVAL 10\n")
    lines = read_lines(port, clock, 2.0)
    assert not any(parse_interval_ack(x) for x in lines)
    t = data_times(lines)
    assert {b - a for a, b in zip(t, t[1:])} <= {50, 51}


def run_reader(profile, interval_ms, seconds, speed=5.0):
    q = queue.Queue()
    reader = SerialReader(MockSerial(profile, speed=speed), q, interval_ms=interval_ms)
    reader.start()
    time.sleep(seconds)
    reader.stop()
    out = []
    while not q.empty():
        ev = q.get()
        if ev[0] == "line":
            out.append(ev[1])
    return out


def test_reader_sends_interval_after_every_header():
    lines = run_reader(MockProfile(reset_every_s=3.0, invalid_every_s=None), 20, 1.6)
    headers = [p for p in lines if p.kind is LineKind.HEADER]
    acks = [parse_interval_ack(p.text) for p in lines if parse_interval_ack(p.text)]
    assert len(headers) >= 2
    assert acks == [(20, 4)] * len(acks) and len(acks) == len(headers)
    t = [p.row.time_ms for p in lines if p.kind is LineKind.DATA]
    diffs = [b - a for a, b in zip(t, t[1:]) if b > a]
    assert diffs.count(20) + diffs.count(21) > len(diffs) * 0.8


def test_reader_without_interval_sends_nothing():
    lines = run_reader(MockProfile(invalid_every_s=None), None, 0.6)
    assert not any(parse_interval_ack(p.text) for p in lines)


def test_firmware_banner():
    from battery_checker.protocol import firmware_banner, parse_firmware_banner

    assert parse_firmware_banner(firmware_banner()) == 2
    assert parse_firmware_banner("# ina226_logger fw=2") == 2
    assert parse_firmware_banner("# I2C scan...") is None
    # 新しいスケッチ（模擬）は名乗り、古いスケッチは名乗らない
    new = [x for _, x in __import__("itertools").islice(
        __import__("battery_checker.mock_serial", fromlist=["mock_events"]).mock_events(MockProfile()), 5)]
    old = [x for _, x in __import__("itertools").islice(
        __import__("battery_checker.mock_serial", fromlist=["mock_events"]).mock_events(MockProfile(ignore_commands=True)), 5)]
    assert firmware_banner() in new and firmware_banner() not in old


class DropFirstWrite(MockSerial):
    """最初の送信だけ届かないポート（Arduino が受け取り損ねた場合）。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.writes = 0

    def write(self, data):
        self.writes += 1
        if self.writes == 1:
            return len(data)
        return super().write(data)


def test_reader_resends_when_no_ack():
    q = queue.Queue()
    port = DropFirstWrite(MockProfile(invalid_every_s=None), speed=5.0)
    reader = SerialReader(port, q, interval_ms=20)
    reader.start()
    time.sleep(1.8)
    reader.stop()
    lines = [ev[1] for ev in list(q.queue) if ev[0] == "line"]
    assert port.writes >= 2
    assert any(parse_interval_ack(p.text) == (20, 4) for p in lines)
    assert not any(ev[0] == "interval_no_ack" for ev in list(q.queue))


def test_reader_gives_up_on_old_sketch():
    q = queue.Queue()
    port = MockSerial(MockProfile(ignore_commands=True, invalid_every_s=None), speed=5.0)
    reader = SerialReader(port, q, interval_ms=20)
    reader.start()
    time.sleep(3.6)
    reader.stop()
    events = list(q.queue)
    assert sum(1 for ev in events if ev[0] == "interval_no_ack") == 1


def test_reader_sends_to_board_that_does_not_reset():
    """ポートを開いてもリセットされないボード：ヘッダ行が来なくてもデータが届けば送る。"""
    q = queue.Queue()
    port = MockSerial(MockProfile(reset_on_open=False, invalid_every_s=None), speed=1.0)
    reader = SerialReader(port, q, interval_ms=20)
    reader.start()
    time.sleep(3.3)
    reader.stop()
    lines = [ev[1] for ev in list(q.queue) if ev[0] == "line"]
    assert not any(p.kind is LineKind.HEADER for p in lines)
    assert any(parse_interval_ack(p.text) == (20, 4) for p in lines)
    t = [p.row.time_ms for p in lines if p.kind is LineKind.DATA][-10:]
    assert {b - a for a, b in zip(t, t[1:])} <= {20, 21}
