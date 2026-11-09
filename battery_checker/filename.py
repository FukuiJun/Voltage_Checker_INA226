"""出力ファイル名（基名）の生成。sanitize・unique_base_name は INA226_Monitor v1.2.1 と同じ。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

_FORBIDDEN_RE = re.compile(r'[\\/:*?"<>|\s]')

OUTPUT_EXTENSIONS = (".csv", ".xlsx")
BASE_SUFFIX = "電圧記録"


def sanitize(text: str) -> str:
    """ファイル名に使えない文字と空白を _ にし、末尾の . を削除する。"""
    return _FORBIDDEN_RE.sub("_", text).rstrip(".")


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%d_%H%M%S")


def build_base_name(lot_name: str, first_no: int, last_no: int, when: datetime) -> str:
    """出力ファイル名の初期値：`<ロット名>_<番号始め>-<番号終わり>_電圧記録_<日付 YYYYMMDD>`。

    例：LOT-A12_0001-0352_電圧記録_20261109。番号は 4 桁（0 で埋める。10000 以上はそのままの桁数）。
    日付は出力した日。ロット名が空なら飛ばす。
    """
    parts = [lot_name.strip()] if lot_name.strip() else []
    parts += [f"{first_no:04d}-{last_no:04d}", BASE_SUFFIX, when.strftime("%Y%m%d")]
    return sanitize("_".join(parts))


def unique_base_name(folder: Path, base: str, extensions=OUTPUT_EXTENSIONS) -> str:
    """同じ基名のファイルが 1 つでもあれば _2, _3 … を付ける。"""
    candidate = base
    k = 2
    while any((folder / f"{candidate}{ext}").exists() for ext in extensions):
        candidate = f"{base}_{k}"
        k += 1
    return candidate
