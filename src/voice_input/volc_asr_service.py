"""火山引擎豆包流式 ASR (bigmodel v1) WebSocket 客户端。

对应 macOS 原项目 VolcASRService.swift，严格复刻协议：
- 端点：wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
- 鉴权 Headers：X-Api-App-Key / X-Api-Access-Key / X-Api-Resource-Id / X-Api-Connect-Id
- 二进制帧格式：0x11 | (msgType<<4)|flags | (序列化<<4)|压缩 | 0x00 | [seq:4B BE] | size:4B BE | payload
- 启动帧：msgType=0b0001, flags=0, 无 seq, byte2=0x10（JSON 无压缩）
- 音频帧：msgType=0b0010, flags=0b0001, seq=N(从 2 递增；启动帧占用序列 1), byte2=0x00（raw 无压缩）
- 结束帧：msgType=0b0010, flags=0b0011（负包标记+带 seq）, seq=-N(取相反数), byte2=0x00, 空 payload
- 结果帧 msgType=0b1001：result.text + result.utterances[].definite
  definite=true 表示一个 utterance（句子）的 final，不是整个会话结束；
  整个会话由客户端发结束帧后服务端 1000 正常关闭来结束
- 错误帧 msgType=0b1111

继承 ASRBase 抽象基类，使用 websockets 异步实现，运行在独立线程的事件循环中。
"""

from __future__ import annotations

import asyncio
import json
import queue
import struct
import threading
import uuid
from typing import Any, Callable, Dict, Optional

from .asr_base import ASRBase, ASRResult
from .diag_log import DiagLog


# WebSocket 端点
_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
# 资源 ID 标识（可由环境变量 VOICEINPUT_RESOURCE_ID 覆盖，方便切换不同版本/计费方式）
# 豆包流式 ASR 1.0 小时版         : volc.bigasr.sauc.duration
# 豆包流式 ASR 1.0 并发版         : volc.bigasr.sauc.concurrent
# 豆包流式 ASR 2.0 小时版（默认） : volc.seedasr.sauc.duration
# 豆包流式 ASR 2.0 并发版         : volc.seedasr.sauc.concurrent
# 注意：默认值需与账号实际开通的服务一致，否则握手会报 HTTP 403 requested resource not granted。
# 本账号 2.0 小时版与并发版均已开通；个人语音输入场景推荐小时版（按实际音频时长计费，
# 并发版按并发路数计费，闲置也可能产生费用）。可用 diagnose_asr.py 探测开通状态，
# 再按需修改此处或设置 VOICEINPUT_RESOURCE_ID。
import os as _os
_RESOURCE_ID = _os.environ.get("VOICEINPUT_RESOURCE_ID") or "volc.seedasr.sauc.duration"


