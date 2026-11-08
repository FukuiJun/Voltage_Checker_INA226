"""記録の CSV 形式（仕様 5.2）。作業ファイルと出力 CSV で同じ。

UTF-8（BOM 付き）・CRLF・カンマ区切り。1 行目は列名行、2 行目から番号順のデータ。
列は後ろに足せる形にしておく（Q-07：将来 ID 列を追加する場合も、既存の列の位置は変えない）。
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from .records import NOTE_SPREAD, Judge, Record

CSV_COLUMNS = ("no", "voltage_V", "judge", "min_V", "max_V", "measured_at", "remeasured", "note")
CSV_HEADER = ",".join(CSV_COLUMNS)
CSV_ENCODING = "utf-8-sig"
NEWLINE = "\r\n"
KNOWN_NOTES = ("", NOTE_SPREAD)

_INT_RE = re.compile(r"^\d+$")
_V_RE = re.compile(r"^-?\d+\.\d{4}$")
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_JUDGES = {j.value: j for j in Judge}


def record_fields(r: Record) -> list[str]:
    return [
        str(r.no),
        f"{r.voltage_V:.4f}",
        r.judge.value,
        f"{r.min_V:.4f}",
        f"{r.max_V:.4f}",
        r.measured_at,
        str(r.remeasured),
        r.note,
    ]


def format_row(r: Record) -> str:
    """1 件を CSV の 1 行（改行なし）にする。"""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(record_fields(r))
    return buf.getvalue()


def format_csv(records: Iterable[Record]) -> str:
    """列名行と全件。改行は CRLF（ファイルには newline="" で書く）。"""
    lines = [CSV_HEADER, *(format_row(r) for r in records)]
    return NEWLINE.join(lines) + NEWLINE


def parse_row(line: str) -> Record | None:
    """CSV の 1 行を記録に戻す。形式が違えば None（途中で切れた行など）。"""
    try:
        cols = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    if len(cols) < len(CSV_COLUMNS):
        return None
    no, v, j, vmin, vmax, at, rem, note = cols[: len(CSV_COLUMNS)]
    if not (_INT_RE.match(no) and _V_RE.match(v) and _V_RE.match(vmin) and _V_RE.match(vmax)):
        return None
    if j not in _JUDGES or not _DT_RE.match(at) or not _INT_RE.match(rem) or "�" in note:
        return None
    return Record(int(no), float(v), _JUDGES[j], float(vmin), float(vmax), at, int(rem), note)
