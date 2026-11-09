import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---- テストの後の終了処理（Windows の CI 対策）
# Windows の CI で、全件合格して結果を表示したあと、Python の終了処理（Qt の画面の後片付け）で
# まれに落ち、終了コードが 1 になることがあった（テスト結果とは無関係）。
# 結果は確定しているので、Qt の画面を閉じてから、pytest の終了コードのまま終わる。
_EXIT = {"status": None}


def pytest_sessionfinish(session, exitstatus):
    _EXIT["status"] = int(exitstatus)


def pytest_unconfigure(config):
    if _EXIT["status"] is None or not (sys.platform == "win32" or os.environ.get("CI")):
        return
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            for w in app.topLevelWidgets():
                w.close()
            app.processEvents()
    except Exception:
        pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_EXIT["status"])
