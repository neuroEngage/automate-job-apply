"""
JobRadar v2 — Company Opportunity Score

Computes a 0–100 sub-score from cheap, explainable signals:
  - Funding/stability lookup against funded_companies.yaml
  - Learning/ownership keyword hits in the JD
  - Stability signal from company size/age if available

No paid API calls — pure YAML lookup + keyword matching.
Unknown companies default to a neutral midpoint (50), never zero.
"""
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

FUNDED_COMPANIES_PATH = Path(__file__).parent.parent / "funded_companies.yaml"

# ─────────────────────────────────────────────────────────────────────────────
# Funded companies lookup
# ─────────────────────────────────────────────────────────────────────────────

_funded_cache: Optional[list[dict]] = None


def _load_funded_companies() -> list[dict]:
    """Loads funded_companies.yaml. Cached after first load."""
    global _funded_cache
    if _funded_cache is not None:
        return _funded_cache
    try:
        if FUNDED_COMPANIES_PATH.exists():
            with open(FUNDED_COMPANIES_PATH, encoding="utf-8") as f:
                _funded_cache = yaml.safe_load(f) or []
        else:
            logger.warning(f"funded_companies.yaml not found at {FUNDED_COMPANIES_PATH}")
            _funded_cache = []
    except Exception as e:
        logger.error(f"Failed to load funded_companies.yaml: {e}")
        _funded_cache = []
    return _funded_cache


def _fuzzy_match_company(company_name: str, funded_list: list[dict]) -> Optional[dict]:
    """
    Fuzzy match a company name against the funded companies list.
    Uses lowercased substring matching — good enough for this use case.
    """
    if not company_name:
        return None
    name_lower = company_name.lower().strip()
    # Remove common suffixes for matching
    name_clean = re.sub(r'\b(pvt|ltd|llp|inc|corp|private|limited|technologies|tech|solutions)\b',
                        '', name_lower).strip()

    for entry in funded_list:
        entry_name = entry.get("name", "").lower().strip()
        entry_clean = re.sub(r'\b(pvt|ltd|llp|inc|corp|private|limited|technologies|tech|solutions)\b',
                             '', entry_name).strip()
        # Exact match (cleaned)
        if name_clean == entry_clean:
            return entry
        # One contains the other
        if len(name_clean) > 3 and len(entry_clean) > 3:
            if name_clean in entry_clean or entry_clean in name_clean:
                return entry
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Funding score
# ─────────────────────────────────────────────────────────────────────────────

FUNDING_SCORES = {
    "seed": 40,
    "series a": 55,
    "series b": 65,
    "series c": 75,
    "series c+": 80,
    "public": 85,
    "acquired": 70,
    "bootstrapped": 50,   # neutral — not penalized, not over-ranked
}


def _funding_score(funded_entry: Optional[dict]) -> float:
    """Returns a funding-based score (0–100). Unknown = neutral 50."""
    if funded_entry is None:
        return 50.0  # neutral midpoint — never punish unknown companies

    last_round = (funded_entry.get("last_round", "") or "").lower().strip()
    base = float(FUNDING_SCORES.get(last_round, 50))

    # Boost for large recent funding
    amount = funded_entry.get("amount_usd", 0) or 0
    year = funded_entry.get("year", 2020) or 2020
    if amount > 100_000_000 and year >= 2021:
        base = min(100, base + 10)
    elif amount > 500_000_000:
        base = min(100, base + 15)

    return base


# ─────────────────────────────────────────────────────────────────────────────
# Ownership / learning keyword score
# ─────────────────────────────────────────────────────────────────────────────

OWNERSHIP_KEYWORDS = [
    "ownership", "0 to 1", "0-to-1", "cross-functional", "cross functional",
    "founder", "high-growth", "high growth", "fast-paced", "fast paced",
    "wear many hats", "hands-on", "hands on", "build from scratch",
    "early stage", "greenfield", "autonomous", "end-to-end ownership",
    "take ownership", "drive initiatives",
]


def _ownership_keyword_score(jd_text: str) -> float:
    """Returns 0–100 based on ownership/learning keyword hits in the JD."""
    if not jd_text:
        return 0.0
    jd_lower = jd_text.lower()
    hits = sum(1 for kw in OWNERSHIP_KEYWORDS if kw in jd_lower)
    # Scale: 0 hits = 0, 1 hit = 30, 2 hits = 50, 3+ hits = 70, 5+ = 90, 7+ = 100
    if hits == 0:
        return 0.0
    if hits == 1:
        return 30.0
    if hits == 2:
        return 50.0
    if hits <= 4:
        return 70.0
    if hits <= 6:
        return 90.0
    return 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Company size/stability signal
# ─────────────────────────────────────────────────────────────────────────────

def _size_stability_score(funded_entry: Optional[dict]) -> float:
    """
    Returns a stability/growth score (0–100) from company size.
    Larger = more stability. Smaller + funded = more growth-potential.
    Neither is penalized — both are tracked as positive signals.
    """
    if funded_entry is None:
        return 50.0  # neutral

    size = (funded_entry.get("size", "") or "").lower()
    last_round = (funded_entry.get("last_round", "") or "").lower()

    if size == "large":
        return 70.0   # stability bonus
    elif size == "medium":
        return 60.0
    elif size == "small":
        # Small + funded = growth-potential
        if last_round in ("seed", "series a", "series b"):
            return 65.0  # funded startup = exciting
        return 45.0
    return 50.0  # unknown size


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_company_opportunity(company_name: str, jd_text: str) -> dict:
    """
    Computes the Company Opportunity Score for a single job.

    Returns:
        dict with keys:
        - company_opportunity_score: float 0–100
        - funding_score: float
        - ownership_score: float
        - stability_score: float
        - funded_company_match: str or None (matched company name)
    """
    funded_list = _load_funded_companies()
    match = _fuzzy_match_company(company_name, funded_list)

    funding = _funding_score(match)
    ownership = _ownership_keyword_score(jd_text)
    stability = _size_stability_score(match)

    # Blend: 40% funding + 35% ownership/learning + 25% stability
    overall = round(0.40 * funding + 0.35 * ownership + 0.25 * stability, 1)
    # Clamp to 0–100
    overall = max(0.0, min(100.0, overall))

    return {
        "company_opportunity_score": overall,
        "funding_score": funding,
        "ownership_score": ownership,
        "stability_score": stability,
        "funded_company_match": match.get("name") if match else None,
    }


def reset_cache() -> None:
    """Reset the funded companies cache (for testing)."""
    global _funded_cache
    _funded_cache = None
