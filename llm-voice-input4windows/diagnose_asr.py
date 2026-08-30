"""ASR 鉴权与资源 ID 探测脚本。

用途：依次用 4 个候选资源 ID 连接火山引擎 WebSocket，自动找出账号开通的是哪个，
并打印每次握手失败时的详细响应体（错误原因）。

运行方式：
    set PYTHONPATH=src
    python diagnose_asr.py
"""

import asyncio
import os
import sys
import uuid
from typing import List, Tuple

# 让脚本能找到 voice_input 包
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import websockets
from websockets.exceptions import InvalidStatus

from voice_input.config import load
from voice_input.diag_log import DiagLog

# 火山引擎大模型流式 ASR WebSocket 端点
# 注意：bigmodel_async 支持 1.0 和 2.0 资源；bigmodel 只支持 1.0
# 应用代码用的是 bigmodel_async，诊断脚本与之保持一致
_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

# 4 个候选资源 ID（用户已开通 2.0，所以把 2.0 排在前面优先尝试）
_CANDIDATE_RESOURCE_IDS: List[str] = [
    "volc.seedasr.sauc.duration",    # 豆包流式 ASR 2.0 小时版（用户已开通，优先尝试）
    "volc.seedasr.sauc.concurrent",  # 豆包流式 ASR 2.0 并发版
    "volc.bigasr.sauc.duration",     # 豆包流式 ASR 1.0 小时版（默认）
    "volc.bigasr.sauc.concurrent",   # 豆包流式 ASR 1.0 并发版
]


async def try_connect(app_id: str, access_token: str, resource_id: str) -> Tuple[bool, str]:
    """用指定凭证和资源 ID 尝试 WebSocket 握手。

    返回元组 (是否成功, 描述信息)：
    - 成功：(True, "✅ WebSocket 握手成功！")
    - 失败：(False, "HTTP 状态码: 响应体") 或异常描述
    """
    # 构造鉴权 Headers（同时包含新旧版所有可能的字段，最大化通过率）
    headers = {
        "X-Api-Resource-Id": resource_id,
        "X-Api-Connect-Id": str(uuid.uuid4()),
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1",
    }
    if access_token:
        # 旧版鉴权：APP ID + Access Token
        headers["X-Api-App-Key"] = app_id
        headers["X-Api-Access-Key"] = access_token
    else:
        # 新版鉴权：仅需 APP Key
        headers["X-Api-Key"] = app_id

    try:
        async with websockets.connect(
            _ENDPOINT, additional_headers=headers, ping_interval=None
        ) as ws:
            return True, "✅ WebSocket 握手成功"
    except InvalidStatus as e:
        # 提取响应体作为错误信息
        body = ""
        try:
            if e.response.body:
                body = e.response.body.decode("utf-8", errors="replace")
        except Exception:
            body = "(无法解码)"
        return False, f"HTTP {e.response.status_code}: {body}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def main() -> None:
    """主流程：加载凭证并依次尝试 4 个候选资源 ID。"""
    cfg = load()
    print("=" * 60)
    print("配置加载结果：")
    print(f"  app_id       : {cfg.app_id!r}")
    print(f"  access_token : {cfg.access_token!r}")
    print(f"  is_configured: {cfg.is_configured}")
    print("=" * 60)

    if not cfg.is_configured:
        print("\n❌ 凭证未配置，无法测试")
        return

    auth_mode = "旧版 X-Api-App-Key + X-Api-Access-Key" if cfg.access_token else "新版 X-Api-Key"
    print(f"\n鉴权模式：{auth_mode}")
    print(f"将依次尝试 {len(_CANDIDATE_RESOURCE_IDS)} 个候选资源 ID：\n")

    success_resource: str = ""

    for idx, rid in enumerate(_CANDIDATE_RESOURCE_IDS, 1):
        print(f"[{idx}/{len(_CANDIDATE_RESOURCE_IDS)}] 资源 ID: {rid}")
        ok, msg = await try_connect(cfg.app_id, cfg.access_token, rid)
        print(f"  结果: {msg}\n")
        if ok and not success_resource:
            success_resource = rid

    print("=" * 60)
    if success_resource:
        print(f"\n🎉 找到可用资源 ID：{success_resource}")
        print("\n设置环境变量后重启应用即可：")
        print(f"  set VOICEINPUT_RESOURCE_ID={success_resource}")
        print(f"  set PYTHONPATH=src")
        print(f"  python -m voice_input")
    else:
        print("\n❌ 4 个候选资源 ID 都不可用")
        print("\n这说明：")
        print("  1. 你的账号可能未开通任何流式 ASR 资源")
        print("  2. 或者开通的是其他不在候选列表中的资源 ID")
        print("\n解决步骤：")
        print("  1. 登录 https://console.volcengine.com/speech/new?projectName=default")
        print("  2. 左侧菜单找「流式语音识别」")
        print("  3. 查看「已开通服务」或「资源管理」")
        print("  4. 找到具体的 resource_id 字符串")
        print("  5. set VOICEINPUT_RESOURCE_ID=找到的资源ID")


if __name__ == "__main__":
    DiagLog.shared().write("[Diag] ASR 资源 ID 探测脚本启动")
    asyncio.run(main())
