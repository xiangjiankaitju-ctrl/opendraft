#!/usr/bin/env python3
"""
Unified LLM provider interface.

Provides a single factory + response shape so the pipeline can switch among
Gemini / OpenAI / Claude / Groq without changing agent execution code.
"""

from dataclasses import dataclass
from typing import Any, Optional

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

        text = (response.choices[0].message.content or "") if response.choices else ""
        usage = response.usage
        usage_meta = UsageMetadata(
            prompt_token_count=getattr(usage, "prompt_tokens", 0) or 0,
            candidates_token_count=getattr(usage, "completion_tokens", 0) or 0,
            total_token_count=getattr(usage, "total_tokens", 0) or 0,
        )
        candidates = [Candidate(content=Content(parts=[Part(text=text)]), finish_reason="STOP")]
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
        candidates = [Candidate(content=Content(parts=[Part(text=text)]), finish_reason="STOP")]
        return UnifiedResponse(text=text, usage_metadata=usage_meta, candidates=candidates)


def create_llm_model(model_override: Optional[str] = None) -> Any:
    """Create model adapter by configured provider using one unified interface."""
    cfg = get_config()
    provider = cfg.model.provider
    model_name = model_override or cfg.model.model_name

    if provider == "gemini":
        return create_gemini_client(
            api_key=cfg.google_api_key,
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

    raise ValueError(f"Unsupported AI_PROVIDER: {provider}")
