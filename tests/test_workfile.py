"""作業ファイルの自動保存と再開（F-08, AC-09, AC-10）。"""

import json
from datetime import datetime, timedelta

import pytest

from battery_checker.csvformat import CSV_HEADER
from battery_checker.records import LotInfo, Measurement, RecordBook, make_record
from battery_checker.workfile import (
    WorkFile,
    cleanup_finished,
    find_unfinished,
    finish_lot,
    load_lot,
    new_lot_id,
    read_records,
)

T = datetime(2026, 11, 6, 14, 30, 0)


def new_lot(tmp_path, start_no=1, name="LOT-A12"):
    lot_id = new_lot_id(tmp_path, T)
    info = LotInfo(lot_id, name, 3.6, 4.2, start_no, "福井", "メモ", started_at="2026-11-06 14:30:00")
    book = RecordBook(start_no)
    return info, book, WorkFile.create(tmp_path, info, book)


def add(info, book, wf, v, spread=0.0):
    m = Measurement(v, round(v - spread, 4), round(v + spread, 4))
    book.append(make_record(book.next_no, m, info.lower_V, info.upper_V, 0.005, T))
    info.revision += 1
    assert wf.save(info, book, appended=True)


def test_create_writes_header_and_info(tmp_path):
    info, book, wf = new_lot(tmp_path)
    assert wf.csv_path.read_bytes() == b"\xef\xbb\xbf" + CSV_HEADER.encode() + b"\r\n"
    raw = json.loads(wf.json_path.read_text(encoding="utf-8"))
    assert raw["lot_name"] == "LOT-A12" and raw["finished"] is False and raw["start_no"] == 1
    # 同じ秒のロットは _2
    assert new_lot_id(tmp_path, T) == "20261106_143000_2"


def test_ac09_resume_after_kill(tmp_path):
    """300 件記録した状態で強制終了 → 再開すると 300 件と次の番号 301 が戻る。"""
    info, book, wf = new_lot(tmp_path)
    for i in range(300):
        add(info, book, wf, 3.7 + (i % 50) / 1000)
    del wf  # プロセスが落ちた（後片付けなし）
    entries = find_unfinished(tmp_path)
    assert [e.lot_id for e in entries] == [info.lot_id]
    state, wf2 = load_lot(entries[0])
    assert len(state.book) == 300 and state.book.next_no == 301
    assert state.dropped_no is None and not state.warnings
    assert state.info.lot_name == "LOT-A12" and state.info.lower_V == 3.6 and state.info.memo == "メモ"
    assert [r.voltage_V for r in state.book.records] == [r.voltage_V for r in book.records]


def test_ac10_truncated_last_line_is_dropped(tmp_path):
    info, book, wf = new_lot(tmp_path)
    for v in (3.81, 3.82, 3.83):
        add(info, book, wf, v)
    data = wf.csv_path.read_bytes()
    wf.csv_path.write_bytes(data[:-20])  # 最終行（No.3）の途中で切れた
    state, wf2 = load_lot(find_unfinished(tmp_path)[0])
    assert len(state.book) == 2 and state.book.next_no == 3
    assert state.dropped_no == 3
    # 作業ファイルも壊れた行を除いて書き直されている
    records, dropped, _ = read_records(wf2.csv_path, 1)
    assert len(records) == 2 and dropped is None
    assert wf2.csv_path.read_bytes().endswith(b"\r\n")


def test_truncated_note_is_detected(tmp_path):
    """備考の途中で切れた最終行（列数は合っている）も壊れているとみなす。"""
    info, book, wf = new_lot(tmp_path)
    add(info, book, wf, 3.81)
    add(info, book, wf, 3.82, spread=0.004)  # ばらつき大
    data = wf.csv_path.read_bytes()
    assert data.endswith("ばらつき大\r\n".encode())
    wf.csv_path.write_bytes(data[:-5])  # 「ばらつ」の途中
    records, dropped, _ = read_records(wf.csv_path, 1)
    assert [r.no for r in records] == [1] and dropped == 2


def test_complete_last_line_without_newline_is_kept(tmp_path):
    info, book, wf = new_lot(tmp_path)
    add(info, book, wf, 3.81)
    add(info, book, wf, 3.82)
    wf.csv_path.write_bytes(wf.csv_path.read_bytes()[:-2])  # 改行だけ書けなかった
    records, dropped, _ = read_records(wf.csv_path, 1)
    assert len(records) == 2 and dropped is None


def test_rewrite_after_undo_and_remeasure(tmp_path):
    info, book, wf = new_lot(tmp_path, start_no=11)
    for v in (3.81, 3.82, 3.83):
        add(info, book, wf, v)
    book.undo_last()
    book.replace(11, make_record(11, Measurement(3.9, 3.9, 3.9), 3.6, 4.2, 0.005, T))
    assert wf.save(info, book)
    records, _, _ = read_records(wf.csv_path, 11)
    assert [(r.no, r.voltage_V, r.remeasured) for r in records] == [(11, 3.9, 1), (12, 3.82, 0)]


def test_write_failure_is_rewritten_next_time(tmp_path, monkeypatch):
    """書けなかった分は次の記録時にまとめて書き直す（F-04 の異常時）。"""
    import battery_checker.workfile as wfmod

    info, book, wf = new_lot(tmp_path)
    add(info, book, wf, 3.81)
    real = wfmod._fsync_write

    def broken(path, data, mode="wb"):
        raise PermissionError("locked")

    monkeypatch.setattr(wfmod, "_fsync_write", broken)
    book.append(make_record(2, Measurement(3.82, 3.82, 3.82), 3.6, 4.2, 0.005, T))
    assert not wf.save(info, book, appended=True)
    assert wf.dirty
    monkeypatch.setattr(wfmod, "_fsync_write", real)
    book.append(make_record(3, Measurement(3.83, 3.83, 3.83), 3.6, 4.2, 0.005, T))
    assert wf.save(info, book, appended=True)
    assert not wf.dirty
    records, _, _ = read_records(wf.csv_path, 1)
    assert [r.no for r in records] == [1, 2, 3]


def test_finish_and_cleanup(tmp_path):
    info, book, wf = new_lot(tmp_path)
    add(info, book, wf, 3.81)
    finish_lot(wf, info, when=T)
    assert find_unfinished(tmp_path) == []
    # 30 日以内は残す、過ぎたら消す
    assert cleanup_finished(tmp_path, now=T + timedelta(days=29)) == []
    assert wf.csv_path.exists()
    assert cleanup_finished(tmp_path, now=T + timedelta(days=31)) == [info.lot_id]
    assert not wf.csv_path.exists() and not wf.json_path.exists()


def test_unfinished_newest_first_and_broken_json_skipped(tmp_path):
    a, _, _ = new_lot(tmp_path, name="A")
    b, _, _ = new_lot(tmp_path, name="B")
    (tmp_path / "20261107_000000.json").write_text("{broken", encoding="utf-8")
    assert [e.info.lot_name for e in find_unfinished(tmp_path)] == ["B", "A"]


def test_header_mismatch_raises(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("a,b\r\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_records(p, 1)


def test_create_fails_when_folder_cannot_be_made(tmp_path):
    ro = tmp_path / "ro"
    ro.write_text("ファイルなのでフォルダを作れない")
    with pytest.raises(OSError):
        new_lot(ro / "lots")
