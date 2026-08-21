"""
JobRadar v2 — Normalizer
Maps every source's raw output into the common normalized schema before
dedup and scoring. Also extracts experience_required_min via regex.

v2 changes:
  - 6-tier priority_tier resolution (replaces 2-tier role_tier)
  - Region detection: mumbai_metro, navi_mumbai, thane, pune, india_remote, global_remote
  - Backward compat: role_tier field still populated (tiers 1-3 → tier1_core_data, 4-6 → tier2_broader)
"""
import hashlib
import logging
import re
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# YOE extraction (from §7 of the brief)
# ─────────────────────────────────────────────────────────────────────────────

YOE_PATTERNS = [
    r"(\d+)\s*\+\s*years?",                     # "4+ years"
    r"(\d+)\s*-\s*(\d+)\s*years?",              # "2-5 years"
    r"minimum\s*(?:of\s*)?(\d+)\s*years?",      # "minimum of 3 years"
    r"at least\s*(\d+)\s*years?",               # "at least 5 years"
    r"(\d+)\s*to\s*(\d+)\s*years?",             # "1 to 3 years"
    r"(\d+)\s*yrs?",                            # "2 yrs"
]

JUNIOR_SIGNALS = re.compile(
    r"\b(fresher|freshers|entry.level|junior|jr\.|associate|graduate program|"
    r"accelerator|trainee|0.1|0 to 1|zero to one|recent graduate)\b",
    re.IGNORECASE,
)


def extract_min_yoe(jd_text: str) -> int | None:
    if not jd_text:
        return None
    text = jd_text.lower()
    candidates = []
    for pattern in YOE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            candidates.append(int(m.group(1)))
    if JUNIOR_SIGNALS.search(text):
        candidates.append(0)
    return min(candidates) if candidates else None


# ─────────────────────────────────────────────────────────────────────────────
# Stable job_id hash
# ─────────────────────────────────────────────────────────────────────────────

def make_job_id(source: str, company: str, title: str, location: str) -> str:
    """Content-based hash — immune to source re-issuing listing IDs."""
    raw = f"{source}|{company.lower().strip()}|{title.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


# ─────────────────────────────────────────────────────────────────────────────
# Per-source field extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_str(val: Any, limit: int | None = None) -> str:
    s = str(val).strip() if val is not None and str(val) not in ("nan", "None", "") else ""
    return s[:limit] if limit else s


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val if isinstance(val, date) else val.date()
    try:
        return datetime.fromisoformat(str(val)).date()
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(str(val)[:20], fmt).date()
        except Exception:
            pass
    return None


def _extract_linkedin(raw: dict) -> dict:
    return {
        "title": _safe_str(raw.get("title") or raw.get("jobTitle")),
        "company": _safe_str(raw.get("companyName") or raw.get("company")),
        "location": _safe_str(raw.get("location")),
        "url": _safe_str(raw.get("url") or raw.get("applyUrl")),
        "posted_date": _parse_date(raw.get("postedDate") or raw.get("publishedAt")),
        "experience_required_text": _safe_str(raw.get("experienceLevel")),
        "salary_text": _safe_str(raw.get("salary") or raw.get("salaryText")),
        "salary_currency": "",
        "description_text": _safe_str(raw.get("description"), limit=4000),
        "is_startup": False,
    }


def _extract_naukri(raw: dict) -> dict:
    return {
        "title": _safe_str(raw.get("title") or raw.get("jobTitle")),
        "company": _safe_str(raw.get("company") or raw.get("companyName")),
        "location": _safe_str(raw.get("location")),
        "url": _safe_str(raw.get("url") or raw.get("jobUrl")),
        "posted_date": _parse_date(raw.get("postedDate") or raw.get("createdDate")),
        "experience_required_text": _safe_str(
            raw.get("experience") or raw.get("experienceRequired") or raw.get("minExperience", "")
        ),
        "salary_text": _safe_str(raw.get("salary") or raw.get("salaryDetail")),
        "salary_currency": "INR",
        "description_text": _safe_str(raw.get("description") or raw.get("jobDescription"), limit=4000),
        "is_startup": False,
    }


def _extract_jobspy(raw: dict) -> dict:
    return {
        "title": _safe_str(raw.get("title")),
        "company": _safe_str(raw.get("company")),
        "location": _safe_str(raw.get("location")),
        "url": _safe_str(raw.get("job_url") or raw.get("url")),
        "posted_date": _parse_date(raw.get("date_posted") or raw.get("posted_date")),
        "experience_required_text": _safe_str(raw.get("job_level") or ""),
        "salary_text": _safe_str(raw.get("min_amount", "")),
        "salary_currency": _safe_str(raw.get("currency", "")),
        "description_text": _safe_str(raw.get("description"), limit=4000),
        "is_startup": False,
    }


