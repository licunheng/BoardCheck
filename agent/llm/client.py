"""LLM 客户端。

支持 Anthropic Claude 与 OpenAI 兼容 API(通过 base_url 配置)。

设计原则:
- 不强依赖 anthropic/openai SDK,优先使用 HTTP 直接调用,降低 EXE 体积
- 统一接口,内部按 provider 分支
- 失败可重试,带超时控制
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from ..config import LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """LLM 调用响应"""

    text: str
    raw: Dict[str, Any]
    elapsed_seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient:
    """统一的 LLM 客户端(目前支持 Anthropic / OpenAI 兼容)。"""

    def __init__(self, config: LLMConfig):
        self.config = config
        if not config.api_key:
            raise ValueError(
                "未设置 LLM API Key。请设置环境变量 ANTHROPIC_API_KEY 或 OPENAI_API_KEY,"
                "或在 Web UI 启动时填入。"
            )

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """发起一次对话补全。"""
        if self.config.provider == "anthropic":
            return self._call_anthropic(system_prompt, user_prompt, max_tokens, temperature)
        if self.config.provider in ("openai", "openai_compat"):
            return self._call_openai(system_prompt, user_prompt, max_tokens, temperature)
        raise ValueError(f"不支持的 LLM provider: {self.config.provider}")

    def _call_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> LLMResponse:
        base = (self.config.base_url or "https://api.anthropic.com").rstrip("/")
        # base_url 约定为根地址(如 http://host:3000);若已含完整路径则直接用
        url = base if base.endswith("/v1/messages") else base + "/v1/messages"
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": self.config.temperature if temperature is None else temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        return self._do_request(url, headers, payload)

    def _call_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> LLMResponse:
        url = (self.config.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": self.config.temperature if temperature is None else temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        return self._do_request(url, headers, payload)

    def _do_request(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any]
    ) -> LLMResponse:
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                started = time.time()
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.config.timeout_seconds
                )
                elapsed = time.time() - started
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"LLM API 返回 {resp.status_code}: {resp.text[:500]}"
                    )
                data = resp.json()
                text = self._extract_text(data)
                usage = data.get("usage", {})
                return LLMResponse(
                    text=text,
                    raw=data,
                    elapsed_seconds=elapsed,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
            except Exception as e:
                last_err = e
                logger.warning("LLM 调用失败 (尝试 %d/3): %s", attempt + 1, e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"LLM 调用失败,已重试 3 次: {last_err}")

    def _extract_text(self, data: Dict[str, Any]) -> str:
        """从不同 provider 的响应中抽取文本。"""
        # Anthropic
        if "content" in data and isinstance(data["content"], list):
            parts = data["content"]
            return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        # OpenAI
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        return json.dumps(data, ensure_ascii=False)


def create_default_client() -> Optional[LLMClient]:
    """根据配置创建默认客户端(若 API Key 缺失则返回 None)。"""
    from ..config import get_config

    cfg = get_config().llm
    if not cfg.api_key:
        return None
    return LLMClient(cfg)