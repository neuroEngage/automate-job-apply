"""
JobRadar v2 — Scraping Layer
Sources:
  1. Direct Company Career Pages & ATS APIs (Greenhouse, Lever, Ashby, JSON-LD)
  2. JobSpy (Direct LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter)
  3. Apify (LinkedIn, Naukri) when APIFY_TOKEN is provided (with fallback to direct JobSpy)

Iterates across all 6 role-priority tiers with full Mumbai metro, Pune, India Remote, and Global Remote coverage.
"""
import logging
import os
import time
from typing import Any
from apify_client import ApifyClient
from jobspy import scrape_jobs  # type: ignore[import]

from src.career_page_scraper import scrape_all_company_career_pages
from src.company_watchlist import load_watchlist

logger = logging.getLogger(__name__)

_APIFY_WARNED = False


# ─────────────────────────────────────────────────────────────────────────────
# Apify helpers (Optional paid layer, graceful fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _get_apify_client() -> ApifyClient | None:
    global _APIFY_WARNED
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        if not _APIFY_WARNED:
            logger.info("APIFY_TOKEN not set — using direct multi-site and ATS scrapers")
            _APIFY_WARNED = True
        return None
    try:
        return ApifyClient(token)
    except Exception as e:
        logger.warning(f"Failed to initialize Apify client: {e}")
        return None


