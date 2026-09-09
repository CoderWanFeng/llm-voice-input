"""语音唤醒模块（Vosk 离线关键词唤醒）。

功能：
- 常驻后台监听麦克风，使用 Vosk 离线识别 + 关键词语法（grammar）检测唤醒词
- 唤醒词与结束词均支持自定义（2~6 个常用汉字），保存后热更新无需重启
- 命中唤醒词（默认「小薇小薇」）后触发 on_wake 回调，自动开始录音
- 录音期间通过录音器分接音频（feed_audio）检测结束词，
  命中（默认「结束录音」）后触发 on_stop 回调，自动停止录音
- 与正式录音共存：录音/识别期间 pause()（关闭监听流让出麦克风），回到空闲 resume()

线程模型：
- sounddevice RawInputStream 回调线程只做入队（bytes），不做识别计算
- 独立工作线程从队列取音频送 Vosk 识别（空闲时来自监听流、录音时来自分接）
- on_wake / on_stop 在工作线程触发，由调用方自行调度到主线程（tkinter 要求）
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
from typing import Callable, List, Optional, Tuple

import numpy as np

from .diag_log import DiagLog

try:
    import sounddevice as sd
    _SD_AVAILABLE = True
    _SD_IMPORT_ERROR = ""
except Exception as e:  # sounddevice 依赖 PortAudio，可能未安装
    _SD_AVAILABLE = False
    _SD_IMPORT_ERROR = str(e)

try:
    from vosk import KaldiRecognizer, Model, SetLogLevel
    _VOSK_AVAILABLE = True
    _VOSK_IMPORT_ERROR = ""
except Exception as e:  # vosk 未安装
    _VOSK_AVAILABLE = False
    _VOSK_IMPORT_ERROR = str(e)


# ===== 音频参数（与 AudioRecorder 一致：16kHz mono Int16）=====
_SAMPLE_RATE = 16000
_CHANNELS = 1
_BLOCK_FRAMES = 4000  # 每块 0.25 秒，降低回调频率与队列开销

# ===== 唤醒词/结束词定义 =====
# 默认唤醒词（用户可在设置对话框中自定义）
WAKE_WORD = "小薇小薇"
# 默认结束词（录音中说出即停止录音；默认不启用，由用户在设置对话框开启）
STOP_WORD = "结束录音"
# 命中后冷却时间（秒），防止一次唤醒触发多次
_WAKE_COOLDOWN_S = 2.5
# 结束词命中去抖（秒）
_STOP_COOLDOWN_S = 1.0

# VAD 门限：块 RMS 低于该值视为环境噪声，不送 Vosk 识别。
# 语法模式的解码器会把噪声强行匹配成候选短语（误唤醒），
# 用能量门限过滤可显著降低误唤醒（实测噪声底约 0.004~0.007，语音约 0.05+）
_VAD_RMS_GATE = 0.012
# 滞后块数：语音结束后继续送 3 块（0.75 秒）音频，
# 让 Vosk 能收到语音尾部的静音从而产出 final 结果
_VAD_HANGOVER_BLOCKS = 3

# 模型目录名（Vosk 中文小模型，约 42MB）
_MODEL_DIR_NAME = "vosk-model-small-cn-0.22"

# Vosk 词库警告解析：语法构建时词库外的 token 会输出该警告到进程 stderr
_MISSING_TOKEN_RE = re.compile(r"Ignoring word missing in vocabulary: '([^']+)'")

# 自定义唤醒词的合法格式：2~6 个汉字
_WAKE_WORD_RE = re.compile(r"^[\u4e00-\u9fff]{2,6}$")


def _normalize_word(word: str) -> str:
    """规范化唤醒词文本：去掉空格、[unk] 标记与首尾空白。"""
    return word.replace(" ", "").replace("[unk]", "").strip()


def _build_grammar_phrase(word: str) -> str:
    """把唤醒词转为逐字分词的语法短语。

    自定义词无法预知词典分词方式，逐字空格分词最稳：
    Vosk 语法模式的输出只可能是语法里的 token 或 [unk]，
    输出文本去空格后与唤醒词精确比对即可判定命中。
    """
    return " ".join(_normalize_word(word))


def _make_grammar(word: str) -> str:
    """根据唤醒词生成 Vosk 语法 JSON（候选短语 + [unk]）。"""
    return json.dumps([_build_grammar_phrase(word), "[unk]"], ensure_ascii=False)


def _create_recognizer_capturing(
    model: "Model", grammar: str
) -> Tuple[Optional["KaldiRecognizer"], str]:
    """创建 KaldiRecognizer 并捕获期间 C 层 stderr（fd 2）的输出。

    Vosk 的词库警告直接写往进程级 stderr（fd 2），Python 的
    redirect_stderr 无法捕获，须用 os.dup2 在 OS 层临时重定向到临时文件。
    noconsole 打包模式下 fd 2 可能无效（os.dup 抛 OSError），此时不捕获，
    输出文本返回空串。

    Returns:
        (识别器或 None, 捕获的 stderr 文本)
    """
    try:
        saved_fd = os.dup(2)
    except (OSError, AttributeError):
        # fd 2 不可用（如 noconsole 打包）：直接创建，放弃捕获
        try:
            return KaldiRecognizer(model, _SAMPLE_RATE, grammar), ""
        except Exception:
            return None, ""
    tmp = tempfile.TemporaryFile(mode="w+b")
    recognizer: Optional[KaldiRecognizer] = None
    output = ""
    try:
        os.dup2(tmp.fileno(), 2)
        try:
            recognizer = KaldiRecognizer(model, _SAMPLE_RATE, grammar)
        finally:
            # 无论创建是否成功都立即恢复 stderr
            os.dup2(saved_fd, 2)
        tmp.seek(0)
        output = tmp.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    finally:
        tmp.close()
        os.close(saved_fd)
    return recognizer, output


def _resolve_model_dir() -> Optional[str]:
    """定位 Vosk 模型目录，返回第一个存在的路径。

    查找顺序：
    1. 打包模式：exe 同级 models/ 目录（用户可自行替换模型，无需重新打包）
    2. 打包模式：PyInstaller 解包临时目录 _MEIPASS/models/（--add-data 打进包的模型）
    3. 开发模式：项目内 resources/models/
    """
    candidates: List[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "models", _MODEL_DIR_NAME))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "models", _MODEL_DIR_NAME))
    # 开发模式：从模块位置向上推项目根（src/voice_input/ -> 项目根）
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    candidates.append(os.path.join(project_root, "resources", "models", _MODEL_DIR_NAME))
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


class WakeWordDetector:
    """Vosk 离线唤醒词检测器（常驻后台线程）。

    生命周期：start() 启动 → 录音期间 pause() / 结束后 resume() → stop() 停止。
    pause/resume 通过关闭/重开输入流实现，确保正式录音独占麦克风时无设备冲突。
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        wake_word: str = WAKE_WORD,
        stop_word: str = STOP_WORD,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        """初始化唤醒检测器。

        Args:
            on_wake: 唤醒命中回调（在检测工作线程触发，无参数；
                     调用方需自行调度到主线程）
            wake_word: 唤醒词（2~6 个常用汉字，可后续 update_wake_word 热更新）
            stop_word: 结束词（录音中说出自动停止；空串表示不启用）
            on_stop: 结束词命中回调（在检测工作线程触发，
                     调用方需自行调度到主线程）
        """
        # 唤醒命中回调
        self.on_wake = on_wake
        # 结束词命中回调
        self.on_stop = on_stop
        # 模型是否加载成功（决定 start 是否可用、面板是否显示唤醒提示）
        self.available = False
        # 当前唤醒词（规范化后的匹配键，同时用于 UI 显示）
        self._wake_word = _normalize_word(wake_word) or WAKE_WORD
        # 唤醒词语法（逐字分词短语 + [unk]，降低误唤醒）
        self._grammar = _make_grammar(self._wake_word)
        # 当前结束词（空串表示不启用语音结束词）
        self._stop_word = _normalize_word(stop_word)
        # 音频块队列（sounddevice 回调线程 -> 识别工作线程）
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=32)
        # 停止标志（控制工作线程退出）
        self._running = False
        # 工作线程引用
        self._thread: Optional[threading.Thread] = None
        # sounddevice 输入流（RawInputStream，int16）
        self._stream = None
        # Vosk 模型与识别器
        self._model: Optional["Model"] = None
        self._recognizer: Optional["KaldiRecognizer"] = None
        # 结束词识别器（模型加载后按需创建；与唤醒识别器共用模型）
        self._stop_recognizer: Optional["KaldiRecognizer"] = None
        # 结束词监听模式：True 表示正在录音，音频来自录音器分接（feed_audio）
        self._stop_listening = False
        # 上次唤醒命中时间（冷却去抖用）
        self._last_hit_time = 0.0
        # 上次结束词命中时间（去抖用）
        self._last_stop_time = 0.0
        # VAD 滞后计数：>0 表示仍在语音结束后的滞后窗口内，继续送音频
        self._hangover = 0

    # ===== 对外属性 =====

    @property
    def wake_word(self) -> str:
        """当前唤醒词。"""
        return self._wake_word

    @property
    def stop_word(self) -> str:
        """当前结束词（空串表示未启用）。"""
        return self._stop_word

    @property
    def is_running(self) -> bool:
        """返回检测线程是否正在运行。"""
        return self._running

    # ===== 生命周期 =====

    def start(self) -> bool:
        """加载模型并启动后台检测，返回是否成功。

        模型缺失、vosk/sounddevice 不可用时返回 False（不抛异常，
        由调用方决定是否提示用户）。
        """
        if self._running:
            return True
        if not _VOSK_AVAILABLE:
            DiagLog.shared().write(f"[Wake] vosk 不可用: {_VOSK_IMPORT_ERROR}")
            return False
        if not _SD_AVAILABLE:
            DiagLog.shared().write(f"[Wake] sounddevice 不可用: {_SD_IMPORT_ERROR}")
            return False
        if not self._load_model():
            DiagLog.shared().write(
                f"[Wake] 未找到 Vosk 模型 {_MODEL_DIR_NAME}，语音唤醒不可用"
            )
            return False
        # 启动识别工作线程
        self._running = True
        self._thread = threading.Thread(target=self._detect_loop, name="wake-word", daemon=True)
        self._thread.start()
        # 打开监听输入流
        if not self._open_stream():
            self.stop()
            return False
        DiagLog.shared().write(f"[Wake] 语音唤醒已启动（说「{self._wake_word}」唤醒）")
        return True

    def stop(self) -> None:
        """停止检测：关流、结束工作线程（保留已加载模型以便快速重启）。"""
        self._running = False
        self._stop_listening = False
        # 发送结束哨兵，解除工作线程的队列阻塞
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close_stream()
        DiagLog.shared().write("[Wake] 语音唤醒已停止")

    def pause(self) -> None:
        """暂停检测：关闭输入流，把麦克风完全让给正式录音。"""
        self._close_stream()
        self._drain_queue()

    def resume(self) -> None:
        """恢复检测：重置解码状态并重新打开输入流（模型未加载时静默跳过）。"""
        if not self.available:
            return
        self._drain_queue()
        # 重置 Vosk 内部解码状态与 VAD 滞后计数：
        # 录音结束回到空闲时，残留的解码状态可能把环境噪声拼成唤醒词（误唤醒）
        if self._recognizer is not None:
            try:
                self._recognizer.Reset()
            except Exception:
                pass
        self._hangover = 0
        self._open_stream()

    def update_wake_word(self, word: str) -> None:
        """运行时热更新唤醒词：重建语法识别器，下一个音频块即生效。

        模型未加载时只记录新词，待 start() 时按新词构建语法。

        Args:
            word: 新唤醒词（2~6 个常用汉字）
        """
        word = _normalize_word(word)
        if not word or word == self._wake_word:
            return
        self._wake_word = word
        self._grammar = _make_grammar(word)
        # 丢弃积压音频与滞后状态，避免旧上下文误触发
        self._drain_queue()
        self._hangover = 0
        # 模型已加载则立即重建识别器（检测线程每次读取 _recognizer 属性，
        # 原子替换引用即可安全生效，无需加锁）
        if self._model is not None:
            try:
                self._recognizer = KaldiRecognizer(self._model, _SAMPLE_RATE, self._grammar)
                DiagLog.shared().write(f"[Wake] 唤醒词已更新: {word}")
            except Exception as e:
                DiagLog.shared().write(f"[Wake] 重建识别器失败: {e}")

    def validate_wake_word(self, word: str) -> List[str]:
        """校验唤醒词：返回不在 Vosk 模型词库中的字（去重、保持顺序）。

        词库外的字会被 Vosk 直接忽略，导致唤醒词永远无法命中，
        因此保存前需校验。校验原理：用「逐字语法」创建临时识别器，
        Vosk 会对词库外的字输出警告到进程 stderr（fd 2），
        在 OS 层重定向 fd 2 捕获输出后解析警告文本。

        模型未加载时临时加载一个模型实例（约几百毫秒），校验后释放。

        Returns:
            缺失字列表；无法校验（依赖/模型缺失或 fd 2 不可用）时返回空列表，
            调用方应将空列表视为「校验通过/跳过校验」
        """
        if not _VOSK_AVAILABLE:
            return []
        chars = list(_normalize_word(word))
        if not chars:
            return []
        # 复用已加载的模型；未加载则临时创建，校验后释放
        temp_model = None
        model = self._model
        if model is None:
            model_dir = _resolve_model_dir()
            if model_dir is None:
                return []
            try:
                SetLogLevel(-1)
                temp_model = Model(model_dir)
            except Exception as e:
                DiagLog.shared().write(f"[Wake] 校验唤醒词时加载模型失败: {e}")
                return []
            model = temp_model
        # 用逐字语法创建临时识别器，捕获词库警告
        grammar = json.dumps([" ".join(chars), "[unk]"], ensure_ascii=False)
        _, output = _create_recognizer_capturing(model, grammar)
        # 释放临时模型，避免常驻内存翻倍
        if temp_model is not None:
            del temp_model
            model = None
        # 解析缺失字，去重且保持出现顺序
        missing: List[str] = []
        for token in _MISSING_TOKEN_RE.findall(output):
            if token not in missing:
                missing.append(token)
        return missing

    def update_stop_word(self, word: str) -> None:
        """运行时热更新结束词：重建结束词识别器。

        Args:
            word: 新结束词（2~6 个常用汉字；空串表示禁用语音结束词）
        """
        word = _normalize_word(word)
        if word == self._stop_word:
            return
        self._stop_word = word
        self._stop_listening = False
        if self._model is None:
            return  # 模型未加载：仅记录，待 start() 时构建
        if not word:
            self._stop_recognizer = None
            DiagLog.shared().write("[Wake] 语音结束词已禁用")
            return
        try:
            self._stop_recognizer = KaldiRecognizer(
                self._model, _SAMPLE_RATE, _make_grammar(word)
            )
            DiagLog.shared().write(f"[Wake] 结束词已更新: {word}")
        except Exception as e:
            DiagLog.shared().write(f"[Wake] 重建结束词识别器失败: {e}")
            self._stop_recognizer = None

    def start_stop_listen(self) -> None:
        """录音开始：进入结束词监听模式（音频来自录音器分接 feed_audio）。"""
        if not self._stop_word or self._stop_recognizer is None:
            return
        self._drain_queue()
        if self._stop_recognizer is not None:
            try:
                self._stop_recognizer.Reset()
            except Exception:
                pass
        self._stop_listening = True

    def stop_stop_listen(self) -> None:
        """录音结束：退出结束词监听模式并清空分接积压音频。

        必须清空队列：残留的分接音频随后会被当作唤醒检测输入，
        可能被语法模式强行匹配成唤醒词造成误唤醒。
        """
        if self._stop_listening:
            self._stop_listening = False
            self._drain_queue()

    def feed_audio(self, chunk: bytes) -> None:
        """录音器回调线程调用：分接音频块送结束词检测。

        仅在结束词监听模式下入队，由检测工作线程消费识别；
        不在音频回调线程做识别计算，避免录音卡顿。

        Args:
            chunk: 本音频块的 Int16 LE PCM bytes（来自录音器）
        """
        if not self._stop_listening or not self._running:
            return
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # 队满丢弃：结束词检测可容忍少量丢帧，不阻塞录音回调
            pass

    # ===== 内部实现 =====

    def _load_model(self) -> bool:
        """加载 Vosk 模型并创建关键词语法识别器，返回是否成功。"""
        # 已加载则直接复用（stop 后再次 start 的快速路径）
        if self._model is not None and self._recognizer is not None:
            self.available = True
            return True
        model_dir = _resolve_model_dir()
        if model_dir is None:
            return False
        try:
            # 关闭 Vosk 的 stdout 日志，避免污染 noconsole 的 GUI 进程
            SetLogLevel(-1)
            self._model = Model(model_dir)
            # 语法模式：识别结果只可能是候选短语或 [unk]，天然过滤闲聊误唤醒
            self._recognizer = KaldiRecognizer(self._model, _SAMPLE_RATE, self._grammar)
            # 结束词识别器与唤醒识别器共用同一模型（仅语法不同）
            if self._stop_word:
                self._stop_recognizer = KaldiRecognizer(
                    self._model, _SAMPLE_RATE, _make_grammar(self._stop_word)
                )
            self.available = True
            DiagLog.shared().write(f"[Wake] 模型加载成功: {model_dir}")
            return True
        except Exception as e:
            DiagLog.shared().write(f"[Wake] 模型加载失败: {e}")
            self._model = None
            self._recognizer = None
            self.available = False
            return False

    def _open_stream(self) -> bool:
        """打开常驻监听输入流（幂等），返回是否成功。"""
        if self._stream is not None:
            return True
        if not _SD_AVAILABLE:
            return False
        try:
            # RawInputStream：回调拿到原始 int16 bytes，可直接送 Vosk
            self._stream = sd.RawInputStream(
                samplerate=_SAMPLE_RATE,
                channels=_CHANNELS,
                dtype="int16",
                blocksize=_BLOCK_FRAMES,
                callback=self._on_audio_block,
            )
            self._stream.start()
            return True
        except Exception as e:
            DiagLog.shared().write(f"[Wake] 监听输入流打开失败: {e}")
            self._stream = None
            return False

    def _close_stream(self) -> None:
        """关闭监听输入流（幂等，安全吞掉异常）。"""
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _detect_loop(self) -> None:
        """识别工作线程主循环：从队列取音频块送 Vosk 检测唤醒词。"""
        while self._running:
            try:
                chunk = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            # None 为停止哨兵
            if chunk is None:
                break
            self._handle_chunk(chunk)

    def _on_audio_block(self, indata, frames, time_info, status) -> None:
        """sounddevice 回调线程：按 VAD 门限过滤后音频块入队。

        RMS 低于门限且不在滞后窗口内的块视为环境噪声直接丢弃，
        避免语法模式把噪声强行匹配成唤醒词（误唤醒）。
        """
        if not self._running:
            return
        # VAD 能量门限 + 滞后窗口
        rms = self._block_rms(indata)
        if rms >= _VAD_RMS_GATE:
            # 检测到声音：刷新滞后窗口
            self._hangover = _VAD_HANGOVER_BLOCKS
        elif self._hangover > 0:
            # 语音刚结束：滞后窗口内继续送音频，让 Vosk 产出 final 结果
            self._hangover -= 1
        else:
            # 静音/噪声：不送识别
            return
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            # 识别线程处理不过来：丢弃最旧块，避免延迟累积
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(bytes(indata))
            except Exception:
                pass

    @staticmethod
    def _block_rms(indata) -> float:
        """计算音频块 RMS（int16 缓冲归一化到 -1~1 后计算）。

        Args:
            indata: sounddevice RawInputStream 回调的原始 int16 缓冲

        Returns:
            块 RMS（0~1），空块返回 0.0
        """
        samples = np.frombuffer(indata, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples * samples)))

    def _handle_chunk(self, chunk: bytes) -> None:
        """将一块音频送 Vosk 识别：按当前模式检测结束词或唤醒词。

        结束词模式（录音期间）：音频来自录音器分接，用结束词识别器检测；
        唤醒模式（空闲期间）：音频来自常驻监听流，用唤醒识别器检测。
        两者共用同一个工作线程与队列。
        """
        if self._stop_listening and self._stop_recognizer is not None:
            self._detect_with(self._stop_recognizer, chunk, self._is_stop_text, self._notify_stop)
            return
        if self._recognizer is None:
            return
        self._detect_with(self._recognizer, chunk, self._is_wake_text, self._notify_wake)

    def _detect_with(self, recognizer, chunk: bytes, matcher, notify) -> None:
        """通用检测：将一块音频送指定识别器，命中时触发回调。

        Args:
            recognizer: Vosk KaldiRecognizer（唤醒词或结束词）
            chunk: Int16 LE PCM 音频块
            matcher: 文本命中判断函数（识别文本 -> bool）
            notify: 命中回调（无参数）
        """
        try:
            if recognizer.AcceptWaveform(chunk):
                # 一段语音结束（final 结果），命中概率最高的时机
                text = self._extract_text(recognizer.Result())
            else:
                # 中间结果（partial），降低响应延迟
                text = self._extract_text(recognizer.PartialResult())
            if text and matcher(text):
                notify()
        except Exception as e:
            DiagLog.shared().write(f"[Wake] 识别异常: {e}")

    @staticmethod
    def _extract_text(result_json: str) -> str:
        """从 Vosk 结果 JSON 中提取识别文本。

        Args:
            result_json: Vosk 的 Result()/PartialResult() 返回的 JSON 字符串

        Returns:
            识别文本（可能为空串）
        """
        try:
            data = json.loads(result_json)
            return str(data.get("text", "") or data.get("partial", ""))
        except Exception:
            return ""

    def _is_wake_text(self, text: str) -> bool:
        """判断识别文本是否命中当前唤醒词（去空格与 [unk] 后精确比对）。"""
        return _normalize_word(text) == self._wake_word

    def _is_stop_text(self, text: str) -> bool:
        """判断识别文本是否命中当前结束词（去空格与 [unk] 后精确比对）。"""
        return _normalize_word(text) == self._stop_word

    def _notify_stop(self) -> None:
        """命中结束词：去抖后清空积压音频并触发 on_stop 回调。

        命中后立即退出监听模式并清空队列：残留分接音频
        不能再进入识别，避免重复触发或误触发唤醒。
        """
        now = time.time()
        if now - self._last_stop_time < _STOP_COOLDOWN_S:
            return
        self._last_stop_time = now
        self._stop_listening = False
        self._drain_queue()
        DiagLog.shared().write(f"[Wake] 命中结束词「{self._stop_word}」")
        if self.on_stop:
            try:
                self.on_stop()
            except Exception as e:
                DiagLog.shared().write(f"[Wake] on_stop 异常: {e}")

    def _notify_wake(self) -> None:
        """命中唤醒词：冷却去抖后清空积压音频、播放提示音并触发回调。"""
        now = time.time()
        # 冷却期内忽略重复命中（partial + final 可能连续命中同一句话）
        if now - self._last_hit_time < _WAKE_COOLDOWN_S:
            return
        self._last_hit_time = now
        # 清空积压音频，避免唤醒词后的残留语音被误判为再次唤醒
        self._drain_queue()
        DiagLog.shared().write(f"[Wake] 命中唤醒词「{self._wake_word}」")
        self._play_wake_sound()
        if self.on_wake:
            try:
                self.on_wake()
            except Exception as e:
                DiagLog.shared().write(f"[Wake] on_wake 异常: {e}")

    @staticmethod
    def _play_wake_sound() -> None:
        """播放唤醒提示音（两个上行短音「叮咚」，仅 Windows；失败静默忽略）。"""
        try:
            import winsound
            winsound.Beep(988, 90)   # 短音 B5
            winsound.Beep(1319, 130)  # 长音 E6
        except Exception:
            pass

    def _drain_queue(self) -> None:
        """清空音频队列，丢弃所有积压数据。"""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
