"""記録時の音（Q-08）：OK と NG で違う Windows の標準音。設定で消せる。"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def play(ok: bool) -> None:
    """鳴らして、すぐ戻る（Windows の MessageBeep は非同期）。"""
    try:
        if sys.platform == "win32":
            import winsound

            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        else:
            from PySide6.QtWidgets import QApplication

            QApplication.beep()
    except Exception:  # 音が鳴らなくても記録は続ける
        log.warning("音を鳴らせません", exc_info=True)
