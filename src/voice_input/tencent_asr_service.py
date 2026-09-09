"""腾讯云实时语音识别（API 2.0 WebSocket）客户端。

基于腾讯云语音识别「实时语音识别（WebSocket）」接口实现：
- 端点：wss://asr.cloud.tencent.com/asr/v2/<appid>?{请求参数}
- 鉴权：HMAC-SHA1 签名（signature = base64(HMAC-SHA1(secret_key, 签名原文))）
  签名原文 = "asr.cloud.tencent.com/asr/v2/<appid>?<按字典序排序的参数串>"
  其中参数值用原始字符串拼接，最终 URL 再对 signature 做 url 编码
- 音频格式：16kHz / 16bit / 单声道 PCM（voice_format=1）
- 数据发送：建议接近 1:1 实时率，每 40ms 发送 1280 字节（16k 采样率）

握手阶段连接建立后无需发送启动帧，直接上传二进制音频即可。
服务端返回 JSON 文本消息：
- code=0 正常；非 0 表示错误
- result.slice_type：0=一句话开始 / 1=识别中(非稳态) / 2=一句话结束(稳态)
- result.voice_text_str：当前一句话的文本结果
- final=1：整个音频流识别全部结束

多句拼接：slice_type=2 的稳态结果按返回顺序累加，得到完整文本。

继承 ASRBase 抽象基类，使用 websockets 异步实现，运行在独立线程的事件循环中。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

from .asr_base import ASRBase, ASRResult
from .diag_log import DiagLog


# WebSocket 端点（实时语音识别 API 2.0）
_HOST = "asr.cloud.tencent.com"
_PATH = f"/asr/v2/"
# 每次发送的音频块大小（字节），40ms × 16000Hz × 2bytes = 1280
_CHUNK_SIZE = 1280
# 引擎模型类型：16k_zh 为中文通用引擎
_ENGINE_MODEL_TYPE = "16k_zh"
# 语音编码方式：1 = PCM
_VOICE_FORMAT = 1
# 签名有效期（秒），expired - timestamp 需小于 90 天
_SIGN_TTL = 600


class TencentASRService(ASRBase):
    """腾讯云实时语音识别 ASR WebSocket 客户端。

    继承 ASRBase，实现腾讯云实时语音识别 API 2.0 的 WebSocket 协议。
    构造函数接收凭证字典，适配工厂模式。
    """

    def __init__(self, credentials: Dict[str, str]) -> None:
        """初始化腾讯云 ASR 服务。

        Args:
            credentials: 凭证字典，包含:
                - app_id: AppID（账号 ID，用于 URL 路径，必填）
                - secret_id: Secret ID（用于签名，必填）
                - secret_key: Secret Key（用于签名，必填）
        """
        # 凭证
        self._app_id = credentials.get("app_id", "")
        self._secret_id = credentials.get("secret_id", "")
        self._secret_key = credentials.get("secret_key", "")
        # 验证必填字段
        if not self._app_id or not self._secret_id or not self._secret_key:
            raise ValueError("腾讯云 ASR 需要 app_id、secret_id、secret_key 凭证")
        # 回调（在主线程被调度）
        self.on_partial: Optional[Callable[[str], None]] = None
        self.on_complete: Optional[Callable[[Optional[ASRResult]], None]] = None
        # 异步事件循环（独立线程）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        # WebSocket 连接
        self._ws: Optional[Any] = None  # websockets.WebSocketClientProtocol
        # 音频数据队列：录音线程入队，事件循环内 _sender 取出发送
        self._audio_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._sender_task: Optional[asyncio.Task] = None
        # 状态标记
        self._send_finished = False
        self._closed = False
        # 已识别的稳态文本（slice_type=2 的累积，用于多句拼接）
        self._final_text = ""
        DiagLog.shared().write("[Tencent] 腾讯云 ASR 初始化成功（实时语音识别 API 2.0）")

    # ===== 对外 API（在主线程调用，转发到事件循环线程） =====

    def start(self) -> None:
        """启动 ASR 会话：连接 WebSocket 并进入接收循环。

        重置状态，启动独立线程的事件循环，提交连接任务。
        连接建立后即可接收音频帧（无需发送启动帧）。
        """
        # 重置状态
        self._send_finished = False
        self._closed = False
        self._final_text = ""
        self._audio_queue = queue.Queue()
        self._sender_task = None

        # 启动事件循环线程
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        # 提交连接任务
        asyncio.run_coroutine_threadsafe(self._connect_and_start(), self._loop)

    def send_audio(self, pcm: bytes) -> None:
        """投递一帧 PCM 音频（线程安全）。

        只入队不直接发送：连接建立后由事件循环内的 _sender 统一取帧发送。

        Args:
            pcm: 16kHz / 16bit / mono 的 PCM 字节数据
        """
        if self._send_finished or self._closed or self._loop is None:
            return
        if not pcm:
            return
        self._audio_queue.put(pcm)

    def finish(self) -> None:
        """通知服务端音频流结束。

        入队 sentinel，_sender 收到后先发完所有音频，再关闭连接让服务端收尾。
        """
        if self._send_finished or self._closed or self._loop is None:
            return
        self._send_finished = True
        self._audio_queue.put(None)

    def cancel(self) -> None:
        """取消会话，关闭 WebSocket。"""
        self._send_finished = True
        self._closed = True
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)

    # ===== 内部实现 =====

    def _run_loop(self) -> None:
        """事件循环线程主函数，运行至 loop 被停止。"""
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def _build_signature(self, params: Dict[str, str]) -> str:
        """生成腾讯云实时识别签名。

        签名算法：
        1. 对除 signature 外的所有参数按字典序排序
        2. 拼接签名原文："{host}{path}{appid}?{排序后的参数串}"
           其中参数串为 key=value 用 & 连接，value 用原始字符串（不 url 编码）
        3. 用 secret_key 对签名原文做 HMAC-SHA1，再 base64 编码得到 signature
        4. 最终 URL 中对 signature 做 url 编码

        Args:
            params: 除 signature 外的请求参数字典

        Returns:
            base64 编码后的签名字符串（未做 url 编码）
        """
        # 按字典序排序参数
        sorted_items = sorted(params.items())
        # 拼接查询串（value 用原始字符串，与官方文档示例一致）
        query = "&".join(f"{k}={v}" for k, v in sorted_items)
        # 签名原文：不含协议头，不含 signature
        sign_str = f"{_HOST}{_PATH}{self._app_id}?{query}"
        # HMAC-SHA1 加密后 base64 编码
        hmac_result = hmac.new(
            self._secret_key.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(hmac_result).decode("utf-8")

    def _build_ws_url(self) -> str:
        """构造带签名参数的 WebSocket URL。

        URL 格式：wss://asr.cloud.tencent.com/asr/v2/<appid>?<参数>&signature=<url编码签名>

        Returns:
            完整的 WebSocket URL
        """
        now = int(time.time())
        # 请求参数（除 signature 外）
        params: Dict[str, str] = {
            "engine_model_type": _ENGINE_MODEL_TYPE,
            "expired": str(now + _SIGN_TTL),
            "needvad": "1",
            "nonce": str(now),
            "secretid": self._secret_id,
            "timestamp": str(now),
            "voice_format": str(_VOICE_FORMAT),
            "voice_id": uuid.uuid4().hex,
        }
        signature = self._build_signature(params)
        # 拼接最终 URL：参数 value 做 url 编码（安全起见），signature 也做 url 编码
        query = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(params.items())
        )
        url = (
            f"wss://{_HOST}{_PATH}{quote(self._app_id, safe='')}?{query}"
            f"&signature={quote(signature, safe='')}"
        )
        return url

    async def _connect_and_start(self) -> None:
        """连接 WebSocket 并进入接收循环。"""
        try:
            import websockets
        except ImportError as e:
            DiagLog.shared().write(f"[Tencent] websockets 未安装: {e}")
            self._fail("websockets not installed")
            return

        ws_url = self._build_ws_url()
        # 调试日志：脱敏打印凭证前缀
        appid_preview = self._app_id[:4] + "***" if len(self._app_id) > 4 else "***"
        sid_preview = self._secret_id[:6] + "***" if len(self._secret_id) > 6 else "***"
        DiagLog.shared().write(
            f"[Tencent] 准备连接 WebSocket: appid={appid_preview}(len={len(self._app_id)}) "
            f"secretid={sid_preview}(len={len(self._secret_id)})"
        )

        try:
            self._ws = await websockets.connect(ws_url, ping_interval=None)
            DiagLog.shared().write("[Tencent] WebSocket 已连接")
        except Exception as e:
            DiagLog.shared().write(f"[Tencent] WebSocket 连接失败: {e}")
            self._fail(f"connect failed: {e}")
            return

        # 连接已就绪：启动音频发送协程
        self._sender_task = asyncio.create_task(self._sender())
        # 启动接收循环
        await self._receive_loop()

    async def _receive_loop(self) -> None:
        """持续接收服务端消息，解析并触发回调。"""
        while not self._closed and self._ws is not None:
            try:
                message = await self._ws.recv()
            except Exception as e:
                if not self._closed:
                    DiagLog.shared().write(f"[Tencent] receive error: {e}")
                    # 接收循环结束，若已有稳态文本则作为最终结果返回
                    self._finish_with_final_text()
                return

            # 腾讯云返回的是文本格式的 JSON
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            if not isinstance(message, str):
                continue

            self._handle_message(message)

    def _handle_message(self, message: str) -> None:
        """解析服务端 JSON 消息，触发 partial / complete 回调。

        消息格式：
        - {"code": 0, "result": {...}, "final": 0/1, ...}：识别结果
        - {"code": <非0>, "message": "..."}：错误

        result.slice_type：0=开始 / 1=识别中(非稳态) / 2=一句话结束(稳态)
        result.voice_text_str：当前一句话文本
        final=1：整个音频流识别全部结束
        """
        try:
            obj = json.loads(message)
        except json.JSONDecodeError:
            DiagLog.shared().write(f"[Tencent] JSON 解析失败: {message[:200]}")
            return

        code = obj.get("code", 0)
        if code != 0:
            # 错误
            msg_text = obj.get("message", "")
            DiagLog.shared().write(f"[Tencent] ❌ 错误: code={code} message={msg_text}")
            # 给出常见错误诊断提示
            hint = self._error_hint(code, msg_text)
            if hint:
                DiagLog.shared().write(f"[Tencent] 诊断提示: {hint}")
            self._fail(f"server error: code={code} {msg_text}")
            return

        # final=1 表示整个音频流识别结束
        final_flag = obj.get("final", 0)
        result = obj.get("result")
        if isinstance(result, dict):
            slice_type = result.get("slice_type", 0)
            text = str(result.get("voice_text_str", ""))
            # slice_type=2 为稳态结果（一句话结束），累积到最终文本
            if slice_type == 2 and text:
                self._final_text += text
                DiagLog.shared().write(
                    f"[Tencent] 稳态结果(slice=2): {text[:80]} | 累积: {self._final_text[:80]}"
                )
                self._schedule_partial(self._final_text)
            elif text:
                # slice_type=0/1 为非稳态结果，显示「累积稳态 + 当前句」
                display = self._final_text + text
                DiagLog.shared().write(
                    f"[Tencent] 非稳态结果(slice={slice_type}): {text[:80]}"
                )
                self._schedule_partial(display)

        # final=1：整个音频流识别结束，触发完成回调
        if final_flag == 1:
            DiagLog.shared().write(f"[Tencent] 音频流识别结束: final=1")
            self._finish_with_final_text()

    def _error_hint(self, code: int, message: str) -> str:
        """根据错误码给出常见问题诊断提示。

        Args:
            code: 服务端错误码
            message: 错误描述

        Returns:
            诊断提示文本，无则返回空串
        """
        # 4002：AppID 校验失败，单独给出精准提示
        if code == 4002:
            return (
                "腾讯云 4002 鉴权失败（AppID 与实际访问的 AppID 不一致）常见原因：\n"
                "1. AppID 填错：AppID 是 10 位纯数字（如 125xxxxxxxx），\n"
                "   不要填成 UIN（12 位用户 ID，1 开头）或 SecretID（AKID 开头）\n"
                "2. AppID 与 SecretID / SecretKey 不属于同一腾讯云账号\n"
                "3. AppID 在 https://console.cloud.tencent.com/developer 「账号信息」查看\n"
                "   SecretID/SecretKey 在 https://console.cloud.tencent.com/cam/capi 获取\n"
                "4. 确认已开通「实时语音识别」服务"
            )
        # 其他签名/鉴权类错误
        if code in (4001, 4003):
            return (
                "腾讯云签名/鉴权类错误：\n"
                "1. 检查 AppID / SecretID / SecretKey 是否正确\n"
                "2. SecretID/SecretKey 需在 https://console.cloud.tencent.com/cam/capi 获取\n"
                "3. 确认已开通实时语音识别服务"
            )
        # 4008：客户端超过 15 秒未发送音频数据（会话未正常关闭）
        if code == 4008:
            return (
                "腾讯云 4008：客户端超过 15 秒未发送音频数据。\n"
                "通常因音频发送完毕后未及时关闭连接导致，已在新版本修复。\n"
                "若仍出现，请检查录音是否中途断开或音频流是否提前结束。"
            )
        # 引擎参数错误
        if code in (4004, 4005):
            return "引擎模型参数错误：检查 engine_model_type 是否与账号开通的引擎匹配。"
        return ""

    def _finish_with_final_text(self) -> None:
        """以已累积的稳态文本作为最终结果触发完成回调。"""
        if self._closed:
            return
        self._closed = True
        if self._final_text:
            DiagLog.shared().write(f"[Tencent] 最终文本: {self._final_text[:80]}")
            self._schedule_complete(ASRResult(text=self._final_text, is_final=True))
        else:
            DiagLog.shared().write("[Tencent] 无稳态文本，返回失败")
            self._schedule_complete(None)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)

    async def _sender(self) -> None:
        """事件循环内：从队列取音频帧并发送；收到 sentinel 后发完并关闭。

        音频按 _CHUNK_SIZE 分块发送，接近 1:1 实时率。
        """
        loop = asyncio.get_running_loop()
        buffer = bytearray()

        while not self._closed:
            # 阻塞取帧放到线程池，避免卡住事件循环
            pcm = await loop.run_in_executor(None, self._audio_queue.get)
            if pcm is None:
                # sentinel：发完缓冲区剩余音频，结束发送
                break
            buffer.extend(pcm)
            # 按 1280 字节分块发送
            while len(buffer) >= _CHUNK_SIZE:
                chunk = bytes(buffer[:_CHUNK_SIZE])
                del buffer[:_CHUNK_SIZE]
                await self._send_chunk(chunk)

        # 发送缓冲区剩余的不足 1280 字节
        if buffer and not self._closed:
            await self._send_chunk(bytes(buffer))

        # 腾讯云实时识别无显式结束帧：发完音频后等待服务端返回 final=1，
        # 由接收循环处理；若 2 秒内未收到 final=1，则主动关闭连接完成会话。
        # 不主动关闭会导致服务端等待 15 秒后报 4008（未发送音频数据）。
        if not self._closed:
            DiagLog.shared().write("[Tencent] 音频发送完毕，等待服务端返回 final=1（最多 2 秒）")
            try:
                await asyncio.sleep(2.0)
            except Exception:
                pass
            # 期间若已收到 final=1 并完成，则直接退出
            if not self._closed:
                DiagLog.shared().write("[Tencent] 未收到 final=1，主动关闭连接完成会话")
                self._finish_with_final_text()

    async def _send_chunk(self, chunk: bytes) -> None:
        """发送一个音频块到 WebSocket。

        Args:
            chunk: PCM 音频字节块
        """
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(chunk)
        except Exception as e:
            DiagLog.shared().write(f"[Tencent] send error: {e}")
            self._fail(f"send failed: {e}")

    async def _close_ws(self) -> None:
        """关闭 WebSocket 连接。"""
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    def _schedule_partial(self, text: str) -> None:
        """调度 partial 文本回调（在事件循环线程执行，注意 UI 线程安全）。"""
        if self.on_partial:
            try:
                self.on_partial(text)
            except Exception as e:
                DiagLog.shared().write(f"[Tencent] on_partial 异常: {e}")

    def _schedule_complete(self, result: Optional[ASRResult]) -> None:
        """调度完成回调。"""
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                DiagLog.shared().write(f"[Tencent] on_complete 异常: {e}")

    def _fail(self, message: str) -> None:
        """失败处理：标记状态、调度失败回调、关闭连接。"""
        self._send_finished = True
        self._closed = True
        DiagLog.shared().write(f"[Tencent] ❌ fail: {message}")
        self._schedule_complete(None)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
