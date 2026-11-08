"""ビルドした exe にアプリアイコン（assets/app_icon.ico）が埋め込まれているか確かめる（CI 用）。

使い方: python tools/check_exe_icon.py dist/INA226_BatteryChecker/INA226_BatteryChecker.exe assets/app_icon.ico
.ico の画像がすべて exe のアイコンリソースに入っていれば 0、足りなければ 1 で終わる。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pefile


def ico_images(path: Path) -> dict[tuple[int, int], bytes]:
    """.ico の画像を {(幅, 高さ): データ} で返す（0 は 256 を表す）。"""
    raw = path.read_bytes()
    _, kind, count = struct.unpack_from("<HHH", raw, 0)
    if kind != 1:
        raise ValueError(f"{path} はアイコンファイルではありません")
    images = {}
    for i in range(count):
        w, h, _, _, _, _, size, offset = struct.unpack_from("<BBBBHHII", raw, 6 + 16 * i)
        images[(w or 256, h or 256)] = raw[offset : offset + size]
    return images


def exe_icon_images(path: Path) -> list[bytes]:
    """exe のリソース RT_ICON の中身をすべて返す。"""
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    out = []
    if not hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
        return out
    for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if entry.id != pefile.RESOURCE_TYPE["RT_ICON"]:
            continue
        for name_entry in entry.directory.entries:
            for lang_entry in name_entry.directory.entries:
                d = lang_entry.data.struct
                out.append(pe.get_data(d.OffsetToData, d.Size))
    return out


def main(argv: list[str]) -> int:
    exe, ico = Path(argv[1]), Path(argv[2])
    expected = ico_images(ico)
    embedded = set(exe_icon_images(exe))
    missing = [size for size, data in expected.items() if data not in embedded]
    # Windows の CI はコンソールが cp1252 なので、出力は ASCII だけにする
    print(f"{exe.name}: {len(embedded)} icon images / {ico.name}: {len(expected)} sizes")
    if missing:
        print("MISSING: " + ", ".join(f"{w}x{h}" for w, h in sorted(missing)))
        return 1
    print("OK: all sizes embedded (" + ", ".join(f"{w}x{h}" for w, h in sorted(expected)) + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
