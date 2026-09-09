"""ASR 服务抽象基类模块。

定义统一的语音识别接口，所有 ASR 提供商实现必须继承此类。
新增提供商只需创建新的实现文件，无需修改上层业务代码。

音频格式约定：16kHz / 16bit / mono / PCM little-endian。
"""

from __future__ import annotations

import abc
from typing import Callable, Optional


class ASRResult:
    """ASR 识别结果数据类。

    Attributes:
        text: 识别到的文本内容
        is_final: 是否为最终结果（True 表示识别完成，False 表示 partial 结果）
    """

    def __init__(self, text: str, is_final: bool) -> None:
        self.text = text
        self.is_final = is_final


class ASRBase(abc.ABC):
    """ASR 服务抽象基类。

    所有 ASR 提供商实现必须实现以下接口：
    - start(): 建立连接、发送启动配置
    - send_audio(pcm): 发送 PCM 音频数据（16kHz/16bit/mono）
    - finish(): 通知服务端音频流结束
    - cancel(): 取消会话、关闭连接

    回调由外部设置：
    - on_partial: 实时 partial 文本回调
    - on_complete: 最终结果回调（None 表示失败）
    """

    # 回调类型定义（由外部注入）
    on_partial: Optional[Callable[[str], None]]
    on_complete: Optional[Callable[[Optional[ASRResult]], None]]

    @abc.abstractmethod
    def start(self) -> None:
        """启动 ASR 会话：建立连接并发送启动配置。

        实现需在独立线程中运行事件循环，避免阻塞主线程。
        连接建立后即可接收音频帧。
        """
        ...

    @abc.abstractmethod
    def send_audio(self, pcm: bytes) -> None:
        """发送一帧 PCM 音频数据（线程安全）。

        Args:
            pcm: 16kHz / 16bit / mono 的 PCM 字节数据
        """
        ...

    @abc.abstractmethod
    def finish(self) -> None:
        """通知服务端音频流结束。

        调用后应继续发送缓冲区中剩余的音频，再发送结束标记。
        """
        ...

    @abc.abstractmethod
    def cancel(self) -> None:
        """取消会话并关闭连接。

        用于错误恢复或用户主动取消。
        """
        ...
