"""Recorder 录音器：常驻流 + 前置缓冲，解决冷启动丢音头。

关键设计：麦克风流**常驻打开**，按键只是切换"是否累积"。
这样按下瞬间不会因开流耗时（200~500ms）丢掉第一个字。
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import soxr

from .constants import (
    AUDIO_CHANNELS,
    AUDIO_RATE,
    AUDIO_WIDTH,
    BLOCK_BYTES,
    BLOCK_MS,
    PREROLL_BLOCKS,
)
from .models import AudioClip

logger = logging.getLogger("voice_input.recorder")


class Recorder:
    """麦克风录音器（线程安全）。"""

    def __init__(self, device: Optional[str] = None) -> None:
        self._device = device
        self._stream: Optional[sd.InputStream] = None
        self._native_rate = AUDIO_RATE
        self._need_resample = False

        self._lock = threading.RLock()
        self._preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_BLOCKS)
        self._head: list[bytes] = []      # 按下瞬间保留的前置音频
        self._chunks: list[bytes] = []    # 正式录音内容
        self._recording = False
        self._has_preroll = False
        self._last_block = b""
        self._error: Optional[str] = None

    # ---------------- 生命周期 ----------------

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def error(self) -> Optional[str]:
        return self._error

    def open(self) -> bool:
        """打开常驻输入流。设备不支持 16k 时改用原生采样率 + 重采样。"""
        if self._stream is not None:
            return True
        blocksize = int(AUDIO_RATE * BLOCK_MS / 1000)
        try:
            self._stream = sd.InputStream(
                samplerate=AUDIO_RATE,
                channels=AUDIO_CHANNELS,
                dtype="int16",
                blocksize=blocksize,
                callback=self._callback,
                device=self._device,
            )
            self._native_rate = AUDIO_RATE
            self._need_resample = False
        except Exception:
            # 回退：按设备原生采样率开流，stop() 时重采样到 16k
            try:
                idx = self._device if self._device is not None else sd.default.device[0]
                info = sd.query_devices(idx, "input")
                native = int(round(float(info["default_samplerate"])))
                self._stream = sd.InputStream(
                    samplerate=native,
                    channels=AUDIO_CHANNELS,
                    dtype="int16",
                    blocksize=int(native * BLOCK_MS / 1000),
                    callback=self._callback,
                    device=self._device,
                )
                self._native_rate = native
                self._need_resample = True
                logger.warning("设备不支持 16k，改用 %d Hz 并在封口时重采样", native)
            except Exception as exc:  # 无可用输入设备
                self._error = f"无法打开麦克风：{exc}"
                logger.error(self._error)
                self._stream = None
                return False

        self._stream.start()
        logger.info("录音流已就绪：%d Hz，块大小 %d 字节", self._native_rate, BLOCK_BYTES)
        return True

    def close(self) -> None:
        with self._lock:
            self._recording = False
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        logger.info("录音流已关闭")

    # ---------------- 录音 ----------------

    def start(self) -> bool:
        """开始累积（不重启流，因此不丢音头）。"""
        if not self.is_open and not self.open():
            return False
        with self._lock:
            if self._recording:  # 规矩 R1：重复触发视为幂等
                return True
            self._head = list(self._preroll)
            self._has_preroll = len(self._head) > 0
            self._chunks = []
            self._recording = True
        logger.info("开始录音（前置缓冲 %d 块）", len(self._head) if self._has_preroll else 0)
        return True

    def stop(self) -> Optional[AudioClip]:
        """封口，产出不可变 AudioClip。"""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            blocks = self._head + self._chunks
            has_preroll = self._has_preroll
            self._head, self._chunks = [], []

        pcm = b"".join(blocks)
        if self._need_resample and pcm:
            pcm = _resample(pcm, self._native_rate, AUDIO_RATE)

        clip = AudioClip(pcm=pcm, has_preroll=has_preroll)
        logger.info("录音封口：%d ms，%d 字节", clip.duration_ms, len(clip.pcm))
        return clip

    # ---------------- 辅助 ----------------

    def level(self) -> float:
        """最近一块的音量（0~1），供浮窗波形使用。"""
        with self._lock:
            last = self._last_block
        if not last:
            return 0.0
        arr = np.frombuffer(last, dtype="<i2").astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(arr * arr)))

    def _callback(self, indata, frames, time_info, status) -> None:
        """音频回调：只写缓冲，绝不阻塞（规矩 R4）。"""
        data = indata.tobytes()
        with self._lock:
            self._preroll.append(data)
            if self._recording:
                self._chunks.append(data)
            self._last_block = data
        if status:
            self._error = f"音频流状态异常：{status}"


def _resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """重采样到目标采样率（soxr，质量高且比 scipy 轻）。"""
    if src_rate == dst_rate or not pcm:
        return pcm
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    out = soxr.resample(arr, src_rate, dst_rate)
    out = np.clip(out, -1.0, 1.0)
    return (out * 32767.0).astype("<i2").tobytes()


def list_input_devices() -> list[str]:
    """列出可用输入设备名，供设置界面使用。"""
    try:
        return [d["name"] for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
    except Exception:
        return []
