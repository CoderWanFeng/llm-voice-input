"""LLM 文本润色模块（OpenAI 兼容接口）。

在 ASR 识别完成后调用大模型做后处理，解决三类问题：
1. 发音不标准导致的专有名词错误（如「long chat」→「LangChain」）
2. 口误/停顿导致的错误断词（如「百度、OCR」→「百度OCR」）
3. 多要点内容按要点边界换行（而非每个句号都换行）

接口约定：兼容 OpenAI Chat Completions 协议的服务均可使用，
如阿里云百炼 DashScope、DeepSeek、智谱、OpenAI 等，
只需配置 base_url + api_key + model。

调用在后台线程执行（见 app.py），失败/超时由调用方回退原始文本。
"""

from __future__ import annotations

from typing import List

import requests

from .diag_log import DiagLog


# 默认请求超时（秒）。
# 30 秒兜底：正常关闭思考后 5-6s 返回，但留足余量应对网络抖动与长文本。
# 超时后自动回退原文注入，不会丢失识别结果。
_DEFAULT_TIMEOUT = 30

# 纠错与排版系统提示词
_SYSTEM_PROMPT = (
    "你是一个语音识别（ASR）结果纠错与排版助手。用户输入的文本来自语音识别，"
    "常见问题包括：\n"
    "1. 发音不标准导致的专有名词错误（例如「long chat」应为「LangChain」，"
    "「open o i」应为「OpenAI」）\n"
    "2. 口误或停顿导致的错误断词与标点（例如「百度、OCR」应为「百度OCR」）\n"
    "3. 同音字/近音字错误\n\n"
    "请严格遵守以下规则：\n"
    "- 修正上述错误，恢复正确的专有名词写法与标点\n"
    "- 删除口头禅（嗯、啊、呃、就是说等）与重复词\n"
    "- 若内容包含多个要点，仅在要点之间用换行分隔；"
    "单个要点内部不要换行，没有明显多个要点时不要换行\n"
    "- 不要改写句式、不要增删内容、不要回答问题、不要解释\n"
    "- 只输出修正后的文本，不要任何前后缀、引号或代码块"
)


def polish_text(
    text: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """调用 OpenAI 兼容接口润色 ASR 文本。

    Args:
        text: 待润色的 ASR 原始文本
        base_url: 接口基础地址（如 https://dashscope.aliyuncs.com/compatible-mode/v1）
        api_key: API Key（Bearer 认证）
        model: 模型名（如 qwen3.7-plus）
        timeout: 请求超时秒数

    Returns:
        润色后的文本；服务端返回空内容时返回原文

    Raises:
        RuntimeError: 网络错误、HTTP 错误或响应格式异常（调用方回退原文）
    """
    if not text or not text.strip():
        return text
    if not base_url or not api_key or not model:
        raise RuntimeError("LLM 配置不完整（base_url / api_key / model）")

    # 规范化 URL：去掉末尾斜杠；若误填到 /chat/completions 则截去，避免拼出重复路径
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url += "/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        # 低温度：纠错任务要求稳定输出，不要发散
        "temperature": 0.1,
        "stream": False,
    }
    # 关闭思考模式：qwen3 / qwen3.7 等思考型模型默认会进行长推理（30s+），
    # 导致请求超时。纠错任务不需要推理过程，关闭后 5-6s 即可返回。
    # 该参数为 DashScope 扩展，对其他 OpenAI 兼容服务通常会被忽略。
    if _is_dashscope(url):
        payload["enable_thinking"] = False
        DiagLog.shared().write("[LLM] 检测到 DashScope，已关闭思考模式")

    DiagLog.shared().write(f"[LLM] 请求润色: model={model} url={url} 文本长度={len(text)}")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(f"LLM 请求失败: {e}") from e

    # HTTP 错误：附带响应体片段写入日志，方便排查 Key/模型名错误
    if resp.status_code != 200:
        body = resp.text[:200].replace("\n", " ")
        raise RuntimeError(f"LLM HTTP {resp.status_code}: {body}")

    try:
        data = resp.json()
        content: str = data["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"LLM 响应格式异常: {e}") from e

    result = _strip_code_fence(content.strip())
    DiagLog.shared().write(f"[LLM] 润色完成: {result[:80]}")
    # 空响应兜底：返回原文，避免注入空文本
    return result if result else text


def _strip_code_fence(content: str) -> str:
    """去除模型偶发的 Markdown 代码块包裹。

    部分模型即使被要求不要用代码块，也可能输出 ```文本``` 形式，
    这里做一层保险剥离。

    Args:
        content: 模型返回的文本

    Returns:
        去除首尾代码围栏后的文本
    """
    lines = content.splitlines()
    # 找到非空首行与尾行，若都是 ``` 围栏则去掉
    if len(lines) >= 2:
        first = next((l for l in lines if l.strip()), "")
        last = next((l for l in reversed(lines) if l.strip()), "")
        if first.strip().startswith("```") and last.strip() == "```":
            # 去掉首行（可能带语言名）与最后一个围栏行
            lines = lines[1:] if lines[0].strip().startswith("```") else lines
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].strip() == "```":
                    del lines[i]
                    break
    return "\n".join(lines).strip()


def _is_dashscope(url: str) -> bool:
    """判断 URL 是否指向阿里云 DashScope 服务。

    DashScope 的 qwen3/qwen3.7 等思考型模型支持 enable_thinking 参数
    关闭长推理过程，纠错任务不需要推理，关闭后可显著缩短响应时间。

    Args:
        url: 规范化后的完整请求 URL

    Returns:
        True 表示该 URL 指向 DashScope，应附加 enable_thinking=False
    """
    return "dashscope.aliyuncs.com" in url
