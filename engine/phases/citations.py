#!/usr/bin/env python3
"""
ABOUTME: Citation management phase — deterministic pipeline (no LLM)
ABOUTME: Deduplication, scraping, filtering, and summary generation
"""

import logging

from .context import DraftContext

logger = logging.getLogger(__name__)


def run_citation_management(ctx: DraftContext) -> None:
    """
    Execute the citation management pipeline (deterministic, no LLM).

    Mutates ctx: citation_database, citation_summary
    """
    from utils.agent_runner import rate_limit_delay
    from utils.citation_database import CitationDatabase, save_citation_database, load_citation_database
    from utils.deduplicate_citations import deduplicate_citations
    from utils.scrape_citation_titles import TitleScraper
    from utils.scrape_citation_metadata import MetadataScraper
    from utils.citation_quality_filter import CitationQualityFilter

    if ctx.verbose:
        print("\n📚 PHASE 2.5: CITATION MANAGEMENT")

    # Create citation database from Scout results
    scout_citations = ctx.scout_result['citations']

    # Prefer peer-reviewed/complete records over preprints and malformed entries
    def _citation_rank(c):
        source_type = (getattr(c, 'source_type', '') or '').lower()
        is_preprint = source_type == 'preprint'
        has_core_fields = bool(getattr(c, 'title', None) and getattr(c, 'year', None) and getattr(c, 'authors', None))
        has_journal_or_publisher = bool(getattr(c, 'journal', None) or getattr(c, 'publisher', None))
        return (
            1 if not is_preprint else 0,
            1 if has_core_fields else 0,
            1 if has_journal_or_publisher else 0,
        )

    scout_citations = sorted(scout_citations, key=_citation_rank, reverse=True)
    for i, citation in enumerate(scout_citations, start=1):
        citation.id = f"cite_{i:03d}"

    # Map CLI-style values to internal CitationStyle format
    style_map = {"apa": "APA 7th", "ieee": "IEEE", "nalt": "NALT", "chicago": "Chicago", "mla": "MLA"}
    resolved_style = style_map.get(ctx.citation_style, "APA 7th")

    # Import get_language_name from text_utils (shared to avoid circular imports)
    from utils.text_utils import get_language_name

    ctx.citation_database = CitationDatabase(
        citations=scout_citations,
        citation_style=resolved_style,
        draft_language=get_language_name(ctx.language).lower(),
    )

    # Deduplicate citations
    deduplicated_citations, dedup_stats = deduplicate_citations(
        ctx.citation_database.citations,
        strategy='keep_best',
        verbose=ctx.verbose,
    )
    ctx.citation_database.citations = deduplicated_citations

    # Scrape titles and metadata for web sources
    title_scraper = TitleScraper(verbose=False)
    title_scraper.scrape_citations(ctx.citation_database.citations)

    metadata_scraper = MetadataScraper(verbose=False)
    metadata_scraper.scrape_citations(ctx.citation_database.citations)

    # Save citation database to research folder
    citation_db_path = ctx.folders['research'] / "bibliography.json"
    save_citation_database(ctx.citation_database, citation_db_path)

    # Quality filtering (auto-fix mode for automated runs)
    filter_obj = CitationQualityFilter(strict_mode=False)
    filter_obj.filter_database(citation_db_path, citation_db_path)

    # Reload filtered database
    ctx.citation_database = load_citation_database(citation_db_path)

    if ctx.verbose:
        print(f"\u2705 Citations: {len(ctx.citation_database.citations)} unique")

    # MILESTONE: Research Complete - Stream to user
    if ctx.streamer:
        ctx.streamer.stream_research_complete(
            sources_count=len(ctx.citation_database.citations),
            bibliography_path=citation_db_path,
        )

    if ctx.tracker:
        ctx.tracker.update_phase(
            "structure",
            progress_percent=23,
            sources_count=len(ctx.citation_database.citations),
            details={"stage": "research_complete", "milestone": "research_complete"},
        )

    # Build citation summary for writing agents
    ctx.citation_metrics = _build_citation_metrics(ctx.citation_database)
    ctx.citation_summary = _build_citation_summary(ctx.citation_database)

    rate_limit_delay()


def _build_citation_summary(citation_database) -> str:
    """Build comprehensive citation database string for writing agent prompts."""
    citation_summary = f"\n\n{'='*80}\n## CITATION DATABASE - {len(citation_database.citations)} CITATIONS AVAILABLE\n{'='*80}\n\n"
    citation_summary += "\u26a0\ufe0f  **CRITICAL CITATION RESTRICTION** \u26a0\ufe0f\n\n"
    citation_summary += "You MUST ONLY cite papers from this database. DO NOT:\n"
    citation_summary += "- Cite papers from your training data\n"
    citation_summary += "- Invent or hallucinate citations\n"
    citation_summary += "- Reference papers not listed below\n"
    citation_summary += "- Use author names not in this database\n\n"
    citation_summary += "Citation format: Use {{cite_XXX}} where XXX is the citation ID shown below.\n"
    citation_summary += f"\n{'='*80}\n\n"

    for i, citation in enumerate(citation_database.citations, 1):
        authors_str = ", ".join(citation.authors[:3])
        if len(citation.authors) > 3:
            authors_str += " et al."

        citation_summary += f"{i}. **[{citation.id}]** {authors_str} ({citation.year})\n"
        citation_summary += f"   Title: {citation.title}\n"

        if citation.doi:
            citation_summary += f"   DOI: {citation.doi}\n"
        if citation.journal:
            citation_summary += f"   Journal: {citation.journal}\n"
        if citation.abstract:
            abstract_preview = citation.abstract[:300]
            if len(citation.abstract) > 300:
                abstract_preview += "..."
            citation_summary += f"   Abstract: {abstract_preview}\n"

        citation_summary += f"   Citation format: {{{{{citation.id}}}}}\n\n"

    citation_summary += f"\n{'='*80}\n"
    citation_summary += f"Total citations available: {len(citation_database.citations)}\n"
    english_count = sum(1 for c in citation_database.citations if (getattr(c, 'language', '') or '').lower() == 'english')
    chinese_count = sum(1 for c in citation_database.citations if (getattr(c, 'language', '') or '').lower() == 'chinese')
    citation_summary += f"Chinese citations available: {chinese_count}\n"
    citation_summary += f"English citations available: {english_count}\n"
    if english_count > 0:
        citation_summary += "For Chinese papers, you MUST retain and actually use relevant English-language citations where available.\n"
    citation_summary += "Remember: ONLY cite from this list. No external citations allowed.\n"
    citation_summary += f"{'='*80}\n"

    return citation_summary


def _build_citation_metrics(citation_database) -> dict:
    citations = citation_database.citations or []
    return {
        "total": len(citations),
        "english_available": sum(1 for c in citations if (getattr(c, 'language', '') or '').lower() == 'english'),
        "chinese_available": sum(1 for c in citations if (getattr(c, 'language', '') or '').lower() == 'chinese'),
    }
