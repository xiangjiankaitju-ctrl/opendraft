#!/usr/bin/env python3
"""
ABOUTME: Smart query router for citation source discovery
ABOUTME: Routes queries to appropriate APIs based on source type detection

Production-grade query classification following SOLID principles.

Purpose:
Routes citation research queries to the most appropriate API source:
10 | - Industry queries → Crossref/OpenAlex/OpenAIRE/CORE/DOAJ first
11 | - Academic queries → Crossref/OpenAlex first
12 | - Mixed queries → OpenAlex/Crossref first, Semantic Scholar supplemental

This maximizes source diversity while maintaining efficiency (1 API call per query).
"""

from typing import Literal, List, Tuple
import re
from dataclasses import dataclass


# Type definitions
QueryType = Literal['academic', 'industry', 'mixed']
APIName = Literal['crossref', 'openalex', 'semantic_scholar', 'openaire', 'core', 'doaj']


@dataclass
class QueryClassification:
    """
    Classification result for a research query.

    Attributes:
        query_type: Classified type ('academic', 'industry', 'mixed')
        confidence: Confidence score 0.0-1.0
        matched_patterns: List of patterns that triggered classification
        api_chain: Prioritized list of APIs to try
    """
    query_type: QueryType
    confidence: float
    matched_patterns: List[str]
    api_chain: List[APIName]
    query_quality: Literal['high', 'medium', 'low'] = 'medium'
    should_query: bool = True
    rewrite_hint: str = ""


