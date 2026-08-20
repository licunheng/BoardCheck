"""LLM 输出解析器。

LLM 输出可能包含:
- 纯 JSON
- JSON 被 markdown ``` 包围
- JSON 前后有自然语言说明

采用"找到第一个 { 与最后一个 } 之间的内容"作为 JSON 候选,
再 json.loads 解析。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出文本中解析 JSON。

    兼容:
    - ```json ... ``` 包裹
    - 前后有自然语言说明
    - 多个 JSON 块(取第一个)
    """
    if not text:
        return None

    # 1. 尝试直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 2. 剥离 markdown 包裹
    stripped = _strip_markdown_codeblock(text)
    try:
        return json.loads(stripped.strip())
    except json.JSONDecodeError:
        pass

    # 3. 抽取第一个 { ... } 块
    m = _JSON_OBJECT_RE.search(text)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # 4. 尝试修复常见的 JSON 错误(尾随逗号等)
            fixed = _try_fix_json(candidate)
            if fixed:
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    pass

    logger.warning("无法从 LLM 输出解析 JSON:\n%s", text[:500])
    return None


def _strip_markdown_codeblock(text: str) -> str:
    """去除 markdown ```json ... ``` 包裹。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉首行的 ```json 或 ```
        first_newline = text.find("\n")
        if first_newline > 0:
            text = text[first_newline + 1:]
        # 去掉末尾的 ```
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _try_fix_json(text: str) -> Optional[str]:
    """尝试修复常见的 JSON 格式问题。"""
    # 1. 去除尾部逗号
    fixed = re.sub(r",(\s*[}\]])", r"\1", text)
    # 2. 替换单引号为双引号(若 key/value 不含双引号)
    if '"' not in fixed:
        fixed = fixed.replace("'", '"')
    return fixed