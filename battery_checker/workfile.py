"""作業ファイル：記録のたびの自動保存と、異常終了からの再開（仕様 F-08, 5.2）。

%LOCALAPPDATA%\\INA226_BatteryChecker\\lots\\ に、ロットごとに
  <ロットID>.csv  … 記録（出力 CSV と同じ列。記録のたびに 1 行追記）
  <ロットID>.json … ロット情報（ロット名・測定者の社員番号・上下限・開始番号・開始日時・メモ・終了したか）
を置く。

- 追記・書き直しのたびにファイルを閉じ、flush と fsync で OS のキャッシュに残さない
- 書き直し（取り消し・測り直し・判定のやり直し）は一時ファイルに書いてから置き換える
- 書けなかったときは dirty にし、次の保存で全件を書き直す（F-04 の異常時）
- 画面からは save_async() で頼む：書き込みは専用のスレッドが順番に行い、画面を待たせない
  （ディスクの flush が遅いときでも記録の結果はすぐ出る）。終了・ロット情報の書き換えの前は flush() で待つ
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from .csvformat import (
    CSV_ENCODING,
    CSV_HEADER,
    KNOWN_NOTES,
    LEGACY_CSV_HEADER,
    NEWLINE,
    format_csv,
    format_row,
    parse_row,
)
from .filename import stamp
from .records import DATETIME_FMT, LotInfo, LotState, Record, RecordBook

log = logging.getLogger(__name__)

RETENTION_DAYS = 30  # 終了したロットの作業ファイルを残す期間（Q-09）


def _fsync_write(path: Path, data: bytes, mode: str = "wb") -> None:
    with open(path, mode) as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    _fsync_write(tmp, data)
    os.replace(tmp, path)


def lot_paths(lots_dir: Path, lot_id: str) -> tuple[Path, Path]:
    return lots_dir / f"{lot_id}.csv", lots_dir / f"{lot_id}.json"


def new_lot_id(lots_dir: Path, when: datetime) -> str:
    """`YYYYMMDD_HHMMSS`。同じ秒のロットがあれば `_2`, `_3` …"""
    base = stamp(when)
    lot_id, k = base, 2
    while any(p.exists() for p in lot_paths(lots_dir, lot_id)):
        lot_id, k = f"{base}_{k}", k + 1
    return lot_id


@dataclass(frozen=True)
class _SaveJob:
    """1 回分の保存。画面のスレッドで作った写し（書き込み中に記録が変わっても影響しない）。"""

    row: bytes | None  # 最後の 1 件の行（追記のとき）
    full_csv: bytes  # 全件（追記できないとき・書き直しのとき）
    info: bytes  # ロット情報（JSON）


class WorkFile:
    def __init__(self, lots_dir: Path, lot_id: str) -> None:
        self.lots_dir = Path(lots_dir)
        self.lot_id = lot_id
        self.csv_path, self.json_path = lot_paths(self.lots_dir, lot_id)
        self.dirty = False  # 前回の保存に失敗した（次は全件を書き直す）
        self.last_saved: float | None = None  # 最後に保存できた時刻（time.time()）
        self.last_error: OSError | None = None
        # 書き込みの結果を知らせる（書き込みのスレッドから呼ばれる）：(書けたか, 誤りの文)
        self.on_saved: Callable[[bool, str], None] | None = None
        self._queue: queue.Queue[_SaveJob | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- 作成
    @classmethod
    def create(cls, lots_dir: Path, info: LotInfo, book: RecordBook) -> "WorkFile":
        """新しいロットの作業ファイルを作る。作れなければ OSError（ロットを始めない。F-02）。"""
        lots_dir = Path(lots_dir)
        lots_dir.mkdir(parents=True, exist_ok=True)
        wf = cls(lots_dir, info.lot_id)
        _atomic_write(wf.csv_path, format_csv(book.records).encode(CSV_ENCODING))
        _atomic_write(wf.json_path, _info_bytes(info))
        wf.last_saved = time.time()
        return wf

    # ---------------------------------------------------------------- 保存
    @staticmethod
    def _job(info: LotInfo, book: RecordBook, appended: bool) -> _SaveJob:
        row = (format_row(book.last) + NEWLINE).encode("utf-8") if appended and book.last is not None else None
        return _SaveJob(row, format_csv(book.records).encode(CSV_ENCODING), _info_bytes(info))

    def _write(self, job: _SaveJob) -> bool:
        try:
            if job.row is not None and not self.dirty and self.csv_path.exists():
                _fsync_write(self.csv_path, job.row, mode="ab")
            else:
                _atomic_write(self.csv_path, job.full_csv)
            _atomic_write(self.json_path, job.info)
        except OSError as e:
            log.warning("作業ファイルに書き込めません: %s", self.csv_path, exc_info=True)
            self.dirty = True
            self.last_error = e
            return False
        self.dirty = False
        self.last_error = None
        self.last_saved = time.time()
        return True

    def save(self, info: LotInfo, book: RecordBook, appended: bool = False) -> bool:
        """すぐに書く（書き終わるまで待つ）。書けたら True。

        appended=True（最後に 1 件足しただけ）なら 1 行追記、それ以外は全件を書き直す。
        前回失敗していれば全件を書き直す。
        """
        self.flush()
        return self._write(self._job(info, book, appended))

    def save_async(self, info: LotInfo, book: RecordBook, appended: bool = False) -> None:
        """書き込みのスレッドに頼んで、すぐ戻る（順番は頼んだ順）。結果は on_saved で知らせる。"""
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name=f"workfile-{self.lot_id}", daemon=True)
            self._thread.start()
        self._queue.put(self._job(info, book, appended))

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                ok = self._write(job)
                cb = self.on_saved
                if cb is not None:
                    try:
                        cb(ok, str(self.last_error) if not ok else "")
                    except Exception:
                        log.warning("保存の結果を知らせられません", exc_info=True)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float = 10.0) -> bool:
        """頼んだ書き込みが全部終わるまで待つ。終わったら True。"""
        if self._thread is None:
            return True
        end = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() > end:
                log.warning("作業ファイルの書き込みが終わりません（%s）", self.csv_path)
                return False
            time.sleep(0.005)
        return True

    def close(self) -> None:
        """残りを書いてから、書き込みのスレッドを止める（アプリの終了・ロットを閉じるとき）。"""
        self.flush()
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(2.0)
        self._thread = None

    def save_info(self, info: LotInfo) -> bool:
        self.flush()  # 先に頼んだ書き込みが、あとからロット情報を上書きしないように
        try:
            _atomic_write(self.json_path, _info_bytes(info))
        except OSError as e:
            log.warning("作業ファイル（ロット情報）に書き込めません: %s", self.json_path, exc_info=True)
            self.last_error = e
            return False
        return True


def _info_bytes(info: LotInfo) -> bytes:
    return json.dumps(info.to_json(), ensure_ascii=False, indent=2).encode("utf-8")


# ================================================================ 読み込み・再開
@dataclass(frozen=True)
class LotEntry:
    """lots フォルダにあるロット。"""

    lot_id: str
    info: LotInfo
    csv_path: Path
    json_path: Path


def list_lots(lots_dir: Path) -> list[LotEntry]:
    """ロット情報（JSON）を読めたロットを新しい順に返す。"""
    lots_dir = Path(lots_dir)
    out: list[LotEntry] = []
    if not lots_dir.is_dir():
        return out
    for jp in lots_dir.glob("*.json"):
        lot_id = jp.stem
        try:
            raw = json.loads(jp.read_text(encoding="utf-8"))
            info = LotInfo.from_json(raw)
        except (OSError, ValueError, TypeError, AttributeError):
            log.warning("作業ファイル（ロット情報）を読めません: %s", jp, exc_info=True)
            continue
        info.lot_id = lot_id
        out.append(LotEntry(lot_id, info, jp.with_suffix(".csv"), jp))
    out.sort(key=lambda e: e.lot_id, reverse=True)
    return out


def find_unfinished(lots_dir: Path) -> list[LotEntry]:
    """終了していない（「終了」していない）ロット。新しい順。"""
    return [e for e in list_lots(lots_dir) if not e.info.finished and e.csv_path.exists()]


def read_records(csv_path: Path, start_no: int) -> tuple[list[Record], int | None, list[str]]:
    """作業ファイルの CSV を読む。(記録, 除いた最終行の番号, 警告)。

    最終行が途中で切れていたら除く（番号が読めなければ次の番号とみなす）。
    改行で終わっていない最終行は、形式が正しくても備考が途中で切れている場合があるので、備考も確かめる。
    途中の行が壊れていた場合は、そこまでの連続した記録だけを使う。
    v0.1.5 までの形式（測定者の列なし）も読める（測定者は空になる）。
    """
    raw = Path(csv_path).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    warnings: list[str] = []
    header = lines[0].strip() if lines else ""
    if header not in (CSV_HEADER, LEGACY_CSV_HEADER):
        raise ValueError(f"作業ファイルの列名行が違います: {csv_path}")
    legacy = header == LEGACY_CSV_HEADER
    body = [ln for ln in lines[1:]]
    while body and not body[-1].strip():
        body.pop()
    records: list[Record] = []
    dropped_no: int | None = None
    for i, line in enumerate(body):
        is_last = i == len(body) - 1
        rec = parse_row(line, legacy=legacy)
        if is_last and rec is not None and not ends_with_newline and rec.note not in KNOWN_NOTES:
            rec = None
        if rec is None:
            if is_last:
                head = line.split(",", 1)[0]
                dropped_no = int(head) if head.isdigit() else start_no + len(records)
                log.warning("作業ファイルの最終行が壊れていたため除きました: %r", line)
            else:
                warnings.append(f"{i + 2} 行目が壊れています")
                log.warning("作業ファイルの %d 行目が壊れています: %r", i + 2, line)
            continue
        records.append(rec)
    # 番号順に並べ、start_no から連続している分だけ使う
    records.sort(key=lambda r: r.no)
    ok: list[Record] = []
    for r in records:
        if r.no == start_no + len(ok):
            ok.append(r)
        elif r.no < start_no + len(ok):
            warnings.append(f"No.{r.no} が重複しています")
        else:
            warnings.append(f"No.{start_no + len(ok)} 以降が欠けているため、No.{start_no + len(ok) - 1} までで再開します")
            break
    return ok, dropped_no, warnings


def is_legacy_file(csv_path: Path) -> bool:
    """v0.1.5 までの形式（測定者の列なし）の作業ファイルか。"""
    try:
        with open(csv_path, encoding=CSV_ENCODING, newline="") as f:
            return f.readline().strip() == LEGACY_CSV_HEADER
    except (OSError, UnicodeDecodeError):
        return False


def load_lot(entry: LotEntry) -> tuple[LotState, WorkFile]:
    """ロットを作業ファイルから戻す（F-08）。直した場合は作業ファイルも書き直す。"""
    info = entry.info
    records, dropped, warnings = read_records(entry.csv_path, info.start_no)
    legacy = is_legacy_file(entry.csv_path)
    if legacy:  # v0.1.5 までの作業ファイル：測定者はロットの社員番号とみなす
        records = [replace(r, operator=info.operator) for r in records]
    book = RecordBook(info.start_no, records)
    wf = WorkFile(entry.csv_path.parent, entry.lot_id)
    if dropped is not None or warnings or legacy:
        info.revision += 1
        # 壊れた行を除いた内容・新しい形式で書き直す（旧形式のファイルに新形式の行を足さないため）
        wf.save(info, book)
    wf.last_saved = entry.csv_path.stat().st_mtime if entry.csv_path.exists() else None
    return LotState(info, book, dropped, warnings), wf


def finish_lot(wf: WorkFile, info: LotInfo, when: datetime | None = None) -> bool:
    """「終了」：作業ファイルを終了扱いにする（ファイルは RETENTION_DAYS 日残す）。"""
    info.finished = True
    info.finished_at = (when or datetime.now()).strftime(DATETIME_FMT)
    return wf.save_info(info)


def cleanup_finished(lots_dir: Path, now: datetime | None = None, days: int = RETENTION_DAYS) -> list[str]:
    """終了してから days 日を過ぎたロットの作業ファイルを消す。消したロット ID を返す。"""
    now = now or datetime.now()
    removed = []
    for e in list_lots(lots_dir):
        if not e.info.finished:
            continue
        try:
            done = datetime.strptime(e.info.finished_at, DATETIME_FMT)
        except ValueError:
            continue
        if now - done > timedelta(days=days):
            for p in (e.csv_path, e.json_path):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    log.warning("古い作業ファイルを消せません: %s", p, exc_info=True)
            removed.append(e.lot_id)
    return removed
