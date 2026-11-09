"""前回値・設定の保存（%APPDATA%\\INA226_BatteryChecker\\settings.json）。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from pathlib import Path

log = logging.getLogger(__name__)


DEFAULT_CONNECT_THRESHOLD_V = 0.5  # 接続判定電圧（仕様 Q-03）
DEFAULT_STABLE_WIDTH_MV = 5.0  # 安定幅（仕様 Q-04）


@dataclass
class Settings:
    last_port: str = ""
    # ロットの前回値（F-02）
    lot_name: str = ""
    operator: str = ""  # 測定者の社員番号
    lower_V: float = 0.0  # 0 / 0 は「前回値なし」（Q-01：既定値は持たない）
    upper_V: float = 0.0
    save_dir: str = ""  # 出力先フォルダ（F-09）
    # 設定画面で変えられる値（Q-03, Q-04, Q-06, Q-08）
    connect_threshold_V: float = DEFAULT_CONNECT_THRESHOLD_V
    stable_width_mV: float = DEFAULT_STABLE_WIDTH_MV
    confirm_duplicate: bool = True
    confirm_ng: bool = True  # NG のときに「記録する／もう一度測定」を聞く
    sound: bool = True

    @classmethod
    def load(cls, path: Path) -> "Settings":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            log.warning("設定ファイルを読めません: %s", path, exc_info=True)
            return cls()
        s = cls()
        if not isinstance(raw, dict):
            return s
        for f in fields(cls):
            if f.name not in raw:
                continue
            value, default = raw[f.name], getattr(s, f.name)
            if isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if type(value) is type(default):
                setattr(s, f.name, value)
        return s

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            log.warning("設定ファイルを書けません: %s", path, exc_info=True)
