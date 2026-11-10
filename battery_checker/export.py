"""出力：CSV と Excel（.xlsx）（仕様 F-09, 5.2）。GUI なしでテストできる。

- CSV：番号・電圧・判定・記録日時・測定者（社員番号）・備考。UTF-8（BOM 付き）・CRLF
- xlsx：「記録」「集計」「条件」の 3 シート（openpyxl）

両方を一時ファイルに書いてから置き換える。どちらかが書けなければ、今回書いた方も消して
両方とも失敗にする（INA226_Monitor の保存と同じ考え方）。
ファイル名の初期値は `<ロット名>_<番号始め>-<番号終わり>_電圧記録_<出力した日>`（出力の画面で変えられる）。
同じロットを同じフォルダに同じ名前でもう一度出力したときは、前回の出力を上書きする。
それ以外で同じ名前のファイルがあるときは _2, _3 … を付ける（ほかのファイルは上書きしない）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path

from . import APP_NAME, __version__
from .csvformat import CSV_ENCODING, format_export_csv
from .filename import OUTPUT_EXTENSIONS, build_base_name, sanitize, unique_base_name
from .records import (
    SAMPLES_PER_RECORD,
    Judge,
    LotInfo,
    Record,
    compute_stats,
    histogram,
)
from .colors import XLSX_COLORS

log = logging.getLogger(__name__)

SHEET_RECORDS = "記録"
SHEET_SUMMARY = "集計"
SHEET_CONDITIONS = "条件"
RECORD_HEADERS = ("No", "電圧 [V]", "判定", "記録日時", "測定者（社員番号）", "備考")
METHOD_TEXT = f"{SAMPLES_PER_RECORD} 回測定の中央値"


@dataclass
class ExportResult:
    folder: Path
    base: str
    paths: list[Path] = field(default_factory=list)
    overwritten: bool = False  # 前回の出力を上書きした


class ExportError(Exception):
    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path.name} に書き込めません。Excel などで開いていないか確認してください")
        self.path = path
        self.cause = cause


@dataclass(frozen=True)
class Conditions:
    """「条件」シートの内容。"""

    port: str = ""
    interval_ms: int | None = None
    firmware: int | None = None

    @property
    def firmware_text(self) -> str:
        return f"fw={self.firmware}" if self.firmware is not None else "不明"

    @property
    def interval_text(self) -> str:
        return f"{self.interval_ms} ms" if self.interval_ms is not None else "不明"


def default_base(info: LotInfo, records: Sequence[Record], when: datetime | None = None) -> str:
    """ファイル名の初期値：`<ロット名>_<番号始め>-<番号終わり>_電圧記録_<YYYYMMDD>`。"""
    nos = [r.no for r in records]
    first = min(nos) if nos else info.start_no
    last = max(nos) if nos else info.start_no
    return build_base_name(info.lot_name, first, last, when or datetime.now())


def resolve_base(folder: Path, info: LotInfo, base: str) -> tuple[str, bool]:
    """実際に使う基名と、前回の出力を上書きするか。

    同じロットを同じフォルダに同じ名前で出力し直すときは上書き。
    それ以外で同じ名前のファイルがあれば _2, _3 … を付ける。
    """
    folder = Path(folder)
    base = sanitize(base.strip())
    if info.last_export_base == base and _same_dir(info.last_export_dir, folder):
        return base, True
    return unique_base_name(folder, base, OUTPUT_EXTENSIONS), False


def _same_dir(a: str, b: Path) -> bool:
    if not a:
        return False
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except (OSError, ValueError):
        return False


# ================================================================ CSV
def write_csv(path: Path, records: Sequence[Record]) -> None:
    with open(path, "w", encoding=CSV_ENCODING, newline="") as f:
        f.write(format_export_csv(sorted(records, key=lambda r: r.no)))


# ================================================================ xlsx
def _text_width(value) -> int:
    """列幅の目安（全角は 2 文字分）。"""
    s = "" if value is None else str(value)
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def _fit_columns(ws, min_width: int = 6, max_width: int = 60) -> None:
    from openpyxl.utils import get_column_letter

    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            fmt = cell.number_format
            if isinstance(cell.value, float) and fmt.startswith("0."):
                w = len(f"{cell.value:.{len(fmt) - 2}f}")
            elif isinstance(cell.value, float) and fmt.endswith("%"):
                w = 7
            else:
                w = _text_width(cell.value)
            widths[cell.column] = max(widths.get(cell.column, 0), w)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(min_width, min(max_width, w + 2))


def operators_text(info: LotInfo, records: Sequence[Record]) -> str:
    """集計の測定者：記録に出てくる社員番号を出てきた順に（記録がなければロットの社員番号）。"""
    seen: list[str] = []
    for r in records:
        if r.operator and r.operator not in seen:
            seen.append(r.operator)
    return "、".join(seen) if seen else info.operator


def build_workbook(info: LotInfo, records: Sequence[Record], cond: Conditions):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_fill = PatternFill("solid", fgColor=XLSX_COLORS["header_bg"])
    header_font = Font(bold=True, color=XLSX_COLORS["header_text"])
    ng_fill = PatternFill("solid", fgColor=XLSX_COLORS["ng_bg"])
    ng_font = Font(bold=True, color=XLSX_COLORS["ng_text"])
    bold = Font(bold=True)
    records = sorted(records, key=lambda r: r.no)
    stats = compute_stats(records)

    wb = Workbook()
    # ---- 記録
    ws = wb.active
    ws.title = SHEET_RECORDS
    ws.append(list(RECORD_HEADERS))
    for c in ws[1]:
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")
    for r in records:
        ws.append([r.no, r.voltage_V, r.judge.label, r.measured_at, r.operator, r.note])
    for row in ws.iter_rows(min_row=2, max_row=len(records) + 1):
        no_c, v_c, j_c, at_c, op_c = row[:5]
        v_c.number_format = "0.0000"
        op_c.number_format = "@"  # 社員番号は文字列（先頭の 0 を残す）
        j_c.alignment = Alignment(horizontal="center")
        if j_c.value != Judge.OK.label:
            j_c.fill = ng_fill
            j_c.font = ng_font
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(RECORD_HEADERS))}{len(records) + 1}"
    _fit_columns(ws)

    # ---- 集計
    ss = wb.create_sheet(SHEET_SUMMARY)
    end_text = info.finished_at if info.finished else (records[-1].measured_at if records else "")

    def v4(x):
        return round(x, 4) if x is not None else "-"

    rows = [
        ("ロット名", info.lot_name),
        ("測定者（社員番号）", operators_text(info, records)),
        ("開始日時", info.started_at),
        ("終了日時", end_text),
        ("下限 [V]", info.lower_V),
        ("上限 [V]", info.upper_V),
        ("件数", stats.count),
        ("OK 数", stats.ok),
        ("NG 数", stats.ng),
        ("　うち下限未満", stats.ng_low),
        ("　うち上限超え", stats.ng_high),
        ("OK 率", stats.ok_rate if stats.ok_rate is not None else "-"),
        ("平均 [V]", v4(stats.mean)),
        ("中央値 [V]", v4(stats.median)),
        ("最小 [V]", v4(stats.min_V)),
        ("最小の No", stats.min_no if stats.min_no is not None else "-"),
        ("最大 [V]", v4(stats.max_V)),
        ("最大の No", stats.max_no if stats.max_no is not None else "-"),
        ("標準偏差 [V]", round(stats.stdev, 5) if stats.stdev is not None else "-"),
    ]
    ss.append(["項目", "値"])
    for row in rows:
        ss.append(list(row))
    for c in ss[1]:
        c.fill, c.font = header_fill, header_font
    for (label, value), excel_row in zip(rows, range(2, len(rows) + 2)):
        cell = ss.cell(row=excel_row, column=2)
        ss.cell(row=excel_row, column=1).font = bold
        if isinstance(value, float):
            if label == "OK 率":
                cell.number_format = "0.0%"
            elif label.startswith("標準偏差"):
                cell.number_format = "0.00000"
            elif label.startswith(("下限", "上限")):
                cell.number_format = "0.000"
            else:
                cell.number_format = "0.0000"
        cell.alignment = Alignment(horizontal="left")
    # 度数分布表（D〜E 列）とグラフ
    bins = histogram([r.voltage_V for r in records], info.lower_V, info.upper_V)
    ss.cell(row=1, column=4, value="電圧の区間 [V]")
    ss.cell(row=1, column=5, value="件数")
    for c in (ss.cell(row=1, column=4), ss.cell(row=1, column=5)):
        c.fill, c.font = header_fill, header_font
    for i, b in enumerate(bins, start=2):
        ss.cell(row=i, column=4, value=b.label)
        ss.cell(row=i, column=5, value=b.count)
    note_row = len(bins) + 3
    ss.cell(row=note_row, column=4, value="区間は [下端, 上端)。10 mV 刻み、下限−50 mV〜上限＋50 mV（範囲外は「未満」「超え」にまとめる）")
    chart = BarChart()
    chart.type = "col"
    chart.title = "電圧の度数分布"
    chart.y_axis.title = "件数"
    chart.x_axis.title = "電圧 [V]"
    chart.legend = None
    chart.gapWidth = 10
    data = Reference(ss, min_col=5, min_row=1, max_row=len(bins) + 1)
    cats = Reference(ss, min_col=4, min_row=2, max_row=len(bins) + 1)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.series[0].graphicalProperties.solidFill = XLSX_COLORS["bar"]
    chart.series[0].graphicalProperties.line.solidFill = XLSX_COLORS["bar"]
    chart.width = 26
    chart.height = 12
    ss.add_chart(chart, "G2")
    _fit_columns(ss, max_width=24)
    ss.column_dimensions["D"].width = 18

    # ---- 条件
    cs = wb.create_sheet(SHEET_CONDITIONS)
    cs.append(["項目", "値"])
    for c in cs[1]:
        c.fill, c.font = header_fill, header_font
    for row in (
        ("ソフトウェア", f"{APP_NAME} v{__version__}"),
        ("スケッチの版", cond.firmware_text),
        ("送信周期", cond.interval_text),
        ("記録方法", METHOD_TEXT),
        ("COM ポート", cond.port or "不明"),
        ("メモ", info.memo),
    ):
        cs.append(list(row))
    for r in range(2, cs.max_row + 1):
        cs.cell(row=r, column=1).font = bold
        cs.cell(row=r, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    _fit_columns(cs)
    return wb


def write_xlsx(path: Path, info: LotInfo, records: Sequence[Record], cond: Conditions) -> None:
    build_workbook(info, records, cond).save(path)


# ================================================================ まとめて出力
def export_lot(
    folder: Path,
    info: LotInfo,
    records: Sequence[Record],
    cond: Conditions,
    base: str | None = None,
    when: datetime | None = None,
) -> ExportResult:
    """`<基名>.csv` と `<基名>.xlsx` を保存する。失敗したら ExportError（両方とも残さない）。

    base を省くと初期値（default_base）。
    """
    folder = Path(folder)
    wanted = base if base and sanitize(base.strip()) else default_base(info, records, when)
    base, overwritten = resolve_base(folder, info, wanted)
    result = ExportResult(folder, base, overwritten=overwritten)
    csv_path, xlsx_path = (folder / f"{base}{ext}" for ext in OUTPUT_EXTENSIONS)
    tmps = {p: p.with_name(f"~{p.stem}.tmp{p.suffix}") for p in (csv_path, xlsx_path)}

    def cleanup(paths) -> None:
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    # 1) 一時ファイルに書く
    for final, fn in ((csv_path, lambda p: write_csv(p, records)), (xlsx_path, lambda p: write_xlsx(p, info, records, cond))):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            fn(tmps[final])
        except OSError as e:
            log.warning("出力に失敗: %s", final, exc_info=True)
            cleanup(tmps.values())
            raise ExportError(final, e) from e
    # 2) 置き換える（Excel で開いていると、ここで失敗する）。xlsx を先にする
    for final in (xlsx_path, csv_path):
        try:
            os.replace(tmps[final], final)
        except OSError as e:
            log.warning("出力に失敗: %s", final, exc_info=True)
            cleanup([*result.paths, *tmps.values()])
            raise ExportError(final, e) from e
        result.paths.append(final)
    result.paths.sort(key=lambda p: p.suffix)  # .csv, .xlsx の順
    return result