class QueryRouter:
    """
    Routes research queries to appropriate citation sources.

    Implements smart routing to maximize source diversity while maintaining
    efficiency. Classifies queries based on keyword patterns and returns
    prioritized API chains.

    Design Principles:
    - Single Responsibility: Only handles query classification and routing
    - Open/Closed: Extensible via pattern lists without modifying core logic
    - Dependency Inversion: Returns API names, not implementations

    Examples:
        >>> router = QueryRouter()
        >>> result = router.classify_and_route("McKinsey digital transformation report 2023")
        >>> result.query_type
        'industry'
        >>> result.api_chain
        ['crossref', 'openalex', 'openaire']

        >>> result = router.classify_and_route("peer-reviewed studies on climate change")
        >>> result.query_type
        'academic'
        >>> result.api_chain
        ['crossref', 'openalex', 'core']
    """

    # Industry source indicators (organizations, document types)
    INDUSTRY_PATTERNS = [
        # Consulting firms
        'mckinsey', 'boston consulting', 'bcg', 'bain', 'deloitte',
        'accenture', 'pwc', 'kpmg', 'ey', 'gartner', 'forrester',
        'idc', 'ovum', 'frost & sullivan',

        # Think tanks & research institutes
        'brookings', 'rand corporation', 'carnegie', 'cato institute',
        'heritage foundation', 'pew research', 'urban institute',
        'chatham house', 'cfr', 'council on foreign relations',

        # International organizations
        'world bank', 'imf', 'international monetary fund',
        'oecd', 'united nations', 'who', 'world health organization',
        'wef', 'world economic forum', 'itu', 'wto',

        # Government & regulatory bodies
        'european commission', 'eu commission', 'ec report',
        'european parliament', 'us congress', 'congressional',
        'government accountability office', 'gao',
        'federal reserve', 'european central bank', 'ecb',
        'fda', 'epa', 'cdc', 'nih', 'nist', 'nasa',

        # Standards bodies (use word boundaries to avoid false positives like "comparison")
        'iso standard', 'iso ', 'ieee', 'ietf', 'w3c', 'oasis', 'ansi',

        # Document types
        'white paper', 'whitepaper', 'policy brief', 'policy paper',
        'technical report', 'industry report', 'market research',
        'working paper', 'briefing', 'position paper',
        'guidelines', 'framework', 'best practices',
        'standards document', 'regulation', 'directive',

        # Business/Industry focus
        'market analysis', 'industry trends', 'sector overview',
        'competitive landscape', 'market forecast',

        # Tech companies & products (NEW - Day 3A enhancement)
        'openai', 'anthropic', 'google', 'microsoft', 'meta',
        'amazon', 'apple', 'ibm', 'oracle', 'salesforce',
        'gpt-4', 'claude', 'gemini', 'chatgpt', 'copilot',
        'aws', 'azure', 'gcp', 'cloud platform',

        # Consulting/Industry sources (NEW - Day 3A enhancement)
        'comparison', 'benchmark', 'pricing comparison',
        'vendor', 'product', 'service provider',
        'platform', 'saas', 'enterprise software',
        'implementation', 'deployment', 'migration',
    ]

    # Academic source indicators (peer-reviewed, scholarly)
    ACADEMIC_PATTERNS = [
        # Publication types
        'peer-reviewed', 'peer reviewed', 'scholarly article',
        'journal article', 'academic paper', 'research paper',
        'conference paper', 'proceedings', 'dissertation',
        'draft', 'monograph',

        # Research methodology
        'empirical study', 'empirical research', 'empirical analysis',
        'systematic review', 'meta-analysis', 'literature review',
        'randomized controlled trial', 'rct', 'cohort study',
        'case-control study', 'longitudinal study',
        'qualitative research', 'quantitative research',

        # Academic rigor indicators
        'published in', 'indexed in', 'scopus', 'web of science',
        'impact factor', 'cited by', 'citations',

        # Scholarly databases
        'pubmed', 'jstor', 'springer', 'elsevier', 'wiley',
        'taylor & francis', 'sage', 'oxford university press',
        # Chinese scholarly databases
        'cnki', '中国知网', '知网', 'wanfang', '万方', 'cqvip', '维普',

        # Research focus
        'theoretical framework', 'conceptual model',
        'research methodology', 'data analysis',

        # Economic & business theory (NEW - Day 3A enhancement)
        'economics', 'economic theory', 'economic model',
        'pricing theory', 'market theory', 'game theory',
        'transaction cost', 'information goods', 'public goods',
        'two-sided market', 'platform economics', 'network effects',
        'demand elasticity', 'price discrimination', 'marginal cost',
        'economies of scale', 'market equilibrium',

        # Technology/CS theory (NEW - Day 3A enhancement)
        'algorithm', 'computational complexity', 'machine learning',
        'neural network', 'natural language processing',
        'computer vision', 'distributed systems', 'cryptography',
        'information retrieval', 'data mining',

        # Social sciences (NEW - Day 3A enhancement)
        'sociological', 'psychological', 'anthropological',
        'behavioral', 'cognitive', 'organizational behavior',

        # Environmental/climate science (NEW - Day 3A enhancement)
        'climate science', 'environmental impact', 'carbon emissions',
        'renewable energy', 'sustainability assessment',
        'ecological', 'biodiversity',
    ]

    POLICY_PATTERNS = [
        'white paper', 'whitepaper', 'guidelines', 'framework', 'regulation',
        'directive', 'standard', 'standards', 'policy', 'best practices',
        '财政部', '工信部', '国家标准', '白皮书', '政策', '规范', '指南',
        '中国信通院', '赛迪', '前瞻产业研究院', '国务院', '发改委',
    ]

    NOISE_PATTERNS = [
        '完整版', '線上看', '在线播放', '下载', 'download', 'movie', 'film',
        'episode', 'torrent', 'lyrics', 'recipe', 'coupon', 'promo code'
    ]

    def __init__(self, enable_multilingual: bool = True):
        """
        Initialize QueryRouter.

        Args:
            enable_multilingual: Support German/Spanish patterns (default: True)
        """
        self.enable_multilingual = enable_multilingual

        # Add multilingual patterns if enabled
        if enable_multilingual:
            self._add_multilingual_patterns()

    def _add_multilingual_patterns(self) -> None:
        """Add German and Spanish pattern support."""
        # German patterns
        self.INDUSTRY_PATTERNS.extend([
            'bericht', 'studie', 'whitepaper', 'leitfaden',  # Document types
            'richtlinien', 'verordnung', 'rahmenwerk',  # Policy/standards
        ])

        self.ACADEMIC_PATTERNS.extend([
            'wissenschaftliche arbeit', 'forschungsarbeit',  # Research types
            'peer-review', 'fachzeitschrift',  # Peer review
            'empirische studie', 'meta-analyse',  # Methodology
            # Chinese database terms frequently appearing in multilingual prompts
            'chinesische datenbank', 'cnki',
        ])

        # Spanish patterns
        self.INDUSTRY_PATTERNS.extend([
            'informe', 'libro blanco', 'directrices',  # Document types
            'marco', 'regulación', 'normativa',  # Policy/standards
        ])

        self.ACADEMIC_PATTERNS.extend([
            'artículo académico', 'trabajo de investigación',  # Research types
            'revisión por pares', 'revista académica',  # Peer review
            'estudio empírico', 'metaanálisis',  # Methodology
            # Chinese database terms (commonly kept in romanization)
            'cnki', 'wanfang',
        ])

    def classify_query(self, query: str) -> Tuple[QueryType, float, List[str]]:
        """
        Classify a research query as academic, industry, or mixed.

        Args:
            query: Research query string

        Returns:
            Tuple of (query_type, confidence, matched_patterns)

        Examples:
            >>> router = QueryRouter()
            >>> query_type, confidence, patterns = router.classify_query(
            ...     "McKinsey report on digital transformation"
            ... )
            >>> query_type
            'industry'
            >>> confidence
            0.9
        """
        query_lower = query.lower()

        # Count pattern matches
        industry_matches = [p for p in self.INDUSTRY_PATTERNS if p in query_lower]
        academic_matches = [p for p in self.ACADEMIC_PATTERNS if p in query_lower]

        # Classify based on matches
        if industry_matches and not academic_matches:
            # Clear industry query
            confidence = min(0.9, 0.5 + (len(industry_matches) * 0.1))
            return 'industry', confidence, industry_matches

        elif academic_matches and not industry_matches:
            # Clear academic query
            confidence = min(0.9, 0.5 + (len(academic_matches) * 0.1))
            return 'academic', confidence, academic_matches

        elif industry_matches and academic_matches:
            # Mixed query (both types)
            if len(industry_matches) > len(academic_matches):
                confidence = 0.6
                return 'industry', confidence, industry_matches + academic_matches
            elif len(academic_matches) > len(industry_matches):
                confidence = 0.6
                return 'academic', confidence, industry_matches + academic_matches
            else:
                confidence = 0.5
                return 'mixed', confidence, industry_matches + academic_matches

        else:
            # No clear indicators - default to mixed
            confidence = 0.3
            return 'mixed', confidence, []

    def assess_query_quality(self, query: str) -> Tuple[str, bool, str]:
        """Assess whether a query is well-formed enough for provider search.

        Returns:
            (quality, should_query, rewrite_hint)
        """
        q = (query or "").strip()
        if not q:
            return ('low', False, 'empty query')

        q_lower = q.lower()
        tokens = [t for t in re.split(r'\s+', q) if t]
        has_zh = bool(re.search(r'[\u4e00-\u9fff]', q))
        has_en = bool(re.search(r'[A-Za-z]{3,}', q))

        if any(p in q_lower for p in self.NOISE_PATTERNS):
            return ('low', False, 'contains obvious noise/media terms')

        # Mixed-script corruption inside a token is a strong signal of malformed planner output.
        malformed_mixed_token = re.search(r'[A-Za-z]+[\u4e00-\u9fff]+[A-Za-z]+|[\u4e00-\u9fff]+[A-Za-z]{2,}[\u4e00-\u9fff]+', q)
        if malformed_mixed_token:
            return ('low', False, 'contains malformed mixed-script token')

        if len(tokens) < 2 and len(q) < 8:
            return ('low', False, 'underspecified query')

        if len(tokens) > 18 or len(q) > 180:
            return ('medium', True, 'query too long; prefer normalized rewrite')

        policy_hits = [p for p in self.POLICY_PATTERNS if p in q_lower or p in q]
        academic_hits = [p for p in self.ACADEMIC_PATTERNS if p in q_lower]
        industry_hits = [p for p in self.INDUSTRY_PATTERNS if p in q_lower]

        if has_zh and has_en and not (academic_hits or industry_hits or policy_hits):
            return ('medium', True, 'mixed-language query should be normalized into aligned bilingual phrases')

        if policy_hits and not academic_hits and not industry_hits:
            return ('medium', True, 'policy/institution query; use narrower provider budget')

        if academic_hits or industry_hits or policy_hits or len(tokens) >= 3:
            return ('high', True, '')

        return ('medium', True, 'generic query; enrich with topic + method/entity terms')

    def get_api_chain(self, query_type: QueryType) -> List[APIName]:
        """
        Get prioritized API chain for a query type.

        Args:
            query_type: Classified query type

        Returns:
            List of API names in priority order

        API Priority Chains:
        - industry: Crossref → OpenAlex → OpenAIRE → CORE → DOAJ → Semantic Scholar
        - academic: Crossref → OpenAlex → OpenAIRE → CORE → DOAJ → Semantic Scholar
        - mixed: OpenAlex → Crossref → OpenAIRE → CORE → DOAJ → Semantic Scholar
        """
        if query_type == 'industry':
            return ['crossref', 'openalex', 'openaire', 'core', 'doaj', 'semantic_scholar']

        elif query_type == 'academic':
            return ['crossref', 'openalex', 'openaire', 'core', 'doaj', 'semantic_scholar']

        else:  # mixed
            return ['openalex', 'crossref', 'openaire', 'core', 'doaj', 'semantic_scholar']

    def classify_and_route(self, query: str) -> QueryClassification:
        """
        Classify query and return complete routing information.

        This is the main entry point for query routing.

        Args:
            query: Research query string

        Returns:
            QueryClassification with type, confidence, patterns, and API chain

        Examples:
            >>> router = QueryRouter()
            >>> result = router.classify_and_route("WHO COVID-19 guidelines")
            >>> result.query_type
            'industry'
            >>> result.api_chain[0]
            'crossref'
        """
        query_type, confidence, patterns = self.classify_query(query)
        quality, should_query, rewrite_hint = self.assess_query_quality(query)
        api_chain = self.get_api_chain(query_type)

        # Lower-value queries should use smaller, cheaper chains.
        if query_type == 'industry':
            api_chain = ['crossref', 'openalex', 'doaj']
        if any(p in (query.lower() if query else '') or p in (query or '') for p in self.POLICY_PATTERNS):
            api_chain = ['crossref', 'openalex', 'doaj']
        if quality == 'medium':
            api_chain = [a for a in api_chain if a != 'semantic_scholar']
        if quality == 'low':
            api_chain = ['crossref', 'openalex']

        # Chinese-topic routing: prioritize stable academic APIs first
        if re.search(r'[\u4e00-\u9fff]', query or ""):
            ordered = ['crossref', 'openalex', 'doaj', 'openaire', 'core', 'semantic_scholar']
            api_chain = [a for a in ordered if a in api_chain] + [a for a in api_chain if a not in ordered]

        return QueryClassification(
            query_type=query_type,
            confidence=confidence,
            matched_patterns=patterns,
            api_chain=api_chain,
            query_quality=quality,
            should_query=should_query,
            rewrite_hint=rewrite_hint,
        )


