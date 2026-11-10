"""記録の CSV 形式（仕様 5.2）。作業ファイル用と出力用の 2 つ。

UTF-8（BOM 付き）・CRLF・カンマ区切り。1 行目は列名行、2 行目から番号順のデータ。
- 作業ファイル（内部用）：再開と画面表示に要る列をすべて持つ（min/max・測り直し回数・測定者）。
  測定者は備考の前に置く（最終行が途中で切れたら、列数か備考のチェックで弾けるように）。
  v0.1.5 までの作業ファイル（測定者の列なし）も読める
- 出力 CSV：作業者が使う列だけ（番号・電圧・判定・記録日時・測定者・備考）
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable

from .records import NOTE_SPREAD, Judge, Record

CSV_COLUMNS = ("no", "voltage_V", "judge", "min_V", "max_V", "measured_at", "remeasured", "operator_id", "note")
CSV_HEADER = ",".join(CSV_COLUMNS)
# v0.1.5 までの作業ファイル（測定者の列なし）
LEGACY_CSV_COLUMNS = ("no", "voltage_V", "judge", "min_V", "max_V", "measured_at", "remeasured", "note")
LEGACY_CSV_HEADER = ",".join(LEGACY_CSV_COLUMNS)
# 出力 CSV
EXPORT_COLUMNS = ("no", "voltage_V", "judge", "measured_at", "operator_id", "note")
EXPORT_HEADER = ",".join(EXPORT_COLUMNS)
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
        r.operator,
        r.note,
    ]


def export_fields(r: Record) -> list[str]:
    return [str(r.no), f"{r.voltage_V:.4f}", r.judge.value, r.measured_at, r.operator, r.note]


def _join(fields: list[str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(fields)
    return buf.getvalue()


def format_row(r: Record) -> str:
    """1 件を作業ファイルの 1 行（改行なし）にする。"""
    return _join(record_fields(r))


def format_csv(records: Iterable[Record]) -> str:
    """作業ファイルの列名行と全件。改行は CRLF（ファイルには newline="" で書く）。"""
    lines = [CSV_HEADER, *(format_row(r) for r in records)]
    return NEWLINE.join(lines) + NEWLINE


def format_export_csv(records: Iterable[Record]) -> str:
    """出力 CSV の列名行と全件（番号・電圧・判定・記録日時・測定者・備考）。"""
    lines = [EXPORT_HEADER, *(_join(export_fields(r)) for r in records)]
    return NEWLINE.join(lines) + NEWLINE


def parse_row(line: str, legacy: bool = False) -> Record | None:
    """作業ファイルの 1 行を記録に戻す。形式が違えば None（途中で切れた行など）。

    legacy=True は v0.1.5 までの形式（測定者の列なし。測定者は空で返す）。
    """
    try:
        cols = next(csv.reader([line]))
    except (csv.Error, StopIteration):
        return None
    n = len(LEGACY_CSV_COLUMNS if legacy else CSV_COLUMNS)
    if len(cols) < n:
        return None
    if legacy:
        no, v, j, vmin, vmax, at, rem, note = cols[:n]
        operator = ""
    else:
        no, v, j, vmin, vmax, at, rem, operator, note = cols[:n]
    if not (_INT_RE.match(no) and _V_RE.match(v) and _V_RE.match(vmin) and _V_RE.match(vmax)):
        return None
    if j not in _JUDGES or not _DT_RE.match(at) or not _INT_RE.match(rem) or "�" in note or "�" in operator:
        return None
    return Record(int(no), float(v), _JUDGES[j], float(vmin), float(vmax), at, int(rem), note, operator)
