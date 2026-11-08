"""画面と Excel の配色（仕様 付録 B）・フォント。INA226_Monitor v1.2.1 の theme.py に色を足したもの。

色は colors.py だけで定義する（Excel 出力からも Qt なしで使うため）。余白・角丸などの見た目は style/app.qss にあり、
app.qss の「ドル記号＋{名前}」はこの COLORS で置き換える（名前_dis は無効時用の 40% 不透明の色）。
"""

from __future__ import annotations

import logging
from string import Template

from PySide6.QtGui import QFont, QIcon

from .colors import COLORS  # noqa: F401（画面の部品は theme.COLORS で読む）
from .resources import APP_ICON, APP_ICON_PNGS, STYLE_QSS, resource_path


CURRENT_LINE_WIDTH = 2.0
VOLTAGE_LINE_WIDTH = 2.0
DISABLED_OPACITY = 0.4

UI_FONTS = ["Yu Gothic UI", "Meiryo UI", "Meiryo", "Noto Sans CJK JP", "sans-serif"]
MONO_FONTS = ["Consolas", "DejaVu Sans Mono", "Noto Sans Mono", "monospace"]


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {round(alpha * 255)})"


def ui_font(px: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(UI_FONTS)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def mono_font(px: int, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FONTS)
    f.setStyleHint(QFont.Monospace)
    f.setFixedPitch(True)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def stylesheet() -> str:
    values = dict(COLORS)
    values.update({f"{k}_dis": rgba(v, DISABLED_OPACITY) for k, v in COLORS.items()})
    qss = resource_path(STYLE_QSS)
    values["style_dir"] = qss.parent.as_posix()  # 矢印などの画像の場所
    text = qss.read_text(encoding="utf-8")
    return Template(text).substitute(values)


def app_icon() -> QIcon:
    """ウィンドウ・タスクバー用のアイコン。.ico に加えて PNG も入れ、どれかが読めれば表示できるようにする。"""
    icon = QIcon()
    for rel in (APP_ICON, *APP_ICON_PNGS):
        path = resource_path(rel)
        if path.is_file():
            icon.addFile(str(path))
    if icon.isNull():
        logging.getLogger(__name__).warning("アプリのアイコンを読めません: %s", resource_path(APP_ICON))
    return icon


def apply(app) -> None:
    """QApplication に配色・フォント・スタイルを当てる。"""
    app.setStyle("Fusion")  # QSS の角丸・色が OS の標準スタイルに邪魔されないように
    app.setFont(ui_font(13))
    app.setStyleSheet(stylesheet())
