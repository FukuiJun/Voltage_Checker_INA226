"""設定・ログ・作業ファイルの置き場所（仕様 4 章・5.2）。

Windows では %APPDATA% / %LOCALAPPDATA% を使う（管理者権限不要）。
それ以外の OS（開発用）ではホームディレクトリ配下を使う。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME


@dataclass(frozen=True)
class AppPaths:
    config_dir: Path  # 設定ファイル・ログ
    lots_dir: Path  # ロットの作業ファイル（記録のたびに保存）

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def log_file(self) -> Path:
        return self.config_dir / "app.log"

    @classmethod
    def default(cls) -> "AppPaths":
        appdata = os.environ.get("APPDATA")
        local = os.environ.get("LOCALAPPDATA")
        home = Path.home()
        config_base = Path(appdata) if appdata else home / ".config"
        local_base = Path(local) if local else home / ".local" / "share"
        return cls(
            config_dir=config_base / APP_NAME,
            lots_dir=local_base / APP_NAME / "lots",
        )

    @classmethod
    def under(cls, root: Path) -> "AppPaths":
        """テスト用：1 つのフォルダの下にまとめる。"""
        root = Path(root)
        return cls(config_dir=root / "config", lots_dir=root / "lots")


def default_save_dir() -> Path:
    docs = Path.home() / "Documents"
    return docs if docs.is_dir() else Path.home()
