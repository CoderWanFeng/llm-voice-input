"""科大讯飞实时语音转写（RTASR v1）WebSocket 客户端。

基于科大讯飞开放平台实时语音转写标准版 API 实现：
- 端点：wss://rtasr.xfyun.cn/v1/ws?{请求参数}
- 鉴权：HMAC-SHA1 签名（signa = base64(HMAC-SHA1(api_key, MD5(appid + ts)))）
- 音频格式：16kHz / 16bit / 单声道 PCM
- 数据发送：建议每 40ms 发送 1280 字节原始 PCM
- 结束标记：发送 {"end": true} 的 UTF-8 字节

服务端返回 JSON：
- action="started"：握手成功
- action="result"：识别结果（data 字段为 JSON 字符串，需二次解析）
- action="error"：异常
- action="over"：识别完成

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
from typing import Any, Callable, Dict, Optional
from urllib.parse import quote

from .asr_base import ASRBase, ASRResult
from .diag_log import DiagLog


# WebSocket 端点（实时语音转写标准版 v1）
_ENDPOINT = "wss://rtasr.xfyun.cn/v1/ws"
# 每次发送的音频块大小（字节），40ms × 16000Hz × 2bytes = 1280
_CHUNK_SIZE = 1280
# 结束标记 JSON
_END_TAG = '{"end": true}'


class XFlyASRService(ASRBase):
    """科大讯飞实时语音转写 ASR WebSocket 客户端。

    继承 ASRBase，实现科大讯飞 RTASR 标准版的 WebSocket 协议。
    构造函数接收凭证字典，适配工厂模式。
    """

    def __init__(self, credentials: Dict[str, str]) -> None:
        """初始化科大讯飞 ASR 服务。

        Args:
            credentials: 凭证字典，包含:
                - app_id: APP ID（必填）
                - app_key: API Key（必填）
        """
        # 凭证
        self._app_id = credentials.get("app_id", "")
        self._app_key = credentials.get("app_key", "")
        # 验证必填字段
        if not self._app_id or not self._app_key:
            raise ValueError("科大讯飞 ASR 需要 app_id 和 app_key 凭证")
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
        # 累积文本：RTASR 一次会话含多个 seg
        # _final_text: 所有已稳定 seg 的累积文本
        # _partial_text: 当前未稳定 seg 的中间结果
        self._final_text = ""
        self._partial_text = ""
        DiagLog.shared().write("[XFly] 科大讯飞 ASR 初始化成功（RTASR v1）")

    # ===== 对外 API（在主线程调用，转发到事件循环线程） =====

    def start(self) -> None:
        """启动 ASR 会话：连接 WebSocket 并发送握手。

        重置状态，启动独立线程的事件循环，提交连接任务。
        连接建立后即可接收音频帧。
        """
        # 重置状态
        self._send_finished = False
        self._closed = False
        self._audio_queue = queue.Queue()
        self._sender_task = None
        self._final_text = ""
        self._partial_text = ""

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

        入队 sentinel，_sender 收到后先发完所有音频，再发送结束标记。
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

    def _build_signa(self, ts: str) -> str:
        """生成科大讯飞 RTASR 签名。

        签名算法：signa = base64(HMAC-SHA1(api_key, MD5(appid + ts)))

        Args:
            ts: 当前时间戳（秒）

        Returns:
            base64 编码的签名字符串
        """
        # 1. 拼接 baseString = appid + ts
        base_string = (self._app_id + ts).encode("utf-8")
        # 2. 对 baseString 进行 MD5
        md5_result = hashlib.md5(base_string).hexdigest().encode("utf-8")
        # 3. 以 api_key 为 key 对 MD5 结果进行 HMAC-SHA1 加密
        hmac_result = hmac.new(
            self._app_key.encode("utf-8"),
            md5_result,
            hashlib.sha1,
        ).digest()
        # 4. base64 编码
        signa = base64.b64encode(hmac_result).decode("utf-8")
        return signa

    def _build_ws_url(self) -> str:
        """构造带签名参数的 WebSocket URL。

        URL 格式：wss://rtasr.xfyun.cn/v1/ws?appid={appid}&ts={ts}&signa={signa}

        Returns:
            完整的 WebSocket URL
        """
        ts = str(int(time.time()))
        signa = self._build_signa(ts)
        # signa 需要 URL 编码（包含 = 等特殊字符）
        url = (
            f"{_ENDPOINT}?appid={self._app_id}"
            f"&ts={ts}&signa={quote(signa)}"
            f"&punc=1"  # 返回标点
        )
        return url

    async def _connect_and_start(self) -> None:
        """连接 WebSocket 并进入接收循环。"""
        try:
            import websockets
        except ImportError as e:
            DiagLog.shared().write(f"[XFly] websockets 未安装: {e}")
            self._fail("websockets not installed")
            return

        ws_url = self._build_ws_url()
        # 调试日志：打印 URL 参数（脱敏，只显示前4位）
        ts = ws_url.split("ts=")[1].split("&")[0] if "ts=" in ws_url else "?"
        appid_preview = self._app_id[:4] + "***" if len(self._app_id) > 4 else "***"
        apikey_preview = self._app_key[:4] + "***" if len(self._app_key) > 4 else "***"
        DiagLog.shared().write(
            f"[XFly] 准备连接 WebSocket: appid={appid_preview}(len={len(self._app_id)}) "
            f"apikey={apikey_preview}(len={len(self._app_key)}) ts={ts}"
        )
        DiagLog.shared().write(f"[XFly] 完整 URL: {ws_url}")

        try:
            self._ws = await websockets.connect(ws_url, ping_interval=None)
            DiagLog.shared().write("[XFly] WebSocket 已连接")
        except Exception as e:
            DiagLog.shared().write(f"[XFly] WebSocket 连接失败: {e}")
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
                    # 科大讯飞 RTASR 服务端发完所有结果后会直接用
                    # 1000（正常关闭）关闭 WebSocket，而不是发送
                    # action="over" 消息。因此当已发送结束标记且服务端
                    # 正常关闭连接时，应视为会话正常结束，触发 on_complete
                    # 传递累积文本，而不是当成失败处理。
                    if self._is_normal_close(e) and self._send_finished:
                        DiagLog.shared().write(
                            "[XFly] 服务端正常关闭连接，会话结束"
                        )
                        self._closed = True
                        final_text = self._final_text or self._partial_text
                        self._schedule_complete(
                            ASRResult(text=final_text, is_final=True)
                            if final_text
                            else None
                        )
                    else:
                        DiagLog.shared().write(f"[XFly] receive error: {e}")
                        self._fail(f"ws recv: {e}")
                return

            # RTASR 返回的是文本格式的 JSON
            if isinstance(message, bytes):
                message = message.decode("utf-8", errors="replace")
            if not isinstance(message, str):
                continue

            self._handle_message(message)

    def _is_normal_close(self, exc: Exception) -> bool:
        """判断 WebSocket 异常是否为服务端正常关闭（code=1000）。

        科大讯飞 RTASR 服务端发完所有结果后会直接用 1000（正常关闭）
        关闭 WebSocket，websockets 库会抛 ConnectionClosedOK，
        其消息形如 'received 1000 (OK); then sent 1000 (OK)'。

        Args:
            exc: recv() 抛出的异常

        Returns:
            True 表示是服务端正常关闭，False 表示异常断开
        """
        # 优先用 websockets 库的异常类型判断
        try:
            from websockets.exceptions import ConnectionClosedOK
            if isinstance(exc, ConnectionClosedOK):
                return True
        except ImportError:
            pass
        # 兜底：通过错误消息文本判断（包含 1000 (OK) 字样）
        msg = str(exc)
        return "received 1000" in msg or "1000 (OK)" in msg

    def _handle_message(self, message: str) -> None:
        """解析服务端 JSON 消息，触发 partial / complete 回调。

        消息格式：
        - {"action": "started", "code": "0", ...}：握手成功
        - {"action": "result", "code": "0", "data": "..."}：识别结果
        - {"action": "error", "code": "...", "desc": "..."}：异常
        - {"action": "over", "code": "0", ...}：识别完成（实际中服务端
          可能不发 over，而是直接用 1000 code 关闭 WebSocket）

        data 字段是一个 JSON 字符串，需要二次解析：
        - data.cn.st.rt：识别文本片段
        - data.cn.st.type：0=该 seg 最终结果，1=该 seg 中间结果
        """
        try:
            obj = json.loads(message)
        except json.JSONDecodeError:
            DiagLog.shared().write(f"[XFly] JSON 解析失败: {message[:200]}")
            return

        action = obj.get("action", "")
        code = str(obj.get("code", ""))

        if action == "started":
            DiagLog.shared().write(f"[XFly] 握手成功: code={code}")
            return

        if action == "error":
            desc = obj.get("desc", "")
            DiagLog.shared().write(f"[XFly] ❌ 错误: code={code} desc={desc}")
            # 10105 错误给出详细诊断提示
            if code == "10105":
                hint = (
                    "科大讯飞 10105 错误（没有权限）常见原因：\n"
                    "1. APIKey 不正确或与其他服务的 Key 混用\n"
                    "2. 未在控制台配置 IP 白名单（控制台→我的应用→实时语音转写→IP白名单）\n"
                    "3. 未开通「实时语音转写」服务（控制台→服务页→领取免费包/开通服务）\n"
                    f"当前 appid={self._app_id[:4]}*** (len={len(self._app_id)}) "
                    f"apikey={self._app_key[:4]}*** (len={len(self._app_key)})"
                )
                DiagLog.shared().write(f"[XFly] 诊断提示: {hint}")
                self._fail(f"10105: {desc}\n请检查 APIKey、IP白名单、服务开通状态")
            else:
                self._fail(f"server error: code={code} desc={desc}")
            return

        if action == "result":
            # data 字段是 JSON 字符串，需要二次解析
            data_str = obj.get("data", "")
            text, is_final = self._parse_data(data_str)
            # RTASR 一次会话包含多个 seg：
            # - type=1（中间结果）：覆盖 _partial_text
            # - type=0（seg 最终结果）：把 text 并入 _final_text，清空 _partial_text
            # 关键：单个 seg 稳定不等于整个会话结束，会话仅在收到 action="over"
            #       或客户端发完 {"end": true} 后服务端主动发 over 时才结束
            if is_final:
                self._final_text = (self._final_text + text).strip()
                self._partial_text = ""
            else:
                self._partial_text = text
            # 累积显示文本：已稳定文本 + 当前中间结果
            if self._partial_text:
                accumulated = (
                    self._final_text + self._partial_text
                    if self._final_text
                    else self._partial_text
                )
            else:
                accumulated = self._final_text
            if accumulated:
                DiagLog.shared().write(
                    f"[XFly] resp: text={accumulated[:80]} final={is_final}"
                )
                self._schedule_partial(accumulated)
            return

        if action == "over":
            DiagLog.shared().write(f"[XFly] 识别完成: code={code}")
            # over 表示整个会话结束：触发 on_complete 传递最终累积文本
            # 优先使用已稳定的 _final_text；若最后一句未稳定则补上 _partial_text
            if not self._closed:
                self._closed = True
                final_text = self._final_text or self._partial_text
                self._schedule_complete(
                    ASRResult(text=final_text, is_final=True)
                )
                asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
            return

        # 未知 action
        DiagLog.shared().write(f"[XFly] 未知 action: {action}")

    def _parse_data(self, data_str: str) -> tuple:
        """解析 RTASR result 的 data 字段。

        data 字段是 JSON 字符串，格式如下：
        {
            "cn": {
                "st": {
                    "rt": [{"ws": [{"cw": [{"w": "文字"}]}]}],
                    "type": 0  # 0=该 seg 最终结果，1=该 seg 中间结果
                }
            }
        }

        从 rt 数组中的 ws → cw → w 拼接出完整文本。

        Args:
            data_str: data 字段的 JSON 字符串

        Returns:
            (text, is_final) 元组
        """
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, TypeError):
            return ("", False)

        cn = data.get("cn")
        if not isinstance(cn, dict):
            return ("", False)
        st = cn.get("st")
        if not isinstance(st, dict):
            return ("", False)

        # type 字段含义（依据 RTASR v1 官方文档）：
        #   0 = 该 seg 的最终结果（已稳定）
        #   1 = 该 seg 的中间结果（未稳定，可能被后续结果修正）
        # 注意：type=0 仅表示当前 seg 稳定，不代表整个会话结束
        st_type = st.get("type", 0)
        is_final = str(st_type) == "0"

        # 从 rt 数组中提取文本
        rt_arr = st.get("rt", [])
        if not isinstance(rt_arr, list):
            return ("", is_final)

        text_parts = []
        for rt_item in rt_arr:
            if not isinstance(rt_item, dict):
                continue
            ws_arr = rt_item.get("ws", [])
            if not isinstance(ws_arr, list):
                continue
            for ws_item in ws_arr:
                if not isinstance(ws_item, dict):
                    continue
                cw_arr = ws_item.get("cw", [])
                if not isinstance(cw_arr, list):
                    continue
                for cw_item in cw_arr:
                    if not isinstance(cw_item, dict):
                        continue
                    w = cw_item.get("w", "")
                    if w:
                        text_parts.append(w)

        text = "".join(text_parts)
        return (text, is_final)

    async def _sender(self) -> None:
        """事件循环内：从队列取音频帧并发送；收到 sentinel 后发结束标记。

        音频按 _CHUNK_SIZE 分块发送（建议每 40ms 发送 1280 字节）。
        """
        loop = asyncio.get_running_loop()
        buffer = bytearray()

        while not self._closed:
            # 阻塞取帧放到线程池，避免卡住事件循环
            pcm = await loop.run_in_executor(None, self._audio_queue.get)
            if pcm is None:
                # sentinel：发完缓冲区剩余音频，再发结束标记
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

        # 发送结束标记
        if not self._closed and self._ws is not None:
            try:
                await self._ws.send(_END_TAG.encode("utf-8"))
                DiagLog.shared().write("[XFly] 结束标记已发送")
            except Exception as e:
                DiagLog.shared().write(f"[XFly] 结束标记发送失败: {e}")

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
            DiagLog.shared().write(f"[XFly] send error: {e}")
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
                DiagLog.shared().write(f"[XFly] on_partial 异常: {e}")

    def _schedule_complete(self, result: Optional[ASRResult]) -> None:
        """调度完成回调。"""
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                DiagLog.shared().write(f"[XFly] on_complete 异常: {e}")

    def _fail(self, message: str) -> None:
        """失败处理：标记状态、调度失败回调、关闭连接。"""
        self._send_finished = True
        self._closed = True
        DiagLog.shared().write(f"[XFly] ❌ fail: {message}")
        self._schedule_complete(None)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
