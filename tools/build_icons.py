"""アプリアイコン（assets/app_icon.svg）から PNG と ICO を作る。

使い方: python tools/build_icons.py
  assets/app_icon.svg を読み、次を書き出す（ファイル名は INA226_BatteryChecker.spec・resources.py と同じ）
  - assets/app_icon_256.png, assets/app_icon_512.png
  - assets/app_icon.ico（16, 20, 24, 32, 40, 48, 64, 96, 128, 256 px。各サイズを SVG から直接描くので小さくてもぼやけない）

SVG の文字（「INA226」）は、このスクリプトを動かした PC のフォントで描かれて PNG・ICO に焼き込まれる
（Consolas、無ければ DejaVu Sans Mono など）。exe はこの PNG・ICO を使うので、配布先のフォントには左右されない。
必要なもの：PySide6、Pillow（requirements-dev.txt）
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SVG = ASSETS / "app_icon.svg"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
PNG_SIZES = (256, 512)


def render(renderer: QSvgRenderer, px: int) -> Image.Image:
    """SVG を px 角の透過 PNG に描いて Pillow の画像にする。"""
    img = QImage(px, px, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


_APP: QGuiApplication | None = None  # 文字（フォント）を描くのに必要。描き終わるまで持っておく


def main() -> int:
    global _APP
    _APP = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    renderer = QSvgRenderer(QByteArray(SVG.read_bytes()))
    if not renderer.isValid():
        print(f"SVG を読めません: {SVG}")
        return 1
    for px in PNG_SIZES:
        out = ASSETS / f"app_icon_{px}.png"
        render(renderer, px).save(out, optimize=True)
        print(f"wrote {out.relative_to(ROOT)}")
    images = [render(renderer, px) for px in ICO_SIZES]
    largest = images[-1]
    ico = ASSETS / "app_icon.ico"
    largest.save(ico, format="ICO", sizes=[(px, px) for px in ICO_SIZES], append_images=images[:-1])
    print(f"wrote {ico.relative_to(ROOT)} ({', '.join(str(px) for px in ICO_SIZES)} px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
