"""同梱ファイル（アイコン・スタイル）の場所。exe 化後は PyInstaller の展開先から読む。"""

from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parent.parent
    return root / relative


APP_ICON = "assets/app_icon.ico"
# .ico を読めなかったときの予備（PNG は Qt のプラグインなしで読める）
APP_ICON_PNGS = ("assets/app_icon_256.png", "assets/app_icon_512.png")
STYLE_QSS = "battery_checker/style/app.qss"
