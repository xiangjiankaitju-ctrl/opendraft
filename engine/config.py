#!/usr/bin/env python3
"""
ABOUTME: Centralized configuration management for OpenDraft
ABOUTME: Single source of truth for all settings, models, and environment variables
"""

import os
from dataclasses import dataclass, field
from typing import Literal, Optional
from pathlib import Path


# Try to load environment variables from .env files
# Priority: .env.local > .env (local overrides default)
try:
    from dotenv import load_dotenv

    # Get directory where config.py is located and project root
    config_dir = Path(__file__).parent
    project_root = config_dir.parent

    # Load .env first (defaults): support both project root and engine directory
    for env_path in [project_root / '.env', config_dir / '.env']:
        if env_path.exists():
            load_dotenv(env_path)

    # Load .env.local second (overrides, gitignored): same search strategy
    for env_local_path in [project_root / '.env.local', config_dir / '.env.local']:
        if env_local_path.exists():
            load_dotenv(env_local_path, override=True)

except ImportError:
    # dotenv is optional - will use system environment variables
    pass


@dataclass
class ModelConfig:
    """
    Simplified model configuration without hardcoded defaults.
    """
    provider: str = field(default_factory=lambda: os.getenv('AI_PROVIDER', 'generic'))
    model_name: str = field(default_factory=lambda: os.getenv('LLM_MODEL', ''))
    temperature: float = 0.7
    max_output_tokens: Optional[int] = None
    api_key: Optional[str] = None

    def __post_init__(self):
        """Validate that a model name is provided and matches provider prefixes."""
        self.provider = (self.provider or 'generic').strip().lower()

        if not self.model_name:
            raise ValueError("model_name is required (set LLM_MODEL env var or pass explicitly)")

        # Provider-agnostic mode: any model name is allowed when using a custom endpoint.
        # This also prevents accidental failures when AI_PROVIDER is left as "gemini"
        # but LLM_BASE_URL points to a non-Gemini OpenAI-compatible gateway.
        if self.provider == 'generic' or os.getenv('LLM_BASE_URL', '').strip():
            return

        prefixes = {
            'gemini': ['gemini-'],
            'openai': ['gpt-'],
            'claude': ['claude-'],
            'groq': ['meta-llama/', 'llama-', 'openai/gpt-oss-']
        }

        allowed_prefixes = prefixes.get(self.provider, [])
        if allowed_prefixes and not any(self.model_name.startswith(p) for p in allowed_prefixes):
            raise ValueError(f"Invalid {self.provider} model: {self.model_name}. Must start with: {allowed_prefixes}")


@dataclass
class ValidationConfig:
    """Configuration for validation agents (Skeptic, Verifier, Referee, FactCheck)."""
    # Required field: must be specified explicitly or passed via environment
    pro_model_name: str = field(
        default_factory=lambda: os.getenv('PRO_MODEL_NAME') or os.getenv('pro_model_name', '')
    )
    
    # All other options pull from env vars with sensible defaults
    use_pro_model: bool = field(
        default_factory=lambda: os.getenv('USE_PRO_FOR_VALIDATION', 'false').lower() == 'true'
    )
    validate_per_section: bool = field(
        default_factory=lambda: (
            os.getenv('VALIDATE_PER_SECTION') or os.getenv('validate_per_section', 'true')
        ).lower() == 'true'
    )
    enable_factcheck: bool = field(
        default_factory=lambda: (
            os.getenv('ENABLE_FACTCHECK')
            or os.getenv('ENABLE_FACTCHECKING')
            or os.getenv('enable_factchecking', 'true')
        ).lower() == 'true'
    )

    def __post_init__(self):
        """Validate that a pro model name is provided if pro mode is requested."""
        if self.use_pro_model and not self.pro_model_name:
            raise ValueError("pro_model_name is required when use_pro_model is enabled.")

    def get_validation_model(self, base_model: str) -> str:
        """Return appropriate model for validation tasks."""
        return self.pro_model_name if self.use_pro_model else base_model


@dataclass
class PathConfig:
    """Path configuration for outputs and prompts."""
    project_root: Path = field(default_factory=lambda: Path(__file__).parent)
    output_dir: Path = field(default_factory=lambda: Path('tests/outputs'))
    prompts_dir: Path = field(default_factory=lambda: Path('prompts'))

    def __post_init__(self):
        """Ensure paths are absolute."""
        self.output_dir = self.project_root / self.output_dir
        self.prompts_dir = self.project_root / self.prompts_dir


@dataclass
class AppConfig:
    """
    Application-wide configuration (Provider-Agnostic Mode).

    Enforces usage of a single OpenAI-compatible endpoint for all tasks.
    """
    # Mandatory Generic Endpoint Configuration
    llm_base_url: str = field(default_factory=lambda: os.getenv('LLM_BASE_URL', '').strip())
    llm_api_key: str = field(default_factory=lambda: os.getenv('LLM_API_KEY', '').strip())
    llm_api_path: str = field(default_factory=lambda: os.getenv('LLM_API_PATH', '/chat/completions').strip())

    # Sub-configurations
    model: ModelConfig = field(default_factory=ModelConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    # Citation and paper settings
    citation_style: str = field(default_factory=lambda: os.getenv('CITATION_STYLE', 'apa'))
    ai_detection_threshold: float = field(default_factory=lambda: float(os.getenv('AI_DETECTION_THRESHOLD', '0.20')))

    def validate_api_keys(self) -> None:
        """
        Validate that the generic LLM endpoint is configured.
        """
        if not self.llm_base_url:
            raise ValueError("LLM_BASE_URL is required for the provider-agnostic mode.")
        if not self.llm_api_key:
            raise ValueError("LLM_API_KEY is required for the provider-agnostic mode.")
        if not self.model.model_name:
            raise ValueError("LLM_MODEL (model_name) must be specified for the generic provider.")

    @property
    def has_api_key(self) -> bool:
        """Check if the generic endpoint and key are present."""
        return bool(self.llm_base_url and self.llm_api_key and self.model.model_name)

# Global configuration instance - lazy loaded
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """
    Get the global configuration instance (lazy loaded).

    Returns:
        AppConfig: The application configuration
    """
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def update_model(model_name: str) -> None:
    """
    Update the model name at runtime.

    Args:
        model_name: New model name to use
    """
    cfg = get_config()
    cfg.model.model_name = model_name
    cfg.model.__post_init__()  # Re-validate


if __name__ == '__main__':
    # Configuration validation test
    cfg = get_config()
    print(f"✅ Configuration loaded successfully")
    print(f"Model: {cfg.model.model_name}")
    print(f"Provider: {cfg.model.provider}")
    print(f"API Key configured: {cfg.has_api_key}")
    print(f"Validation per section: {cfg.validation.validate_per_section}")
    print(f"Output directory: {cfg.paths.output_dir}")
