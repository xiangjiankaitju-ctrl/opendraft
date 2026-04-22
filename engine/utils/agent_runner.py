#!/usr/bin/env python3
"""
ABOUTME: Production agent utilities for draft generation
ABOUTME: Core functions for model setup, agent execution, and citation research

Extracted from tests/test_utils.py for proper production use.
These are the essential utilities needed by draft_generator.py and modal_worker.py.
"""

import sys
import time
import logging
import os
import json
from pathlib import Path
from typing import Optional, Callable, Tuple, List, TYPE_CHECKING, Any, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

# Safe print function that handles broken pipes and respects CLI quiet mode
def safe_print(*args, **kwargs):
    """Print wrapper that catches BrokenPipeError and respects CLI quiet mode."""
    # Check verbosity setting from orchestrator (CLI quiet mode)
    try:
        from utils.api_citations.orchestrator import _verbose_research
        if not _verbose_research:
            return  # Suppress in CLI quiet mode
    except ImportError:
        pass  # If orchestrator not available, continue normally

    try:
        print(*args, **kwargs)
    except (BrokenPipeError, OSError):
        # Pipe is closed (worker running with stdio: 'ignore'), use logger instead
        message = ' '.join(str(arg) for arg in args)
        logger.debug(message)
        # Prevent further broken pipe errors by redirecting stdout
        try:
            sys.stdout = open(os.devnull, 'w')
        except:
            pass


def _build_research_fallback_queries(topic: str, scope: Optional[str] = None) -> List[str]:
    """High-signal deterministic fallback queries for standard-mode degradation.

    Avoid appending meaningless numbers and low-value English suffixes to Chinese topics.
    """
    import re

    topic = (topic or "").strip()
    scope = (scope or "").strip()
    if not topic:
        return []

    queries: List[str] = []

    def add(q: str):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    is_chinese = bool(re.search(r'[\u4e00-\u9fff]', topic + scope))
    add(topic)
    if scope and scope != topic:
        add(f"{topic} {scope}")

    if is_chinese:
        zh_templates = [
            "{topic}",
            "{topic} 实证研究",
            "{topic} 机制研究",
            "{topic} 路径研究",
            "{topic} 评价研究",
            "{topic} 文献综述",
        ]
        for tmpl in zh_templates:
            add(tmpl.format(topic=topic))

    else:
        for suffix in ["empirical study", "literature review", "framework", "policy analysis", "systematic mapping"]:
            add(f"{topic} {suffix}")

    return queries[:20]


