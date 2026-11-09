"""記録の計算とデータ構造（仕様 F-04〜F-07, F-10, 5.2）。GUI なしでテストできる。

- 5 回測定の中央値・最小・最大と、上下限による判定
- ロット情報と、番号順に並んだ記録の一覧（記録・取り消し・測り直し・判定のやり直し）
- 集計（件数・OK/NG 数・平均・中央値・最小・最大・標準偏差）と度数分布
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import Enum

SAMPLES_PER_RECORD = 5  # 記録 1 件の測定回数（F-04）
VOLTAGE_DECIMALS = 4
NOTE_SPREAD = "ばらつき大"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class Judge(str, Enum):
    OK = "OK"
    NG_LOW = "NG_LOW"  # 下限未満
    NG_HIGH = "NG_HIGH"  # 上限超え

    @property
    def is_ok(self) -> bool:
        return self is Judge.OK

    @property
    def label(self) -> str:
        """画面・Excel に出す文字。"""
        return JUDGE_LABELS[self]


JUDGE_LABELS = {Judge.OK: "OK", Judge.NG_LOW: "NG（下限未満）", Judge.NG_HIGH: "NG（上限超え）"}


def round_v(v: float) -> float:
    """電圧を小数 4 桁（0.1 mV）に丸める。INA226 の分解能 1.25 mV より十分細かい。"""
    return round(v, VOLTAGE_DECIMALS)


def judge(value_V: float, lower_V: float, upper_V: float) -> Judge:
    """下限 ≦ 値 ≦ 上限 → OK、値 < 下限 → NG_LOW、値 > 上限 → NG_HIGH。境界ちょうどは OK。

    比較は小数 4 桁に丸めた値どうしで行う（表示・CSV の値と判定が食い違わないように）。
    """
    v, lo, hi = round_v(value_V), round_v(lower_V), round_v(upper_V)
    if v < lo:
        return Judge.NG_LOW
    if v > hi:
        return Judge.NG_HIGH
    return Judge.OK


@dataclass(frozen=True)
class Measurement:
    """5 回測定の結果。"""

    median_V: float
    min_V: float
    max_V: float

    @property
    def spread_V(self) -> float:
        return self.max_V - self.min_V


def summarize_samples(values: Sequence[float]) -> Measurement:
    if not values:
        raise ValueError("測定値がありません")
    return Measurement(round_v(statistics.median(values)), round_v(min(values)), round_v(max(values)))


def is_spread_large(m: Measurement, stable_width_V: float) -> bool:
    # 1.25 mV 単位の値どうしの差なので、浮動小数の誤差で境界を誤らないよう 0.1 mV 単位で比べる
    return round(m.spread_V * 10_000) > round(stable_width_V * 10_000)


@dataclass
class Record:
    no: int
    voltage_V: float
    judge: Judge
    min_V: float
    max_V: float
    measured_at: str  # YYYY-MM-DD HH:MM:SS（PC 時刻）
    remeasured: int = 0
    note: str = ""

    @property
    def has_warning(self) -> bool:
        return bool(self.note)


def make_record(
    no: int,
    m: Measurement,
    lower_V: float,
    upper_V: float,
    stable_width_V: float,
    when: datetime,
    remeasured: int = 0,
) -> Record:
    return Record(
        no=no,
        voltage_V=round_v(m.median_V),
        judge=judge(m.median_V, lower_V, upper_V),
        min_V=round_v(m.min_V),
        max_V=round_v(m.max_V),
        measured_at=when.strftime(DATETIME_FMT),
        remeasured=remeasured,
        note=NOTE_SPREAD if is_spread_large(m, stable_width_V) else "",
    )


@dataclass
class LotInfo:
    """ロット情報（作業ファイルの JSON に保存する）。"""

    lot_id: str
    lot_name: str
    lower_V: float
    upper_V: float
    start_no: int = 1
    operator: str = ""  # 測定者の社員番号
    memo: str = ""
    started_at: str = ""  # YYYY-MM-DD HH:MM:SS
    finished: bool = False
    finished_at: str = ""
    # 出力（F-09）
    revision: int = 0  # 記録・取り消し・測り直し・判定のやり直しのたびに 1 増える
    exported_revision: int = -1  # 最後に出力したときの revision
    last_export_dir: str = ""
    last_export_base: str = ""
    # 条件（Excel の「条件」シート。記録のたびに最新の値にする）
    port: str = ""
    interval_ms: int | None = None
    firmware: int | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> "LotInfo":
        names = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in raw.items() if k in names}
        info = cls(**kwargs)
        info.lower_V = float(info.lower_V)
        info.upper_V = float(info.upper_V)
        info.start_no = int(info.start_no)
        return info

    @property
    def started_dt(self) -> datetime:
        try:
            return datetime.strptime(self.started_at, DATETIME_FMT)
        except ValueError:
            return datetime.now().replace(microsecond=0)

    @property
    def unexported(self) -> bool:
        return self.revision != self.exported_revision

    def limits_text(self) -> str:
        return f"下限 {self.lower_V:.3f} V・上限 {self.upper_V:.3f} V"


class RecordBook:
    """1 ロットの記録。番号は start_no から欠番・重複なく続く（F-05）。

    記録は番号順（昇順）に持つ。最後の 1 件の取り消しと、番号を指定した置き換えだけができるので、
    番号はいつも start_no, start_no+1, … と連続する。
    """

    def __init__(self, start_no: int = 1, records: Iterable[Record] = ()) -> None:
        self.start_no = int(start_no)
        self.records: list[Record] = []
        for r in records:
            self.append(r)

    def __len__(self) -> int:
        return len(self.records)

    @property
    def next_no(self) -> int:
        return self.start_no + len(self.records)

    @property
    def last(self) -> Record | None:
        return self.records[-1] if self.records else None

    def get(self, no: int) -> Record | None:
        i = no - self.start_no
        return self.records[i] if 0 <= i < len(self.records) else None

    def append(self, record: Record) -> Record:
        if record.no != self.next_no:
            raise ValueError(f"番号が連続していません（次は No.{self.next_no}、渡されたのは No.{record.no}）")
        self.records.append(record)
        return record

    def undo_last(self) -> Record:
        if not self.records:
            raise IndexError("取り消す記録がありません")
        return self.records.pop()

    def replace(self, no: int, record: Record) -> Record:
        """測り直し（F-07）：その番号の値・判定・日時を置き換え、remeasured を 1 増やす。"""
        old = self.get(no)
        if old is None:
            raise KeyError(no)
        new = replace(record, no=no, remeasured=old.remeasured + 1)
        self.records[no - self.start_no] = new
        return new

    def rejudge(self, lower_V: float, upper_V: float) -> int:
        """上下限を変えたときに既存の記録を判定し直す。変わった件数を返す。"""
        changed = 0
        for r in self.records:
            j = judge(r.voltage_V, lower_V, upper_V)
            if j is not r.judge:
                r.judge = j
                changed += 1
        return changed


# ================================================================ 集計（F-10, 5.2）
@dataclass(frozen=True)
class Stats:
    count: int
    ok: int
    ng_low: int
    ng_high: int
    mean: float | None
    median: float | None
    min_V: float | None
    min_no: int | None
    max_V: float | None
    max_no: int | None
    stdev: float | None  # 標本標準偏差（n-1。Excel の STDEV.S と同じ）。2 件未満は None

    @property
    def ng(self) -> int:
        return self.ng_low + self.ng_high

    @property
    def ok_rate(self) -> float | None:
        return self.ok / self.count if self.count else None


def compute_stats(records: Sequence[Record]) -> Stats:
    n = len(records)
    ok = sum(1 for r in records if r.judge is Judge.OK)
    low = sum(1 for r in records if r.judge is Judge.NG_LOW)
    high = sum(1 for r in records if r.judge is Judge.NG_HIGH)
    if not n:
        return Stats(0, 0, 0, 0, None, None, None, None, None, None, None)
    values = [r.voltage_V for r in records]
    rmin = min(records, key=lambda r: r.voltage_V)  # 同じ値なら番号の小さい方
    rmax = max(records, key=lambda r: (r.voltage_V, -r.no))
    return Stats(
        count=n,
        ok=ok,
        ng_low=low,
        ng_high=high,
        mean=statistics.fmean(values),
        median=statistics.median(values),
        min_V=rmin.voltage_V,
        min_no=rmin.no,
        max_V=rmax.voltage_V,
        max_no=rmax.no,
        stdev=statistics.stdev(values) if n >= 2 else None,
    )


def summary_text(s: Stats) -> str:
    """集計行（7 章）：件数 352 ／ OK 349 ／ NG 3（低 2・高 1）／ 平均 3.8012 V ／ 最小 … ／ 最大 …"""
    if not s.count:
        return "件数 0"
    return (
        f"件数 {s.count:,} ／ OK {s.ok:,} ／ NG {s.ng:,}（低 {s.ng_low:,}・高 {s.ng_high:,}）／ "
        f"平均 {s.mean:.4f} V ／ 最小 {s.min_V:.4f} V（No.{s.min_no}）／ 最大 {s.max_V:.4f} V（No.{s.max_no}）"
    )


# ---- 度数分布（Q-05：10 mV 刻み、下限−50 mV〜上限＋50 mV。範囲外は「未満」「超え」にまとめる）
HIST_BIN_V = 0.010
HIST_MARGIN_V = 0.050
_UNIT = 10_000  # 0.1 mV 単位の整数で区切る（浮動小数の誤差で区間を誤らないように）


@dataclass(frozen=True)
class HistBin:
    label: str
    lo_V: float | None  # None は「それ未満」
    hi_V: float | None  # None は「それ超え」
    count: int


def histogram(
    values: Iterable[float],
    lower_V: float,
    upper_V: float,
    bin_V: float = HIST_BIN_V,
    margin_V: float = HIST_MARGIN_V,
) -> list[HistBin]:
    """区間 [a, a+幅) の度数。最後の区間だけ右端（上限＋余白）を含む。

    先頭に「<始点> V 未満」、末尾に「<終点> V 超え」の行を付ける。
    """
    start = round((lower_V - margin_V) * _UNIT)
    end = round((upper_V + margin_V) * _UNIT)
    width = max(1, round(bin_V * _UNIT))
    n_bins = max(1, math.ceil((end - start) / width))
    end = start + n_bins * width
    counts = [0] * n_bins
    below = above = 0
    for v in values:
        x = round(v * _UNIT)
        if x < start:
            below += 1
        elif x > end:
            above += 1
        else:
            counts[min((x - start) // width, n_bins - 1)] += 1
    fmt = lambda u: f"{u / _UNIT:.3f}"  # noqa: E731
    out = [HistBin(f"{fmt(start)} V 未満", None, start / _UNIT, below)]
    for i, c in enumerate(counts):
        a, b = start + i * width, start + (i + 1) * width
        out.append(HistBin(f"{fmt(a)}–{fmt(b)}", a / _UNIT, b / _UNIT, c))
    out.append(HistBin(f"{fmt(end)} V 超え", end / _UNIT, None, above))
    return out


@dataclass
class LotState:
    """作業ファイルから戻したロット（F-08）。"""

    info: LotInfo
    book: RecordBook
    dropped_no: int | None = None  # 最終行が壊れていて除いた記録の番号（番号が読めなければ -1）
    warnings: list[str] = field(default_factory=list)
