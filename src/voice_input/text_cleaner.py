"""文本清洗模块。

对应 macOS 原项目 TextCleaner.swift：
- 移除 ASR 常见中文口头禅
- 压缩多余空白
- strip 两端

后续可替换为 LLM 增强（如加标点、口语化转书面语）。
"""

from __future__ import annotations

import re

# ASR 常见中文口头禅列表
_FILLERS = ["嗯", "啊", "呃", "那个", "这个", "就是说", "然后"]

# 多余空白匹配正则（编译一次复用）
_MULTI_SPACE_RE = re.compile(r"\s+")


def basic_cleanup(text: str) -> str:
    """基础清洗：删除口头禅，压缩空白，strip 两端。"""
    if not text:
        return ""
    result = text
    # 删除每个口头禅
    for filler in _FILLERS:
        result = result.replace(filler, "")
    # 压缩多余空白为单个空格
    result = _MULTI_SPACE_RE.sub(" ", result)
    # 去除两端空白
    return result.strip()
