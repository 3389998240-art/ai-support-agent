from __future__ import annotations

import json
import re
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 各家 OpenAI 兼容网关的默认地址
DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
}


class LLMClient:
    """对 LangChain ChatModel 的薄封装。

    设计原则：
    1. 任何外部调用失败都不允许把异常抛进业务图，一律降级为“空结果”，
       由调用方走确定性兜底（抽取式回答 / 规则引擎）。
    2. 未配置密钥时 enabled=False，整条链路完全不依赖网络。
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._model: Any = None
        self.degraded = False

    # ------------------------------------------------------------------ 基础
    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    @property
    def provider(self) -> str:
        return self.settings.llm_provider

    def _build(self) -> Any:
        if self._model is not None:
            return self._model

        from langchain_openai import ChatOpenAI

        provider = self.settings.llm_provider
        base_url = self.settings.llm_base_url or DEFAULT_BASE_URLS.get(provider, "")

        self._model = ChatOpenAI(
            model=self.settings.llm_model,
            api_key=self.settings.llm_api_key or "not-needed",
            base_url=base_url or None,
            temperature=self.settings.llm_temperature,
            timeout=self.settings.llm_timeout,
            max_tokens=self.settings.llm_max_tokens,
        )
        return self._model

    @staticmethod
    def _as_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):  # 多模态返回
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts).strip()
        return str(content).strip()

    # ------------------------------------------------------------------ 调用
    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """返回模型文本；不可用或失败时返回空字符串。"""
        if not self.enabled:
            return ""

        try:
            model = self._build()
            if temperature is not None or max_tokens is not None:
                model = model.bind(
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            message = model.invoke([("system", system), ("human", user)])
            return self._as_text(message)
        except Exception as exc:  # noqa: BLE001 - 外部依赖失败一律降级
            self.degraded = True
            logger.warning("LLM 调用失败，降级为确定性链路: %s", exc)
            return ""

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
    ) -> dict[str, Any] | None:
        """要求模型输出 JSON 并解析；失败返回 None。"""
        raw = self.complete(system, user, temperature=temperature)
        if not raw:
            return None

        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("LLM 返回非法 JSON: %s", raw[:200])
            return None
        return parsed if isinstance(parsed, dict) else None


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
