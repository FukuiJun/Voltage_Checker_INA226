"""記録時の音（Q-08）：OK と NG で違う音。設定で消せる。

- OK：同梱の効果音 assets/sound_ok.wav（無ければ Windows の標準音）
- NG：Windows の警告音（MB_ICONHAND）
どちらも鳴らし始めたらすぐ戻る（記録の処理を待たせない）。
"""

from __future__ import annotations

import logging
import sys

from .resources import SOUND_OK, resource_path

log = logging.getLogger(__name__)


def play(ok: bool) -> None:
    """鳴らして、すぐ戻る（PlaySound の SND_ASYNC・MessageBeep はどちらも非同期）。"""
    try:
        if sys.platform == "win32":
            import winsound

            wav = resource_path(SOUND_OK)
            if ok and wav.is_file():
                # SND_NODEFAULT：読めなくても既定の音に置き換えない（下で標準音を鳴らす）
                flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
                try:
                    winsound.PlaySound(str(wav), flags)
                    return
                except RuntimeError:
                    log.warning("OK の効果音を鳴らせません: %s", wav, exc_info=True)
            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        else:
            from PySide6.QtWidgets import QApplication

            QApplication.beep()
    except Exception:  # 音が鳴らなくても記録は続ける
        log.warning("音を鳴らせません", exc_info=True)
