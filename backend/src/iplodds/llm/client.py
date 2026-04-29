"""LLM client wrapping GitHub Models (OpenAI-compatible) and Azure OpenAI.

Uses the official `openai` SDK pointed at the GitHub Models endpoint when
LLM_PROVIDER=github. Falls back to Azure OpenAI when configured.

Token comes from Key Vault via env injection — never logged, never echoed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import structlog
from openai import AsyncAzureOpenAI, AsyncOpenAI

from iplodds.config import get_settings

log = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI | AsyncAzureOpenAI | None:
    s = get_settings()
    if s.llm_provider == "github":
        if not s.github_models_token:
            log.warning("llm.no_token", provider="github")
            return None
        return AsyncOpenAI(api_key=s.github_models_token, base_url=s.github_models_endpoint)
    if s.llm_provider == "azure_openai":
        if not s.azure_openai_endpoint or not s.azure_openai_deployment:
            log.warning("llm.misconfigured", provider="azure_openai")
            return None
        # Use Managed Identity in production; fall back to env-based key only in dev.
        from azure.identity.aio import DefaultAzureCredential
        cred = DefaultAzureCredential()

        async def _token_provider() -> str:
            tok = await cred.get_token("https://cognitiveservices.azure.com/.default")
            return tok.token

        return AsyncAzureOpenAI(
            azure_endpoint=s.azure_openai_endpoint,
            api_version=s.azure_openai_api_version,
            azure_ad_token_provider=_token_provider,
        )
    return None


def model_name() -> str:
    s = get_settings()
    if s.llm_provider == "github":
        return s.github_models_model
    if s.llm_provider == "azure_openai":
        return s.azure_openai_deployment
    return ""


async def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 800,
    response_format: dict[str, str] | None = None,
) -> Any:
    client = get_client()
    if client is None:
        raise RuntimeError("LLM not configured")
    kwargs: dict[str, Any] = {
        "model": model_name(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format
    return await client.chat.completions.create(**kwargs)
