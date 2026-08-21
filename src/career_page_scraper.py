"""
JobRadar v2 — Direct Company Career Page & ATS Scraper

Bypasses LinkedIn/third-party platform scraping limitations by querying:
  1. Public ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters) directly.
  2. Company career portals with JSON-LD (schema.org/JobPosting) & HTML extraction.
  3. Direct career page links from company_watchlist.yaml & funded_companies.yaml.

Returns normalized job listings that seamlessly flow into the JobRadar scoring engine.
"""
import json
import logging
import re
import urllib.parse
from datetime import date
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Common browser user agents for direct HTTP requests
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Known ATS slug mappings for prominent tech/AI companies
KNOWN_ATS_COMPANIES = {
    # Greenhouse
    "fractal": ("greenhouse", "fractalanalytics"),
    "atlan": ("greenhouse", "atlan"),
    "hasura": ("greenhouse", "hasura"),
    "postman": ("greenhouse", "postman"),
    "observeai": ("greenhouse", "observeai"),
    "browserstack": ("greenhouse", "browserstack"),
    "freshworks": ("greenhouse", "freshworks"),
    "sprinklr": ("greenhouse", "sprinklr"),
    # Lever
    "rubixe": ("lever", "rubixe"),
    "scribbledata": ("lever", "scribbledata"),
    "sigmoid": ("lever", "sigmoid"),
    # Ashby
    "sarvam": ("ashby", "sarvamai"),
    "krutrim": ("ashby", "krutrim"),
    "5x": ("ashby", "5x"),
}


def _get_headers() -> dict:
    import random
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


# ─────────────────────────────────────────────────────────────────────────────
# ATS Direct API Scrapers (Free, Public, High-Reliability)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_greenhouse_api(board_token: str, company_name: str, role_priorities: list[dict]) -> list[dict]:
    """Scrapes jobs directly from Greenhouse public API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"
    jobs = []
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=12)
        if resp.status_code != 200:
            return []
        data = resp.json()
        for item in data.get("jobs", []):
            title = item.get("title", "").strip()
            if not title:
                continue
            tier_num, tier_name = _match_role_tier(title, role_priorities)
            location = (item.get("location") or {}).get("name", "")
            job_url = item.get("absolute_url", "")
            content = _clean_html(item.get("content", ""))

            jobs.append({
                "_source": "career_page_ats",
                "_role_tier": "tier1_core_data" if tier_num <= 3 else "tier2_broader",
                "_priority_tier": tier_num,
                "_priority_tier_name": tier_name,
                "_category": "company_careers",
                "title": title,
                "company": company_name,
                "location": location,
                "url": job_url,
                "description": content,
                "posted_date": item.get("updated_at", str(date.today())),
            })
    except Exception as e:
        logger.debug(f"Greenhouse scrape failed for {company_name}: {e}")
    return jobs


def scrape_lever_api(company_slug: str, company_name: str, role_priorities: list[dict]) -> list[dict]:
    """Scrapes jobs directly from Lever public JSON API."""
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    jobs = []
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=12)
        if resp.status_code != 200:
            return []
        data = resp.json()
        for item in data:
            title = item.get("text", "").strip()
            if not title:
                continue
            tier_num, tier_name = _match_role_tier(title, role_priorities)
            categories = item.get("categories", {})
            location = categories.get("location", "")
            job_url = item.get("hostedUrl") or item.get("applyUrl", "")
            desc = _clean_html(item.get("descriptionPlain", "") or item.get("description", ""))

            jobs.append({
                "_source": "career_page_ats",
                "_role_tier": "tier1_core_data" if tier_num <= 3 else "tier2_broader",
                "_priority_tier": tier_num,
                "_priority_tier_name": tier_name,
                "_category": "company_careers",
                "title": title,
                "company": company_name,
                "location": location,
                "url": job_url,
                "description": desc,
                "posted_date": str(date.today()),
            })
    except Exception as e:
        logger.debug(f"Lever scrape failed for {company_name}: {e}")
    return jobs


def scrape_ashby_api(board_token: str, company_name: str, role_priorities: list[dict]) -> list[dict]:
    """Scrapes jobs directly from Ashby public API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_token}"
    jobs = []
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=12)
        if resp.status_code != 200:
            return []
        data = resp.json()
        for item in data.get("jobs", []):
            title = item.get("title", "").strip()
            if not title:
                continue
            tier_num, tier_name = _match_role_tier(title, role_priorities)
            location = item.get("locationName", "")
            job_url = item.get("jobUrl", "")
            desc = _clean_html(item.get("descriptionHtml", ""))

            jobs.append({
                "_source": "career_page_ats",
                "_role_tier": "tier1_core_data" if tier_num <= 3 else "tier2_broader",
                "_priority_tier": tier_num,
                "_priority_tier_name": tier_name,
                "_category": "company_careers",
                "title": title,
                "company": company_name,
                "location": location,
                "url": job_url,
                "description": desc,
                "posted_date": item.get("publishedAt", str(date.today())),
            })
    except Exception as e:
        logger.debug(f"Ashby scrape failed for {company_name}: {e}")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Direct HTML / JSON-LD Career Page Scraper
# ─────────────────────────────────────────────────────────────────────────────

