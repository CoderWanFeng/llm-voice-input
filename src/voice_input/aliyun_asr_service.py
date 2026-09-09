"""阿里云智能语音交互（NLS）实时语音转写 WebSocket 客户端。

基于阿里云「实时语音识别」WebSocket 协议实现：
- 鉴权：先用 AccessKey ID/Secret 通过 RPC API（CreateToken）换取临时 Token，
  再用 Token 连接 WebSocket 网关
- 网关端点：wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1?token=<token>
- Token 获取：GET https://nls-meta.cn-shanghai.aliyuncs.com/ (Action=CreateToken)，
  使用阿里云 RPC API v1.0 的 HMAC-SHA1 签名

交互流程（指令为 Text JSON 帧，音频为 Binary 帧）：
1. 建立 WebSocket 连接（URL 携带 token）
2. 发送 StartTranscription 指令（header.namespace=SpeechTranscriber）
3. 收到 TranscriptionStarted 事件后开始上传音频
4. 持续发送二进制 PCM 音频帧
5. 发送 StopTranscription 指令通知结束
6. 接收事件直至 TranscriptionCompleted

服务端事件（header.name）：
- TranscriptionStarted：服务端就绪，可发送音频
- SentenceBegin：一句话开始
- TranscriptionResultChanged：中间识别结果（partial），payload.result
- SentenceEnd：一句话结束（稳态结果），payload.result，需按 index 累积
- TranscriptionCompleted：全部识别完成
- TaskFailed：任务失败（header.status 非 20000000）

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
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

from .asr_base import ASRBase, ASRResult
from .diag_log import DiagLog


# WebSocket 网关端点（默认上海地域）
_WS_ENDPOINT = "wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1"
# Token 服务地址（CreateToken RPC 接口）
_TOKEN_HOST = "nls-meta.cn-shanghai.aliyuncs.com"
# Token 服务 API 版本
_TOKEN_API_VERSION = "2019-02-28"
# 每次发送的音频块大小（字节），40ms × 16000Hz × 2bytes = 1280
_CHUNK_SIZE = 1280


class AliyunASRService(ASRBase):
    """阿里云实时语音识别 ASR WebSocket 客户端。

    继承 ASRBase，实现阿里云 NLS 实时语音转写的 WebSocket 协议。
    构造函数接收凭证字典，适配工厂模式。
    """

    def __init__(self, credentials: Dict[str, str]) -> None:
        """初始化阿里云 ASR 服务。

        Args:
            credentials: 凭证字典，包含:
                - app_key: 项目 AppKey（控制台创建项目获取，必填）
                - access_key_id: AccessKey ID（用于换取 Token，必填）
                - access_key_secret: AccessKey Secret（用于换取 Token，必填）
        """
        # 凭证
        self._app_key = credentials.get("app_key", "")
        self._access_key_id = credentials.get("access_key_id", "")
        self._access_key_secret = credentials.get("access_key_secret", "")
        # 验证必填字段
        if not self._app_key or not self._access_key_id or not self._access_key_secret:
            raise ValueError("阿里云 ASR 需要 app_key、access_key_id、access_key_secret 凭证")
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
        # 是否已收到 TranscriptionStarted（收到后才开始发送音频）
        self._started = False
        # 会话 task_id（StartTranscription 与 StopTranscription 需保持一致）
        self._task_id = uuid.uuid4().hex
        # 已识别的稳态文本（SentenceEnd 的 result 按 index 累积）
        self._final_text = ""
        # 缓存的 Token
        self._token: Optional[str] = None
        DiagLog.shared().write("[Aliyun] 阿里云 ASR 初始化成功（NLS 实时语音转写）")

    # ===== 对外 API（在主线程调用，转发到事件循环线程） =====

    def start(self) -> None:
        """启动 ASR 会话：获取 Token、连接 WebSocket、发送 StartTranscription。

        重置状态，启动独立线程的事件循环，提交连接任务。
        """
        # 重置状态
        self._send_finished = False
        self._closed = False
        self._started = False
        self._final_text = ""
        self._token = None
        self._task_id = uuid.uuid4().hex
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

        只入队不直接发送：TranscriptionStarted 后由事件循环内的 _sender 统一取帧发送。

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

        入队 sentinel，_sender 收到后先发完所有音频，再发送 StopTranscription 指令。
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

    def _fetch_token(self) -> str:
        """通过阿里云 RPC API（CreateToken）换取临时 Token（同步阻塞）。

        阿里云 RPC API v1.0 签名算法：
        1. 构造请求参数（含 AccessKeyId、Action、SignatureMethod 等，不含 Signature）
        2. 参数按字典序排序，url 编码后拼接成 canonical query string
        3. string_to_sign = "GET&" + url_encode("/") + "&" + url_encode(canonical_query)
        4. signature = base64(HMAC-SHA1(access_key_secret + "&", string_to_sign))
        5. 把 Signature 加入请求参数发起 GET 请求

        Returns:
            访问 Token 字符串

        Raises:
            RuntimeError: 获取 Token 失败
        """
        # 请求参数（不含 Signature）
        params: Dict[str, str] = {
            "AccessKeyId": self._access_key_id,
            "Action": "CreateToken",
            "Format": "JSON",
            "RegionId": "cn-shanghai",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": uuid.uuid4().hex,
            "SignatureVersion": "1.0",
            "Timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
            "Version": _TOKEN_API_VERSION,
        }
        # 计算签名
        signature = self._compute_rpc_signature(params)
        params["Signature"] = signature
        # 构造完整 URL
        query = urllib.parse.urlencode(params)
        url = f"https://{_TOKEN_HOST}/?{query}"
        # 发起 GET 请求（超时 10 秒）
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        token_obj = data.get("Token")
        if not isinstance(token_obj, dict):
            raise RuntimeError(f"Token 响应异常: {body[:200]}")
        token = token_obj.get("Id")
        if not token:
            raise RuntimeError(f"Token 为空: {body[:200]}")
        return token

    def _compute_rpc_signature(self, params: Dict[str, str]) -> str:
        """计算阿里云 RPC API v1.0 的 HMAC-SHA1 签名。

        Args:
            params: 请求参数字典（不含 Signature）

        Returns:
            base64 编码的签名字符串
        """
        # 1. 参数按字典序排序
        sorted_items = sorted(params.items())
        # 2. url 编码后拼接 canonical query string
        canonical = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted_items
        )
        # 3. 构造待签名串：GET&/<url编码>&<url编码(canonical)>
        string_to_sign = "GET&" + quote("/", safe="") + "&" + quote(canonical, safe="")
        # 4. HMAC-SHA1 加密（key 为 access_key_secret + "&"）后 base64 编码
        hmac_result = hmac.new(
            (self._access_key_secret + "&").encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(hmac_result).decode("utf-8")

    def _build_start_instruction(self) -> str:
        """构造 StartTranscription 指令的 JSON 文本。

        指令由 header 与 payload 两部分组成，header 需包含 appkey、task_id、
        namespace、name 等字段，payload 包含音频格式与识别参数。

        Returns:
            StartTranscription 指令的 JSON 字符串
        """
        instruction = {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": self._task_id,
                "namespace": "SpeechTranscriber",
                "name": "StartTranscription",
                "appkey": self._app_key,
            },
            "payload": {
                "format": "pcm",
                "sample_rate": 16000,
                "enable_intermediate_result": True,
                "enable_punctuation_prediction": True,
                "enable_inverse_text_normalization": True,
            },
        }
        return json.dumps(instruction, ensure_ascii=False)

    def _build_stop_instruction(self) -> str:
        """构造 StopTranscription 指令的 JSON 文本（payload 为空）。

        Returns:
            StopTranscription 指令的 JSON 字符串
        """
        instruction = {
            "header": {
                "message_id": uuid.uuid4().hex,
                "task_id": self._task_id,
                "namespace": "SpeechTranscriber",
                "name": "StopTranscription",
                "appkey": self._app_key,
            }
        }
        return json.dumps(instruction, ensure_ascii=False)

    async def _connect_and_start(self) -> None:
        """获取 Token、连接 WebSocket、发送 StartTranscription、启动收发循环。"""
        try:
            import websockets
        except ImportError as e:
            DiagLog.shared().write(f"[Aliyun] websockets 未安装: {e}")
            self._fail("websockets not installed")
            return

        # 调试日志：脱敏打印凭证前缀
        akid_preview = self._access_key_id[:6] + "***" if len(self._access_key_id) > 6 else "***"
        appkey_preview = self._app_key[:6] + "***" if len(self._app_key) > 6 else "***"
        DiagLog.shared().write(
            f"[Aliyun] 准备获取 Token: access_key_id={akid_preview}"
            f"(len={len(self._access_key_id)}) appkey={appkey_preview}(len={len(self._app_key)})"
        )

        # 1. 获取 Token（在线程池中执行同步 HTTP 调用，避免阻塞事件循环）
        loop = asyncio.get_running_loop()
        try:
            self._token = await loop.run_in_executor(None, self._fetch_token)
            DiagLog.shared().write("[Aliyun] Token 获取成功")
        except Exception as e:
            DiagLog.shared().write(f"[Aliyun] Token 获取失败: {e}")
            self._fail(f"fetch token failed: {e}")
            return

        # 2. 连接 WebSocket 网关
        ws_url = f"{_WS_ENDPOINT}?token={quote(self._token, safe='')}"
        try:
            self._ws = await websockets.connect(ws_url, ping_interval=None)
            DiagLog.shared().write("[Aliyun] WebSocket 已连接")
        except Exception as e:
            DiagLog.shared().write(f"[Aliyun] WebSocket 连接失败: {e}")
            self._fail(f"connect failed: {e}")
            return

        # 3. 发送 StartTranscription 指令
        try:
            await self._ws.send(self._build_start_instruction())
            DiagLog.shared().write("[Aliyun] StartTranscription 指令已发送")
        except Exception as e:
            DiagLog.shared().write(f"[Aliyun] StartTranscription 发送失败: {e}")
            self._fail(f"start instruction failed: {e}")
            return

        # 4. 启动音频发送协程（内部会等待 TranscriptionStarted 后再发音频）
        self._sender_task = asyncio.create_task(self._sender())
        # 5. 启动接收循环
        await self._receive_loop()

    async def _receive_loop(self) -> None:
        """持续接收服务端事件，解析并触发回调。"""
        while not self._closed and self._ws is not None:
            try:
                message = await self._ws.recv()
            except Exception as e:
                if not self._closed:
                    DiagLog.shared().write(f"[Aliyun] receive error: {e}")
                    self._finish_with_final_text()
                return

            # 阿里云事件为 Text 帧（JSON）
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            if not isinstance(message, str):
                continue

            self._handle_message(message)

    def _handle_message(self, message: str) -> None:
        """解析服务端 JSON 事件，触发 partial / complete 回调。

        事件由 header.name 标识：
        - TranscriptionStarted：服务端就绪，标记可发送音频
        - SentenceBegin：一句话开始
        - TranscriptionResultChanged：中间结果（partial），payload.result
        - SentenceEnd：一句话结束（稳态），payload.result 累积
        - TranscriptionCompleted：全部完成，触发完成回调
        - TaskFailed / header.status 非 20000000：失败
        """
        try:
            obj = json.loads(message)
        except json.JSONDecodeError:
            DiagLog.shared().write(f"[Aliyun] JSON 解析失败: {message[:200]}")
            return

        header = obj.get("header", {})
        if not isinstance(header, dict):
            return
        name = header.get("name", "")
        status = header.get("status", 20000000)

        # 失败状态：status 非 20000000 视为错误
        if status != 20000000:
            status_msg = header.get("status_message", "")
            DiagLog.shared().write(f"[Aliyun] ❌ 任务失败: name={name} status={status} {status_msg}")
            hint = self._error_hint(name, status, status_msg)
            if hint:
                DiagLog.shared().write(f"[Aliyun] 诊断提示: {hint}")
            self._fail(f"task failed: name={name} status={status} {status_msg}")
            return

        payload = obj.get("payload", {}) or {}

        if name == "TranscriptionStarted":
            # 服务端就绪，标记可发送音频
            self._started = True
            DiagLog.shared().write("[Aliyun] TranscriptionStarted，开始发送音频")
            return

        if name == "SentenceBegin":
            DiagLog.shared().write(f"[Aliyun] SentenceBegin index={payload.get('index')}")
            return

        if name == "TranscriptionResultChanged":
            # 中间结果（partial）：显示「累积稳态 + 当前句」
            result = str(payload.get("result", ""))
            if result:
                display = self._final_text + result
                DiagLog.shared().write(f"[Aliyun] resp: text={result[:80]} final=False")
                self._schedule_partial(display)
            return

        if name == "SentenceEnd":
            # 句子结束（稳态结果），累积到最终文本
            result = str(payload.get("result", ""))
            if result:
                self._final_text += result
                DiagLog.shared().write(
                    f"[Aliyun] SentenceEnd: {result[:80]} | 累积: {self._final_text[:80]}"
                )
                self._schedule_partial(self._final_text)
            return

        if name == "TranscriptionCompleted":
            # 全部识别完成
            DiagLog.shared().write("[Aliyun] TranscriptionCompleted")
            self._finish_with_final_text()
            return

        # 其他事件（如 TaskFailed）
        if name == "TaskFailed":
            status_msg = header.get("status_message", "")
            DiagLog.shared().write(f"[Aliyun] ❌ TaskFailed: {status_msg}")
            self._fail(f"task failed: {status_msg}")
            return

        DiagLog.shared().write(f"[Aliyun] 未知事件: {name}")

    def _error_hint(self, name: str, status: int, status_msg: str) -> str:
        """根据事件名与状态码给出常见问题诊断提示。

        Args:
            name: 事件名
            status: 状态码
            status_msg: 状态描述

        Returns:
            诊断提示文本，无则返回空串
        """
        msg_lower = status_msg.lower()
        # Token / 鉴权类失败
        if "token" in msg_lower or "auth" in msg_lower or status in (401, 403, 50013, 50014):
            return (
                "阿里云鉴权类错误：\n"
                "1. 检查 AccessKey ID / AccessKey Secret 是否正确（需在 RAM 获取）\n"
                "2. 检查 AppKey 是否正确（需在 NLS 控制台创建项目后获取）\n"
                "3. 确认已开通「智能语音交互」服务并在项目中配置了实时语音识别模型"
            )
        return ""

    def _finish_with_final_text(self) -> None:
        """以已累积的稳态文本作为最终结果触发完成回调。"""
        if self._closed:
            return
        self._closed = True
        if self._final_text:
            DiagLog.shared().write(f"[Aliyun] 最终文本: {self._final_text[:80]}")
            self._schedule_complete(ASRResult(text=self._final_text, is_final=True))
        else:
            DiagLog.shared().write("[Aliyun] 无稳态文本，返回失败")
            self._schedule_complete(None)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)

    async def _sender(self) -> None:
        """事件循环内：等待 Started 后从队列取音频帧发送；收到 sentinel 发 Stop。

        TranscriptionStarted 前入队的音频会在 Started 后按序补发，保证不丢音频。
        音频按 _CHUNK_SIZE 分块发送。
        """
        loop = asyncio.get_running_loop()
        buffer = bytearray()

        # 等待 TranscriptionStarted（最多等待 10 秒，避免永久阻塞）
        wait_deadline = loop.time() + 10
        while not self._started and not self._closed:
            if loop.time() > wait_deadline:
                DiagLog.shared().write("[Aliyun] 等待 TranscriptionStarted 超时")
                self._fail("wait TranscriptionStarted timeout")
                return
            await asyncio.sleep(0.05)
        if self._closed:
            return

        while not self._closed:
            # 阻塞取帧放到线程池，避免卡住事件循环
            pcm = await loop.run_in_executor(None, self._audio_queue.get)
            if pcm is None:
                # sentinel：发完缓冲区剩余音频，再发 StopTranscription 指令
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

        # 发送 StopTranscription 指令
        if not self._closed and self._ws is not None:
            try:
                await self._ws.send(self._build_stop_instruction())
                DiagLog.shared().write("[Aliyun] StopTranscription 指令已发送")
            except Exception as e:
                DiagLog.shared().write(f"[Aliyun] StopTranscription 发送失败: {e}")

    async def _send_chunk(self, chunk: bytes) -> None:
        """发送一个音频块到 WebSocket（二进制帧）。

        Args:
            chunk: PCM 音频字节块
        """
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(chunk)
        except Exception as e:
            DiagLog.shared().write(f"[Aliyun] send error: {e}")
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
                DiagLog.shared().write(f"[Aliyun] on_partial 异常: {e}")

    def _schedule_complete(self, result: Optional[ASRResult]) -> None:
        """调度完成回调。"""
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                DiagLog.shared().write(f"[Aliyun] on_complete 异常: {e}")

    def _fail(self, message: str) -> None:
        """失败处理：标记状态、调度失败回调、关闭连接。"""
        self._send_finished = True
        self._closed = True
        DiagLog.shared().write(f"[Aliyun] ❌ fail: {message}")
        self._schedule_complete(None)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
