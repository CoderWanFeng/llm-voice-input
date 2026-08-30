"""麦克风录音模块。

对应 macOS 原项目 AudioRecorder.swift：
- 使用 sounddevice 直接采集 16kHz / mono / Int16 PCM
- 维护累计采样缓冲区，供 pull_since_last 增量发送
- 实时计算 RMS 用于悬浮面板音量显示
- 线程安全：sounddevice 在独立线程回调，主线程通过 lock 拉取
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
except Exception as e:  # sounddevice 依赖 PortAudio，可能未安装
    _SD_AVAILABLE = False
    _SD_IMPORT_ERROR = str(e)

from .diag_log import DiagLog


# 目标音频参数：与原项目一致
_SAMPLE_RATE = 16000  # 16kHz
_CHANNELS = 1         # 单声道
_BLOCK_SIZE = 1024     # 每次回调块大小（约 64ms）

# 静音检测：RMS 低于阈值持续超过该时长时触发 on_silence_timeout
# （用于"会议中麦克风被静音/默认设备被切换导致白录"的防御：自动结束并提示）
_SILENCE_RMS_THRESHOLD = 0.004
_SILENCE_TIMEOUT_S = 5.0


class AudioRecorder:
    """麦克风录音器，输出 16kHz mono Int16 LE PCM。"""

    def __init__(self) -> None:
        # 音量回调（RMS，0~1 范围）
        self.on_amplitude: Optional[Callable[[float], None]] = None
        # 累计 float32 采样（-1.0~1.0）
        self._all_samples: np.ndarray = np.array([], dtype=np.float32)
        # 已被 pull_since_last 消费的采样数
        self._consumed = 0
        # 缓冲锁
        self._lock = threading.Lock()
        # sounddevice 输入流
        self._stream: Optional["sd.InputStream"] = None
        # 诊断计数
        self._tap_count = 0
        self._last_log_time = 0.0
        # RMS 统计（周期日志用：1 秒窗口内的均值/峰值）
        self._rms_sum = 0.0
        self._rms_count = 0
        self._rms_max = 0.0
        # 静音检测状态
        self._silence_start: Optional[float] = None
        self._silence_notified = False
        # 静音超时回调（在录音回调线程触发）
        self.on_silence_timeout: Optional[Callable[[], None]] = None
        # 启动状态
        self._running = False

    def start(self) -> None:
        """启动录音，若已在运行则先停止再启动。"""
        if not _SD_AVAILABLE:
            DiagLog.shared().write(f"[Audio] sounddevice 不可用: {_SD_IMPORT_ERROR}")
            raise RuntimeError("sounddevice 不可用，请检查 PortAudio 安装")

        # 先停止
        self.stop()

        with self._lock:
            self._all_samples = np.array([], dtype=np.float32)
            self._consumed = 0
            self._tap_count = 0
            self._last_log_time = 0.0
            self._rms_sum = 0.0
            self._rms_count = 0
            self._rms_max = 0.0
            self._silence_start = None
            self._silence_notified = False

        # 打印实际使用的默认输入设备名（排查"设备被会议/系统切换"问题）
        try:
            dev = sd.query_devices(kind="input")
            DiagLog.shared().write(f"[Audio] 默认输入设备: {dev['name']}")
        except Exception as e:
            DiagLog.shared().write(f"[Audio] 查询默认输入设备失败: {e}")

        # 创建 InputStream，回调在 sounddevice 独立线程触发
        DiagLog.shared().write(f"[Audio] 启动录音 rate={_SAMPLE_RATE} ch={_CHANNELS} block={_BLOCK_SIZE}")
        try:
            self._stream = sd.InputStream(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype="float32",
                blocksize=_BLOCK_SIZE,
                callback=self._audio_callback,
            )
            self._stream.start()
            self._running = True
            DiagLog.shared().write("[Audio] InputStream 已启动")
        except Exception as e:
            DiagLog.shared().write(f"[Audio] 启动失败: {e}")
            raise

    def stop(self) -> np.ndarray:
        """停止录音，返回累计 float32 采样数组（完整副本）。"""
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._running = False
        with self._lock:
            return self._all_samples.copy()

    def pull_since_last(self) -> np.ndarray:
        """增量拉取自上次以来新增的采样数组。"""
        with self._lock:
            total = len(self._all_samples)
            if total <= self._consumed:
                return np.array([], dtype=np.float32)
            new = self._all_samples[self._consumed:total].copy()
            self._consumed = total
            return new

    def is_running(self) -> bool:
        """返回录音是否仍在运行。"""
        return self._running

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """sounddevice 输入回调：将数据追加到累计缓冲，并通知音量。"""
        # indata shape: (frames, channels)，dtype float32
        self._tap_count += 1
        with self._lock:
            # 单声道取第 0 通道
            mono = indata[:, 0] if _CHANNELS == 1 else indata.mean(axis=1)
            self._all_samples = np.concatenate([self._all_samples, mono])

        # 计算 RMS 音量
        rms = float(np.sqrt(np.mean(mono * mono))) if len(mono) > 0 else 0.0
        if self.on_amplitude:
            try:
                self.on_amplitude(rms)
            except Exception:
                pass

        now = time.time()

        # RMS 统计（供周期日志）
        self._rms_sum += rms
        self._rms_count += 1
        if rms > self._rms_max:
            self._rms_max = rms

        # 静音检测：持续低音量超过阈值时长时通知一次（用于自动结束白录）
        if rms < _SILENCE_RMS_THRESHOLD:
            if self._silence_start is None:
                self._silence_start = now
            elif not self._silence_notified and now - self._silence_start >= _SILENCE_TIMEOUT_S:
                self._silence_notified = True
                DiagLog.shared().write(
                    f"[Audio] ⚠️ 已连续 {int(now - self._silence_start)} 秒未检测到声音"
                    f"（RMS < {_SILENCE_RMS_THRESHOLD}），疑似麦克风静音或输入设备被切换"
                )
                if self.on_silence_timeout:
                    try:
                        self.on_silence_timeout()
                    except Exception:
                        pass
        else:
            self._silence_start = None

        # 每 1 秒打一次累计日志（含音量统计，便于判断"采到的是不是静音"）
        if now - self._last_log_time > 1.0:
            self._last_log_time = now
            avg = self._rms_sum / self._rms_count if self._rms_count else 0.0
            with self._lock:
                DiagLog.shared().write(
                    f"[Audio] tap={self._tap_count} 累计={len(self._all_samples)}"
                    f" 音量 avg={avg:.4f} max={self._rms_max:.4f}"
                )
            self._rms_sum = 0.0
            self._rms_count = 0
            self._rms_max = 0.0


def samples_to_int16_le(samples: np.ndarray) -> bytes:
    """将 float32 采样（-1.0~1.0）转为 Int16 little-endian PCM 字节串。

    与原项目 floatSamplesToInt16LE 行为一致，供 ASR 发送使用。
    """
    if len(samples) == 0:
        return b""
    # 钳位到 [-1.0, 1.0]
    clamped = np.clip(samples, -1.0, 1.0)
    # 转 Int16
    int16 = (clamped * 32767.0).astype("<i2")  # little-endian Int16
    return int16.tobytes()
