"""AsrClient：语音识别客户端（动作 A6）。

统一接口是为了不被任何一家厂商绑架：换服务商只需换一个实现类，
Orchestrator 与其余零件一行都不用改。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import requests

from .constants import ASR_MAX_AUDIO_BYTES, ASR_TIMEOUT_S
from .models import AudioClip, Transcript

logger = logging.getLogger("voice_input.asr")


class AsrClient(ABC):
    """识别客户端抽象。"""

    name: str = "abstract"

    @abstractmethod
    def transcribe(self, clip: AudioClip) -> Transcript:
        """把音频片段转成文字。失败时返回带 error 的 Transcript，不抛异常。"""


class MockAsrClient(AsrClient):
    """假识别：ASR 服务商未定时的占位实现，用于跑通整条链路。"""

    name = "mock"

    def __init__(self, replies: Optional[list[str]] = None, delay_s: float = 0.3) -> None:
        self.replies = replies or [
            "这是一段模拟识别结果，用于验证链路是否通畅。",
            "语音输入工具的最小链路已跑通。",
        ]
        self.delay_s = delay_s
        self._i = 0

    def transcribe(self, clip: AudioClip) -> Transcript:
        time.sleep(self.delay_s)
        text = self.replies[self._i % len(self.replies)]
        self._i += 1
        logger.info("Mock 识别返回（音频 %d ms）", clip.duration_ms)
        return Transcript(text=text, confidence=1.0, duration_ms=clip.duration_ms)


class OpenAILikeAsrClient(AsrClient):
    """OpenAI 兼容的 /audio/transcriptions 接口（Whisper / SenseVoice 等均适用）。"""

    name = "openai-like"

    def __init__(
        self,
        endpoint: str,
        api_key: Optional[str] = None,
        model: str = "whisper-1",
        timeout_s: float = ASR_TIMEOUT_S,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def transcribe(self, clip: AudioClip) -> Transcript:
        if not self.endpoint:
            return Transcript(error="未配置 ASR 端点")
        url = self.endpoint if self.endpoint.endswith("/audio/transcriptions") else (
            self.endpoint + "/audio/transcriptions"
        )
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            resp = requests.post(
                url,
                files={"file": ("audio.wav", clip.to_wav_bytes(), "audio/wav")},
                data={"model": self.model},
                headers=headers,
                timeout=(5.0, self.timeout_s),
            )
            resp.raise_for_status()
            text = (resp.json() or {}).get("text", "").strip()
            return Transcript(text=text, duration_ms=clip.duration_ms, confidence=1.0)
        except requests.Timeout:
            return Transcript(error="ASR 请求超时")
        except Exception as exc:
            return Transcript(error=f"ASR 请求失败：{exc}")


class TencentAsrClient(AsrClient):
    """腾讯云"一句话识别"：SecretId/SecretKey + 官方 SDK（SDK 负责 TC3 签名）。

    限制：音频 ≤60 秒、≤3MB。见 constants.ASR_MAX_AUDIO_BYTES 与 MAX_RECORD_MS。
    """

    name = "tencent"

    def __init__(
        self,
        secret_id: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: str = "ap-shanghai",
        engine: str = "16k_zh",
        timeout_s: float = ASR_TIMEOUT_S,
    ) -> None:
        self.secret_id = secret_id or ""
        self.secret_key = secret_key or ""
        self.region = region
        self.engine = engine
        self.timeout_s = timeout_s
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from tencentcloud.asr.v20190614 import asr_client  # noqa: PLC0415
        from tencentcloud.common import credential  # noqa: PLC0415
        from tencentcloud.common.profile.client_profile import ClientProfile  # noqa: PLC0415
        from tencentcloud.common.profile.http_profile import HttpProfile  # noqa: PLC0415

        cred = credential.Credential(self.secret_id, self.secret_key)
        http_profile = HttpProfile(endpoint="asr.tencentcloudapi.com")
        client_profile = ClientProfile(httpProfile=http_profile)
        self._client = asr_client.AsrClient(cred, self.region, client_profile)
        return self._client

    def transcribe(self, clip: AudioClip) -> Transcript:
        if not (self.secret_id and self.secret_key):
            return Transcript(error="未配置腾讯云密钥（SecretId / SecretKey）")

        wav = clip.to_wav_bytes()
        if clip.duration_ms > 60_000:
            return Transcript(error=f"音频 {clip.duration_ms} ms 超过一句话识别 60 秒上限")
        if len(wav) > ASR_MAX_AUDIO_BYTES:
            return Transcript(error=f"音频 {len(wav)} 字节超过 3MB 上限")

        try:
            import base64

            from tencentcloud.asr.v20190614 import models  # noqa: PLC0415

            client = self._ensure_client()
            req = models.SentenceRecognitionRequest()
            req.ProjectId = 0
            req.SubServiceType = 2          # 2 = 一句话识别
            req.EngSerViceType = self.engine  # 16k_zh 中文普通话
            req.SourceType = 1              # 1 = 本地上传（base64）
            req.VoiceFormat = "wav"
            req.UsrAudioKey = f"vi-{int(time.time() * 1000)}"
            req.Data = base64.b64encode(wav).decode("utf-8")
            req.DataLen = len(wav)

            resp = client.SentenceRecognition(req)
            text = (getattr(resp, "Result", "") or "").strip()
            return Transcript(text=text, duration_ms=clip.duration_ms, confidence=1.0)
        except Exception as exc:
            msg = str(exc)
            if "AuthFailure" in msg:
                msg = "鉴权失败：密钥错误、被禁用，或子账号缺少 ASR 权限"
            return Transcript(error=f"腾讯云识别失败：{msg}")


def build_asr_client(config) -> AsrClient:
    """工厂：按配置产出具体客户端。密钥统一由 Credential 提供。"""
    from .config import Credential  # noqa: PLC0415  避免循环导入

    provider = getattr(config, "asr_provider", "mock")

    timeout = getattr(config, "asr_timeout_s", ASR_TIMEOUT_S) or ASR_TIMEOUT_S

    if provider == "tencent":
        return TencentAsrClient(
            secret_id=Credential.get("asr_secret_id"),
            secret_key=Credential.get("asr_secret_key"),
            region=getattr(config, "asr_region", "ap-shanghai"),
            engine=getattr(config, "asr_engine", "16k_zh"),
            timeout_s=timeout,
        )
    if provider == "openai":
        return OpenAILikeAsrClient(
            endpoint=config.asr_endpoint,
            api_key=Credential.get("asr_api_key"),
            model=getattr(config, "asr_model", "whisper-1"),
            timeout_s=timeout,
        )
    return MockAsrClient()
