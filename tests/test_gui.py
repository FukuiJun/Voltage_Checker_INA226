"""GUI を模擬シリアルで動かす結合テスト（画面なし・offscreen）。AC-01, 04, 06, 09, 14, 15 など。"""

import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from battery_checker.gui.dialogs import LotInput  # noqa: E402
from battery_checker.paths import AppPaths  # noqa: E402

LOT = LotInput("LOT-A12", "福井", 3.6, 4.2, 1, "")


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
        w._ask_export_folder = lambda: str(tmp_path / "out")
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
    assert w.lot_label.text() == "ロットなし"
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
    assert w.msg_label.text() == MSG_NO_BATTERY
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
        latencies.append(w.last_latency_s)
    assert [r.no for r in w.book.records] == list(range(1, 21))
    assert w.asked == []  # 毎回バッテリを外しているので確認は出ない
    assert max(latencies) < 0.5, latencies
    assert w.table.model.rowCount() == 20 and w.table.selected_no() == 20  # 最新の行を選ぶ
    assert w.number_card.number == "21" and w.number_card.sub_label.text() == "記録済み 20 件"
    assert w.summary_label.text().startswith("件数 20 ／ OK ")
    # 作業ファイルにも 20 件
    from battery_checker.workfile import read_records

    assert len(read_records(w.workfile.csv_path, 1)[0]) == 20


def test_record_latency_real_time(make_window, qapp):
    """実時間（40 ms 周期）で、記録ボタンから結果表示・保存まで 0.5 秒以内（8 章）。"""
    w = make_window(speed=1.0)
    connect(w, qapp, "MOCK3")
    w.start_lot(LOT)
    assert wait_until(qapp, lambda: w.interval_ack is not None, 5.0)
    assert wait_stable_new_battery(w, qapp)
    assert record_and_wait(w, qapp)
    assert w.last_latency_s < 0.5, w.last_latency_s
    assert w.last_latency_s >= 0.15  # 押した後の 5 個（40 ms × 5）を待っている


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
    assert w.remeasure_no == 1 and w.result_main.text() == "No.1 を測り直します"
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
    assert w.msg_label.text().startswith(MSG_WORKFILE_ERROR) and w.msg_label.property("error")
    assert w.workfile.dirty and "（未保存あり）" in w.saved_label.text()
    monkeypatch.setattr(wfmod, "_fsync_write", real)
    assert record_and_wait(w, qapp)  # 次の記録で全件を書き直す
    assert not w.workfile.dirty
    from battery_checker.workfile import read_records

    assert len(read_records(w.workfile.csv_path, 1)[0]) == 2


def test_ac09_resume_in_new_window(make_window, qapp):
    w = make_window(speed=5.0)
    w.settings.confirm_duplicate = False
    connect(w, qapp, "MOCK3")
    w.start_lot(LotInput("LOT-B", "福井", 3.6, 4.2, 101, "メモ"))
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
    assert names[0].endswith("_LOT-A12_電圧記録.csv") and names[1].endswith("_LOT-A12_電圧記録.xlsx")
    assert w.msg_label.text().startswith(f"保存しました：{out}")
    assert w.lot is not None and not w.lot.unexported
    # 続けて記録できる。出力していない記録があればロット終了で確認する
    assert record_and_wait(w, qapp)
    w.answers = ["end"]
    assert w.on_end_lot()
    assert w.asked[-1].startswith("出力していない記録があります")
    assert w.lot is None and w.lot_label.text() == "ロットなし"
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
    w.apply_lot_settings(LotInput("LOT-A12", "福井", round(v + 0.1, 3), round(v + 0.2, 3), 1, ""))
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
    assert d.name_edit.maxLength() == 30 and d.operator_edit.maxLength() == 30


# ================================================================ グラフの線をドラッグして上下限を変える
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