class VolcASRService(ASRBase):
    """火山引擎豆包 ASR WebSocket 客户端。

    继承 ASRBase，实现火山引擎豆包 ASR 的 WebSocket 协议。
    构造函数接收凭证字典，适配工厂模式。
    """

    def __init__(self, credentials: Dict[str, str]) -> None:
        """初始化火山引擎 ASR 服务。

        Args:
            credentials: 凭证字典，包含:
                - app_id: APP ID 或 APP Key（必填）
                - access_token: Access Token（可选，旧版控制台需要）
        """
        # 凭证
        self._app_id = credentials.get("app_id", "")
        self._access_token = credentials.get("access_token", "")
        # 验证必填字段
        if not self._app_id:
            raise ValueError("火山引擎豆包 ASR 需要 app_id 凭证")
        # 回调（在主线程被调度）
        self.on_partial: Optional[Callable[[str], None]] = None
        self.on_complete: Optional[Callable[[Optional[ASRResult]], None]] = None
        # 异步事件循环（独立线程）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        # WebSocket 连接
        self._ws: Optional[Any] = None  # websockets.WebSocketClientProtocol
        # 音频数据队列：录音线程入队，事件循环内 _sender 取出发送。
        # 连接建立前的音频也会缓存于此，连接后按序补发，保证 seq 从 1 连续递增。
        self._audio_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._sender_task: Optional[asyncio.Task] = None
        # 音频包序列号，从 2 递增（服务端 auto-assign 把启动帧算作序列 1，
        # 首个音频包必须是 seq=2，否则报 autoAssignedSequence mismatch）
        self._next_seq = 2
        # 状态标记
        self._send_finished = False
        self._closed = False
        # 最近一次服务端返回的识别文本（正常关闭时兜底使用，避免结果丢失）
        self._last_text = ""

    # ===== 对外 API（在主线程调用，转发到事件循环线程） =====

    def start(self) -> None:
        """启动 ASR 会话：连接 WebSocket 并发送启动帧。"""
        # 重置状态（音频包序列号从 2 开始，启动帧占用序列 1）
        self._next_seq = 2
        self._send_finished = False
        self._closed = False
        self._last_text = ""
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

        只入队不直接发送：连接建立后由事件循环内的 _sender 统一取帧，
        从 seq=2 开始连续分配序列号（启动帧占用序列 1）。连接前的音频会缓存排队，不会被丢弃，
        避免序列号与服务端 auto-assign 错位（否则报
        autoAssignedSequence mismatch sequence in request）。
        """
        if self._send_finished or self._closed or self._loop is None:
            return
        if not pcm:
            return
        self._audio_queue.put(pcm)

    def finish(self) -> None:
        """发送结束帧（负包：seq 取相反数），通知服务端音频流结束。"""
        if self._send_finished or self._closed or self._loop is None:
            return
        self._send_finished = True
        # sentinel 入队：_sender 收到后先发完所有音频包，再发结束帧
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

    async def _connect_and_start(self) -> None:
        """连接 WebSocket 并发送启动帧，启动接收循环。"""
        try:
            # websockets 12+ 使用 connect
            import websockets
        except ImportError as e:
            DiagLog.shared().write(f"[ASR] websockets 未安装: {e}")
            self._fail("websockets not installed")
            return

        # 构造鉴权 Headers：智能适配新旧版控制台
        # - 新版控制台（推荐）：仅需 X-Api-Key（凭证页只给一个 APP Key）
        # - 旧版控制台（即将下线）：需 X-Api-App-Key + X-Api-Access-Key（APP ID + Access Token）
        # 通过 access_token 是否为空自动切换模式
        # 官方文档（双向流式 bigmodel_async）要求 X-Api-Request-Id / X-Api-Sequence 为必选头，
        # 缺失会导致服务端在收到启动帧后返回错误帧并断开，因此两种鉴权模式都必须带上。
        headers = {
            "X-Api-Resource-Id": _RESOURCE_ID,
            "X-Api-Connect-Id": str(uuid.uuid4()),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        if self._access_token:
            # 旧版鉴权：APP ID（纯数字）+ Access Token（长字符串）
            headers["X-Api-App-Key"] = self._app_id
            headers["X-Api-Access-Key"] = self._access_token
            DiagLog.shared().write("[ASR] 鉴权模式：旧版 X-Api-App-Key + X-Api-Access-Key")
        else:
            # 新版鉴权：仅需 APP Key
            headers["X-Api-Key"] = self._app_id
            DiagLog.shared().write("[ASR] 鉴权模式：新版 X-Api-Key")
        DiagLog.shared().write("[ASR] start() called, 准备连接 WebSocket")

        try:
            self._ws = await websockets.connect(_ENDPOINT, additional_headers=headers, ping_interval=None)
            DiagLog.shared().write("[ASR] WebSocket 已连接")
        except Exception as e:
            DiagLog.shared().write(f"[ASR] WebSocket 连接失败: {e}")
            self._fail(f"connect failed: {e}")
            return

        # 构造启动 JSON 配置（对齐官方 2.0 文档：show_utterances 替代 1.0 的 result_type）
        config: Dict[str, Any] = {
            "user": {"uid": "voiceinput-win"},
            "audio": {
                "format": "pcm",
                "rate": 16000,
                "bits": 16,
                "channel": 1,
                "codec": "raw",
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "show_utterances": True,
            },
        }
        payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
        frame = self._build_frame(message_type=0b0001, flags=0, seq=None, payload=payload, serialization=1)
        try:
            await self._ws.send(frame)
            DiagLog.shared().write("[ASR] 启动帧已发送，进入接收循环")
        except Exception as e:
            DiagLog.shared().write(f"[ASR] 启动帧发送失败: {e}")
            self._fail(f"start frame failed: {e}")
            return

        # 连接已就绪：启动音频发送协程（队列中积压的音频从 seq=2 开始补发）
        self._sender_task = asyncio.create_task(self._sender())

        # 启动接收循环
        await self._receive_loop()

    async def _receive_loop(self) -> None:
        """持续接收服务端消息，解析并触发回调。"""
        while not self._closed and self._ws is not None:
            try:
                message = await self._ws.recv()
            except Exception as e:
                if self._closed:
                    return
                # 服务端在收到结束帧后会以 1000 (OK) 正常关闭连接，
                # 这不是错误：按会话正常结束处理，避免误报 fail
                #（与科大讯飞服务的正常关闭处理方式一致）
                code = getattr(e, "code", None)
                if code is None:
                    # websockets 新版把收到的关闭码放在 e.rcvd.code
                    code = getattr(getattr(e, "rcvd", None), "code", None)
                if code == 1000 and self._send_finished:
                    DiagLog.shared().write("[ASR] 服务端正常关闭 (1000 OK)，会话结束")
                    self._closed = True
                    final_text = self._last_text
                    self._schedule_complete(
                        ASRResult(text=final_text, is_final=True) if final_text else None
                    )
                    return
                DiagLog.shared().write(f"[ASR] receive error: {e}")
                self._fail(f"ws recv: {e}")
                return
            # websockets 接收 bytes
            if isinstance(message, str):
                # 字符串消息转 bytes
                message = message.encode("utf-8")
            if not isinstance(message, (bytes, bytearray)):
                continue
            data = bytes(message)
            DiagLog.shared().write(f"[ASR] 收到 {len(data)} bytes: {data[:20].hex(' ')}")
            self._handle_frame(data)

    def _handle_frame(self, data: bytes) -> None:
        """解析一帧服务端消息，触发 partial / complete 回调。

        头部布局自适应：flags 为 0b0001/0b0011 时必带 4B seq；实测服务端对部分帧
        （如错误帧 msgType=0b1111）也会携带 seq，因此按「带 seq / 不带 seq」两种
        布局分别尝试，取 size 字段落在数据边界内且一致的那一种，避免解析错位。
        """
        if len(data) < 4:
            return
        b1 = data[1]
        msg_type = (b1 >> 4) & 0x0F
        flags = b1 & 0x0F
        # 尝试两种布局，找出合法的 (offset, size, payload)
        payload = b""
        for has_seq in (True, False):
            offset = 4 + (4 if has_seq else 0)
            if len(data) < offset + 4:
                continue
            size = struct.unpack_from(">I", data, offset)[0]
            if 0 <= size <= len(data) - offset - 4:
                payload = data[offset + 4:offset + 4 + size]
                break
        if not payload:
            DiagLog.shared().write(f"[ASR] 帧解析失败: {data[:32].hex(' ')}")
            return
        DiagLog.shared().write(f"[ASR] handleFrame: msgType={bin(msg_type)} flags={bin(flags)} size={len(payload)}")

        # 错误帧
        if msg_type == 0b1111:
            try:
                err_msg = payload.decode("utf-8", errors="replace")
            except Exception:
                err_msg = "<binary>"
            DiagLog.shared().write(f"[ASR] ❌ 错误帧: {err_msg}")
            self._fail(f"server error: {err_msg}")
            return

        # 结果帧
        if msg_type == 0b1001:
            try:
                obj = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # 非 JSON 结果帧：flags=0b100 时 payload 通常是 X-Api-Request-Id 的
                # UUID 回显（36 字节），属服务端会话标识/异常关联通知，不是结果数据，
                # 按 UUID 识别并以可读形式记录，避免误导性的"JSON 解析失败"
                try:
                    text = payload.decode("utf-8").strip()
                except UnicodeDecodeError:
                    text = ""
                if (
                    len(text) == 36
                    and text.count("-") == 4
                    and all(c in "0123456789abcdefABCDEF-" for c in text)
                ):
                    DiagLog.shared().write(
                        f"[ASR] 会话标识帧 (UUID 回显): {text} —— 服务端回传请求 ID，"
                        "通常伴随会话异常/静音超时，可用此 ID 向火山查询服务端日志"
                    )
                elif text:
                    DiagLog.shared().write(f"[ASR] 非 JSON 结果帧: {text[:200]}")
                else:
                    DiagLog.shared().write(f"[ASR] 非 JSON 结果帧 (binary): {payload[:64].hex(' ')}")
                return
            result = obj.get("result")
            if not isinstance(result, dict):
                DiagLog.shared().write(f"[ASR] 没有 result 字段: {obj}")
                return
            text = str(result.get("text", ""))
            utterances = result.get("utterances") or []
            # 判断是否完成：任一 utterance 的 definite=true
            is_definite = any(isinstance(u, dict) and u.get("definite") is True for u in utterances)
            DiagLog.shared().write(
                f"[ASR] resp: text={text[:80]} definite={is_definite} utter={len(utterances)}"
            )
            if text:
                # 记录最近文本：服务端可能不发 definite 帧直接关闭，
                # 正常关闭时用该文本兜底完成
                self._last_text = text
                self._schedule_partial(text)
            if is_definite:
                # 句子级 final：一个 utterance 结束，不是整个会话结束。
                # 长语音场景下用户可能继续说下一句，会话应等用户主动
                # finish()（按热键/说结束词）后由服务端 1000 正常关闭来结束。
                # _last_text 已在上面更新，这里不结束会话、不关闭 WebSocket。
                DiagLog.shared().write(
                    f"[ASR] 句子级 final（utter={len(utterances)}），会话继续等待用户结束"
                )

    def _build_frame(self, message_type: int, flags: int, seq: Optional[int], payload: bytes, serialization: int = 0) -> bytes:
        """按协议构造二进制帧字节串。

        byte2 高4位=序列化方式（1=JSON 用于启动帧，0=raw 用于音频/结束帧），
        低4位=压缩方式（0=无压缩，与启动帧协商一致即可）。
        """
        frame = bytearray([0x11, ((message_type << 4) | flags) & 0xFF, (serialization << 4) & 0xFF, 0x00])
        # 可选 seq：4 字节大端 Int32
        if seq is not None:
            frame.extend(struct.pack(">i", seq))
        # payload size：4 字节大端 UInt32
        frame.extend(struct.pack(">I", len(payload)))
        # payload 本体
        frame.extend(payload)
        return bytes(frame)

    async def _sender(self) -> None:
        """事件循环内：从队列取音频帧、分配 seq 并发送；收到 sentinel 后发结束帧。

        序列号规则（实测服务端 auto-assign 行为）：启动帧占序列 1，音频包从 2 递增；
        结束帧为负包：flags=0b0011（负包标记 + 带 seq），seq = -next_seq
        （取相反数，官方交互流程：同一请求的多个包序号连续递增，最后一包取相反数）。
        """
        loop = asyncio.get_running_loop()
        while not self._closed:
            # 阻塞取帧放到线程池，避免卡住事件循环
            pcm = await loop.run_in_executor(None, self._audio_queue.get)
            if pcm is None:
                break  # 结束帧 sentinel
            seq = self._next_seq
            self._next_seq += 1
            frame = self._build_frame(message_type=0b0010, flags=0b0001, seq=seq, payload=pcm, serialization=0)
            if self._ws is None or self._closed:
                return
            try:
                await self._ws.send(frame)
            except Exception as e:
                DiagLog.shared().write(f"[ASR] send error: {e}")
                self._fail(f"send failed: {e}")
                return
            if seq == 2:
                DiagLog.shared().write(f"[ASR] 第一个 audio 包 seq=2 发送 ({len(pcm)} bytes)")

        # 发送结束帧（负包：flags=0b0011，seq 取相反数）
        end_seq = -self._next_seq
        DiagLog.shared().write(f"[ASR] 结束帧 seq={end_seq} (nextSeq={self._next_seq})")
        frame = self._build_frame(message_type=0b0010, flags=0b0011, seq=end_seq, payload=b"", serialization=0)
        if self._ws is not None and not self._closed:
            try:
                await self._ws.send(frame)
                DiagLog.shared().write("[ASR] 结束帧已发送")
            except Exception as e:
                DiagLog.shared().write(f"[ASR] 结束帧发送失败: {e}")

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
                DiagLog.shared().write(f"[ASR] on_partial 异常: {e}")

    def _schedule_complete(self, result: Optional[ASRResult]) -> None:
        """调度完成回调。"""
        if self.on_complete:
            try:
                self.on_complete(result)
            except Exception as e:
                DiagLog.shared().write(f"[ASR] on_complete 异常: {e}")

    def _fail(self, message: str) -> None:
        """失败处理：标记状态、调度失败回调、关闭连接。"""
        self._send_finished = True
        self._closed = True
        DiagLog.shared().write(f"[ASR] ❌ fail: {message}")
        self._schedule_complete(None)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._close_ws(), self._loop)
