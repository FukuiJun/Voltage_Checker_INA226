"""電圧の監視（F-03）と 5 回測定（F-04）。"""

from battery_checker.measure import BatteryMonitor, BatteryState, CollectFailure, SampleCollector

TH = 0.5


def test_ac02_collector_median_of_five_after_press():
    c = SampleCollector(started_at=10.0, threshold_V=TH)
    # 押す前に届いていた行は使わない
    assert c.feed(3.9000, 9.99) is None
    vals = [3.8125, 3.8113, 3.8138, 3.8125, 3.8100]
    out = [c.feed(v, 10.0 + 0.04 * (i + 1)) for i, v in enumerate(vals)]
    assert out[:4] == [None] * 4
    res = out[4]
    assert res.ok and res.samples == tuple(vals)
    m = res.measurement
    assert (m.median_V, m.min_V, m.max_V) == (3.8125, 3.8100, 3.8138)
    assert c.feed(3.0, 11.0) is None  # 終わったあとは何もしない


def test_ac05_detached_during_collection():
    c = SampleCollector(started_at=0.0, threshold_V=TH)
    c.feed(3.81, 0.04)
    c.feed(3.81, 0.08)
    res = c.feed(0.0, 0.12)
    assert not res.ok and res.failure is CollectFailure.DETACHED
    assert res.failure.value == "測定中にバッテリが外れました"


def test_timeout_when_rows_do_not_arrive():
    c = SampleCollector(started_at=0.0, threshold_V=TH)
    c.feed(3.81, 0.04)
    assert c.poll(0.9) is None
    res = c.poll(1.01)
    assert res.failure is CollectFailure.NO_DATA
    assert res.failure.value == "データを受信できませんでした。もう一度記録してください"
    assert c.feed(3.81, 1.02) is None


def test_monitor_states():
    m = BatteryMonitor(TH, 0.005)
    assert m.state is BatteryState.NO_DATA and not m.connected
    m.feed(0.0, 0.0)
    assert m.state is BatteryState.NONE and not m.connected
    t = 1.0
    for v in (2.0, 3.0, 3.8):  # 繋いだ直後（接点が落ち着かない）
        m.feed(v, t)
        t += 0.04
    assert m.connected and m.state is BatteryState.UNSTABLE
    for _ in range(13):  # 0.5 秒ぶん安定（繋いだ直後の揺れた値が時間幅から外れる）
        m.feed(3.8125, t)
        t += 0.04
    assert m.state is BatteryState.STABLE
    m.feed(3.8175, t)  # 5 mV ちょうどは安定
    assert m.state is BatteryState.STABLE
    m.feed(3.8188, t + 0.04)
    assert m.state is BatteryState.UNSTABLE


def test_monitor_detached_flag_for_duplicate_check():
    m = BatteryMonitor(TH, 0.005)
    m.feed(3.8, 0.0)
    assert m.detached_since_mark  # 最初は確認を出さない
    m.mark_recorded()
    m.feed(3.8, 0.1)
    assert not m.detached_since_mark  # 外さずに次を押した → 確認を出す
    m.feed(0.0, 0.2)
    m.feed(3.9, 0.3)
    assert m.detached_since_mark  # 一度外した


def test_not_stable_until_window_is_filled():
    """繋いだ直後は 1 個だけでも幅 0 になるが、0.5 秒ぶんたまるまでは「安定」にしない。"""
    m = BatteryMonitor(TH, 0.005)
    m.feed(0.0, 0.0)
    t = 1.0
    states = []
    for _ in range(14):
        m.feed(3.8125, t)
        states.append(m.state)
        t += 0.04
    assert states[0] is BatteryState.UNSTABLE
    assert states[:11] == [BatteryState.UNSTABLE] * 11  # 0.40 秒まで
    assert states[12] is BatteryState.STABLE  # 0.48 秒（約 12 個）で安定
    m.feed(0.0, t)
    m.feed(3.8125, t + 0.04)
    assert m.state is BatteryState.UNSTABLE  # 付け直したら最初から
