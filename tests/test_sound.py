"""記録時の音（Q-08）：OK は同梱の効果音、NG は Windows の警告音。"""

import sys
import types
import wave

from battery_checker import sound
from battery_checker.resources import SOUND_OK, resource_path


class FakeWinsound(types.SimpleNamespace):
    def __init__(self, fail_play=False):
        super().__init__(
            SND_FILENAME=0x20000, SND_ASYNC=0x1, SND_NODEFAULT=0x2, MB_OK=0x0, MB_ICONHAND=0x10, calls=[]
        )
        self.fail_play = fail_play

    def PlaySound(self, path, flags):  # noqa: N802（winsound と同じ名前）
        self.calls.append(("PlaySound", path, flags))
        if self.fail_play:
            raise RuntimeError("Failed to play sound")

    def MessageBeep(self, kind):  # noqa: N802
        self.calls.append(("MessageBeep", kind))


def _use_fake(monkeypatch, fake):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", fake)


def test_ok_sound_file_is_bundled():
    wav = resource_path(SOUND_OK)
    with wave.open(str(wav)) as w:
        seconds = w.getnframes() / w.getframerate()
        assert w.getsampwidth() == 2 and w.getframerate() == 44100
    assert 0.05 < seconds < 1.0  # 短い音（次の記録の邪魔をしない）


def test_ok_plays_bundled_wav_asynchronously(monkeypatch):
    fake = FakeWinsound()
    _use_fake(monkeypatch, fake)
    sound.play(True)
    assert len(fake.calls) == 1
    name, path, flags = fake.calls[0]
    assert name == "PlaySound" and path.endswith("sound_ok.wav")
    assert flags & fake.SND_ASYNC and flags & fake.SND_FILENAME and flags & fake.SND_NODEFAULT


def test_ng_uses_windows_warning_sound(monkeypatch):
    fake = FakeWinsound()
    _use_fake(monkeypatch, fake)
    sound.play(False)
    assert fake.calls == [("MessageBeep", fake.MB_ICONHAND)]


def test_ok_falls_back_to_standard_sound(monkeypatch, tmp_path):
    fake = FakeWinsound(fail_play=True)
    _use_fake(monkeypatch, fake)
    sound.play(True)  # 効果音を鳴らせない → 標準音
    assert fake.calls[-1] == ("MessageBeep", fake.MB_OK)
    fake2 = FakeWinsound()
    _use_fake(monkeypatch, fake2)
    monkeypatch.setattr(sound, "resource_path", lambda rel: tmp_path / "missing.wav")
    sound.play(True)  # ファイルが無い → 標準音
    assert fake2.calls == [("MessageBeep", fake2.MB_OK)]
