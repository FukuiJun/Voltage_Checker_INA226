"""起動処理。`python -m battery_checker [--mock]` または exe から呼ばれる。"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="INA226_BatteryChecker")
    parser.add_argument("--mock", action="store_true", help="模擬シリアル（MOCK1〜）をポート一覧に出す（実機なしで試す）")
    parser.add_argument("--mock-speed", type=float, default=1.0, help="模擬シリアルの速さ（1 = 実時間）")
    args = parser.parse_args(argv)

    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, theme
    from .logsetup import setup_logging
    from .paths import AppPaths

    paths = AppPaths.default()
    setup_logging(paths.log_file)
    if sys.platform == "win32":
        # タスクバーに Python ではなくこのアプリのアイコンを出す
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"FukuiJun.{APP_NAME}")
        except Exception:
            pass

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    app.setWindowIcon(theme.app_icon())

    from .gui.main_window import MainWindow

    w = MainWindow(paths, mock=args.mock, mock_speed=args.mock_speed)
    w.show()
    QTimer.singleShot(0, w.check_resume)  # 途中のロットがあれば続けるか聞く（F-08）
    return app.exec()