def _cap_research_queries(queries: List[str], topic: str, parallel_workers: int) -> List[str]:
    """Cap deep-research query count to match execution capacity.

    Prevents huge plans (e.g., 90 queries) from running for hours in low-concurrency mode.
    """
    import re

    if not queries:
        return []

    is_chinese_topic = bool(re.search(r'[\u4e00-\u9fff]', topic or ""))
    if parallel_workers <= 1:
        limit = 12 if is_chinese_topic else 15
    elif parallel_workers <= 2:
        limit = 20
    else:
        limit = 30

    # Chinese topics need explicit quota protection, otherwise English queries can
    # crowd out Chinese-language coverage and fail the zh coverage gate.
    if is_chinese_topic:
        def _is_chinese_query(q: str) -> bool:
            return bool(re.search(r'[\u4e00-\u9fff]', q or ""))

        def _zh_score(q: str) -> int:
            ql = (q or "").lower()
            score = 0
            if _is_chinese_query(q):
                score += 10
            # High-signal Chinese academic intent
            for kw in ["人工智能", "新质生产力", "实证研究", "机制研究", "路径研究", "文献综述", "影响研究", "中国", "产业", "政策"]:
                if kw in (q or ""):
                    score += 3
            # Reward bilingual bridge queries for cross-lingual retrieval
            if _is_chinese_query(q) and re.search(r'[a-zA-Z]{3,}', q or ""):
                score += 4
            # Penalize pure generic English queries for Chinese topic when quota is tight
            if not _is_chinese_query(q):
                score -= 2
            return score

        ranked = sorted(queries, key=_zh_score, reverse=True)
        chinese_queries = [q for q in ranked if _is_chinese_query(q)]
        bilingual_queries = [q for q in ranked if _is_chinese_query(q) and re.search(r'[a-zA-Z]{3,}', q or "")]
        english_queries = [q for q in ranked if q not in chinese_queries]

        protected: List[str] = []

        def add_unique(items: List[str], max_take: Optional[int] = None) -> None:
            taken = 0
            for item in items:
                if item not in protected:
                    protected.append(item)
                    taken += 1
                    if max_take is not None and taken >= max_take:
                        break

        min_zh = min(limit, max(8, limit // 3))
        min_bilingual = min(len(bilingual_queries), max(3, limit // 6))
        add_unique(chinese_queries, min_zh)
        add_unique(bilingual_queries, min_bilingual)
        add_unique(ranked, limit)
        return protected[:limit]

    capped = queries[:limit]
    return capped


def _build_chinese_coverage_rescue_queries(topic: str, scope: Optional[str] = None) -> List[str]:
    """High-signal Chinese rescue queries used when zh coverage is insufficient."""
    topic = (topic or "").strip()
    scope = (scope or "").strip()
    if not topic:
        return []

    queries: List[str] = []

    def add(q: str) -> None:
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    add(topic)
    if scope and scope != topic:
        add(f"{topic} {scope}")

    for suffix in ["实证研究", "机制研究", "路径研究", "影响研究", "文献综述", "中国", "产业应用", "政策研究"]:
        add(f"{topic} {suffix}")

    return queries[:8]

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from concurrency.concurrency_config import get_concurrency_config
from utils.output_validators import ValidationResult
from utils.api_citations.orchestrator import CitationResearcher
from utils.citation_database import Citation
from utils.llm_provider import (
    create_llm_model,
    is_stop_finish_reason,
    is_function_call_finish_reason,
)
from utils.deep_research import DeepResearchPlanner
from utils.token_tracker import CallStatus

# Configure logging
logger = logging.getLogger(__name__)


def _count_chinese_language_hits(citations: List[Citation]) -> Dict[str, int]:
    """Count Chinese-language coverage in collected citations.

    Uses title language as the primary signal and keeps a secondary signal for
    citations explicitly marked as Chinese language metadata.
    """
    import re

    title_zh_hits = 0
    language_zh_hits = 0

    for citation in citations or []:
        title = (getattr(citation, "title", "") or "").strip()
        language = (getattr(citation, "language", "") or "").lower().strip()

        if re.search(r'[\u4e00-\u9fff]', title):
            title_zh_hits += 1
        if language in {"zh", "zh-cn", "zh-tw", "chinese", "中文"}:
            language_zh_hits += 1

    return {
        "title_zh_hits": title_zh_hits,
        "language_zh_hits": language_zh_hits,
        "effective_zh_hits": max(title_zh_hits, language_zh_hits),
    }


def _count_chinese_primary_hits(citations: List[Citation]) -> Dict[str, int]:
    """Backward-compatible counter for CNKI/Baidu Scholar primary hits.

    Kept for diagnostics/tests compatibility. New quality gates should use
    `_count_chinese_language_hits` instead.
    """
    cnki_hits = 0
    baidu_scholar_hits = 0

    for citation in citations or []:
        publisher = (getattr(citation, "publisher", "") or "").lower()
        url = (getattr(citation, "url", "") or "").lower()

        is_cnki = (
            "cnki" in publisher
            or "中国知网" in publisher
            or "cnki.net" in url
            or "oversea.cnki.net" in url
            or "kns.cnki.net" in url
        )
        is_baidu = (
            "baidu scholar" in publisher
            or "xueshu.baidu" in publisher
            or "xueshu.baidu.com" in url
        )

        if is_cnki:
            cnki_hits += 1
        elif is_baidu:
            baidu_scholar_hits += 1

    return {
        "cnki_hits": cnki_hits,
        "baidu_scholar_hits": baidu_scholar_hits,
        "primary_hits": cnki_hits + baidu_scholar_hits,
    }


def setup_model(model_override: Optional[str] = None) -> Any:
    """
    Initialize and return configured LLM model wrapper.

    Args:
        model_override: Optional model name to override config default

    Returns:
        Model wrapper with generate_content() method

    Raises:
        ValueError: If API key is missing or model name is invalid
    """
    config = get_config()
    config.validate_api_keys()
    return create_llm_model(model_override=model_override)


def load_prompt(prompt_path: str) -> str:
    """
    Load agent prompt from markdown file.

    Args:
        prompt_path: Path to prompt file (relative to project root or absolute)

    Returns:
        str: Content of the prompt file

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    config = get_config()
    path = Path(prompt_path)

    # If relative path, try relative to project root
    if not path.is_absolute():
        path = config.paths.project_root / path

    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def run_agent(
    model: Any,
    name: str,
    prompt_path: str,
    user_input: str,
    save_to: Optional[Path] = None,
    verbose: bool = True,
    validators: Optional[List[Callable[[str], ValidationResult]]] = None,
    max_retries: int = 3,
    skip_validation: bool = False,
    token_tracker: Optional[Any] = None,
    token_stage: Optional[str] = None,
) -> str:
    """
    Run an AI agent with given prompt and input, with optional validation.

    This function is the core execution layer for all agents in the draft pipeline.
    It handles LLM interaction, output validation, retries, and file I/O.

    Args:
        model: Configured model instance
        name: Human-readable name for the agent (for logging)
        prompt_path: Path to agent prompt file
        user_input: User's request/input for the agent
        save_to: Optional path to save output
        verbose: Whether to print progress messages
        validators: Optional list of validation functions to apply to output
        max_retries: Maximum retry attempts if validation fails (default: 3)
        skip_validation: If True, skip all validation checks (for automated runs)

    Returns:
        str: Validated agent output text

    Raises:
        Exception: If agent execution fails or validation fails after all retries
    """
    # Override validators if skip_validation is True
    if skip_validation:
        validators = None
        logger.info(f"Agent '{name}': Validation skipped (skip_validation=True)")
    if verbose:
        safe_print(f"\n{'='*70}")
        safe_print(f"🤖 {name}")
        safe_print(f"{'='*70}")

    # Load agent prompt
    agent_prompt = load_prompt(prompt_path)

    # Combine agent prompt with user input
    full_prompt = f"{agent_prompt}\n\n---\n\nUser Request:\n{user_input}"

    logger.debug(f"Agent '{name}': Starting execution")
    logger.debug(f"Prompt length: {len(full_prompt)} chars")
    logger.debug(f"Validators: {len(validators) if validators else 0}")

    # Initialize output variable with explicit type
    output: str = ""

    # Empty loop detection (V3 feature): exit early if model produces empty output repeatedly
    consecutive_empty_outputs = 0
    MAX_CONSECUTIVE_EMPTY = 3

    # Retry loop with exponential backoff
    for attempt in range(max_retries):
        if verbose and attempt > 0:
            safe_print(f"Retry attempt {attempt}/{max_retries}...", end=' ', flush=True)
        elif verbose:
            safe_print("Generating...", end=' ', flush=True)

        start_time = time.time()

        try:
            # Generate LLM response
            # Note: If model tools (web search/URL context) hit rate limits,
            # we catch the exception and use fallbacks (DataForSEO/OpenPull)
            try:
                response = model.generate_content(full_prompt)
            except Exception as tool_error:
                error_str = str(tool_error)
                # Check if it's a tool rate limit error (429)
                if "429" in error_str or "rate limit" in error_str.lower() or "quota" in error_str.lower():
                    logger.warning(f"Agent '{name}': model tools rate limited - will retry without tools")
                    # Retry without tools (fallback to direct generation)
                    # Note: For web search/URL context, we'd need to manually call fallbacks
                    # This is a simplified retry - full fallback integration would require
                    # detecting which tool failed and calling appropriate fallback
                    response = model.generate_content(full_prompt)
                else:
                    raise  # Re-raise if not a rate limit error
            
            # Handle function calls and other edge cases
            # Check if response has candidates
            if not response.candidates:
                raise ValueError(f"Agent '{name}': No candidates in response (likely blocked)")
            
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            
            # Check what parts exist in the response BEFORE accessing response.text
            # This prevents ValueError when response has function calls or no text parts
            has_text_part = False
            has_function_call = False
            
            if hasattr(candidate, 'content') and candidate.content:
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text:
                        has_text_part = True
                        break
                    if hasattr(part, 'function_call'):
                        has_function_call = True
            
            # If no text part exists, try to extract from parts directly
            output = None
            
            if has_text_part:
                # Safe to use response.text when text part exists
                try:
                    output = str(response.text)
                except ValueError:
                    # Fallback: extract from parts
                    if hasattr(candidate, 'content') and candidate.content:
                        text_parts = [part.text for part in candidate.content.parts if hasattr(part, 'text') and part.text]
                        if text_parts:
                            output = ''.join(text_parts)
            elif has_function_call:
                # Function call detected but no text - this is an error
                raise ValueError(
                    f"Agent '{name}': Response contains function call but no text content. "
                    f"Finish reason: {finish_reason}. "
                    f"This may indicate the model attempted to call a function incorrectly. "
                    f"Try regenerating or check if tools are properly configured."
                )
            else:
                # No text part and no function call - check finish_reason
                if is_stop_finish_reason(finish_reason):  # STOP/UNSPECIFIED but no text
                    raise ValueError(
                        f"Agent '{name}': Response has finish_reason={finish_reason} (STOP-like) but no text parts. "
                        f"This may indicate an empty response or safety filter block."
                    )
                elif is_function_call_finish_reason(finish_reason):
                    raise ValueError(
                        f"Agent '{name}': Response has finish_reason={finish_reason} (FUNCTION_CALL-like) but no text content. "
                        f"This indicates an invalid function call attempt."
                    )
                else:
                    raise ValueError(
                        f"Agent '{name}': No text content in response. "
                        f"Finish reason: {finish_reason}. "
                        f"Response may be blocked or empty."
                    )
            
            if not output:
                raise ValueError(f"Agent '{name}': Unable to extract text from response")
            
            output = str(output)

            # Defense-in-depth: scrub planning preambles, metadata, and cite_MISSING markers
            from utils.text_utils import clean_agent_output
            output = clean_agent_output(output)

            # Empty loop detection (V3 feature): track consecutive empty/trivial outputs
            if len(output.strip()) < 50:  # Effectively empty or trivial
                consecutive_empty_outputs += 1
                logger.warning(
                    f"Agent '{name}': Empty/trivial output detected ({consecutive_empty_outputs}/{MAX_CONSECUTIVE_EMPTY})"
                )
                if consecutive_empty_outputs >= MAX_CONSECUTIVE_EMPTY:
                    logger.error(
                        f"Agent '{name}': Exiting early after {MAX_CONSECUTIVE_EMPTY} consecutive empty outputs"
                    )
                    if verbose:
                        safe_print(f"⚠️ Model stuck - {MAX_CONSECUTIVE_EMPTY} consecutive empty outputs")
                    # Return whatever partial output we have (could be empty)
                    break
                # Continue to retry
                if attempt < max_retries - 1:
                    backoff_seconds = 2 ** attempt
                    time.sleep(backoff_seconds)
                    continue
            else:
                # Reset counter on successful non-empty output
                consecutive_empty_outputs = 0

            logger.debug(f"Agent '{name}': Generated {len(output)} chars in {time.time() - start_time:.1f}s")

            # Track token usage if tracker is provided
            if token_tracker and hasattr(response, 'usage_metadata'):
                try:
                    meta = response.usage_metadata
                    token_tracker.add_call(
                        stage=token_stage or name,
                        input_tokens=getattr(meta, 'prompt_token_count', 0) or 0,
                        output_tokens=getattr(meta, 'candidates_token_count', 0) or 0,
                    )
                except Exception:
                    pass  # Never break generation for tracking failures

            # Validate output if validators provided (and not skipped)
            if validators and not skip_validation:
                validation_passed = True
                for i, validator in enumerate(validators):
                    logger.debug(f"Agent '{name}': Running validator {i+1}/{len(validators)}")
                    result = validator(output)

                    if not result.is_valid:
                        validation_passed = False
                        logger.warning(
                            f"Agent '{name}': Validation failed on attempt {attempt+1}/{max_retries}: "
                            f"{result.error_message}"
                        )

                        if verbose:
                            safe_print(f"⚠️ Validation failed: {result.error_message}")

                        # If not last attempt, retry with backoff
                        if attempt < max_retries - 1:
                            backoff_seconds = 2 ** attempt  # Exponential: 1s, 2s, 4s
                            logger.debug(f"Agent '{name}': Backing off for {backoff_seconds}s")
                            time.sleep(backoff_seconds)
                            break  # Break validator loop to retry LLM call
                        else:
                            # Last attempt failed - raise error
                            error_msg = (
                                f"Agent '{name}' validation failed after {max_retries} attempts: "
                                f"{result.error_message}"
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                    else:
                        logger.debug(f"Agent '{name}': Validator {i+1} passed")

                # If all validators passed, break retry loop
                if validation_passed:
                    logger.info(f"Agent '{name}': All {len(validators)} validators passed")
                    break
            else:
                # No validators - success on first attempt
                logger.debug(f"Agent '{name}': No validators, accepting output")
                break

        except Exception as e:
            if verbose:
                safe_print(f"❌ Error")

            logger.error(f"Agent '{name}': Exception on attempt {attempt+1}: {str(e)}")

            # Track failed call if tracker is provided
            if token_tracker:
                try:
                    token_tracker.add_call(
                        stage=token_stage or name,
                        input_tokens=0,
                        output_tokens=0,
                        status=CallStatus.FAILURE,
                        error_message=str(e),
                    )
                except Exception:
                    pass  # Never break generation for tracking failures

            # If not last attempt and it's a transient error, retry
            if attempt < max_retries - 1 and _is_transient_error(e):
                backoff_seconds = 2 ** attempt
                logger.debug(f"Agent '{name}': Transient error, retrying after {backoff_seconds}s")
                time.sleep(backoff_seconds)
                continue
            else:
                # Partial output capture (V3 feature): on timeout, check for any files written
                if save_to and ('timeout' in str(e).lower() or 'timed out' in str(e).lower()):
                    partial_output = _capture_partial_output(save_to, name)
                    if partial_output:
                        logger.warning(
                            f"Agent '{name}': Timeout, but captured partial output ({len(partial_output)} chars)"
                        )
                        if verbose:
                            safe_print(f"⚠️ Timeout - captured {len(partial_output)} chars of partial output")
                        output = partial_output
                        break  # Exit retry loop with partial output
                raise Exception(f"Agent '{name}' execution failed: {str(e)}") from e

    elapsed = time.time() - start_time

    # Save output if path provided
    if save_to:
        try:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            with open(save_to, 'w', encoding='utf-8') as f:
                f.write(output)

            # Verify file was created successfully
            if not save_to.exists():
                raise IOError(f"Output file not created: {save_to}")

            file_size = save_to.stat().st_size
            if file_size == 0:
                raise IOError(f"Output file is empty: {save_to}")

            logger.info(f"Agent '{name}': Saved output to {save_to} ({file_size} bytes)")

        except Exception as e:
            logger.error(f"Agent '{name}': Failed to save output to {save_to}: {str(e)}")
            raise

    if verbose:
        safe_print(f"✅ Done ({elapsed:.1f}s, {len(output):,} chars)")
        if save_to:
            safe_print(f"Saved to: {save_to}")

    return output


def _capture_partial_output(save_path: Path, agent_name: str) -> Optional[str]:
    """
    Capture partial output on timeout (V3 feature).

    When an agent times out, check if any partial work was written to the output
    directory. This recovers work that would otherwise be lost.

    Args:
        save_path: Path where output would be saved
        agent_name: Name of the agent (for logging)

    Returns:
        str or None: Partial output if found, None otherwise
    """
    try:
        output_dir = save_path.parent
        if not output_dir.exists():
            return None

        # Check for the output file itself (might have been partially written)
        if save_path.exists() and save_path.stat().st_size > 0:
            content = save_path.read_text(encoding='utf-8')
            if len(content.strip()) > 50:  # Non-trivial content
                logger.info(f"Agent '{agent_name}': Found partial output in {save_path}")
                return content

        # Check for any recent files in the output directory
        recent_files = []
        for f in output_dir.iterdir():
            if f.is_file() and f.suffix in ['.md', '.txt', '.json']:
                recent_files.append((f, f.stat().st_mtime))

        if not recent_files:
            return None

        # Get most recently modified file
        recent_files.sort(key=lambda x: x[1], reverse=True)
        most_recent = recent_files[0][0]

        if most_recent.stat().st_size > 0:
            content = most_recent.read_text(encoding='utf-8')
            if len(content.strip()) > 50:
                logger.info(f"Agent '{agent_name}': Found partial output in {most_recent}")
                return content

        return None
    except Exception as e:
        logger.debug(f"Agent '{agent_name}': Partial output capture failed: {e}")
        return None


def _is_transient_error(error: Exception) -> bool:
    """
    Check if error is transient and worth retrying.
    Also signals backpressure system for rate limit errors.

    Args:
        error: Exception to check

    Returns:
        bool: True if error is transient (network, rate limit, etc.)
    """
    error_str = str(error).lower()
    # Expanded patterns from V3 - covers more network/API edge cases
    transient_patterns = [
        # Rate limiting
        'timeout',
        'rate limit',
        'rate_limit',
        'ratelimit',
        'quota',
        'throttl',
        '429',  # Too Many Requests
        # Server errors
        'service unavailable',
        'temporarily unavailable',
        'server error',
        'internal error',
        '500',  # Internal Server Error
        '502',  # Bad Gateway
        '503',  # Service Unavailable
        '504',  # Gateway Timeout
        # Network errors
        'connection reset',
        'connection refused',
        'connection closed',
        'server disconnected',
        'broken pipe',
        'network unreachable',
        'dns',
        'ssl',
        'certificate',
        'handshake',
        # API-specific
        'resource exhausted',
        'overloaded',
        'capacity',
        'retry',
        'try again',
        'temporary',
    ]

    is_transient = any(pattern in error_str for pattern in transient_patterns)
    
    # Signal backpressure for rate limit errors
    if is_transient and ('429' in error_str or 'rate limit' in error_str or 'quota' in error_str):
        try:
            from utils.backpressure import BackpressureManager, APIType
            bp = BackpressureManager()
            bp.signal_429(APIType.GEMINI_PRIMARY)
            logger.debug("Signaled backpressure for rate limit error")
        except Exception:
            pass  # Don't fail on backpressure errors
    
    return is_transient


def rate_limit_delay(seconds: Optional[float] = None) -> None:
    """
    Sleep for rate limiting with tier-adaptive delays.

    Automatically adjusts delay based on detected API tier:
    - Free tier (10 RPM): 7 seconds (safe for 1 req/6s limit)
    - Paid tier (2,000 RPM): 0.3 seconds (safe for high throughput)

    Args:
        seconds: Manual override (default: None = use tier-adaptive delay)
    """
    if seconds is None:
        # Use tier-adaptive delay
        config = get_concurrency_config(verbose=False)
        seconds = config.rate_limit_delay

    time.sleep(seconds)


def research_citations_via_api(
    model: Any,
    research_topics: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
    target_minimum: int = 50,
    verbose: bool = True,
    # Deep Research Mode parameters
    use_deep_research: bool = False,
    topic: Optional[str] = None,
    scope: Optional[str] = None,
    seed_references: Optional[List[str]] = None,
    min_sources_deep: int = 100,
    # Timeout control
    per_topic_timeout_seconds: int = 45,
    # Progress reporting
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Research citations using API-backed fallback chain with optional deep research mode.

    Two modes of operation:
    1. **Standard Mode** (use_deep_research=False):
       - Uses manually provided research_topics list
       - Executes each topic through API fallback chain
       - Best for targeted, curated research queries

    2. **Deep Research Mode** (use_deep_research=True):
       - Uses DeepResearchPlanner for autonomous research strategy
       - Creates 50+ systematic queries from topic + scope + seed references
       - Best for comprehensive literature reviews (dissertations, draft)

    API Fallback Chain:
    Crossref → OpenAlex → OpenAIRE → CORE → DOAJ → Semantic Scholar (supplemental) → LLM fallback

    Args:
        model: Configured model instance (used for planning and optional LLM fallback)
        research_topics: List of research topics (required if use_deep_research=False)
        output_path: Path to save Scout-compatible markdown output (required if provided)
        target_minimum: Minimum citations required to pass quality gate (default: 50)
        verbose: Whether to print progress messages (default: True)

        use_deep_research: Enable deep research mode (default: False)
        topic: Main research topic (required if use_deep_research=True)
        scope: Optional research scope constraints (e.g., "EU focus; B2C and B2B")
        seed_references: Optional seed papers to expand from
        min_sources_deep: Minimum sources for deep research (default: 100)
        per_topic_timeout_seconds: Maximum time to spend on each research topic (default: 90)
        progress_callback: Optional callback(message, event_type) for progress reporting

    Returns:
        Dict with keys:
            - citations: List[Citation] - Valid citations found
            - count: int - Number of valid citations
            - sources: Dict[str, int] - Breakdown by source (Crossref, Semantic Scholar, etc.)
            - failed_topics: List[str] - Topics that failed to find citations
            - research_plan: Optional[Dict] - Deep research plan (if deep mode enabled)

    Raises:
        ValueError: If citation count < target_minimum (quality gate failure)
        ValueError: If invalid mode parameters (missing required args)
    """
    # Validate mode parameters
    if use_deep_research:
        if not topic:
            raise ValueError("Deep research mode requires 'topic' parameter")
        mode_name = "DEEP RESEARCH MODE"
    else:
        if not research_topics:
            raise ValueError("Standard mode requires 'research_topics' parameter")
        mode_name = "STANDARD MODE"

    if verbose:
        safe_print("\n" + "=" * 80)
        safe_print(f"🔬 API-BACKED SCOUT - {mode_name}")
        safe_print("=" * 80)

    # Deep Research Mode: Autonomous research planning
    research_plan: Optional[Dict[str, Any]] = None

    if use_deep_research:
        if verbose:
            safe_print(f"\n🧠 Deep Research Planning Phase")
            safe_print(f"{'='*80}")
            safe_print(f"\n📋 Input:")
            safe_print(f"   Topic: {topic}")
            if scope:
                safe_print(f"   Scope: {scope}")
            if seed_references:
                safe_print(f"   Seed References: {len(seed_references)}")
            safe_print(f"   Target: {min_sources_deep}+ sources")
            safe_print()

        # Initialize deep research planner
        planner = DeepResearchPlanner(
            llm_model=model,
            min_sources=min_sources_deep,
            verbose=verbose
        )

        # #region agent log
        import json as json_lib
        import time as time_lib
        debug_log_path = "/tmp/opendraft_debug.log"
        try:
            with open(debug_log_path, "a") as f:
                f.write(json_lib.dumps({
                    "timestamp": int(time_lib.time() * 1000),
                    "location": "agent_runner.py:research_citations_via_api",
                    "message": "Starting deep research plan creation",
                    "data": {"topic": topic[:100]},
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A"
                }) + "\n")
        except Exception:
            pass
        # #endregion

        try:
            # Create research plan
            research_plan = planner.create_research_plan(
                topic=topic,
                scope=scope,
                seed_references=seed_references
            )

            # Validate plan quality
            if not planner.validate_plan(research_plan):
                if verbose:
                    safe_print("\n⚠️  Initial plan validation failed - attempting refinement...")

                # Attempt refinement
                research_plan = planner.refine_plan(
                    plan=research_plan,
                    feedback=f"Insufficient queries or coverage. Need minimum {min_sources_deep} sources. "
                            f"Generate more diverse queries covering: author searches, title searches, "
                            f"topic queries, regulatory/standards, and interdisciplinary connections."
                )

                # Validate again
                if not planner.validate_plan(research_plan):
                    raise ValueError(
                        f"Deep research plan validation failed after refinement. "
                        f"Generated {len(research_plan.get('queries', []))} queries, "
                        f"estimated {planner.estimate_coverage(research_plan.get('queries', []))} sources, "
                        f"but need minimum {min_sources_deep}."
                    )

            # Extract queries as research topics and cap by execution capacity
            config = get_concurrency_config(verbose=False)
            raw_topics = research_plan.get('queries', [])
            research_topics = _cap_research_queries(raw_topics, topic or "", config.scout_parallel_workers)

            if verbose:
                safe_print(f"\n✅ Research Plan Created:")
                safe_print(f"   Queries Generated: {len(raw_topics)}")
                safe_print(f"   Queries Selected for Execution: {len(research_topics)}")
                safe_print(f"   Estimated Coverage: {planner.estimate_coverage(research_topics)} sources")
                safe_print(f"\n📝 Research Strategy:")
                strategy_lines = research_plan.get('strategy', '').split('\n')
                for line in strategy_lines[:5]:  # First 5 lines
                    safe_print(f"   {line}")
                if len(strategy_lines) > 5:
                    safe_print(f"   ... (see output file for full strategy)")
                safe_print()
        
        except (TimeoutError, FuturesTimeoutError) as e:
            # Fallback to standard mode: generate basic queries from topic
            logger.warning(f"Deep research planning timed out, falling back to standard mode: {e}")
            if verbose:
                safe_print(f"\n⚠️  Deep Research Planning Timeout")
                safe_print(f"   Falling back to standard mode with basic queries...")
                safe_print()
            
            # #region agent log
            try:
                with open(debug_log_path, "a") as f:
                    f.write(json_lib.dumps({
                        "timestamp": int(time_lib.time() * 1000),
                        "location": "agent_runner.py:research_citations_via_api",
                        "message": "Deep research timeout - falling back to standard mode",
                        "data": {"error": str(e)[:200]},
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A"
                    }) + "\n")
            except Exception:
                pass
            # #endregion
            
            research_topics = _build_research_fallback_queries(topic, scope)
            
            if verbose:
                safe_print(f"   Generated {len(research_topics)} fallback queries")
                safe_print()
        
        except Exception as e:
            # For other exceptions, also fallback to standard mode
            logger.warning(f"Deep research planning failed, falling back to standard mode: {e}")
            if verbose:
                safe_print(f"\n⚠️  Deep Research Planning Failed")
                safe_print(f"   Error: {str(e)[:200]}")
                safe_print(f"   Falling back to standard mode with basic queries...")
                safe_print()
            
            # #region agent log
            try:
                with open(debug_log_path, "a") as f:
                    f.write(json_lib.dumps({
                        "timestamp": int(time_lib.time() * 1000),
                        "location": "agent_runner.py:research_citations_via_api",
                        "message": "Deep research failed - falling back to standard mode",
                        "data": {"error": str(e)[:200]},
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A"
                    }) + "\n")
            except Exception:
                pass
            # #endregion
            
            research_topics = _build_research_fallback_queries(topic, scope)
            
            if verbose:
                safe_print(f"   Generated {len(research_topics)} fallback queries")
                safe_print()

    # Execution Phase: Run queries through API fallback chain
    if verbose:
        safe_print(f"\n📊 Execution Configuration:")
        safe_print(f"   Target Minimum: {target_minimum} citations")
        safe_print(f"   Research Topics/Queries: {len(research_topics)}")
        if output_path:
            safe_print(f"   Output: {output_path}")
        safe_print()

    # Initialize CitationResearcher with API fallback chain
    # Semantic Scholar can be disabled via env var if rate limited (403 errors)
    enable_semantic_scholar = os.environ.get('ENABLE_SEMANTIC_SCHOLAR', 'true').lower() != 'false'

    researcher = CitationResearcher(
        llm_model=model,
        enable_crossref=True,
        enable_openalex=True,
        enable_semantic_scholar=enable_semantic_scholar,
        enable_smart_routing=True,     # Enable query classification for source diversity
        enable_llm_fallback=False,     # DISABLED: LLM hallucinates citations
        verbose=verbose,
        progress_callback=progress_callback,  # Pass through for progress reporting
    )

    capability = researcher.capability_matrix()
    is_chinese_topic = any('\u4e00' <= ch <= '\u9fff' for ch in ((topic or "") + " " + " ".join(research_topics or [])))

    if verbose:
        safe_print("🧪 Research Capability Preflight:")
        safe_print(f"   - Crossref: {'ready' if capability['crossref'].get('enabled') else 'off'}")
        safe_print(f"   - OpenAlex: {'ready' if capability['openalex'].get('enabled') else 'off'}")
        ss_state = capability['semantic_scholar']
        ss_text = "ready" if ss_state.get('enabled') and not ss_state.get('cooled_down') else "degraded"
        safe_print(f"   - OpenAIRE: {'ready' if capability['openaire'].get('enabled') else 'off'}")
        safe_print(f"   - CORE: {'ready' if capability['core'].get('enabled') else 'off'}")
        safe_print(f"   - DOAJ: {'ready' if capability['doaj'].get('enabled') else 'off'}")
        safe_print(f"   - Semantic Scholar (supplemental): {ss_text}")

    if not enable_semantic_scholar and verbose:
        safe_print("   ⚠️  Semantic Scholar disabled (ENABLE_SEMANTIC_SCHOLAR=false)")

    # Track results
    citations: List[Citation] = []
    sources_breakdown: Dict[str, int] = {
        "Crossref": 0,
        "OpenAlex": 0,
        "OpenAIRE": 0,
        "CORE": 0,
        "DOAJ": 0,
        "Semantic Scholar": 0,
        "LLM Fallback": 0,
    }
    failed_topics: List[str] = []

    # Parallel citation research configuration (tier-adaptive)
    config = get_concurrency_config(verbose=False)
    BATCH_SIZE = config.scout_batch_size
    BATCH_DELAY = config.scout_batch_delay
    PARALLEL_WORKERS = config.scout_parallel_workers

    # Detect if proxies are configured for rate limit bypass
    from utils.api_citations.base import PROXY_LIST
    use_proxies = len(PROXY_LIST) > 0

    # Adjust batch delay based on proxy availability
    # With proxies: skip delays for maximum throughput
    # Without proxies: respect API rate limits
    effective_batch_delay = 0 if use_proxies else BATCH_DELAY

    if verbose and use_proxies:
        safe_print(f"\n🔀 Proxy rotation enabled: {len(PROXY_LIST)} proxies")
        safe_print(f"   Batch delays disabled for maximum throughput")

    # Helper function for parallel execution with timeout
    def _research_single_topic(
        topic_with_idx: Tuple[int, str],
        timeout_seconds: int,
    ) -> Tuple[int, str, List[Citation], Optional[str]]:
        """Research a single topic with timeout. Returns (idx, topic, list_of_citations, error_or_None)."""
        idx, research_topic = topic_with_idx
        try:
            # Wrap in executor for timeout control
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(researcher.research_citation, research_topic)
                try:
                    citations_list = future.result(timeout=timeout_seconds)
                    return (idx, research_topic, citations_list, None)
                except FuturesTimeoutError:
                    return (idx, research_topic, [], f"Timeout after {timeout_seconds}s")
        except Exception as e:
            return (idx, research_topic, [], str(e))

    # Dynamic early stopping threshold to avoid unnecessary long runs
    early_stop_threshold = min(max(target_minimum + 5, int(target_minimum * 1.3)), 35)
    timeout_error_count = 0
    semantic_scholar_hits = 0

    # Parallel or sequential based on config
    if PARALLEL_WORKERS > 1:
        if verbose:
            safe_print(f"\n🚀 Parallel citation research enabled ({PARALLEL_WORKERS} workers)")

        # Process in batches with parallel workers
        total_topics = len(research_topics)
        processed = 0

        # Conservative ramp-up: avoid starting at maximum worker pressure
        current_workers = min(PARALLEL_WORKERS, 2 if is_chinese_topic else 3)
        adaptive_batch_delay = float(effective_batch_delay)
        for batch_start in range(0, total_topics, BATCH_SIZE):
            # Early stopping: Check if we've reached target + 10%
            if len(citations) >= early_stop_threshold:
                if verbose:
                    safe_print(f"\n⏩ Early stopping: {len(citations)} citations collected (target: {target_minimum}, threshold: {early_stop_threshold})")
                break

            batch_end = min(batch_start + BATCH_SIZE, total_topics)
            batch = list(enumerate(research_topics[batch_start:batch_end], batch_start + 1))

            if verbose and batch_start > 0 and adaptive_batch_delay > 0:
                safe_print(f"\n⏸️  Batch complete ({batch_start} topics processed). Waiting {adaptive_batch_delay:.1f}s to respect API limits...")
                time.sleep(adaptive_batch_delay)

            if verbose:
                safe_print(f"\n📦 Processing batch {batch_start // BATCH_SIZE + 1} ({len(batch)} topics)...")

            # Execute batch in parallel (adaptive workers under timeout pressure)
            if verbose and current_workers != PARALLEL_WORKERS:
                safe_print(f"   🔧 Adaptive workers: {current_workers} (base={PARALLEL_WORKERS})")
            batch_timeout_count = 0
            with ThreadPoolExecutor(max_workers=current_workers) as executor:
                futures = {}
                for item in batch:
                    idx, q = item
                    dynamic_timeout = max(35, min(75, per_topic_timeout_seconds + (15 if is_chinese_topic else 0) + (10 if current_workers <= 2 else 0)))
                    futures[executor.submit(_research_single_topic, (idx, q), dynamic_timeout)] = item

                for future in as_completed(futures):
                    idx, research_topic, citations_list, error = future.result()
                    processed += 1

                    if verbose:
                        safe_print(f"[{idx}/{total_topics}] 🔎 {research_topic[:55]}{'...' if len(research_topic) > 55 else ''}", end=' ')

                    if error:
                        failed_topics.append(research_topic)
                        if "timeout" in error.lower():
                            timeout_error_count += 1
                            batch_timeout_count += 1
                        if verbose:
                            safe_print(f"❌ Error: {error[:30]}...")
                        logger.error(f"Citation research failed for '{research_topic}': {error}")
                    elif citations_list:
                        # Add ALL citations from this query (multiple sources)
                        citations.extend(citations_list)
                        # Update source breakdown for all citations
                        for citation in citations_list:
                            source = citation.api_source or 'Unknown'
                            if source in sources_breakdown:
                                sources_breakdown[source] += 1
                            if source == "Semantic Scholar":
                                semantic_scholar_hits += 1
                        if verbose:
                            # Show all sources found for this query
                            sources_str = ", ".join([c.api_source or 'Unknown' for c in citations_list])
                            first_citation = citations_list[0]
                            authors_str = first_citation.authors[0] if first_citation.authors else "Unknown"
                            count_str = f" (+{len(citations_list)-1} more)" if len(citations_list) > 1 else ""
                            safe_print(f"✅ {authors_str} et al. ({first_citation.year}) [{sources_str}]{count_str}")

                        # Check for early stopping within batch
                        if len(citations) >= early_stop_threshold:
                            if verbose:
                                safe_print(f"\n⏩ Early stopping: {len(citations)} citations collected")
                            break
                    else:
                        failed_topics.append(research_topic)
                        if verbose:
                            safe_print("❌ No citation found")

            # Adaptive backoff for worker count when timeout pressure is high
            if len(batch) > 0 and batch_timeout_count / len(batch) >= 0.4 and current_workers > 1:
                current_workers = max(1, current_workers // 2)
                adaptive_batch_delay = min(20.0, adaptive_batch_delay + 3.0)
                logger.warning(
                    f"High timeout pressure in batch ({batch_timeout_count}/{len(batch)}). "
                    f"Reducing workers to {current_workers}, delay to {adaptive_batch_delay:.1f}s."
                )
            elif batch_timeout_count == 0 and current_workers < PARALLEL_WORKERS:
                current_workers = min(PARALLEL_WORKERS, current_workers + 1)
                adaptive_batch_delay = max(0.0, adaptive_batch_delay - 1.0)
    else:
        # Sequential execution (free tier or 1 worker)
        if verbose:
            safe_print("\n🔄 Sequential citation research (1 worker)")

        for idx, research_topic in enumerate(research_topics, 1):
            # Early stopping: Check if we've reached target + 10%
            if len(citations) >= early_stop_threshold:
                if verbose:
                    safe_print(f"\n⏩ Early stopping: {len(citations)} citations collected (target: {target_minimum}, threshold: {early_stop_threshold})")
                break

            # Add delay every BATCH_SIZE topics to prevent burst rate limits
            if idx > 1 and (idx - 1) % BATCH_SIZE == 0 and effective_batch_delay > 0:
                if verbose:
                    safe_print(f"\n⏸️  Batch complete ({idx-1} topics processed). Waiting {effective_batch_delay}s to respect API limits...")
                time.sleep(effective_batch_delay)

            if verbose:
                safe_print(f"[{idx}/{len(research_topics)}] 🔎 {research_topic[:65]}{'...' if len(research_topic) > 65 else ''}")

            try:
                # Wrap in executor for timeout control
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(researcher.research_citation, research_topic)
                    try:
                        citations_list = future.result(timeout=per_topic_timeout_seconds)
                    except FuturesTimeoutError:
                        citations_list = []
                        failed_topics.append(research_topic)
                        if verbose:
                            safe_print(f"    ⏱️  Timeout after {per_topic_timeout_seconds}s")
                        logger.warning(f"Citation research timed out for '{research_topic}' after {per_topic_timeout_seconds}s")
                        continue

                if citations_list:
                    # #region agent log
                    # Note: json, time, os already imported at module level
                    try:
                        debug_log_path = os.getenv('DEBUG_LOG_PATH', '/tmp/opendraft/debug.log')
                        os.makedirs(os.path.dirname(debug_log_path), exist_ok=True)
                        with open(debug_log_path, 'a') as f:
                            f.write(json.dumps({
                                "sessionId": "debug-session",
                                "runId": "run1",
                                "hypothesisId": "J",
                                "location": "agent_runner.py:710",
                                "message": "Citations found for topic",
                                "data": {
                                    "topic": research_topic[:100],
                                    "citations_count": len(citations_list),
                                    "sources": [c.api_source or 'Unknown' for c in citations_list],
                                    "total_citations_so_far": len(citations) + len(citations_list)
                                },
                                "timestamp": int(time.time() * 1000)
                            }) + "\n")
                    except Exception as e:
                        logger.debug(f"Debug log write failed: {e}")
                    # #endregion
                    
                    # Add ALL citations from this query (multiple sources)
                    citations.extend(citations_list)

                    # Track sources for all citations
                    for citation in citations_list:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1

                    if verbose:
                        # Show all sources found for this query
                        sources_str = ", ".join([c.api_source or 'Unknown' for c in citations_list])
                        first_citation = citations_list[0]
                        authors_str = first_citation.authors[0] if first_citation.authors else "Unknown"
                        count_str = f" (+{len(citations_list)-1} more)" if len(citations_list) > 1 else ""
                        safe_print(f"    ✅ {authors_str} et al. ({first_citation.year}) [{sources_str}]{count_str}")
                else:
                    failed_topics.append(research_topic)
                    if verbose:
                        safe_print(f"    ❌ No citation found")

            except Exception as e:
                failed_topics.append(research_topic)
                if verbose:
                    safe_print(f"    ❌ Error: {str(e)}")
                logger.error(f"Citation research failed for '{research_topic}': {str(e)}")

    # Calculate success metrics
    citation_count = len(citations)
    success_rate = (citation_count / len(research_topics) * 100) if research_topics else 0

    # Chinese-topic guardrail: signal missing Chinese-language coverage loudly
    chinese_language_hits = _count_chinese_language_hits(citations)
    title_zh_hits = chinese_language_hits["title_zh_hits"]
    language_zh_hits = chinese_language_hits["language_zh_hits"]
    effective_zh_hits = chinese_language_hits["effective_zh_hits"]

    if verbose and timeout_error_count > 0:
        safe_print(f"⚠️  Timeout diagnostics: {timeout_error_count} topic timeouts observed")
    if verbose and semantic_scholar_hits == 0 and enable_semantic_scholar:
        safe_print("⚠️  Semantic Scholar yielded 0 accepted hits in this run (likely rate-limit/cooldown pressure)")

    if verbose:
        safe_print("\n" + "=" * 80)
        safe_print("📊 SCOUT RESULTS")
        safe_print("=" * 80)
        safe_print(f"\n✅ Valid Citations: {citation_count}")
        safe_print(f"❌ Failed Topics: {len(failed_topics)}")
        safe_print(f"📈 Success Rate: {success_rate:.1f}%")
        safe_print(f"\n📚 Sources Breakdown:")
        for source, count in sources_breakdown.items():
            percentage = (count / citation_count * 100) if citation_count > 0 else 0
            safe_print(f"   {source}: {count} ({percentage:.1f}%)")
        if is_chinese_topic:
            safe_print("\n🇨🇳 Chinese Language Coverage:")
            safe_print(f"   Chinese title hits: {title_zh_hits}")
            safe_print(f"   Chinese language-tag hits: {language_zh_hits}")
            safe_print(f"   Effective Chinese hits: {effective_zh_hits}")
        safe_print()

    # Tiered Quality Gate
    excellent_threshold = target_minimum
    acceptable_threshold = int(target_minimum * 0.9)
    minimal_threshold = int(target_minimum * 0.85)
    # Chinese-topic hard floor: prevent under-supported draft generation.
    # Before failing hard, run a small zh-rescue round when primary retrieval is
    # successful overall but Chinese-language hits are still insufficient.
    # Chinese-topic hard floor: prevent under-supported draft generation
    if is_chinese_topic:
        chinese_min_required = max(1, min(3, target_minimum // 4))
        if effective_zh_hits < chinese_min_required:
            rescue_queries = _build_chinese_coverage_rescue_queries(topic or "", scope)
            rescue_candidates = [q for q in rescue_queries if q not in (research_topics or [])]
            if rescue_candidates:
                if verbose:
                    safe_print("⚠️  Chinese coverage below threshold. Running targeted Chinese rescue queries...")
                for rescue_query in rescue_candidates[:6]:
                    try:
                        rescue_citations = researcher.research_citation(rescue_query)
                        if rescue_citations:
                            citations.extend(rescue_citations)
                            for citation in rescue_citations:
                                source = citation.api_source or 'Unknown'
                                if source in sources_breakdown:
                                    sources_breakdown[source] += 1
                    except Exception as e:
                        logger.warning(f"Chinese coverage rescue query failed '{rescue_query}': {e}")

                citation_count = len(citations)
                chinese_language_hits = _count_chinese_language_hits(citations)
                title_zh_hits = chinese_language_hits["title_zh_hits"]
                language_zh_hits = chinese_language_hits["language_zh_hits"]
                effective_zh_hits = chinese_language_hits["effective_zh_hits"]

        if effective_zh_hits < chinese_min_required:
            raise ValueError(
                f"Chinese topic quality gate failed: Chinese-language hits {effective_zh_hits} < required {chinese_min_required} "
                f"(title_zh_hits={title_zh_hits}, language_zh_hits={language_zh_hits}). "
                "Please broaden Chinese queries (including bilingual expansions) or narrow topic scope."
            )

    # #region agent log
    # Note: json, time, os already imported at module level
    try:
        debug_log_path = os.getenv('DEBUG_LOG_PATH', '/tmp/opendraft/debug.log')
        with open(debug_log_path, 'a') as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "run1",
                "hypothesisId": "G",
                "location": "agent_runner.py:760",
                "message": "Quality gate evaluation",
                "data": {
                    "citation_count": citation_count,
                    "target_minimum": target_minimum,
                    "excellent_threshold": excellent_threshold,
                    "acceptable_threshold": acceptable_threshold,
                    "minimal_threshold": minimal_threshold,
                    "sources_breakdown": sources_breakdown,
                    "title_zh_hits": title_zh_hits,
                    "language_zh_hits": language_zh_hits,
                    "effective_zh_hits": effective_zh_hits,
                    "failed_topics_count": len(failed_topics)
                },
                "timestamp": int(time.time() * 1000)
            }) + "\n")
    except Exception as e:
        logger.debug(f"Debug log write failed: {e}")
    # #endregion

    if citation_count >= excellent_threshold:
        if verbose:
            safe_print(f"✅ QUALITY GATE PASSED (EXCELLENT): {citation_count} ≥ {target_minimum} required\n")
        logger.info(f"Quality gate: EXCELLENT - {citation_count}/{target_minimum} citations")

    elif citation_count >= acceptable_threshold:
        percentage = (citation_count / target_minimum) * 100
        if verbose:
            safe_print(f"⚠️  QUALITY GATE PASSED (ACCEPTABLE): {citation_count}/{target_minimum} ({percentage:.1f}%)")
            safe_print(f"    Academic quality is good, but {target_minimum - citation_count} more citations recommended.\n")
        logger.warning(f"Quality gate: ACCEPTABLE - {citation_count}/{target_minimum} ({percentage:.1f}%)")

    elif citation_count >= minimal_threshold:
        percentage = (citation_count / target_minimum) * 100
        if verbose:
            safe_print(f"⚠️  QUALITY GATE PASSED (MINIMAL): {citation_count}/{target_minimum} ({percentage:.1f}%)")
            safe_print(f"    ⚠️  WARNING: Citation count is below recommended standards.")
            safe_print(f"    Consider adding {target_minimum - citation_count} more citations for better academic rigor.\n")
        logger.warning(f"Quality gate: MINIMAL - {citation_count}/{target_minimum} ({percentage:.1f}%) - below standards")

    else:
        percentage = (citation_count / target_minimum) * 100
        error_msg = (
            f"\n❌ QUALITY GATE FAILED (INSUFFICIENT CITATIONS)\n\n"
            f"Only {citation_count} citations found ({percentage:.1f}%), but minimum {minimal_threshold} required ({minimal_threshold/target_minimum*100:.0f}% of target).\n"
            f"Target: {target_minimum} citations (100%)\n"
            f"Acceptable: {acceptable_threshold}+ citations (90%)\n"
            f"Minimal: {minimal_threshold}+ citations (85%)\n"
            f"Current: {citation_count} citations ({percentage:.1f}%) ❌\n\n"
            f"Academic draft standards require at least {minimal_threshold} citations.\n\n"
            f"Failed Topics ({len(failed_topics)}):\n"
        )
        for failed_topic in failed_topics[:10]:
            error_msg += f"  - {failed_topic}\n"
        if len(failed_topics) > 10:
            error_msg += f"  ... and {len(failed_topics) - 10} more\n"

        logger.error(f"Quality gate FAILED: {citation_count} < {minimal_threshold} (minimal threshold)")
        raise ValueError(error_msg)

    # Format output as Scout-compatible markdown
    markdown_lines = [
        "# Scout Output - Academic Citation Discovery",
        "",
        "## Summary",
        "",
        f"**Total Valid Citations**: {citation_count}",
        f"**Success Rate**: {success_rate:.1f}%",
        f"**Failed Topics**: {len(failed_topics)}",
        "",
        "### Sources Breakdown",
        ""
    ]

    for source, count in sources_breakdown.items():
        percentage = (count / citation_count * 100) if citation_count > 0 else 0
        markdown_lines.append(f"- **{source}**: {count} ({percentage:.1f}%)")

    markdown_lines.extend([
        "",
        "---",
        "",
        "## Citations Found",
        ""
    ])

    # Add citations grouped by source
    for source in ["Crossref", "OpenAlex", "OpenAIRE", "CORE", "DOAJ", "Semantic Scholar", "LLM Fallback"]:
        source_citations = [c for c in citations if c.api_source == source]
        if not source_citations:
            continue

        markdown_lines.append(f"### From {source} ({len(source_citations)} citations)")
        markdown_lines.append("")

        for idx, citation in enumerate(source_citations, 1):
            markdown_lines.append(f"#### {idx}. {citation.title}")
            markdown_lines.append(f"**Authors**: {', '.join(citation.authors)}")
            markdown_lines.append(f"**Year**: {citation.year}")
            markdown_lines.append(f"**DOI**: {citation.doi}")
            if citation.url:
                markdown_lines.append(f"**URL**: {citation.url}")
            if hasattr(citation, 'abstract') and citation.abstract:
                # Include abstract for Scribe to summarize (prevent hallucination)
                markdown_lines.append("")
                markdown_lines.append(f"**Abstract**: {citation.abstract}")
            markdown_lines.append("")

    if failed_topics:
        markdown_lines.extend([
            "---",
            "",
            "## Failed Topics",
            "",
            "The following topics did not return valid citations:",
            ""
        ])
        for failed_topic in failed_topics:
            markdown_lines.append(f"- {failed_topic}")

    # Write to file
    markdown_content = "\n".join(markdown_lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_content, encoding='utf-8')

    if verbose:
        safe_print(f"💾 Saved Scout output to: {output_path}")
        safe_print(f"   File size: {output_path.stat().st_size:,} bytes\n")

    logger.info(f"Scout completed: {citation_count} citations, {success_rate:.1f}% success rate")

    return {
        "citations": citations,
        "count": citation_count,
        "sources": sources_breakdown,
        "failed_topics": failed_topics,
        "research_plan": research_plan
    }
