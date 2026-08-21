"""
JobRadar v2 — Company Watchlist

Periodically checks career pages of watched companies for new job openings
matching any of the 6 role-priority tiers.

Emits jobs into the normalized schema so they flow through the same
dedup/score/sheet pipeline as scraped jobs.

Cache: Per-company check time stored in .watchlist_cache.json — skip if
checked less than 20h ago to keep this cheap.
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
import yaml

logger = logging.getLogger(__name__)


def load_watchlist(config_file: str = "company_watchlist.yaml") -> list[dict]:
    """Loads the company watchlist from YAML file."""
    path = Path(config_file)
    if not path.exists():
        logger.warning(f"Company watchlist not found: {config_file}")
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or []
        return data
    except Exception as e:
        logger.error(f"Failed to load company watchlist: {e}")
        return []


def load_cache(cache_file: str = ".watchlist_cache.json") -> dict:
    """Loads check timestamps from cache file."""
    path = Path(cache_file)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache: dict, cache_file: str = ".watchlist_cache.json") -> None:
    """Saves check timestamps to cache file."""
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save watchlist cache: {e}")


def _should_check(company_name: str, cache: dict, min_interval_hours: float = 20) -> bool:
    """Returns True if this company should be checked (not checked recently)."""
    last_check = cache.get(company_name)
    if last_check is None:
        return True
    try:
        last_dt = datetime.fromisoformat(last_check)
        hours_since = (datetime.now() - last_dt).total_seconds() / 3600
        return hours_since >= min_interval_hours
    except Exception:
        return True


def _fetch_careers_page(url: str, timeout: int = 10) -> Optional[str]:
    """Fetches a careers page and returns the HTML text."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (JobRadar watchlist bot; contact: nageshkhichade00@gmail.com)",
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"Careers page {url} returned HTTP {resp.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch careers page {url}: {e}")
        return None


def _extract_jobs_from_page(
    html: str,
    company_name: str,
    careers_url: str,
    role_priorities: list[dict],
) -> list[dict]:
    """
    Scans HTML for job title matches against all 6 tier title lists.
    Returns raw job dicts in the normalized schema format.
    """
    if not html:
        return []

    html_lower = html.lower()
    found_jobs = []
    seen_titles = set()

    for tier_config in role_priorities:
        tier_num = tier_config["tier"]
        tier_name = tier_config["name"]

        for title_pattern in tier_config.get("titles", []):
            tp_lower = title_pattern.lower()

            # Check if this title appears in the page
            if tp_lower in html_lower and tp_lower not in seen_titles:
                seen_titles.add(tp_lower)

                # Try to extract a URL for this specific job
                # Look for links near the title text
                job_url = _find_job_link(html, title_pattern, careers_url) or careers_url

                found_jobs.append({
                    "_source": "watchlist",
                    "_role_tier": "tier1_core_data" if tier_num <= 3 else "tier2_broader",
                    "_priority_tier": tier_num,
                    "_priority_tier_name": tier_name,
                    "_category": "watchlist",
                    "_search_title": title_pattern,
                    "title": title_pattern,
                    "company": company_name,
                    "location": "",  # often not on careers pages
                    "url": job_url,
                    "description": "",
                    "posted_date": None,
                })

    return found_jobs


def _find_job_link(html: str, title: str, base_url: str) -> Optional[str]:
    """Tries to find a specific job link near the title on the page."""
    try:
        # Look for <a> tags containing the title text
        pattern = rf'<a[^>]*href=["\']([^"\']+)["\'][^>]*>[^<]*{re.escape(title)}[^<]*</a>'
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            href = match.group(1)
            if href.startswith("http"):
                return href
            if href.startswith("/"):
                from urllib.parse import urljoin
                return urljoin(base_url, href)
    except Exception:
        pass
    return None


def run_watchlist_checks(config: dict) -> list[dict]:
    """
    Main entry point. Checks all companies in the watchlist that are due.
    Returns raw job dicts ready for normalization.
    """
    wl_config = config.get("company_watchlist", {})
    if not wl_config.get("enabled", True):
        logger.info("Company watchlist disabled")
        return []

    config_file = wl_config.get("config_file", "company_watchlist.yaml")
    cache_file = wl_config.get("cache_file", ".watchlist_cache.json")
    min_interval = wl_config.get("min_check_interval_hours", 20)
    timeout = wl_config.get("request_timeout_sec", 10)

    watchlist = load_watchlist(config_file)
    if not watchlist:
        return []

    cache = load_cache(cache_file)
    role_priorities = config.get("role_priorities", [])
    all_jobs = []
    checked = 0

    for entry in watchlist:
        name = entry.get("name", "")
        url = entry.get("careers_page_url", "")

        if not name or not url:
            continue

        if not _should_check(name, cache, min_interval):
            logger.debug(f"Watchlist: skipping {name} (checked recently)")
            continue

        logger.info(f"Watchlist: checking {name} ({url})")
        html = _fetch_careers_page(url, timeout)

        if html:
            jobs = _extract_jobs_from_page(html, name, url, role_priorities)
            all_jobs.extend(jobs)
            logger.info(f"Watchlist: found {len(jobs)} potential matches at {name}")

        # Update cache
        cache[name] = datetime.now().isoformat()
        checked += 1
        time.sleep(2)  # polite delay between requests

    save_cache(cache, cache_file)
    logger.info(f"Watchlist: checked {checked} companies, found {len(all_jobs)} total jobs")
    return all_jobs
