#!/usr/bin/env python3
"""
Unified LLM provider interface.

Provides a single factory + response shape so the pipeline can switch among
Gemini / OpenAI / Claude / Groq without changing agent execution code.
"""

from dataclasses import dataclass
from typing import Any, Optional
import requests
import os

from config import get_config
from utils.gemini_client import create_gemini_client
from utils.groq_adapter import GroqModel


@dataclass
class UsageMetadata:
    prompt_token_count: int = 0
    candidates_token_count: int = 0
    total_token_count: int = 0


@dataclass
class Part:
    text: str
    function_call: Optional[object] = None


@dataclass
class Content:
    parts: list


@dataclass
class Candidate:
    content: Content
    finish_reason: str = "STOP"


@dataclass
class UnifiedResponse:
    text: str
    usage_metadata: UsageMetadata
    candidates: list


def normalize_finish_reason(reason: Any) -> str:
    """Normalize provider-specific finish reason to uppercase string."""
    if reason is None:
        return ""
    if isinstance(reason, int):
        # Gemini numeric enums
        mapping = {
            0: "UNSPECIFIED",
            1: "STOP",
            2: "SAFETY",
            3: "MAX_TOKENS",
            4: "RECITATION",
            10: "FUNCTION_CALL",
        }
        return mapping.get(reason, str(reason))
    return str(reason).strip().upper()


def is_stop_finish_reason(reason: Any) -> bool:
    r = normalize_finish_reason(reason)
    return r in {"", "STOP", "UNSPECIFIED", "FINISH_REASON_UNSPECIFIED"}


def is_safety_block_finish_reason(reason: Any) -> bool:
    r = normalize_finish_reason(reason)
    return r in {"SAFETY", "CONTENT_FILTER", "BLOCKED"}


def is_function_call_finish_reason(reason: Any) -> bool:
    r = normalize_finish_reason(reason)
    return r in {"FUNCTION_CALL", "TOOL_CALL", "TOOL_CALLS"}