def _extract_watchlist(raw: dict) -> dict:
    """Extractor for jobs found via company watchlist career page scraping."""
    return {
        "title": _safe_str(raw.get("title")),
        "company": _safe_str(raw.get("company")),
        "location": _safe_str(raw.get("location", "")),
        "url": _safe_str(raw.get("url", "")),
        "posted_date": _parse_date(raw.get("posted_date")),
        "experience_required_text": "",
        "salary_text": "",
        "salary_currency": "",
        "description_text": _safe_str(raw.get("description", ""), limit=4000),
        "is_startup": False,
    }


_EXTRACTORS = {
    "linkedin": _extract_linkedin,
    "naukri": _extract_naukri,
    "indeed": _extract_jobspy,
    "glassdoor": _extract_jobspy,
    "google": _extract_jobspy,
    "zip_recruiter": _extract_jobspy,
    "jobspy": _extract_jobspy,
    "watchlist": _extract_watchlist,
}


# ─────────────────────────────────────────────────────────────────────────────
# Region detection (v2)
# ─────────────────────────────────────────────────────────────────────────────

# Mumbai suburbs / areas for metro detection
_MUMBAI_AREAS = [
    "andheri", "powai", "bkc", "bandra kurla", "goregaon", "malad",
    "lower parel", "dadar", "worli", "vikhroli", "borivali", "jogeshwari",
    "kandivali", "chembur", "ghatkopar", "mulund", "bandra", "santacruz",
    "juhu", "marol", "saki naka", "kurla", "wadala", "parel",
    "mumbai", "bombay",
]

_NAVI_MUMBAI_AREAS = [
    "airoli", "vashi", "ghansoli", "cbd belapur", "belapur",
    "navi mumbai", "new mumbai", "nerul", "kharghar", "panvel",
    "seawoods", "sanpada",
]

_THANE_AREAS = [
    "thane", "dombivli", "kalyan", "bhiwandi", "ulhasnagar",
]

_PUNE_AREAS = [
    "hinjewadi", "kharadi", "baner", "magarpatta", "pune", "pimpri",
    "chinchwad", "hadapsar", "wakad", "aundh", "viman nagar",
    "koregaon park", "shivajinagar",
]


def detect_region(location: str, category: str) -> str:
    """
    Detects the region from job location string and source category.

    Returns one of: mumbai_metro, navi_mumbai, thane, pune,
                     india_remote, global_remote
    """
    if not location:
        # Fall back to source category
        if category in ("mumbai", "mumbai_local"):
            return "mumbai_metro"
        if category in ("pune", "pune_local"):
            return "pune"
        if category == "naukri":
            return "india_remote"  # naukri can be anywhere in India
        if category == "naukri_pune":
            return "pune"
        if category == "india_remote":
            return "india_remote"
        return "global_remote"

    loc_lower = location.lower()

    # Check specific sub-regions first (before broad "mumbai" match)
    for area in _NAVI_MUMBAI_AREAS:
        if area in loc_lower:
            return "navi_mumbai"

    for area in _THANE_AREAS:
        if area in loc_lower:
            return "thane"

    for area in _PUNE_AREAS:
        if area in loc_lower:
            return "pune"

    for area in _MUMBAI_AREAS:
        if area in loc_lower:
            return "mumbai_metro"

    # India-level detection
    if "india" in loc_lower:
        if "remote" in loc_lower:
            return "india_remote"
        return "india_remote"  # India but not a specific city → india_remote

    if "remote" in loc_lower:
        return "global_remote"

    # Naukri source defaults
    if category in ("naukri", "naukri_pune"):
        return "india_remote"

    return "global_remote"


