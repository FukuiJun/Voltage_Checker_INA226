"""記録の計算（F-04〜F-07）と集計・度数分布（F-10, 5.2）。"""

from datetime import datetime

import pytest

from battery_checker.records import (
    NOTE_SPREAD,
    Judge,
    Measurement,
    RecordBook,
    compute_stats,
    histogram,
    judge,
    make_record,
    summarize_samples,
    summary_text,
)

T = datetime(2026, 11, 6, 14, 30, 5)
WIDTH = 0.005


def rec(book: RecordBook, v: float, lo=3.6, hi=4.2, spread=0.0):
    m = Measurement(v, round(v - spread / 2, 4), round(v + spread / 2, 4))
    return book.append(make_record(book.next_no, m, lo, hi, WIDTH, T))


def test_ac02_median_min_max():
    m = summarize_samples([3.8125, 3.8113, 3.8138, 3.8125, 3.8100])
    assert (m.median_V, m.min_V, m.max_V) == (3.8125, 3.8100, 3.8138)


def test_ac03_judge_boundaries():
    assert judge(3.6000, 3.600, 4.200) is Judge.OK
    assert judge(4.2000, 3.600, 4.200) is Judge.OK
    assert judge(3.5999, 3.600, 4.200) is Judge.NG_LOW
    assert judge(4.2001, 3.600, 4.200) is Judge.NG_HIGH


def test_spread_note():
    # 5 個の最大−最小が安定幅（5 mV）を超えたら「ばらつき大」。ちょうど 5 mV はばらつき大にしない
    r = make_record(1, Measurement(3.81, 3.8075, 3.8125), 3.6, 4.2, WIDTH, T)
    assert r.note == ""
    r = make_record(1, Measurement(3.81, 3.8075, 3.8138), 3.6, 4.2, WIDTH, T)
    assert r.note == NOTE_SPREAD and r.has_warning
    assert r.measured_at == "2026-11-06 14:30:05"


def test_ac07_numbering_after_undo():
    """開始番号 1 で 5 件記録 → 2 件取り消し → 3 件記録 → 番号が 1〜6 で欠番・重複が無い。"""
    b = RecordBook(1)
    for _ in range(5):
        rec(b, 3.8)
    assert b.next_no == 6
    assert b.undo_last().no == 5
    assert b.undo_last().no == 4
    assert b.next_no == 4
    for _ in range(3):
        rec(b, 3.9)
    assert [r.no for r in b.records] == [1, 2, 3, 4, 5, 6]
    assert b.next_no == 7


def test_start_number_and_append_must_be_consecutive():
    b = RecordBook(101)
    assert rec(b, 3.8).no == 101
    with pytest.raises(ValueError):
        b.append(make_record(105, Measurement(3.8, 3.8, 3.8), 3.6, 4.2, WIDTH, T))
    with pytest.raises(IndexError):
        RecordBook().undo_last()


def test_ac08_remeasure_replaces():
    b = RecordBook(1)
    for v in (3.8, 3.81, 3.3, 3.83):
        rec(b, v)
    assert b.get(3).judge is Judge.NG_LOW
    later = datetime(2026, 11, 6, 15, 0, 0)
    new = b.replace(3, make_record(999, Measurement(3.85, 3.849, 3.851), 3.6, 4.2, WIDTH, later))
    assert new.no == 3 and new.remeasured == 1
    assert (new.voltage_V, new.judge, new.measured_at) == (3.85, Judge.OK, "2026-11-06 15:00:00")
    assert len(b) == 4 and b.next_no == 5
    again = b.replace(3, make_record(3, Measurement(3.86, 3.86, 3.86), 3.6, 4.2, WIDTH, later))
    assert again.remeasured == 2


def test_rejudge():
    b = RecordBook(1)
    for v in (3.55, 3.65, 4.25):
        rec(b, v)
    assert [r.judge for r in b.records] == [Judge.NG_LOW, Judge.OK, Judge.NG_HIGH]
    assert b.rejudge(3.5, 4.3) == 2
    assert all(r.judge is Judge.OK for r in b.records)


def test_stats():
    b = RecordBook(1)
    for v in (3.8125, 3.7950, 3.4012, 3.8913, 3.8000):
        rec(b, v)
    s = compute_stats(b.records)
    assert (s.count, s.ok, s.ng_low, s.ng_high, s.ng) == (5, 4, 1, 0, 1)
    assert s.ok_rate == pytest.approx(0.8)
    assert s.mean == pytest.approx((3.8125 + 3.7950 + 3.4012 + 3.8913 + 3.8000) / 5)
    assert s.median == pytest.approx(3.8000)
    assert (s.min_V, s.min_no, s.max_V, s.max_no) == (3.4012, 3, 3.8913, 4)
    import statistics

    assert s.stdev == pytest.approx(statistics.stdev([3.8125, 3.7950, 3.4012, 3.8913, 3.8000]))
    assert summary_text(s) == (
        "件数 5 ／ OK 4 ／ NG 1（低 1・高 0）／ 平均 3.7400 V ／ 最小 3.4012 V（No.3）／ 最大 3.8913 V（No.4）"
    )
    assert compute_stats([]).count == 0 and summary_text(compute_stats([])) == "件数 0"


def test_histogram_bins():
    vals = [3.5000, 3.5499, 3.5500, 3.5599, 3.5600, 3.6000, 4.2000, 4.2499, 4.2500, 4.2501, 5.0]
    bins = histogram(vals, 3.6, 4.2)
    assert bins[0].label == "3.550 V 未満" and bins[0].count == 2
    assert bins[-1].label == "4.250 V 超え" and bins[-1].count == 2
    inner = bins[1:-1]
    assert len(inner) == 70  # (4.25 - 3.55) / 0.010
    assert inner[0].label == "3.550–3.560" and inner[0].count == 2
    assert inner[1].count == 1  # 3.5600
    assert inner[5].count == 1  # 3.6000 は [3.600, 3.610)
    assert inner[-1].count == 2  # 4.2499 と、右端ちょうどの 4.2500
    assert sum(b.count for b in bins) == len(vals)
