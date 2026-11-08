"""出力：CSV と xlsx（F-09, AC-11, AC-12, AC-14 の出力部分）。"""

import csv
import io
import random
import statistics
import time
from datetime import datetime

import pytest

from battery_checker.export import (
    SHEET_CONDITIONS,
    SHEET_RECORDS,
    SHEET_SUMMARY,
    Conditions,
    ExportError,
    export_lot,
)
from battery_checker.records import Judge, LotInfo, Measurement, RecordBook, make_record

openpyxl = pytest.importorskip("openpyxl")

T = datetime(2026, 11, 6, 14, 30, 5)
COND = Conditions(port="COM7", interval_ms=40, firmware=2)


def make_lot(n, seed=1, start_no=1, name="LOT-A12"):
    info = LotInfo("20261106_143000", name, 3.6, 4.2, start_no, "福井", "メモ1\nメモ2", started_at="2026-11-06 14:30:00")
    book = RecordBook(start_no)
    rng = random.Random(seed)
    for _ in range(n):
        v = round(rng.gauss(3.85, 0.15), 4)
        m = Measurement(v, round(v - 0.0013, 4), round(v + 0.0013, 4))
        book.append(make_record(book.next_no, m, info.lower_V, info.upper_V, 0.005, T))
    return info, book


def read_csv(path):
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM 付き（Excel で文字化けしない）
    text = raw[3:].decode("utf-8")
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")  # 改行は CRLF だけ
    return list(csv.reader(io.StringIO(text)))


def test_ac11_csv(tmp_path):
    info, book = make_lot(30)
    book.replace(3, make_record(3, Measurement(3.3, 3.3, 3.3), 3.6, 4.2, 0.005, T))
    res = export_lot(tmp_path, info, book.records, COND)
    assert res.base == "20261106_LOT-A12_電圧記録"
    assert [p.name for p in res.paths] == ["20261106_LOT-A12_電圧記録.csv", "20261106_LOT-A12_電圧記録.xlsx"]
    rows = read_csv(res.paths[0])
    assert rows[0] == ["no", "voltage_V", "judge", "min_V", "max_V", "measured_at", "remeasured", "note"]
    assert len(rows) - 1 == len(book)
    assert [int(r[0]) for r in rows[1:]] == list(range(1, 31))  # 番号順
    assert rows[3] == ["3", "3.3000", "NG_LOW", "3.3000", "3.3000", "2026-11-06 14:30:05", "1", ""]


def test_ac12_xlsx_contents(tmp_path):
    info, book = make_lot(200)
    res = export_lot(tmp_path, info, book.records, COND)
    wb = openpyxl.load_workbook(res.paths[1])
    assert wb.sheetnames == [SHEET_RECORDS, SHEET_SUMMARY, SHEET_CONDITIONS]

    ws = wb[SHEET_RECORDS]
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == f"A1:H{len(book) + 1}"
    assert [c.value for c in ws[1]] == ["No", "電圧 [V]", "判定", "最小 [V]", "最大 [V]", "記録日時", "測り直し", "備考"]
    assert ws["A1"].font.bold and ws["A1"].fill.fgColor.rgb.endswith("2B2A28")
    assert ws.max_row == len(book) + 1
    ng_rows = [r for r in book.records if r.judge is not Judge.OK]
    assert ng_rows, "テストデータに NG が含まれていること"
    for r in book.records:
        row = r.no + 1
        assert ws.cell(row=row, column=2).value == r.voltage_V
        assert ws.cell(row=row, column=2).number_format == "0.0000"
        fill = ws.cell(row=row, column=3).fill.fgColor.rgb
        if r.judge is Judge.OK:
            assert ws.cell(row=row, column=3).value == "OK" and not fill.endswith("A3282C")
        else:
            assert fill.endswith("A3282C")  # NG の判定セルは赤
            assert ws.cell(row=row, column=3).font.color.rgb.endswith("FFFFFF")

    # 集計：CSV から計算した値と一致する
    rows = read_csv(res.paths[0])[1:]
    volts = [float(r[1]) for r in rows]
    judges = [r[2] for r in rows]
    summary = {ws_row[0].value: ws_row[1].value for ws_row in wb[SHEET_SUMMARY].iter_rows(min_row=2, max_col=2)
               if ws_row[0].value}
    assert summary["ロット名"] == "LOT-A12" and summary["測定者"] == "福井"
    assert summary["件数"] == len(rows)
    assert summary["OK 数"] == judges.count("OK")
    assert summary["NG 数"] == len(rows) - judges.count("OK")
    assert summary["　うち下限未満"] == judges.count("NG_LOW")
    assert summary["　うち上限超え"] == judges.count("NG_HIGH")
    assert summary["平均 [V]"] == pytest.approx(statistics.fmean(volts), abs=5e-5)
    assert summary["中央値 [V]"] == pytest.approx(statistics.median(volts), abs=5e-5)
    assert summary["最小 [V]"] == min(volts) and summary["最大 [V]"] == max(volts)
    assert summary["最小の No"] == volts.index(min(volts)) + 1
    assert summary["標準偏差 [V]"] == pytest.approx(statistics.stdev(volts), abs=5e-6)
    assert summary["OK 率"] == pytest.approx(judges.count("OK") / len(rows))
    # 度数分布表と棒グラフ
    ss = wb[SHEET_SUMMARY]
    counts = [ss.cell(row=r, column=5).value for r in range(2, ss.max_row + 1)
              if isinstance(ss.cell(row=r, column=5).value, int)]
    assert sum(counts) == len(rows)
    assert len(ss._charts) == 1

    cond = {r[0].value: r[1].value for r in wb[SHEET_CONDITIONS].iter_rows(min_row=2, max_col=2)}
    assert cond["スケッチの版"] == "fw=2" and cond["送信周期"] == "40 ms"
    assert cond["記録方法"] == "5 回測定の中央値" and cond["COM ポート"] == "COM7"
    assert cond["ソフトウェア"].startswith("INA226_BatteryChecker v")