# ─────────────────────────────────────────────────────────────────────────────
# 6-Tier Priority Resolution (v2 — replaces 2-tier role_tier)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_priority_tier(title: str, jd_text: str, config: dict) -> tuple[int, str]:
    """
    Resolves the priority tier (1–6) for a job based on title matching
    against role_priorities config. Falls back to keyword-overlap if
    no exact title hit.

    Returns (tier_number, tier_name).
    Default: tier 6 if no match found.
    """
    role_priorities = config.get("role_priorities", [])
    if not role_priorities:
        return 2, "Data / Product Analytics"  # safe default

    title_lower = title.lower().strip()

    # ── Pass 1: Exact title match (case-insensitive) ──────────────────────────
    for tier_config in role_priorities:
        tier_num = tier_config["tier"]
        tier_name = tier_config["name"]
        for t in tier_config.get("titles", []):
            if t.lower().strip() == title_lower:
                return tier_num, tier_name

    # ── Pass 2: Substring title match ─────────────────────────────────────────
    for tier_config in role_priorities:
        tier_num = tier_config["tier"]
        tier_name = tier_config["name"]
        for t in tier_config.get("titles", []):
            tl = t.lower().strip()
            # Check if either contains the other
            if tl in title_lower or title_lower in tl:
                return tier_num, tier_name

    # ── Pass 3: Keyword-overlap fallback ──────────────────────────────────────
    combined = f"{title_lower} {(jd_text or '').lower()[:2000]}"
    best_tier = None
    best_score = 0

    for tier_config in role_priorities:
        tier_num = tier_config["tier"]
        tier_name = tier_config["name"]
        keywords = tier_config.get("signal_keywords", [])
        if not keywords:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in combined)
        # Weight by keyword coverage ratio
        coverage = hits / len(keywords) if keywords else 0
        if coverage > best_score:
            best_score = coverage
            best_tier = (tier_num, tier_name)

    # Require at least 15% keyword overlap to assign a tier
    if best_tier and best_score >= 0.15:
        return best_tier

    return 6, "AI Startups / AI+GTM / Growth"  # default fallback


def _tier_to_legacy_role_tier(tier_num: int) -> str:
    """Maps 6-tier number to legacy 2-tier role_tier for backward compat."""
    if tier_num <= 3:
        return "tier1_core_data"
    return "tier2_broader"


# ─────────────────────────────────────────────────────────────────────────────
# Main normalizer
# ─────────────────────────────────────────────────────────────────────────────

def normalize(raw: dict, today: date | None = None, config: dict | None = None) -> dict | None:
    """
    Maps a raw scraped dict into the common normalized job schema.
    Returns None if the record is missing essential fields (title + company).
    """
    today = today or date.today()
    config = config or {}
    source = raw.get("_source", "jobspy").lower()
    category = raw.get("_category", "global_remote")

    extractor = _EXTRACTORS.get(source, _extract_jobspy)
    try:
        fields = extractor(raw)
    except Exception as e:
        logger.warning(f"Extractor failed for source={source}: {e}")
        return None

    title = fields["title"]
    company = fields["company"]
    location = fields["location"]

    if not title or not company:
        return None  # skip useless records

    # v2: Resolve 6-tier priority
    jd_text = fields["description_text"] or ""
    priority_tier, priority_tier_name = resolve_priority_tier(title, jd_text, config)

    # Backward compat: legacy role_tier
    role_tier = _tier_to_legacy_role_tier(priority_tier)

    # v2: Detect region
    region = detect_region(location, category)

    exp_text = fields["experience_required_text"] or ""
    full_exp_text = f"{exp_text} {jd_text[:2000]}"  # check both exp field + beginning of JD
    min_yoe = extract_min_yoe(full_exp_text)

    # Days since posted (for recency scoring)
    posted = fields["posted_date"]
    days_old = (today - posted).days if posted else None

    return {
        "job_id": make_job_id(source, company, title, location),
        "source": source,
        "category": category,
        "role_tier": role_tier,                     # legacy compat
        "priority_tier": priority_tier,             # v2: 1–6
        "priority_tier_name": priority_tier_name,   # v2: human-readable
        "region": region,                           # v2: mumbai_metro / pune / etc.
        "title": title,
        "company": company,
        "location": location,
        "posted_date": str(posted) if posted else "",
        "days_old": days_old,
        "url": fields["url"],
        "experience_required_text": exp_text,
        "experience_required_min": min_yoe,
        "salary_text": fields["salary_text"],
        "salary_currency": fields["salary_currency"],
        "description_text": jd_text,
        "is_startup": fields["is_startup"],
        "first_seen_date": str(today),
        "last_seen_date": str(today),
        # Validation fields (populated by validator.py)
        "validation_status": "pending",
        "validation_note": "",
    }


def normalize_all(raw_jobs: list[dict], today: date | None = None, config: dict | None = None) -> list[dict]:
    """Normalize a list of raw jobs. Skips and logs any that fail."""
    today = today or date.today()
    config = config or {}
    normalized = []
    skipped = 0
    for raw in raw_jobs:
        result = normalize(raw, today, config)
        if result:
            normalized.append(result)
        else:
            skipped += 1
    logger.info(f"Normalized {len(normalized)} jobs; skipped {skipped} (missing title/company)")
    return normalized
