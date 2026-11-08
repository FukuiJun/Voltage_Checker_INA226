"""出力ファイル名（基名）の生成（仕様 5.2）。sanitize・unique_base_name は INA226_Monitor v1.2.1 と同じ。"""

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


def build_base_name(started_at: datetime, lot_name: str) -> str:
    """`<ロット開始日 YYYYMMDD>_<ロット名>_電圧記録`（例：20261106_LOT-A12_電圧記録）。"""
    parts = [started_at.strftime("%Y%m%d")]
    if lot_name.strip():
        parts.append(lot_name.strip())
    parts.append(BASE_SUFFIX)
    return sanitize("_".join(parts))


def unique_base_name(folder: Path, base: str, extensions=OUTPUT_EXTENSIONS) -> str:
    """同じ基名のファイルが 1 つでもあれば _2, _3 … を付ける。"""
    candidate = base
    k = 2
    while any((folder / f"{candidate}{ext}").exists() for ext in extensions):
        candidate = f"{base}_{k}"
        k += 1
    return candidate