class GenericOpenAICompatibleModelAdapter:
    """Generic HTTP adapter for OpenAI-compatible chat/completions endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        temperature: float = 0.7,
        api_path: str = "/chat/completions",
        timeout_seconds: int = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name
        self.temperature = temperature
        self.api_path = api_path if api_path.startswith("/") else f"/{api_path}"
        self.timeout_seconds = timeout_seconds

    def generate_content(self, prompt: Any, generation_config: Any = None, safety_settings: Any = None) -> UnifiedResponse:
        _ = safety_settings
        cfg = generation_config or {}
        temperature = cfg.get("temperature", self.temperature) if isinstance(cfg, dict) else self.temperature
        max_tokens = cfg.get("max_output_tokens") if isinstance(cfg, dict) else None

        if isinstance(prompt, list):
            prompt_text = "\n".join(str(p) for p in prompt)
        else:
            prompt_text = str(prompt)

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt_text}],
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        url = f"{self.base_url}{self.api_path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            body_preview = ""
            try:
                body_preview = response.text[:1000]
            except Exception:
                body_preview = "<unavailable>"
            raise requests.HTTPError(
                f"{e} | provider={self.base_url} | model={self.model_name} | body={body_preview}",
                response=response,
            ) from e
        data = response.json()

        content = ""
        function_call_obj = None
        finish_reason = "STOP"
        choices = data.get("choices", [])
        if choices:
            first_choice = choices[0]
            message = first_choice.get("message", {})
            finish_reason = first_choice.get("finish_reason", "STOP")
            raw_content = message.get("content", "")
            if isinstance(raw_content, str):
                content = raw_content
            elif isinstance(raw_content, list):
                text_parts = []
                for item in raw_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(str(item.get("text", "")))
                content = "\n".join(text_parts).strip()
            else:
                content = str(raw_content)
            tool_calls = message.get("tool_calls")
            if tool_calls:
                function_call_obj = tool_calls[0]

        usage = data.get("usage", {})
        usage_meta = UsageMetadata(
            prompt_token_count=usage.get("prompt_tokens", 0) or 0,
            candidates_token_count=usage.get("completion_tokens", 0) or 0,
            total_token_count=usage.get("total_tokens", 0) or 0,
        )

        candidates = [
            Candidate(
                content=Content(parts=[Part(text=content, function_call=function_call_obj)]),
                finish_reason=finish_reason,
            )
        ]
        return UnifiedResponse(text=content, usage_metadata=usage_meta, candidates=candidates)


class OpenAIModelAdapter:
    """OpenAI Chat Completions adapter with Gemini-like generate_content()."""

    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model_name = model_name
        self.temperature = temperature

    def generate_content(self, prompt: Any, generation_config: Any = None, safety_settings: Any = None) -> UnifiedResponse:
        _ = safety_settings
        cfg = generation_config or {}
        temperature = cfg.get("temperature", self.temperature) if isinstance(cfg, dict) else self.temperature
        max_tokens = cfg.get("max_output_tokens") if isinstance(cfg, dict) else None

        if isinstance(prompt, list):
            prompt_text = "\n".join(str(p) for p in prompt)
        else:
            prompt_text = str(prompt)

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

        text = ""
        function_call_obj = None
        finish_reason = "STOP"
        if response.choices:
            choice = response.choices[0]
            finish_reason = getattr(choice, "finish_reason", "STOP")
            msg = choice.message
            text = (getattr(msg, "content", "") or "")
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                function_call_obj = tool_calls[0]
        usage = response.usage
        usage_meta = UsageMetadata(
            prompt_token_count=getattr(usage, "prompt_tokens", 0) or 0,
            candidates_token_count=getattr(usage, "completion_tokens", 0) or 0,
            total_token_count=getattr(usage, "total_tokens", 0) or 0,
        )
        candidates = [
            Candidate(
                content=Content(parts=[Part(text=text, function_call=function_call_obj)]),
                finish_reason=finish_reason,
            )
        ]
        return UnifiedResponse(text=text, usage_metadata=usage_meta, candidates=candidates)


class ClaudeModelAdapter:
    """Anthropic Claude adapter with Gemini-like generate_content()."""

    def __init__(self, api_key: str, model_name: str, temperature: float = 0.7):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model_name = model_name
        self.temperature = temperature

    def generate_content(self, prompt: Any, generation_config: Any = None, safety_settings: Any = None) -> UnifiedResponse:
        _ = safety_settings
        cfg = generation_config or {}
        temperature = cfg.get("temperature", self.temperature) if isinstance(cfg, dict) else self.temperature
        max_tokens = cfg.get("max_output_tokens", 8192) if isinstance(cfg, dict) else 8192

        if isinstance(prompt, list):
            prompt_text = "\n".join(str(p) for p in prompt)
        else:
            prompt_text = str(prompt)

        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt_text}],
        )

        text_parts = []
        finish_reason = getattr(response, "stop_reason", "STOP") or "STOP"
        for block in (response.content or []):
            block_text = getattr(block, "text", None)
            if block_text:
                text_parts.append(block_text)
        text = "\n".join(text_parts).strip()

        usage = getattr(response, "usage", None)
        usage_meta = UsageMetadata(
            prompt_token_count=getattr(usage, "input_tokens", 0) if usage else 0,
            candidates_token_count=getattr(usage, "output_tokens", 0) if usage else 0,
            total_token_count=(
                (getattr(usage, "input_tokens", 0) or 0)
                + (getattr(usage, "output_tokens", 0) or 0)
            )
            if usage
            else 0,
        )
        candidates = [Candidate(content=Content(parts=[Part(text=text)]), finish_reason=finish_reason)]
        return UnifiedResponse(text=text, usage_metadata=usage_meta, candidates=candidates)


def create_llm_model(model_override: Optional[str] = None) -> Any:
    """Create model adapter by configured provider using one unified interface."""
    cfg = get_config()
    provider = cfg.model.provider
    model_name = model_override or cfg.model.model_name

    llm_base_url = getattr(cfg, "llm_base_url", "")
    llm_api_key = getattr(cfg, "llm_api_key", "")
    llm_api_path = getattr(cfg, "llm_api_path", "/chat/completions")

    # Provider-agnostic endpoint mode: only needs URL + model + API key.
    if llm_base_url and llm_api_key:
        return GenericOpenAICompatibleModelAdapter(
            base_url=llm_base_url,
            api_key=llm_api_key,
            model_name=model_name,
            temperature=cfg.model.temperature,
            api_path=llm_api_path,
        )

    if provider == "gemini":
        gemini_api_key = (
            getattr(cfg, "google_api_key", "")
            or os.getenv("GEMINI_API_KEY", "")
            or os.getenv("GOOGLE_API_KEY", "")
            or getattr(cfg, "llm_api_key", "")
        )
        return create_gemini_client(
            api_key=gemini_api_key,
            model_name=model_name,
            temperature=cfg.model.temperature,
        )

    if provider == "openai":
        return OpenAIModelAdapter(
            api_key=cfg.openai_api_key,
            model_name=model_name,
            temperature=cfg.model.temperature,
        )

    if provider == "claude":
        return ClaudeModelAdapter(
            api_key=cfg.anthropic_api_key,
            model_name=model_name,
            temperature=cfg.model.temperature,
        )

    if provider == "groq":
        return GroqModel(
            model_name=model_name,
            api_key=cfg.groq_api_key,
            temperature=cfg.model.temperature,
        )

    if provider == "generic":
        raise ValueError("AI_PROVIDER=generic requires LLM_BASE_URL and LLM_API_KEY")

    raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
