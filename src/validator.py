"""
JobRadar — Company Page Validator

Cross-validates scraped job listings against the company's actual careers page
to detect stale/deleted postings before they waste Nagesh's time.

Strategy:
  1. Extract a "careers URL" candidate from the job's URL or by searching
     for "{company} careers site" heuristics.
  2. Fetch the page and check if the job title appears in the HTML.
  3. Flag the job with validation_status: "live" | "stale" | "unverified"

Priority: run validation for jobs with stage_a_score >= config threshold.
On failure (timeout / 403 / CAPTCHA): flag as "unverified" — never exclude
a job just because the careers page blocked us.

Naukri listings get priority validation since Naukri sometimes shows roles
that have been closed but not removed from the index.
"""
import logging
import re
import time
import urllib.parse
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 10
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

KNOWN_ATS_PATTERNS = {
    # Maps ATS domain patterns → careers subdomain patterns
    "greenhouse.io": "boards.greenhouse.io",
    "lever.co": "jobs.lever.co",
    "ashbyhq.com": "jobs.ashbyhq.com",
    "workday.com": "wd3.myworkdayjobs.com",
    "taleo.net": None,   # too complex, skip direct validation
    "successfactors.com": None,
    "naukri.com": "naukri.com",
    "linkedin.com": None,   # LinkedIn requires login for full description
}


# ─────────────────────────────────────────────────────────────────────────────
# Main validation function
# ─────────────────────────────────────────────────────────────────────────────

def validate_jobs(
    jobs: list[dict],
    config: dict,
    min_score_to_validate: float = 5.0,
) -> list[dict]:
    """
    Validate listing liveness for jobs above the score threshold.
    Modifies jobs in-place (sets validation_status and validation_note).
    Returns the same list.
    """
    val_cfg = config.get("validation", {})
    if not val_cfg.get("enabled", True):
        for job in jobs:
            job["validation_status"] = "skipped"
        return jobs

    timeout = val_cfg.get("request_timeout_sec", DEFAULT_TIMEOUT)
    user_agent = val_cfg.get("user_agent", DEFAULT_UA)
    stale_label = val_cfg.get("stale_label", "⚠️ Possibly Stale")

    to_validate = [j for j in jobs if (j.get("stage_a_score") or 0) >= min_score_to_validate]
    skip_count = len(jobs) - len(to_validate)
    logger.info(
        f"Validation: checking {len(to_validate)} jobs (skipping {skip_count} below score threshold)"
    )

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"})

    for job in to_validate:
        try:
            status, note = _validate_single(job, session, timeout)
            job["validation_status"] = status
            job["validation_note"] = note
            if status == "stale":
                job["validation_flag"] = stale_label
        except Exception as e:
            logger.warning(f"Validation error for job {job.get('job_id')}: {e}")
            job["validation_status"] = "unverified"
            job["validation_note"] = f"Error: {e}"

        time.sleep(1.5)  # polite delay between career page requests

    # Jobs below threshold
    for job in jobs:
        if "validation_status" not in job or job["validation_status"] == "pending":
            job["validation_status"] = "below_threshold"
            job["validation_note"] = "Score too low to validate"

    return jobs


def _validate_single(job: dict, session: requests.Session, timeout: int) -> tuple[str, str]:
    """
    Returns (status, note) for a single job.
    status: "live" | "stale" | "unverified"
    """
    url = job.get("url", "")
    title = job.get("title", "")
    company = job.get("company", "")
    source = job.get("source", "")

    if not url:
        return "unverified", "No job URL available"

    # ── Step 1: Check if the job's direct URL is still live ──────────────────
    direct_status, direct_note = _check_direct_url(url, title, session, timeout)

    if direct_status == "live":
        return "live", "Job URL still active and title found"

    if direct_status == "stale":
        return "stale", f"Job URL returned 404 or title missing: {direct_note}"

    # ── Step 2: If direct URL inconclusive, check company careers page ────────
    careers_url = _find_careers_url(company, url, source)
    if not careers_url:
        return "unverified", f"Direct URL check inconclusive ({direct_note}); could not find careers page"

    careers_status, careers_note = _check_careers_page(careers_url, title, session, timeout)
    if careers_status == "live":
        return "live", f"Title found on careers page: {careers_url}"
    elif careers_status == "stale":
        return "stale", f"Title not found on careers page: {careers_url}"
    else:
        return "unverified", f"Careers page check inconclusive: {careers_note}"


