"""
JobRadar v2 — AI-Exposure / AI-Company Signal Detector

Scans JD text for AI/ML/GenAI signal terms to produce an ai_exposure_score
(0–100). This feeds the "AI/Technology Exposure" component (10% weight)
in the v2 scoring formula.

Entirely rule-based. No paid API calls. Outside the budget guard entirely.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# AI Signal Terms (from v2 spec)
# ─────────────────────────────────────────────────────────────────────────────

AI_SIGNAL_TERMS = [
    # Core AI/ML
    r"\bAI\b",
    r"\bML\b",
    r"\bGenAI\b",
    r"\bgen[\s-]?AI\b",
    r"\bLLM\b",
    r"\bLLMs\b",
    r"\bRAG\b",
    r"\bAI agents?\b",
    r"\bagentic AI\b",
    r"\bAI automation\b",
    r"\bAI tools?\b",
    r"\bAI[- ]assisted\b",
    # Specific tools/platforms
    r"\bCopilot\b",
    r"\bOpenAI\b",
    r"\bClaude\b",
    r"\bGemini\b",
    r"\bLangChain\b",
    r"\bLlamaIndex\b",
    r"\bHugging\s*Face\b",
    # Technical concepts
    r"\bembeddings?\b",
    r"\bvector\s*database\b",
    r"\bvector\s*DB\b",
    r"\bprompt\s*engineering\b",
    r"\bintelligent\s*automation\b",
    r"\bAI\s*workflows?\b",
    r"\bAI\s*product\b",
    r"\bAI\s*transformation\b",
    r"\bAI\s*adoption\b",
    # General AI signals
    r"\bmachine\s*learning\b",
    r"\bdeep\s*learning\b",
    r"\bneural\s*network\b",
    r"\bNLP\b",
    r"\bnatural\s*language\s*processing\b",
    r"\bcomputer\s*vision\b",
    r"\bgenerative\s*AI\b",
    r"\bfoundation\s*model\b",
    r"\bfine[- ]?tun(e|ing)\b",
    r"\btransformer\b",
    r"\bGPT\b",
    r"\bMLOps\b",
    r"\bAI[- ]powered\b",
    r"\bAI[- ]driven\b",
    r"\bAI[- ]native\b",
    r"\bAI[- ]first\b",
]

# Compile patterns for efficiency
_AI_PATTERNS = [re.compile(p, re.IGNORECASE) for p in AI_SIGNAL_TERMS]

# Heavy AI signal terms (worth double weight)
_HEAVY_TERMS = {
    "llm", "genai", "rag", "langchain", "llamaindex", "ai agents",
    "agentic ai", "openai", "prompt engineering", "hugging face",
    "generative ai", "fine-tuning", "fine tuning", "vector database",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_ai_exposure(jd_text: str) -> dict:
    """
    Computes AI-exposure score from JD text using keyword density/coverage.

    Returns:
        dict with keys:
        - ai_exposure_score: float 0–100
        - ai_terms_found: list[str] (unique terms detected)
        - ai_term_count: int (total weighted hits)
    """
    if not jd_text:
        return {
            "ai_exposure_score": 0.0,
            "ai_terms_found": [],
            "ai_term_count": 0,
        }

    terms_found = set()
    weighted_count = 0

    for pattern in _AI_PATTERNS:
        matches = pattern.findall(jd_text)
        if matches:
            # Normalize the term name
            term = matches[0].strip().lower()
            terms_found.add(term)
            # Check if it's a heavy-weight term
            if term in _HEAVY_TERMS:
                weighted_count += 2
            else:
                weighted_count += 1

    # Also check for heavy terms that might need multi-word matching
    jd_lower = jd_text.lower()
    for heavy in _HEAVY_TERMS:
        if heavy in jd_lower and heavy not in terms_found:
            terms_found.add(heavy)
            weighted_count += 2

    # Score calculation:
    # - Number of unique terms found determines the base score
    # - Weighted count adds density bonus
    unique_count = len(terms_found)

    if unique_count == 0:
        score = 0.0
    elif unique_count <= 2:
        score = 15.0 + min(10.0, weighted_count * 2)
    elif unique_count <= 4:
        score = 35.0 + min(15.0, weighted_count * 1.5)
    elif unique_count <= 6:
        score = 55.0 + min(15.0, weighted_count)
    elif unique_count <= 10:
        score = 70.0 + min(15.0, weighted_count * 0.5)
    else:
        score = 85.0 + min(15.0, unique_count * 0.8)

    score = round(max(0.0, min(100.0, score)), 1)

    return {
        "ai_exposure_score": score,
        "ai_terms_found": sorted(terms_found),
        "ai_term_count": weighted_count,
    }