def _run_apify_actor(
    actor_id: str,
    run_input: dict,
    budget_guard,
    cost_estimate_usd: float,
    timeout_secs: int = 120,
) -> list[dict]:
    """Run an Apify actor synchronously. If token missing, returns empty list."""
    client = _get_apify_client()
    if not client:
        return []
    try:
        if budget_guard:
            budget_guard.check_and_debit("apify", cost_estimate_usd)
        logger.info(f"Running Apify actor {actor_id} with input {run_input}")
        run = client.actor(actor_id).call(run_input=run_input, wait_secs=timeout_secs)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info(f"Apify actor {actor_id} returned {len(items)} items")
        return items
    except Exception as e:
        logger.warning(f"Apify actor {actor_id} call failed: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn scraper (Apify with Direct JobSpy fallback)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_linkedin(
    titles: list[str],
    tier_num: int,
    tier_name: str,
    location: str,
    date_posted: str,
    max_results_per_title: int,
    max_charge_usd: float,
    budget_guard,
) -> list[dict]:
    """
    Scrapes LinkedIn jobs. Uses Apify if token available; falls back to JobSpy direct LinkedIn scraper.
    """
    results = []
    category = _location_to_category(location)
    has_apify = bool(os.environ.get("APIFY_TOKEN"))

    for title in titles:
        if has_apify:
            try:
                run_input = {
                    "title": title,
                    "location": location,
                    "datePosted": date_posted,
                    "limit": max_results_per_title,
                    "remote": (category in ("india_remote", "global_remote")),
                }
                items = _run_apify_actor(
                    "valig/linkedin-jobs-scraper",
                    run_input,
                    budget_guard,
                    cost_estimate_usd=max_charge_usd,
                )
                if items:
                    for item in items:
                        item["_source"] = "linkedin"
                        item["_role_tier"] = _tier_to_legacy(tier_num)
                        item["_priority_tier"] = tier_num
                        item["_priority_tier_name"] = tier_name
                        item["_category"] = category
                        item["_search_title"] = title
                    results.extend(items)
                    time.sleep(1)
                    continue
            except Exception as e:
                logger.debug(f"Apify LinkedIn scrape error for '{title}': {e}")

        # Direct JobSpy LinkedIn scraping (when Apify not used or yielded 0)
        try:
            df = scrape_jobs(
                site_name=["linkedin"],
                search_term=title,
                location=location,
                results_wanted=min(max_results_per_title, 15),
                hours_old=168,
                is_remote=(category in ("india_remote", "global_remote")),
                description_format="markdown",
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    item = row.to_dict()
                    item["_source"] = "linkedin"
                    item["_role_tier"] = _tier_to_legacy(tier_num)
                    item["_priority_tier"] = tier_num
                    item["_priority_tier_name"] = tier_name
                    item["_category"] = category
                    item["_search_title"] = title
                    results.append(item)
        except Exception as e:
            logger.debug(f"Direct LinkedIn scrape for '{title}' in '{location}': {e}")

        time.sleep(1.5)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Naukri scraper (Apify with Direct JobSpy / Google fallback)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_naukri(
    titles: list[str],
    tier_num: int,
    tier_name: str,
    location: str,
    experience_min: int,
    experience_max: int,
    max_results_per_title: int,
    max_charge_usd: float,
    budget_guard,
    category_override: str = "naukri",
) -> list[dict]:
    """
    Scrapes Naukri jobs via Apify if token set, else uses Google/Indeed India search.
    """
    results = []
    has_apify = bool(os.environ.get("APIFY_TOKEN"))

    for title in titles:
        if has_apify:
            try:
                run_input = {
                    "keyword": title,
                    "location": location,
                    "experienceMin": experience_min,
                    "experienceMax": experience_max,
                    "maxItems": max_results_per_title,
                }
                items = _run_apify_actor(
                    "epic-scrapers/naukri-scraper",
                    run_input,
                    budget_guard,
                    cost_estimate_usd=max_charge_usd,
                )
                if items:
                    for item in items:
                        item["_source"] = "naukri"
                        item["_role_tier"] = _tier_to_legacy(tier_num)
                        item["_priority_tier"] = tier_num
                        item["_priority_tier_name"] = tier_name
                        item["_category"] = category_override
                        item["_search_title"] = title
                    results.extend(items)
                    time.sleep(1)
                    continue
            except Exception as e:
                logger.debug(f"Apify Naukri scrape error for '{title}': {e}")

        # Fallback to direct search targeting India postings
        try:
            df = scrape_jobs(
                site_name=["indeed", "google"],
                search_term=f"{title} {location}",
                location=location,
                results_wanted=min(max_results_per_title, 15),
                hours_old=168,
                country_indeed="IN",
                description_format="markdown",
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    item = row.to_dict()
                    item["_source"] = "jobspy"
                    item["_role_tier"] = _tier_to_legacy(tier_num)
                    item["_priority_tier"] = tier_num
                    item["_priority_tier_name"] = tier_name
                    item["_category"] = category_override
                    item["_search_title"] = title
                    results.append(item)
        except Exception as e:
            logger.debug(f"Direct India scrape for '{title}' in '{location}': {e}")

        time.sleep(1.5)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# JobSpy scraper (Indeed / Glassdoor / Google / ZipRecruiter)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_jobspy_source(
    titles: list[str],
    tier_num: int,
    tier_name: str,
    sites: list[str],
    location: str,
    is_remote: bool,
    hours_old: int,
    max_results_per_title: int,
    country_indeed: str | list[str] | None,
    category: str,
) -> list[dict]:
    """
    Calls JobSpy's scrape_jobs() across direct platforms.
    """
    results = []
    countries = country_indeed if isinstance(country_indeed, list) else [country_indeed] if country_indeed else [None]

    for title in titles:
        for country in countries:
            try:
                kwargs: dict[str, Any] = {
                    "site_name": sites,
                    "search_term": title,
                    "location": location,
                    "results_wanted": max_results_per_title,
                    "hours_old": hours_old,
                    "is_remote": is_remote,
                    "description_format": "markdown",
                }
                if country:
                    kwargs["country_indeed"] = country

                df = scrape_jobs(**kwargs)
                if df is None or df.empty:
                    continue

                for _, row in df.iterrows():
                    item = row.to_dict()
                    item["_source"] = row.get("site", "jobspy")
                    item["_role_tier"] = _tier_to_legacy(tier_num)
                    item["_priority_tier"] = tier_num
                    item["_priority_tier_name"] = tier_name
                    item["_category"] = category
                    item["_search_title"] = title
                    results.append(item)
            except Exception as e:
                logger.debug(f"JobSpy direct scrape for '{title}' in '{location}': {e}")
            time.sleep(1.5)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Master scrape function — called by main.py
# ─────────────────────────────────────────────────────────────────────────────

def run_all_scrapers(config: dict, budget_guard) -> list[dict]:
    """
    Runs all configured scrapers:
      1. Direct Company Career Pages & ATS APIs
      2. Multi-site scrapers (Indeed, Google, LinkedIn, Glassdoor, ZipRecruiter)
    """
    sources = config.get("sources", {})
    role_priorities = config.get("role_priorities", [])
    all_raw: list[dict] = []

    # ── 1. Direct Company Career Pages & ATS Scraper ──────────────────────────
    try:
        watchlist = load_watchlist(config.get("company_watchlist", {}).get("config_file", "company_watchlist.yaml"))
        direct_jobs = scrape_all_company_career_pages(config, watchlist)
        if direct_jobs:
            all_raw.extend(direct_jobs)
            logger.info(f"Direct Career Pages: {len(direct_jobs)} jobs added")
    except Exception as e:
        logger.warning(f"Direct career page scraping error: {e}")

    # ── 2. Role Priority Multi-Platform Scrapers ─────────────────────────────
    for tier_config in role_priorities:
        tier_num = tier_config["tier"]
        tier_name = tier_config["name"]
        titles = tier_config.get("titles", [])

        if not titles:
            continue

        logger.info(f"─── Scraping Tier {tier_num}: {tier_name} ({len(titles)} titles) ───")

        # LinkedIn Mumbai
        if "mumbai_local" in sources:
            li_cfg = sources["mumbai_local"]
            all_raw.extend(scrape_linkedin(
                titles=titles[:5],  # Sample top priority titles for speed & breadth
                tier_num=tier_num,
                tier_name=tier_name,
                location=li_cfg["location"],
                date_posted=li_cfg.get("date_posted", "r604800"),
                max_results_per_title=li_cfg.get("max_results_per_title", 20),
                max_charge_usd=li_cfg.get("max_charge_usd_per_call", 0.05),
                budget_guard=budget_guard,
            ))

        # LinkedIn Pune
        if "pune_local" in sources:
            pune_cfg = sources["pune_local"]
            all_raw.extend(scrape_linkedin(
                titles=titles[:5],
                tier_num=tier_num,
                tier_name=tier_name,
                location=pune_cfg["location"],
                date_posted=pune_cfg.get("date_posted", "r604800"),
                max_results_per_title=pune_cfg.get("max_results_per_title", 20),
                max_charge_usd=pune_cfg.get("max_charge_usd_per_call", 0.05),
                budget_guard=budget_guard,
            ))

        # Naukri / Direct India
        if "naukri" in sources:
            na_cfg = sources["naukri"]
            all_raw.extend(scrape_naukri(
                titles=titles[:5],
                tier_num=tier_num,
                tier_name=tier_name,
                location=na_cfg.get("location", "Mumbai,India"),
                experience_min=na_cfg.get("experience_min", 0),
                experience_max=na_cfg.get("experience_max", 3),
                max_results_per_title=na_cfg.get("max_results_per_title", 20),
                max_charge_usd=na_cfg.get("max_charge_usd_per_call", 0.05),
                budget_guard=budget_guard,
                category_override="naukri",
            ))

        # JobSpy India Remote
        if "india_remote" in sources:
            ir_cfg = sources["india_remote"]
            all_raw.extend(scrape_jobspy_source(
                titles=titles[:5],
                tier_num=tier_num,
                tier_name=tier_name,
                sites=ir_cfg.get("sites", ["indeed", "google"]),
                location=ir_cfg.get("location", "India"),
                is_remote=ir_cfg.get("is_remote", True),
                hours_old=ir_cfg.get("hours_old", 168),
                max_results_per_title=ir_cfg.get("max_results_per_title", 20),
                country_indeed=ir_cfg.get("country_indeed", "IN"),
                category="india_remote",
            ))

        # JobSpy Global Remote
        if "global_remote" in sources:
            gr_cfg = sources["global_remote"]
            all_raw.extend(scrape_jobspy_source(
                titles=titles[:3],
                tier_num=tier_num,
                tier_name=tier_name,
                sites=gr_cfg.get("sites", ["indeed", "google", "glassdoor"]),
                location=gr_cfg.get("location", "Remote"),
                is_remote=gr_cfg.get("is_remote", True),
                hours_old=gr_cfg.get("hours_old", 168),
                max_results_per_title=gr_cfg.get("max_results_per_title", 15),
                country_indeed=gr_cfg.get("country_indeed"),
                category="global_remote",
            ))

    logger.info(f"Total raw jobs scraped across all direct & platform sources: {len(all_raw)}")
    return all_raw


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _location_to_category(location: str) -> str:
    loc = location.lower()
    if "mumbai" in loc or "navi mumbai" in loc or "thane" in loc:
        return "mumbai"
    if "pune" in loc:
        return "pune"
    if "india" in loc and "remote" not in loc:
        return "india_remote"
    return "global_remote"


def _tier_to_legacy(tier_num: int) -> str:
    if tier_num <= 3:
        return "tier1_core_data"
    return "tier2_broader"
