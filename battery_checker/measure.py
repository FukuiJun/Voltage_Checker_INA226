"""電圧の監視（F-03）と、記録ボタンの後の 5 回測定（F-04）。GUI なしでテストできる。

どちらも「データ行の bus_V と、その行を受け取った時刻」を順に渡して使う。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum

from .records import SAMPLES_PER_RECORD, Measurement, summarize_samples

STABLE_WINDOW_S = 0.5  # 「安定」を判定する時間幅（F-03：直近 0.5 秒）
STABLE_MIN_COVER = 0.9  # 繋いでから、時間幅の 9 割（40 ms 周期で約 12 個）がたまるまでは「安定」にしない
COLLECT_TIMEOUT_S = 1.0  # この時間内に 5 個そろわなければ記録しない（F-04）


class BatteryState(Enum):
    NO_DATA = "データなし"
    NONE = "バッテリ未接続"
    UNSTABLE = "変動中"
    STABLE = "安定"


class BatteryMonitor:
    """最新の電圧と、バッテリが繋がっているか・安定しているかを見る（F-03）。

    detached_since_mark は、mark_recorded() のあと一度でも接続判定電圧未満になったか
    （＝バッテリを外したか）。同じバッテリの二重記録の確認（F-04, Q-06）に使う。
    """

    def __init__(self, threshold_V: float, stable_width_V: float, window_s: float = STABLE_WINDOW_S) -> None:
        self.threshold_V = threshold_V
        self.stable_width_V = stable_width_V
        self.window_s = window_s
        self._recent: deque[tuple[float, float]] = deque()
        self.last_V: float | None = None
        self.last_t: float | None = None
        self.connected_since: float | None = None  # 接続判定電圧以上になった時刻
        self.detached_since_mark = True

    def reset(self) -> None:
        self._recent.clear()
        self.last_V = None
        self.last_t = None
        self.connected_since = None
        self.detached_since_mark = True

    def feed(self, v: float, t: float) -> None:
        self.last_V = v
        self.last_t = t
        if v < self.threshold_V:
            self.detached_since_mark = True
            self._recent.clear()  # 外れている間の値は安定の判定に使わない
            self.connected_since = None
            return
        if self.connected_since is None:
            self.connected_since = t
        self._recent.append((t, v))
        while self._recent and self._recent[0][0] < t - self.window_s:
            self._recent.popleft()

    def mark_recorded(self) -> None:
        """記録した。ここからバッテリを外すまでは「同じバッテリ」とみなす。"""
        self.detached_since_mark = False

    @property
    def connected(self) -> bool:
        return self.last_V is not None and self.last_V >= self.threshold_V

    @property
    def spread_V(self) -> float | None:
        if not self._recent:
            return None
        vals = [v for _, v in self._recent]
        return max(vals) - min(vals)

    @property
    def state(self) -> BatteryState:
        if self.last_V is None:
            return BatteryState.NO_DATA
        if not self.connected:
            return BatteryState.NONE
        spread = self.spread_V
        covered = self.last_t - self.connected_since >= self.window_s * STABLE_MIN_COVER
        # 0.1 mV 単位で比べる（1.25 mV 刻みの差の浮動小数誤差を避ける）
        if covered and spread is not None and round(spread * 10_000) <= round(self.stable_width_V * 10_000):
            return BatteryState.STABLE
        return BatteryState.UNSTABLE


class CollectFailure(Enum):
    NO_DATA = "データを受信できませんでした。もう一度記録してください"
    DETACHED = "測定中にバッテリが外れました"


@dataclass(frozen=True)
class CollectResult:
    measurement: Measurement | None = None
    failure: CollectFailure | None = None
    samples: tuple[float, ...] = ()

    @property
    def ok(self) -> bool:
        return self.measurement is not None


class SampleCollector:
    """記録ボタンを押した**後に**届いたデータ行を 5 個集め、中央値・最小・最大を出す（F-04）。

    - started_at より前に受け取った行は使わない
    - 途中で接続判定電圧未満が混ざったら DETACHED
    - timeout_s 以内に 5 個そろわなければ NO_DATA（poll() で確かめる）
    """

    def __init__(
        self,
        started_at: float,
        threshold_V: float,
        n: int = SAMPLES_PER_RECORD,
        timeout_s: float = COLLECT_TIMEOUT_S,
    ) -> None:
        self.started_at = started_at
        self.threshold_V = threshold_V
        self.n = n
        self.timeout_s = timeout_s
        self.values: list[float] = []
        self.result: CollectResult | None = None

    @property
    def done(self) -> bool:
        return self.result is not None

    def feed(self, v: float, t: float) -> CollectResult | None:
        """1 行渡す。終わったら結果を返す（以後は何もしない）。"""
        if self.done or t < self.started_at:
            return None
        if v < self.threshold_V:
            self.result = CollectResult(failure=CollectFailure.DETACHED, samples=tuple(self.values + [v]))
            return self.result
        self.values.append(v)
        if len(self.values) >= self.n:
            self.result = CollectResult(summarize_samples(self.values), samples=tuple(self.values))
            return self.result
        return None

    def poll(self, now: float) -> CollectResult | None:
        """時間切れを確かめる。"""
        if self.done:
            return None
        if now - self.started_at > self.timeout_s:
            self.result = CollectResult(failure=CollectFailure.NO_DATA, samples=tuple(self.values))
            return self.result
        return None
