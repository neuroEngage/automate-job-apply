"""
JobRadar — Normalizer
Maps every source's raw output into the common normalized schema before
dedup and scoring. Also extracts experience_required_min via regex.
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


_EXTRACTORS = {
    "linkedin": _extract_linkedin,
    "naukri": _extract_naukri,
    "indeed": _extract_jobspy,
    "glassdoor": _extract_jobspy,
    "google": _extract_jobspy,
    "zip_recruiter": _extract_jobspy,
    "jobspy": _extract_jobspy,
}


# ─────────────────────────────────────────────────────────────────────────────
# Main normalizer
# ─────────────────────────────────────────────────────────────────────────────

def normalize(raw: dict, today: date | None = None) -> dict | None:
    """
    Maps a raw scraped dict into the common normalized job schema.
    Returns None if the record is missing essential fields (title + company).
    """
    today = today or date.today()
    source = raw.get("_source", "jobspy").lower()
    role_tier = raw.get("_role_tier", "tier1_core_data")
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

    # Resolve role_tier: if a Tier 1 pattern matches the title, never downgrade to Tier 2
    resolved_tier = _resolve_role_tier(title, role_tier)

    jd_text = fields["description_text"] or ""
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
        "role_tier": resolved_tier,
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


def normalize_all(raw_jobs: list[dict], today: date | None = None) -> list[dict]:
    """Normalize a list of raw jobs. Skips and logs any that fail."""
    today = today or date.today()
    normalized = []
    skipped = 0
    for raw in raw_jobs:
        result = normalize(raw, today)
        if result:
            normalized.append(result)
        else:
            skipped += 1
    logger.info(f"Normalized {len(normalized)} jobs; skipped {skipped} (missing title/company)")
    return normalized


# ─────────────────────────────────────────────────────────────────────────────
# Role tier resolver — Tier 1 takes priority over Tier 2 if title matches
# ─────────────────────────────────────────────────────────────────────────────

_TIER1_KEYWORDS = [
    "data analyst", "data scientist", "data engineer", "ai engineer",
    "ml engineer", "genai", "llm engineer", "prompt engineer",
    "bi developer", "bi analyst", "business intelligence",
    "analytics engineer", "research analyst", "big data",
]

def _resolve_role_tier(title: str, scraped_tier: str) -> str:
    """
    If any Tier 1 keyword matches the job title, force tier1_core_data —
    never downgrade a genuine data role just because a Tier 2 search surfaced it.
    """
    if scraped_tier == "tier1_core_data":
        return "tier1_core_data"
    tl = title.lower()
    for kw in _TIER1_KEYWORDS:
        if kw in tl:
            return "tier1_core_data"
    return "tier2_broader"
