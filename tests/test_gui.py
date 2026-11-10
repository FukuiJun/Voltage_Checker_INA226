"""GUI を模擬シリアルで動かす結合テスト（画面なし・offscreen）。AC-01, 04, 06, 09, 14, 15 など。"""

import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from battery_checker.gui.dialogs import LotInput  # noqa: E402
from battery_checker.paths import AppPaths  # noqa: E402

LOT = LotInput("LOT-A12", "123456", 3.6, 4.2, 1, "")


@pytest.fixture(scope="module")
def qapp():
    from battery_checker import theme

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def wait(app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return cond()


@pytest.fixture
def make_window(qapp, tmp_path):
    from battery_checker.gui.main_window import MainWindow

    created = []

    def make(speed=5.0, answers=None):
        w = MainWindow(AppPaths.under(tmp_path / "app"), mock=True, mock_speed=speed)
        w.asked, w.warned, w.infos = [], [], []
        w.answers = list(answers or [])

        def ask(text, choices, escape):
            w.asked.append(text)
            return w.answers.pop(0) if w.answers else choices[0][0]

        w._ask = ask
        w._warn = w.warned.append
        w._info = w.infos.append
        w.export_defaults = []

        def ask_export(default):
            w.export_defaults.append(default)
            return str(tmp_path / "out"), default

        w._ask_export = ask_export
        w.settings.sound = False
        w.show()
        created.append(w)
        return w

    yield make
    for w in created:
        w.close()


def connect(w, app, device):
    w.select_port(device)
    assert w.connect_port()
    assert wait_until(app, lambda: w.monitor.last_V is not None), "データが届かない"


def wait_stable_new_battery(w, app, timeout=10.0):
    """前回の記録のあと一度外され、別のバッテリが繋がって安定するまで待つ。"""
    from battery_checker.measure import BatteryState

    return wait_until(
        app, lambda: w.monitor.detached_since_mark and w.monitor.state is BatteryState.STABLE and w.collector is None,
        timeout,
    )


def record_and_wait(w, app, key=False):
    n = len(w.book)
    if key:
        QTest.keyClick(w, Qt.Key_Space)
    else:
        w.on_record()
    assert wait_until(app, lambda: w.collector is None, 3.0)
    return len(w.book) > n


def test_initial_button_states(make_window, qapp):
    w = make_window()
    assert not w.record_button.isEnabled() and not w.undo_button.isEnabled()
    assert not w.export_button.isEnabled() and not w.end_lot_button.isEnabled()
    assert w.new_lot_button.isEnabled() and w.connect_button.isEnabled() and w.port_combo.isEnabled()
    assert w.connect_button.text() == "接続"
    assert w.lot_label.text().startswith("ロットなし")
    assert w.number_card.number == "---" and w.number_card.property("mode") == "none"


def test_ac01_connect_sends_interval_40(make_window, qapp):
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK1")
    assert w.reader.interval_ms == 40
    assert wait_until(qapp, lambda: w.interval_label.text() == "周期 40 ms", 5.0)
    assert w.firmware_version == 2
    # 接続中・ロットなし：記録・取り消し・出力は無効、ポート選択は無効（切断のみ）
    assert not w.record_button.isEnabled() and not w.export_button.isEnabled()
    assert not w.port_combo.isEnabled() and w.connect_button.text() == "切断"
    w.on_connect_toggle()
    assert w.reader is None and w.port_combo.isEnabled()


def test_old_sketch_falls_back_to_50ms(make_window, qapp):
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK5")
    assert wait_until(qapp, lambda: w.interval_no_ack, 8.0)
    assert w.interval_label.text() == "周期 50 ms（スケッチ未対応）"
    assert "周期変更に対応していません（50 ms で記録します）" in w.msg_label.text()
    assert w.effective_interval_ms() == 50


def test_ac04_no_battery_does_not_record(make_window, qapp):
    from battery_checker.gui.main_window import MSG_NO_BATTERY

    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK1")
    assert w.start_lot(LOT)
    assert wait_until(qapp, lambda: not w.monitor.connected and w.monitor.last_V == 0.0, 10.0)
    assert not w.record_button.isEnabled()
    QTest.keyClick(w, Qt.Key_Space)
    assert w.collector is None and len(w.book) == 0
    assert w.msg_label.text().startswith(MSG_NO_BATTERY)
    assert w.state_label.text() == "バッテリ未接続"


def test_ac06_duplicate_confirmation(make_window, qapp):
    from battery_checker.gui.main_window import MSG_DUPLICATE

    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK3")  # バッテリを繋いだまま
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    assert record_and_wait(w, qapp)
    assert w.asked == []
    # 外さずにもう一度 → 確認。「やめる」で記録されない
    w.answers = ["cancel"]
    w.on_record()
    assert w.asked == [MSG_DUPLICATE] and w.collector is None and len(w.book) == 1
    # 「記録する」なら記録する
    w.answers = ["record"]
    assert record_and_wait(w, qapp)
    assert len(w.book) == 2
    # 設定で確認を無効にできる（Q-06）
    w.settings.confirm_duplicate = False
    assert record_and_wait(w, qapp)
    assert len(w.asked) == 2


def test_ac15_space_records_20_in_a_row(make_window, qapp, monkeypatch):
    import battery_checker.serial_io as sio
    from battery_checker.mock_serial import MockProfile

    # 「安定」には実時間で 0.5 秒かかるので、速く回すときは繋いでいる時間を長くする（6 倍速で約 0.8 秒）
    monkeypatch.setitem(sio.MOCK_PORTS, "MOCK1", ("付け外し（長め）", MockProfile(battery_cycle=(5.0, 1.0))))
    w = make_window(speed=6.0)
    connect(w, qapp, "MOCK1")
    w.start_lot(LOT)
    latencies = []
    for _ in range(20):
        assert wait_stable_new_battery(w, qapp), "次のバッテリが安定しない"
        assert record_and_wait(w, qapp, key=True)
        latencies.append((round(w.last_latency_s, 3), round(w.last_collect_s, 3), round(w.last_save_s, 3)))
    assert [r.no for r in w.book.records] == list(range(1, 21))
    from battery_checker.gui.main_window import MSG_DUPLICATE

    assert MSG_DUPLICATE not in w.asked  # 毎回バッテリを外しているので二重記録の確認は出ない
    # (全体, 5 個を集める, 作業ファイルの保存) の秒。共有の CI マシンでは、ディスクの flush（fsync）が
    # たまに 1 回だけ遅くなることがあるので、20 件中 19 件が 0.5 秒以内・全件 1.5 秒以内を確かめる。
    # 1 件の 0.5 秒以内は test_record_latency_real_time（実時間）で厳密に確かめる。
    total = sorted(t for t, _, _ in latencies)
    assert total[-2] < 0.5 and total[-1] < 1.5, latencies
    assert w.table.model.rowCount() == 20 and w.table.selected_no() == 20  # 最新の行を選ぶ
    assert w.number_card.number == "21" and w.number_card.sub_label.text() == "記録済み 20 件"
    assert w.summary_label.text().startswith("件数 20 ／ OK ")
    # 作業ファイルにも 20 件
    from battery_checker.workfile import read_records

    assert w.workfile.flush()  # 作業ファイルは別スレッドで書くので、書き終わるのを待つ
    assert len(read_records(w.workfile.csv_path, 1)[0]) == 20


def test_record_latency_real_time(make_window, qapp):
    """実時間（40 ms 周期）で、記録ボタンから結果表示・保存まで 0.5 秒以内（8 章）。

    共有の CI マシンでは 1 回だけ遅れることがあるので、3 件測って中央値で判定する
    （毎回遅くなる不具合なら中央値も超える）。失敗時は (全体, 5 個を集める, 保存) の秒を出す。
    """
    import statistics

    w = make_window(speed=1.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_until(qapp, lambda: w.interval_ack is not None, 5.0)
    assert wait_stable_new_battery(w, qapp)
    times = []
    for _ in range(3):
        assert record_and_wait(w, qapp)
        times.append((round(w.last_latency_s, 3), round(w.last_collect_s, 3), round(w.last_save_s, 3)))
    total = [t for t, _, _ in times]
    assert statistics.median(total) < 0.5, times
    assert max(total) < 1.0, times
    assert min(total) >= 0.15, times  # 押した後の 5 個（40 ms × 5）を待っている


def test_space_in_text_input_is_typed_not_recorded(make_window, qapp):
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    edit = QLineEdit(w.centralWidget())
    edit.show()
    edit.setFocus()
    qapp.processEvents()
    if QApplication.focusWidget() is not edit:
        pytest.skip("offscreen でフォーカスを移せない")
    QTest.keyClick(edit, Qt.Key_Space)
    assert edit.text() == " " and w.collector is None


def test_undo_and_remeasure(make_window, qapp):
    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    for _ in range(3):
        assert record_and_wait(w, qapp)
    # 取り消し：「いいえ」なら何もしない
    w.answers = ["no"]
    w.on_undo()
    assert len(w.book) == 3
    w.answers = ["yes"]
    w.on_undo()
    assert w.asked[-1].startswith("No.3（") and w.asked[-1].endswith(" V）を取り消しますか？")
    assert len(w.book) == 2 and w.number_card.number == "3"
    assert w.table.model.rowCount() == 2
    # 測り直し：No.1 を選んで「この番号を測り直す」→ 記録
    w.table.select_no(1)
    assert w.remeasure_button.isEnabled()
    w.on_remeasure()
    assert w.remeasure_no == 1 and w.msg_label.text().startswith("No.1 を測り直します")
    # 測り直し中は番号カードが「測り直す番号」に変わり、次の番号は変わらない
    assert w.number_card.number == "1" and w.number_card.property("mode") == "remeasure"
    assert w.number_card.caption_label.text() == "測り直す番号"
    assert w.number_card.sub_label.text() == "次は No.3 のまま"
    assert record_and_wait(w, qapp) is False  # 件数は変わらない
    assert w.book.get(1).remeasured == 1 and len(w.book) == 2 and w.book.next_no == 3
    assert w.remeasure_no is None
    # 測り直しは「やめる」で取り消せる
    w.table.select_no(2)
    w.on_remeasure()
    w.on_remeasure()
    assert w.remeasure_no is None
    from battery_checker.workfile import read_records

    assert w.workfile.flush()
    recs = read_records(w.workfile.csv_path, 1)[0]
    assert [(r.no, r.remeasured) for r in recs] == [(1, 1), (2, 0)]


def test_workfile_error_keeps_record_and_shows_red(make_window, qapp, monkeypatch):
    import battery_checker.workfile as wfmod
    from battery_checker.gui.main_window import MSG_WORKFILE_ERROR

    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    real = wfmod._fsync_write

    def broken(*a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(wfmod, "_fsync_write", broken)
    assert record_and_wait(w, qapp)
    assert len(w.book) == 1  # 画面上の記録は残る
    # 書き込みは別スレッド：失敗の知らせが届いたら赤字になる
    assert wait_until(qapp, lambda: w.msg_label.text().startswith(MSG_WORKFILE_ERROR), 3.0)
    assert w.msg_label.property("error")
    assert w.workfile.dirty and "（未保存あり）" in w.saved_label.text()
    monkeypatch.setattr(wfmod, "_fsync_write", real)
    assert record_and_wait(w, qapp)  # 次の記録で全件を書き直す
    assert w.workfile.flush()
    assert not w.workfile.dirty
    from battery_checker.workfile import read_records

    assert len(read_records(w.workfile.csv_path, 1)[0]) == 2


def test_ac09_resume_in_new_window(make_window, qapp):
    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LotInput("LOT-B", "123456", 3.6, 4.2, 101, "メモ"))
    assert wait_stable_new_battery(w, qapp)
    for _ in range(3):
        assert record_and_wait(w, qapp)
    csv_path = w.workfile.csv_path
    w.close()
    w2 = make_window(answers=["resume"])
    w2.check_resume()
    assert w2.asked == ["前回のロット（LOT-B、3 件、最後は No.103）が途中です。続けますか？"]
    assert w2.lot.lot_name == "LOT-B" and len(w2.book) == 3 and w2.book.next_no == 104
    assert w2.number_card.number == "104" and w2.table.model.rowCount() == 3
    assert w2.lot_label.text() == "LOT-B ／ 下限 3.600 V・上限 4.200 V"
    # 最終行が切れていたら、その 1 件を除いて再開し、メッセージを出す（AC-10）
    w2.close()
    csv_path.write_bytes(csv_path.read_bytes()[:-15])
    w3 = make_window(answers=["resume"])
    w3.check_resume()
    assert len(w3.book) == 2 and w3.book.next_no == 103
    assert "最後の 1 件（No.103）が壊れていたため除きました" in w3.msg_label.text()
    assert w3.infos and "No.103" in w3.infos[0]


def test_resume_later_and_new(make_window, qapp):
    w = make_window()
    w.start_lot(LOT)
    w.close()
    w2 = make_window(answers=["later"])
    w2.check_resume()
    assert w2.lot is None and len(w2.asked) == 1


def test_export_and_end_lot(make_window, qapp, tmp_path):
    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    for _ in range(2):
        assert record_and_wait(w, qapp)
    assert w.export_button.isEnabled()
    w.answers = ["continue"]  # 「ロットを終了しますか？」→ 続ける
    w.on_export()
    out = tmp_path / "out"
    names = sorted(p.name for p in out.iterdir())
    from datetime import datetime

    base = f"LOT-A12_0001-0002_電圧記録_{datetime.now():%Y%m%d}"
    assert w.export_defaults == [base]  # 初期値：ロット_番号始め-番号終わり_電圧記録_日付
    assert names == [f"{base}.csv", f"{base}.xlsx"]
    assert w.msg_label.text().startswith(f"保存しました：{out}")
    assert w.lot is not None and not w.lot.unexported
    # 続けて記録できる。「終了」では出力するか聞く（いいえ → 出力せずに終了）
    assert record_and_wait(w, qapp)
    w.answers = ["no"]
    assert w.on_end_lot()
    assert "CSV・Excel 出力しますか？" in w.asked[-1]
    assert w.lot is None and w.lot_label.text().startswith("ロットなし")
    from battery_checker.workfile import find_unfinished

    assert find_unfinished(w.paths.lots_dir) == []


def test_lot_settings_rejudges(make_window, qapp):
    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    assert record_and_wait(w, qapp)
    v = w.book.get(1).voltage_V
    w.apply_lot_settings(LotInput("LOT-A12", "123456", round(v + 0.1, 3), round(v + 0.2, 3), 1, ""))
    assert w.book.get(1).judge.value == "NG_LOW"
    assert "判定をやり直しました" in w.msg_label.text()


def test_usb_unplugged(make_window, qapp):
    from battery_checker.gui.main_window import MSG_DISCONNECTED

    w = make_window(speed=10.0)
    connect(w, qapp, "MOCK4")  # 20 秒（模擬時間）で USB 抜け
    w.start_lot(LOT)
    assert wait_until(qapp, lambda: w.reader is None, 6.0)
    assert w.msg_label.text() == MSG_DISCONNECTED and w.warned
    assert w.lot is not None and w.port_combo.isEnabled()


def test_ac14_gui_update_with_2000_records(make_window, qapp):
    """2000 件の記録があっても、記録 1 件の処理（判定・保存・一覧とグラフの更新）が 0.3 秒以内。"""
    from datetime import datetime

    from battery_checker.measure import CollectResult
    from battery_checker.records import Measurement, make_record

    w = make_window()
    w.start_lot(LOT)
    t0 = datetime(2026, 11, 6, 9, 0, 0)
    for i in range(2000):
        v = round(3.6 + (i % 700) / 1000, 4)
        w.book.append(make_record(w.book.next_no, Measurement(v, v, v), 3.6, 4.2, 0.005, t0))
    w.workfile.save(w.lot, w.book)
    w._show_lot()
    qapp.processEvents()
    times = []
    for _ in range(5):
        t = time.perf_counter()
        w._finish_collect(CollectResult(Measurement(3.8125, 3.8100, 3.8138)))
        qapp.processEvents()
        times.append(time.perf_counter() - t)
    assert len(w.book) == 2005
    assert max(times) < 0.3, times
    # 取り消しも同じくらい速い
    w.answers = ["yes"]
    t = time.perf_counter()
    w.on_undo()
    qapp.processEvents()
    assert time.perf_counter() - t < 0.3


def test_lot_dialog_validation(qapp):
    from battery_checker.gui.dialogs import LotDialog

    d = LotDialog(None, LotInput("", "", 0.0, 0.0, 1, ""))
    assert not d.ok_button.isEnabled() and d.name_edit.property("invalid")
    d.name_edit.setText("LOT-A12")
    d.lower_edit.setText("4.200")
    d.upper_edit.setText("3.600")
    assert not d.ok_button.isEnabled() and d.lower_edit.property("invalid") and d.upper_edit.property("invalid")
    d.lower_edit.setText("3.6001")  # 小数 4 桁は不可
    assert not d.ok_button.isEnabled()
    d.lower_edit.setText("3.6")
    d.upper_edit.setText("4.2")
    assert d.ok_button.isEnabled() and not d.lower_edit.property("invalid")
    v = d.value()
    assert (v.lot_name, v.lower_V, v.upper_V, v.start_no) == ("LOT-A12", 3.6, 4.2, 1)
    assert d.name_edit.maxLength() == 30 and d.operator_edit.maxLength() == 20


# ================================================================ グラフの線をドラッグして上下限を変える
def test_operator_recorded_per_record(make_window, qapp):
    """記録ごとに、そのときの測定者（社員番号）を残す。ロット設定で交代したら以後の記録だけ変わる。"""
    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    w.settings.confirm_ng = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    assert record_and_wait(w, qapp)
    w.apply_lot_settings(LotInput(w.lot.lot_name, "012345", w.lot.lower_V, w.lot.upper_V, 1, ""))
    assert record_and_wait(w, qapp)
    assert [r.operator for r in w.book.records] == [LOT.operator, "012345"]


def _lot_with_records(make_window, volts):
    from datetime import datetime

    from battery_checker.records import Measurement, make_record

    w = make_window()
    w.start_lot(LOT)
    for v in volts:
        w.book.append(make_record(w.book.next_no, Measurement(v, v, v), 3.6, 4.2, 0.005, datetime(2026, 11, 9, 9, 0)))
    w.workfile.save(w.lot, w.book)
    w._show_lot()
    return w


def _drag(plot, line, value):
    """ドラッグと同じ順で信号を出す（動かす → 離す）。"""
    line.setValue(value)
    line.sigPositionChangeFinished.emit(line)


def test_limit_lines_adjust_toggle(make_window, qapp):
    w = make_window()
    assert not w.plot.adjust_button.isEnabled()  # ロットなし
    w.start_lot(LOT)
    assert w.plot.adjust_button.isEnabled() and not w.plot.upper_line.movable
    assert w.plot.lower_line.label.textItem.toPlainText() == "下限 3.600 V"
    assert w.plot.upper_line.label.textItem.toPlainText() == "上限 4.200 V"
    w.plot.adjust_button.click()
    assert w.plot.adjustable and w.plot.upper_line.movable and w.plot.lower_line.movable
    w.plot.adjust_button.click()
    assert not w.plot.upper_line.movable


def test_drag_upper_limit_rejudges_after_confirm(make_window, qapp):
    from battery_checker.workfile import read_records

    w = _lot_with_records(make_window, [3.80, 3.95, 4.10, 4.15])
    w.plot.set_adjustable(True)
    previews = []
    w.plot.limits_dragging.connect(lambda lo, hi, ng: previews.append((lo, hi, ng)))
    w.answers = ["yes"]
    _drag(w.plot, w.plot.upper_line, 4.0004)  # 0.001 V 刻みに合わせる
    assert w.plot.upper_line.value() == 4.0
    assert previews[-1] == (3.6, 4.0, 2)  # 動かしている間：NG になる件数
    assert len(w.asked) == 1 and "判定が変わる記録が 2 件" in w.asked[0]
    assert (w.lot.lower_V, w.lot.upper_V) == (3.6, 4.0)
    assert [r.judge.value for r in w.book.records] == ["OK", "OK", "NG_HIGH", "NG_HIGH"]
    assert w.lot_label.text() == "LOT-A12 ／ 下限 3.600 V・上限 4.000 V"
    assert w.plot.upper_line.label.textItem.toPlainText() == "上限 4.000 V"
    assert (w.settings.lower_V, w.settings.upper_V) == (3.6, 4.0)  # 次のロットの初期値
    assert w.workfile.flush()
    recs = read_records(w.workfile.csv_path, 1)[0]
    assert [r.judge.value for r in recs] == ["OK", "OK", "NG_HIGH", "NG_HIGH"]


def test_drag_declined_reverts(make_window, qapp):
    w = _lot_with_records(make_window, [3.80, 4.10])
    w.plot.set_adjustable(True)
    w.answers = ["no"]
    _drag(w.plot, w.plot.upper_line, 4.0)
    assert (w.lot.upper_V, w.plot.upper_line.value()) == (4.2, 4.2)
    assert all(r.judge.value == "OK" for r in w.book.records)
    assert w.plot.ng_item.data.size == 0  # 色も元に戻る


def test_drag_without_judgement_change_does_not_ask(make_window, qapp):
    w = _lot_with_records(make_window, [3.80])
    w.plot.set_adjustable(True)
    _drag(w.plot, w.plot.lower_line, 3.55)
    assert w.asked == [] and w.lot.lower_V == 3.55


def test_limit_lines_cannot_cross(make_window, qapp):
    w = _lot_with_records(make_window, [])
    w.plot.set_adjustable(True)
    w.plot.lower_line.setValue(5.0)  # 上限より上には行けない
    assert w.plot.lower_line.value() == pytest.approx(4.199)
    w.plot.upper_line.setValue(1.0)
    assert w.plot.upper_line.value() == pytest.approx(4.2)  # 下限＋0.001 より下には行けない


def test_adjust_disabled_while_measuring(make_window, qapp):
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    w.plot.set_adjustable(True)
    w.on_record()
    assert w.collector is not None
    assert not w.plot.adjust_button.isEnabled() and not w.plot.adjustable
    assert wait_until(qapp, lambda: w.collector is None, 3.0)
    assert w.plot.adjust_button.isEnabled()


def test_number_inputs_are_not_collapsed(qapp):
    """設定・開始番号の数値欄：中の文字欄が幅 1px につぶれて数値が見えなくなる不具合（Qt の NoButtons ＋スタイルシート）の再発防止。"""
    from battery_checker.gui.dialogs import LotDialog, SettingsDialog
    from battery_checker.settings import Settings

    s = SettingsDialog(None, Settings())
    d = LotDialog(None, LotInput("A", "", 3.6, 4.2, 1, ""))
    for dlg in (s, d):
        dlg.show()
    qapp.processEvents()
    for spin in (s.threshold_spin, s.stable_spin, d.start_spin):
        assert spin.lineEdit().width() > spin.width() * 0.6, (spin, spin.lineEdit().geometry())
        assert spin.lineEdit().isVisible()
    assert s.threshold_spin.text() == "0.50 V" and s.stable_spin.text() == "5.0 mV"
    s.threshold_spin.lineEdit().selectAll()
    QTest.keyClicks(s.threshold_spin.lineEdit(), "0.8")
    assert s.threshold_spin.value() == pytest.approx(0.8)
    s.close()
    d.close()


def test_operator_is_employee_id(qapp):
    """測定者は名前ではなく社員番号（半角英数字・ハイフン）。全角は半角に直す。空欄は可。"""
    from battery_checker.gui.dialogs import LotDialog, is_valid_employee_id, normalize_employee_id

    assert normalize_employee_id(" ０１２３４５ ") == "012345"
    assert normalize_employee_id("Ａ１２－３４") == "A12-34"
    for ok in ("", "123456", "A12345", "12-345"):
        assert is_valid_employee_id(ok), ok
    for ng in ("福井", "福井 123", "12 34", "-123", "1" * 21, "12_34"):
        assert not is_valid_employee_id(ng), ng

    d = LotDialog(None, LotInput("LOT-A12", "福井", 3.6, 4.2, 1, ""))  # 前回値が名前のまま
    assert not d.ok_button.isEnabled() and d.operator_edit.property("invalid")
    assert "社員番号" in d.error_label.text()
    d.operator_edit.setText("０１２３４５")
    assert d.ok_button.isEnabled() and not d.operator_edit.property("invalid")
    assert d.value().operator == "012345"
    d.operator_edit.setText("")
    assert d.ok_button.isEnabled() and d.value().operator == ""
    assert not d.operator_edit.testAttribute(Qt.WA_InputMethodEnabled)  # 日本語入力はオフ


def test_export_dialog_preview(qapp, tmp_path):
    from battery_checker.export import resolve_base
    from battery_checker.gui.dialogs import ExportDialog
    from battery_checker.records import LotInfo

    info = LotInfo("x", "LOT-A12", 3.6, 4.2)
    (tmp_path / "LOT-A12_1-5_電圧記録_20261109.csv").write_text("", encoding="utf-8")
    d = ExportDialog(None, str(tmp_path), "LOT-A12_1-5_電圧記録_20261109", lambda f, b: resolve_base(f, info, b))
    assert "LOT-A12_1-5_電圧記録_20261109_2.csv" in d.preview_label.text()  # 同じ名前があれば _2
    info.last_export_dir, info.last_export_base = str(tmp_path), "LOT-A12_1-5_電圧記録_20261109"
    d.name_edit.setText("LOT-A12_1-5_電圧記録_20261109 ")
    assert "前回の出力を上書きします" in d.preview_label.text()
    d.name_edit.setText("a b")
    assert "a_b.csv" in d.preview_label.text() and d.ok_button.isEnabled()
    d.name_edit.setText("")
    assert not d.ok_button.isEnabled()
    d.reset_button.click()
    assert d.base == "LOT-A12_1-5_電圧記録_20261109"


# ================================================================ NG の確認ウィンドウ
NG_LOT = LotInput("LOT-NG", "123456", 3.8, 4.2, 1, "")  # MOCK3 の電圧 3.7363 V は下限未満


def _ng_window(make_window, qapp, answers):
    w = make_window(speed=5.0, answers=answers)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(NG_LOT)
    assert wait_stable_new_battery(w, qapp)
    return w


def _ng_asked(w):
    return [a for a in w.asked if a.startswith("NG です")]


def test_ng_confirm_record(make_window, qapp):
    w = _ng_window(make_window, qapp, ["record"])
    w.on_record()
    assert wait_until(qapp, lambda: len(w.book) == 1, 3.0)
    asked = _ng_asked(w)
    assert len(asked) == 1 and asked[0].startswith("NG です：No.1　3.7363 V（下限未満。下限 3.800 V）")
    assert w.book.get(1).judge.value == "NG_LOW" and not w.busy


def test_ng_confirm_retry_then_record(make_window, qapp):
    w = _ng_window(make_window, qapp, ["retry", "record"])
    w.on_record()
    assert wait_until(qapp, lambda: len(w.book) == 1, 5.0)
    assert len(_ng_asked(w)) == 2  # もう一度測定 → また NG なので再び確認
    assert w.book.next_no == 2


def test_ng_pending_blocks_other_operations(make_window, qapp):
    w = _ng_window(make_window, qapp, [])
    seen = {}

    def ask(text, choices, escape):
        # 確認ウィンドウが出ている間の状態を記録してから「記録する」
        seen.update(busy=w.busy, record=w.record_button.isEnabled(), export=w.export_button.isEnabled(),
                    state=w.state_label.text())
        w.asked.append(text)
        return "record"

    w._ask = ask
    w.on_record()
    assert wait_until(qapp, lambda: len(w.book) == 1, 3.0)
    assert seen == {"busy": True, "record": False, "export": False, "state": "NG の確認中"}


def test_ng_confirm_can_be_turned_off(make_window, qapp):
    w = _ng_window(make_window, qapp, [])
    w.settings.confirm_ng = False
    assert record_and_wait(w, qapp)
    assert _ng_asked(w) == [] and w.book.get(1).judge.value == "NG_LOW"


def test_ok_is_not_confirmed(make_window, qapp):
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    assert record_and_wait(w, qapp)
    assert _ng_asked(w) == []


# ================================================================ 連続測定モード
def test_continuous_mode_records_automatically(make_window, qapp, monkeypatch):
    import battery_checker.serial_io as sio
    from battery_checker.gui.main_window import CONTINUOUS_TEXT, MSG_DUPLICATE
    from battery_checker.mock_serial import MockProfile

    monkeypatch.setitem(sio.MOCK_PORTS, "MOCK1", ("付け外し（長め）", MockProfile(battery_cycle=(5.0, 1.0))))
    w = make_window(speed=6.0)
    w.settings.confirm_ng = False
    assert not w.continuous_button.isEnabled()  # 未接続・ロットなし
    connect(w, qapp, "MOCK1")
    w.start_lot(LOT)
    assert w.continuous_button.isEnabled()
    w.continuous_button.click()
    assert w.continuous and w.record_button.text() == CONTINUOUS_TEXT
    assert w.continuous_button.text() == "連続測定モード：オン"
    # キーを押さずに 5 件記録される（安定 → 記録 → 0 V → 次のバッテリが安定 → 記録 …）
    assert wait_until(qapp, lambda: len(w.book) >= 5, 20.0), len(w.book)
    assert [r.no for r in w.book.records][:5] == [1, 2, 3, 4, 5]
    assert MSG_DUPLICATE not in w.asked
    # 同じバッテリを 2 回記録していない（付け外しのたびに電圧が違う）
    volts = [r.voltage_V for r in w.book.records]
    assert all(a != b for a, b in zip(volts, volts[1:]))
    # オフにすると自動で記録しない
    w.continuous_button.click()
    assert not w.continuous and w.record_button.text() == "● 記録（Space）"
    n = len(w.book)
    wait(qapp, 2.0)
    assert len(w.book) == n


def test_continuous_mode_waits_for_removal(make_window, qapp):
    """繋いだままのバッテリは 1 回だけ記録し、外して 0 V になるまで次を記録しない。"""
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    w.set_continuous(True)
    assert wait_until(qapp, lambda: len(w.book) == 1, 5.0)
    wait(qapp, 1.5)
    assert len(w.book) == 1
    assert "バッテリを外してください" in w.msg_label.text()


def test_continuous_mode_off_on_disconnect(make_window, qapp):
    w = make_window(speed=5.0)
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    w.set_continuous(True)
    assert w.continuous
    w.disconnect_port()
    assert not w.continuous and not w.continuous_button.isChecked()
    assert not w.continuous_button.isEnabled()


# ================================================================ 新しいロット：下限・上限のバー
def _drag_handle(bar, which, to_v):
    from PySide6.QtCore import QPoint

    c = bar.handle_center(which).toPoint()
    target = QPoint(int(round(bar._x_of(to_v))), c.y())
    QTest.mousePress(bar, Qt.LeftButton, Qt.NoModifier, c)
    for i in range(1, 6):
        QTest.mouseMove(bar, QPoint(c.x() + (target.x() - c.x()) * i // 5, c.y()))
    QTest.mouseRelease(bar, Qt.LeftButton, Qt.NoModifier, target)


def test_lot_dialog_range_bar(qapp):
    from battery_checker.gui.dialogs import LotDialog

    d = LotDialog(None, LotInput("CLC", "105619", 3.6, 4.0, 1, ""))
    d.resize(480, 460)
    d.show()
    qapp.processEvents()
    bar = d.range_bar
    assert bar.values() == (3.6, 4.0) and bar.visible_range() == (3.0, 4.5)
    # 上のつまみをドラッグ → 上限の欄が変わる（0.01 V 刻み）
    _drag_handle(bar, "hi", 4.3)
    assert d.upper_edit.text() == "4.300" and d.lower_edit.text() == "3.600"
    assert d.ok_button.isEnabled()
    # 下のつまみ：上限より上には行けない
    _drag_handle(bar, "lo", 4.5)
    lo, hi = bar.values()
    assert lo < hi and float(d.lower_edit.text()) < float(d.upper_edit.text())
    # 矢印キーで 0.001 V、Shift で 0.01 V
    d.lower_edit.setText("3.600")
    _drag_handle(bar, "hi", 4.2)
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key_Right)
    assert d.upper_edit.text() == "4.201"
    QTest.keyClick(bar, Qt.Key_Left, Qt.ShiftModifier)
    assert d.upper_edit.text() == "4.191"
    # 入力欄に書く → つまみが動き、範囲もそれに合わせる
    d.lower_edit.setText("12.000")
    d.upper_edit.setText("14.400")
    assert bar.values() == (12.0, 14.4) and bar.visible_range() == (10.5, 16.0)
    v = d.value()
    assert (v.lower_V, v.upper_V) == (12.0, 14.4)


def test_lot_dialog_range_bar_without_values(qapp):
    from battery_checker.gui.dialogs import LotDialog

    d = LotDialog(None, LotInput("CLC", "", 0.0, 0.0, 1, ""))
    d.resize(480, 460)
    d.show()
    qapp.processEvents()
    assert d.range_bar.visible_range() == (0.0, 5.0)
    assert d.lower_edit.text() == "" and not d.ok_button.isEnabled()
    # つまみを動かすと、下限・上限の両方の欄が入る
    _drag_handle(d.range_bar, "lo", 3.5)
    assert abs(float(d.lower_edit.text()) - 3.5) <= 0.02 and d.upper_edit.text() == "4.200"  # 0〜5 V では 1 px ≒ 17 mV
    assert d.ok_button.isEnabled()


def test_lot_dialog_field_order(qapp):
    """並びと Tab の順：ロット名 → 開始番号 → 測定者 → 下限 → 上限 → バー → メモ。"""
    from battery_checker.gui.dialogs import LotDialog

    d = LotDialog(None, LotInput("CLC", "105619", 3.65, 4.0, 1, ""))
    d.show()
    qapp.processEvents()
    expected = [d.name_edit, d.start_spin, d.operator_edit, d.lower_edit, d.upper_edit, d.range_bar, d.memo_edit]
    ys = [w.mapTo(d, w.rect().topLeft()).y() for w in expected]
    assert ys == sorted(ys) and len(set(ys)) == len(ys)
    targets = {id(w) for w in expected}
    chain, w = [], d.name_edit
    while len(chain) < len(expected):
        if id(w) in targets and (not chain or chain[-1] is not w):
            chain.append(w)
        w = w.nextInFocusChain()
    assert chain == expected


# ================================================================ 右下のボタン（開始・終了・出力）と「終了」
def test_bottom_buttons_labels_and_order(make_window, qapp):
    w = make_window()
    buttons = [w.new_lot_button, w.end_lot_button, w.export_button]
    assert [b.text() for b in buttons] == ["開始", "終了", "出力"]
    xs = [b.mapTo(w, b.rect().topLeft()).x() for b in buttons]
    assert xs == sorted(xs)  # 左から 開始 → 終了 → 出力
    # 左パネルの一番下（記録ボタンより下）にある
    ys = [b.mapTo(w, b.rect().topLeft()).y() for b in buttons]
    assert min(ys) > w.record_button.mapTo(w, w.record_button.rect().topLeft()).y()
    assert all(w.left_scroll.isAncestorOf(b) for b in buttons)


def test_end_asks_export_yes_exports_then_ends(make_window, qapp, tmp_path):
    w = _lot_with_records(make_window, [3.8, 3.9, 4.0])
    w.answers = ["yes"]
    assert w.on_end_lot()
    assert len(w.asked) == 1 and "CSV・Excel 出力しますか？" in w.asked[0]  # 出力のあとにもう一度は聞かない
    out = tmp_path / "out"
    assert sorted(p.suffix for p in out.iterdir()) == [".csv", ".xlsx"]
    assert w.export_defaults and "_0001-0003_" in w.export_defaults[0]
    assert w.lot is None
    assert w.msg_label.text().startswith(f"保存しました：{out}") and "終了しました" in w.msg_label.text()


def test_end_cancel_or_export_cancelled_keeps_lot(make_window, qapp, tmp_path):
    w = _lot_with_records(make_window, [3.8])
    w.answers = ["cancel"]
    assert not w.on_end_lot() and w.lot is not None
    # 「はい」でも、出力の画面でやめたら終了しない
    w._ask_export = lambda default: None
    w.answers = ["yes"]
    assert not w.on_end_lot() and w.lot is not None
    assert "終了していません" in w.msg_label.text()
    assert not (tmp_path / "out").exists()


def test_end_without_records_only_confirms(make_window, qapp):
    w = make_window()
    w.start_lot(LOT)
    w.answers = ["end"]
    assert w.on_end_lot()
    assert w.asked == ["ロット（LOT-A12）を終了しますか？"] and w.lot is None


def test_record_does_not_wait_for_slow_disk(make_window, qapp, monkeypatch):
    """作業ファイルの書き込みが遅くても（0.8 秒）、記録の結果はすぐ出る。書き込みはあとで終わる。"""
    import battery_checker.workfile as wfmod

    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_stable_new_battery(w, qapp)
    real = wfmod._fsync_write

    def slow(*a, **k):
        time.sleep(0.8)
        return real(*a, **k)

    monkeypatch.setattr(wfmod, "_fsync_write", slow)
    assert record_and_wait(w, qapp)
    assert w.last_latency_s < 0.5, (w.last_latency_s, w.last_save_s)
    assert w.workfile.flush(5.0)
    from battery_checker.workfile import read_records

    assert len(read_records(w.workfile.csv_path, 1)[0]) == 1