# ============================================================================
# STANDALONE TESTING
# ============================================================================

def main():
    """Test QueryRouter with sample queries."""
    router = QueryRouter()

    test_queries = [
        # Industry queries
        "McKinsey digital transformation report 2023",
        "Gartner magic quadrant for cloud providers",
        "WHO COVID-19 vaccination guidelines",
        "European Commission AI regulation framework",
        "OECD economic outlook 2024",

        # Academic queries
        "peer-reviewed studies on climate change mitigation",
        "systematic review of carbon pricing mechanisms",
        "empirical analysis of renewable energy adoption",
        "meta-analysis of COVID-19 vaccine efficacy",

        # Mixed queries
        "blockchain technology best practices",
        "artificial intelligence ethics frameworks",
        "cybersecurity incident response strategies",
    ]

    print("="*80)
    print("QUERY ROUTER - CLASSIFICATION TEST")
    print("="*80)

    for query in test_queries:
        result = router.classify_and_route(query)

        print(f"\nQuery: {query}")
        print(f"  Type: {result.query_type} (confidence: {result.confidence:.2f})")
        print(f"  API Chain: {' → '.join(result.api_chain)}")
        if result.matched_patterns:
            print(f"  Patterns: {', '.join(result.matched_patterns[:3])}...")


if __name__ == '__main__':
    main()
