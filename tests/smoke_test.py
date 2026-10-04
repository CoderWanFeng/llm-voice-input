"""冒烟测试：只验证不依赖麦克风和 GUI 的纯逻辑。

运行：.venv/Scripts/python.exe tests/smoke_test.py
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from voice_input.constants import AUDIO_RATE  # noqa: E402
from voice_input.hotkey import parse_combo  # noqa: E402
from voice_input.models import (  # noqa: E402
    AppState,
    AudioClip,
    StateMachine,
    Transcript,
)
from voice_input.postprocess import polish  # noqa: E402
from voice_input.asr import MockAsrClient  # noqa: E402


class TestAudioClip(unittest.TestCase):
    def test_duration(self):
        # 16000Hz * 2字节 * 1声道 = 1 秒
        clip = AudioClip(pcm=b"\x00" * (AUDIO_RATE * 2))
        self.assertEqual(clip.duration_ms, 1000)

    def test_wav_header(self):
        clip = AudioClip(pcm=b"\x00" * 1600)
        wav = clip.to_wav_bytes()
        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:12])


class TestStateMachine(unittest.TestCase):
    def test_valid_transition(self):
        m = StateMachine()
        self.assertTrue(m.transition(AppState.RECORDING))
        self.assertEqual(m.state, AppState.RECORDING)

    def test_invalid_transition_rejected(self):
        """规矩 R3：禁止跨态跳转，IDLE 不能直接到 DONE。"""
        m = StateMachine()
        self.assertFalse(m.transition(AppState.DONE))
        self.assertEqual(m.state, AppState.IDLE)

    def test_force_idle(self):
        m = StateMachine()
        m.transition(AppState.RECORDING)
        m.transition(AppState.TRANSCRIBING)
        self.assertTrue(m.force_idle())
        self.assertEqual(m.state, AppState.IDLE)

    def test_listener_called(self):
        seen = []
        m = StateMachine()
        m.on_change(lambda o, n: seen.append((o, n)))
        m.transition(AppState.RECORDING)
        self.assertEqual(seen, [(AppState.IDLE, AppState.RECORDING)])


class TestTranscript(unittest.TestCase):
    def test_empty_not_ok(self):
        """规矩 R8 依据 ok 判断是否上屏。"""
        self.assertFalse(Transcript(text="   ").ok)
        self.assertFalse(Transcript(text="hi", error="timeout").ok)
        self.assertTrue(Transcript(text="你好").ok)


class TestHotkey(unittest.TestCase):
    def test_parse_combo(self):
        import win32con

        mods, vk = parse_combo("ctrl+alt+space")
        self.assertEqual(mods, win32con.MOD_CONTROL | win32con.MOD_ALT)
        self.assertEqual(vk, win32con.VK_SPACE)

    def test_parse_invalid(self):
        with self.assertRaises(ValueError):
            parse_combo("ctrl++")


class TestPolish(unittest.TestCase):
    def test_glossary_longest_first(self):
        glossary = {"语音输入": "语音输入", "语音": "Speech"}
        out = polish("语音 输入", glossary)
        self.assertIn("Speech", out)


class TestMockAsr(unittest.TestCase):
    def test_returns_text(self):
        client = MockAsrClient(replies=["测试文本"], delay_s=0)
        result = client.transcribe(AudioClip(pcm=b"\x00" * 3200))
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "测试文本")


if __name__ == "__main__":
    unittest.main(verbosity=2)