def test_unknown_firmware(tmp_path):
    info, book = make_lot(3)
    res = export_lot(tmp_path, info, book.records, Conditions(port="COM3"))
    cond = {r[0].value: r[1].value for r in openpyxl.load_workbook(res.paths[1])[SHEET_CONDITIONS].iter_rows(min_row=2, max_col=2)}
    assert cond["スケッチの版"] == "不明"


def test_reexport_same_lot_overwrites_and_other_lot_gets_suffix(tmp_path):
    info, book = make_lot(5)
    res = export_lot(tmp_path, info, book.records, COND)
    info.last_export_dir, info.last_export_base = str(tmp_path), res.base
    book.undo_last()
    res2 = export_lot(tmp_path, info, book.records, COND)
    assert res2.base == res.base
    assert len(read_csv(res2.paths[0])) - 1 == 4
    other, obook = make_lot(2)
    other.lot_id = "20261106_150000"
    res3 = export_lot(tmp_path, other, obook.records, COND)
    assert res3.base == res.base + "_2"


def test_ac13_locked_xlsx_leaves_nothing(tmp_path, monkeypatch):
    """xlsx を Excel で開いている（置き換えられない）→ エラー。CSV も残らない。"""
    import battery_checker.export as ex

    info, book = make_lot(5)
    real = ex.os.replace

    def locked(src, dst):
        if str(dst).endswith(".xlsx"):
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(ex.os, "replace", locked)
    with pytest.raises(ExportError) as e:
        export_lot(tmp_path, info, book.records, COND)
    assert str(e.value) == "20261106_LOT-A12_電圧記録.xlsx に書き込めません。Excel などで開いていないか確認してください"
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(ex.os, "replace", real)
    assert len(export_lot(tmp_path, info, book.records, COND).paths) == 2


def test_csv_locked_after_xlsx_removes_xlsx(tmp_path, monkeypatch):
    import battery_checker.export as ex

    info, book = make_lot(5)
    real = ex.os.replace

    def locked(src, dst):
        if str(dst).endswith(".csv"):
            raise PermissionError(13, "Permission denied")
        return real(src, dst)

    monkeypatch.setattr(ex.os, "replace", locked)
    with pytest.raises(ExportError):
        export_lot(tmp_path, info, book.records, COND)
    assert list(tmp_path.iterdir()) == []


def test_ac14_export_2000_within_10s(tmp_path):
    info, book = make_lot(2000)
    t = time.perf_counter()
    res = export_lot(tmp_path, info, book.records, COND)
    elapsed = time.perf_counter() - t
    assert elapsed < 10.0, elapsed
    assert openpyxl.load_workbook(res.paths[1], read_only=True)[SHEET_RECORDS].max_row == 2001