def scrape_direct_career_page(url: str, company_name: str, role_priorities: list[dict]) -> list[dict]:
    """Fetches career page HTML and extracts job postings from JSON-LD or structured elements."""
    jobs = []
    if not url:
        return jobs
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return jobs
        html = resp.text

        # 1. Parse JSON-LD Schema.org/JobPosting if present
        json_ld_matches = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
        for block in json_ld_matches:
            try:
                parsed = json.loads(block.strip())
                items = parsed if isinstance(parsed, list) else [parsed]
                for p in items:
                    if isinstance(p, dict) and p.get("@type") == "JobPosting":
                        title = p.get("title", "")
                        tier_num, tier_name = _match_role_tier(title, role_priorities)
                        job_url = p.get("url") or url
                        desc = _clean_html(p.get("description", ""))
                        loc = ""
                        if isinstance(p.get("jobLocation"), dict):
                            address = p["jobLocation"].get("address", {})
                            loc = address.get("addressLocality") or address.get("addressRegion", "")

                        jobs.append({
                            "_source": "career_page_direct",
                            "_role_tier": "tier1_core_data" if tier_num <= 3 else "tier2_broader",
                            "_priority_tier": tier_num,
                            "_priority_tier_name": tier_name,
                            "_category": "company_careers",
                            "title": title,
                            "company": company_name,
                            "location": loc,
                            "url": job_url,
                            "description": desc,
                            "posted_date": p.get("datePosted", str(date.today())),
                        })
            except Exception:
                pass

        # 2. Extract job listing links and titles matching role priorities
        if not jobs:
            jobs.extend(_extract_links_from_html(html, company_name, url, role_priorities))

    except Exception as e:
        logger.debug(f"Direct career scrape failed for {company_name} ({url}): {e}")
    return jobs


def _extract_links_from_html(html: str, company_name: str, base_url: str, role_priorities: list[dict]) -> list[dict]:
    """Extracts job titles and links matching target titles directly from page HTML."""
    jobs = []
    seen = set()
    for tier_cfg in role_priorities:
        tier_num = tier_cfg["tier"]
        tier_name = tier_cfg["name"]
        for title in tier_cfg.get("titles", []):
            t_lower = title.lower()
            if t_lower in html.lower() and t_lower not in seen:
                seen.add(t_lower)
                # Find associated link
                pattern = rf'<a[^>]*href=["\']([^"\']+)["\'][^>]*>[^<]*{re.escape(title)}[^<]*</a>'
                match = re.search(pattern, html, re.IGNORECASE)
                href = match.group(1) if match else base_url
                if href.startswith("/"):
                    href = urllib.parse.urljoin(base_url, href)

                jobs.append({
                    "_source": "career_page_direct",
                    "_role_tier": "tier1_core_data" if tier_num <= 3 else "tier2_broader",
                    "_priority_tier": tier_num,
                    "_priority_tier_name": tier_name,
                    "_category": "company_careers",
                    "title": title,
                    "company": company_name,
                    "location": "",
                    "url": href,
                    "description": "",
                    "posted_date": str(date.today()),
                })
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
# Master Direct Career Scraper Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def scrape_all_company_career_pages(config: dict, watchlist: list[dict]) -> list[dict]:
    """
    Main entry point for directly scraping company career pages and ATS platforms.
    Queries both known ATS endpoints and configured career page URLs.
    """
    role_priorities = config.get("role_priorities", [])
    all_jobs = []

    logger.info("─── Starting Direct Career Page & ATS Scraper ───")

    # 1. Scrape Known ATS Platforms
    for slug_key, (ats_type, board_token) in KNOWN_ATS_COMPANIES.items():
        company_display = board_token.capitalize()
        try:
            if ats_type == "greenhouse":
                jobs = scrape_greenhouse_api(board_token, company_display, role_priorities)
            elif ats_type == "lever":
                jobs = scrape_lever_api(board_token, company_display, role_priorities)
            elif ats_type == "ashby":
                jobs = scrape_ashby_api(board_token, company_display, role_priorities)
            else:
                jobs = []
            if jobs:
                logger.info(f"Direct ATS: scraped {len(jobs)} jobs from {company_display} ({ats_type})")
                all_jobs.extend(jobs)
        except Exception as e:
            logger.debug(f"ATS scrape error for {company_display}: {e}")

    # 2. Scrape Watchlist Career URLs
    for entry in watchlist:
        c_name = entry.get("name", "")
        c_url = entry.get("careers_page_url", "")
        if not c_url:
            continue
        try:
            direct_jobs = scrape_direct_career_page(c_url, c_name, role_priorities)
            if direct_jobs:
                logger.info(f"Direct Careers: scraped {len(direct_jobs)} jobs from {c_name}")
                all_jobs.extend(direct_jobs)
        except Exception as e:
            logger.debug(f"Direct scrape error for {c_name}: {e}")

    logger.info(f"Direct Career Scraper completed: {len(all_jobs)} total listings discovered")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _match_role_tier(title: str, role_priorities: list[dict]) -> tuple[int, str]:
    """Matches a job title to its priority tier."""
    t_lower = title.lower()
    for tier_cfg in role_priorities:
        for target in tier_cfg.get("titles", []):
            if target.lower() in t_lower or t_lower in target.lower():
                return tier_cfg["tier"], tier_cfg["name"]
    return 6, "AI Startups / AI+GTM / Growth"


def _clean_html(raw_html: str) -> str:
    """Strips HTML tags to plain text."""
    if not raw_html:
        return ""
    clean = re.sub(r'<[^>]+>', ' ', raw_html)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean[:4000]
