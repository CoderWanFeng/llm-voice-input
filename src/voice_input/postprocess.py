"""PostProcessor 文本加工（动作 A7）。

MVP 只做规则替换：术语表 + 空白规整。LLM 润色留到后面，
因为它会改动用户原话，需要先验证是否真的受欢迎。
"""

from __future__ import annotations

import logging
import re
from typing import Mapping

logger = logging.getLogger("voice_input.postprocess")

_WS = re.compile(r"[ \t\u3000]+")


def polish(text: str, glossary: Mapping[str, str] | None = None) -> str:
    """按术语表替换并规整空白。术语冲突时以更长的词优先，避免短词截断长词。"""
    if not text:
        return ""

    out = text.strip()
    if glossary:
        for src in sorted(glossary.keys(), key=len, reverse=True):
            dst = glossary[src]
            if src and src != dst:
                out = out.replace(src, dst)

    out = _WS.sub(" ", out)
    return out.strip()


def load_glossary(path: str) -> dict[str, str]:
    """读取术语表（每行 "原文=替换"）。文件不存在或损坏时返回空表。"""
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:
        logger.warning("术语表读取失败：%s", exc)
        return {}
