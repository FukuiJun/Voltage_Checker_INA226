import pytest

from battery_checker.protocol import HEADER_LINE, LineKind, LineSplitter, parse_line

SAMPLE = [
    "188002,0.0000,-10.0,-0.125",
    "188052,0.0000,-7.5,-0.094",
    "188102,0.0000,-10.0,-0.125",
    "188152,0.0000,-7.5,-0.094",
    "188203,0.0000,-7.5,-0.094",
]


def test_only_data_rows_are_taken():
    """ヘッダ・# 行・途中で切れた行を混ぜても、データ 5 行だけ取り込み、不正行は 1（仕様 5.1 の判定）。"""
    stream = "\r\n".join(["# I2C scan...", HEADER_LINE, *SAMPLE[:3], "188253,0.00", *SAMPLE[3:]]) + "\r\n"
    splitter = LineSplitter()
    kinds = []
    volts = []
    # 1 バイトずつ届いても行が正しく組み立てられること
    for b in stream.encode("ascii"):
        for line in splitter.feed(bytes([b])):
            p = parse_line(line)
            kinds.append(p.kind)
            if p.kind is LineKind.DATA:
                volts.append(p.row.bus_V)
    assert volts == [0.0] * 5
    assert kinds.count(LineKind.INVALID) == 1
    assert kinds.count(LineKind.HEADER) == 1
    assert kinds.count(LineKind.COMMENT) == 1


def test_spec_sample_battery_lines():
    """仕様 5.1 の受信データ例（バッテリ 3.81 V）。2 列目の bus_V だけを使う。"""
    lines = ["# ina226_logger fw=2", "# I2C scan...", "#  found 0x41", HEADER_LINE, "# interval_ms=40 avg=16",
             "1290,3.8125,-7.5,-0.094", "1330,3.8125,-10.0,-0.125", "1370,3.8113,-7.5,-0.094",
             "1410,3.8125,-7.5,-0.094", "1450,3.8138,-10.0,-0.125"]
    volts = [parse_line(x).row.bus_V for x in lines if parse_line(x).kind is LineKind.DATA]
    assert volts == [3.8125, 3.8125, 3.8113, 3.8125, 3.8138]


def test_parse_values():
    p = parse_line("188002,0.0000,-10.0,-0.125\r\n")
    assert p.kind is LineKind.DATA
    assert p.row.time_ms == 188002
    assert p.row.bus_V == 0.0
    assert p.row.shunt_uV == -10.0
    assert p.row.current_mA == -0.125


def test_invalid_variants():
    for line in ["", "1,2,3", "1,2,3,4,5", "a,0.0,0.0,0.0", "1.5,0,0,0", "1,nan,0,0", "1,0,0,", "1,0,0,inf"]:
        assert parse_line(line).kind is LineKind.INVALID, line


def test_splitter_keeps_partial_line_until_newline():
    s = LineSplitter()
    assert s.feed(b"188002,0.00") == []
    assert s.feed(b"00,-10.0,-0.125\r\n1880") == ["188002,0.0000,-10.0,-0.125"]
    assert s.feed(b"52,0.0000,-7.5,-0.094\r\n") == ["188052,0.0000,-7.5,-0.094"]


def test_current_matches_80_milliohm_shunt():
    """current_mA はシャント電圧 / 80 mΩ（µV / Ω = µA → /1000 で mA）。仕様 5.1 の実データで確認。"""
    for line in SAMPLE:
        r = parse_line(line).row
        assert r.current_mA == pytest.approx(r.shunt_uV / 0.080 / 1000, abs=0.0005)  # 小数 3 桁に丸めた分
    # INA226 のシャント電圧 1 LSB = 2.5 µV → 2.5 µV / 0.080 Ω = 31.25 µA
    assert parse_line("0,0.0,-7.5,-0.094").row.current_mA - parse_line("0,0.0,-10.0,-0.125").row.current_mA == pytest.approx(0.031)