def _check_direct_url(url: str, title: str, session: requests.Session, timeout: int) -> tuple[str, str]:
    """Check if the job's direct application URL is still live."""
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)

        if resp.status_code == 404:
            return "stale", "HTTP 404"

        if resp.status_code in (403, 429, 401):
            return "unverified", f"HTTP {resp.status_code} — access blocked"

        if resp.status_code >= 500:
            return "unverified", f"HTTP {resp.status_code} — server error"

        # Check if the title appears in the response body
        if title and len(title) > 3:
            title_words = title.lower().split()[:4]  # first 4 words of title
            page_lower = resp.text.lower()
            matches = sum(1 for w in title_words if w in page_lower)
            if matches >= min(2, len(title_words)):
                return "live", "Title found in page body"
            # Check for common "job closed" signals
            closed_signals = [
                "this job is no longer available",
                "this position has been filled",
                "this job has expired",
                "application closed",
                "no longer accepting applications",
                "position is filled",
                "job not found",
                "vacancy closed",
            ]
            page_snippet = resp.text.lower()[:5000]
            if any(sig in page_snippet for sig in closed_signals):
                return "stale", "Closed/expired signal found in page"

        return "unverified", "URL returned 200 but title match inconclusive"

    except requests.exceptions.Timeout:
        return "unverified", "Request timed out"
    except requests.exceptions.ConnectionError:
        return "unverified", "Connection error"
    except Exception as e:
        return "unverified", str(e)


def _find_careers_url(company: str, job_url: str, source: str) -> Optional[str]:
    """
    Attempts to derive a company careers page URL from the job URL.
    Falls back to common patterns like company.com/careers.
    """
    if not job_url:
        return None

    # If the URL is from a known ATS, the direct URL check is usually sufficient
    parsed = urllib.parse.urlparse(job_url)
    domain = parsed.netloc.lower().replace("www.", "")

    for ats_domain, ats_pattern in KNOWN_ATS_PATTERNS.items():
        if ats_domain in domain:
            if ats_pattern is None:
                return None  # skip — ATS blocks validation
            # For ATS like greenhouse, the job URL IS the careers page
            return job_url

    # For Naukri, try to extract the company page
    if source == "naukri" and "naukri.com" in domain:
        # Try to construct company search URL
        company_slug = re.sub(r"[^a-z0-9]", "-", company.lower()).strip("-")
        return f"https://www.naukri.com/{company_slug}-jobs"

    # Generic: try {company}.com/careers and {company}.com/jobs
    company_slug = re.sub(r"[^a-z0-9]", "", company.lower())
    if len(company_slug) > 2:
        return f"https://www.{company_slug}.com/careers"

    return None


def _check_careers_page(careers_url: str, title: str, session: requests.Session, timeout: int) -> tuple[str, str]:
    """Check if the job title appears on the company's careers/jobs page."""
    try:
        resp = session.get(careers_url, timeout=timeout, allow_redirects=True)
        if resp.status_code in (404, 403, 429, 401):
            return "unverified", f"Careers page returned HTTP {resp.status_code}"

        if not title or len(title) < 3:
            return "unverified", "No title to match against"

        page_lower = resp.text.lower()
        title_words = title.lower().split()[:4]
        matches = sum(1 for w in title_words if len(w) > 2 and w in page_lower)
        threshold = max(1, min(2, len(title_words) - 1))

        if matches >= threshold:
            return "live", f"Title ({matches}/{len(title_words)} words) found on careers page"
        else:
            return "stale", f"Title not found on careers page ({matches}/{len(title_words)} words matched)"

    except Exception as e:
        return "unverified", str(e)
