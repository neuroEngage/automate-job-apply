"""
JobRadar v2 — Scraping Layer
Sources: Apify LinkedIn, Apify Naukri, JobSpy (Indeed/Google/Glassdoor/ZipRecruiter)

v2 changes:
  - Iterates over 6 role-priority tiers (replaces 2-tier system)
  - Adds Pune sources (pune_local, naukri_pune)
  - Expanded Mumbai metro coverage via broad location query
  - Each source runs ONCE per tier with that tier's title list
"""
import logging
import os
import time
from typing import Any
from apify_client import ApifyClient
from jobspy import scrape_jobs  # type: ignore[import]

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Apify helpers
# ─────────────────────────────────────────────────────────────────────────────

def _apify_client() -> ApifyClient:
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise EnvironmentError("APIFY_TOKEN env var not set")
    return ApifyClient(token)


def _run_apify_actor(
    actor_id: str,
    run_input: dict,
    budget_guard,
    cost_estimate_usd: float,
    timeout_secs: int = 120,
) -> list[dict]:
    """Run an Apify actor synchronously and return its dataset items."""
    budget_guard.check_and_debit("apify", cost_estimate_usd)
    client = _apify_client()
    logger.info(f"Running Apify actor {actor_id} with input {run_input}")
    run = client.actor(actor_id).call(run_input=run_input, wait_secs=timeout_secs)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    logger.info(f"Apify actor {actor_id} returned {len(items)} items")
    return items


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn scraper (valig/linkedin-jobs-scraper)
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
    Calls the Apify LinkedIn Jobs Scraper for each title in the list.
    Returns raw dicts tagged with source='linkedin', tier info, category.
    """
    results = []
    category = _location_to_category(location)

    for title in titles:
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
            for item in items:
                item["_source"] = "linkedin"
                item["_role_tier"] = _tier_to_legacy(tier_num)
                item["_priority_tier"] = tier_num
                item["_priority_tier_name"] = tier_name
                item["_category"] = category
                item["_search_title"] = title
            results.extend(items)
        except Exception as e:
            logger.error(f"LinkedIn scrape failed for title='{title}': {e}")
        time.sleep(1)  # polite delay between calls

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Naukri scraper (epic-scrapers/naukri-scraper)
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
    Calls the Apify Naukri Scraper for each title.
    Returns raw dicts tagged with source='naukri', tier info, category.
    """
    results = []

    for title in titles:
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
            for item in items:
                item["_source"] = "naukri"
                item["_role_tier"] = _tier_to_legacy(tier_num)
                item["_priority_tier"] = tier_num
                item["_priority_tier_name"] = tier_name
                item["_category"] = category_override
                item["_search_title"] = title
            results.extend(items)
        except Exception as e:
            logger.error(f"Naukri scrape failed for title='{title}': {e}")
        time.sleep(1)

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
    Calls JobSpy's scrape_jobs() for each title, each country (for global_remote).
    Returns raw dicts tagged with source, tier info, category.
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
                logger.error(f"JobSpy scrape failed for title='{title}', country='{country}': {e}")
            time.sleep(2)  # polite delay — JobSpy hits real sites

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Master scrape function — called by main.py
# ─────────────────────────────────────────────────────────────────────────────

def run_all_scrapers(config: dict, budget_guard) -> list[dict]:
    """
    Runs all configured scrapers for all 6 role-priority tiers.
    Returns a combined flat list of raw job dicts ready for normalisation.
    """
    sources = config["sources"]
    role_priorities = config.get("role_priorities", [])

    all_raw: list[dict] = []

    for tier_config in role_priorities:
        tier_num = tier_config["tier"]
        tier_name = tier_config["name"]
        titles = tier_config["titles"]

        if not titles:
            continue

        logger.info(f"─── Scraping Tier {tier_num}: {tier_name} ({len(titles)} titles) ───")

        # ── LinkedIn — Mumbai ─────────────────────────────────────────────────
        if "mumbai_local" in sources:
            li_cfg = sources["mumbai_local"]
            logger.info(f"  LinkedIn Mumbai [T{tier_num}] — {len(titles)} titles")
            all_raw.extend(scrape_linkedin(
                titles=titles,
                tier_num=tier_num,
                tier_name=tier_name,
                location=li_cfg["location"],
                date_posted=li_cfg["date_posted"],
                max_results_per_title=li_cfg["max_results_per_title"],
                max_charge_usd=li_cfg["max_charge_usd_per_call"],
                budget_guard=budget_guard,
            ))

        # ── LinkedIn — Pune (v2) ──────────────────────────────────────────────
        if "pune_local" in sources:
            pune_cfg = sources["pune_local"]
            logger.info(f"  LinkedIn Pune [T{tier_num}] — {len(titles)} titles")
            all_raw.extend(scrape_linkedin(
                titles=titles,
                tier_num=tier_num,
                tier_name=tier_name,
                location=pune_cfg["location"],
                date_posted=pune_cfg["date_posted"],
                max_results_per_title=pune_cfg["max_results_per_title"],
                max_charge_usd=pune_cfg["max_charge_usd_per_call"],
                budget_guard=budget_guard,
            ))

        # ── Naukri — Mumbai ───────────────────────────────────────────────────
        if "naukri" in sources:
            na_cfg = sources["naukri"]
            logger.info(f"  Naukri Mumbai [T{tier_num}] — {len(titles)} titles")
            all_raw.extend(scrape_naukri(
                titles=titles,
                tier_num=tier_num,
                tier_name=tier_name,
                location=na_cfg["location"],
                experience_min=na_cfg["experience_min"],
                experience_max=na_cfg["experience_max"],
                max_results_per_title=na_cfg["max_results_per_title"],
                max_charge_usd=na_cfg["max_charge_usd_per_call"],
                budget_guard=budget_guard,
                category_override="naukri",
            ))

        # ── Naukri — Pune (v2) ────────────────────────────────────────────────
        if "naukri_pune" in sources:
            nap_cfg = sources["naukri_pune"]
            logger.info(f"  Naukri Pune [T{tier_num}] — {len(titles)} titles")
            all_raw.extend(scrape_naukri(
                titles=titles,
                tier_num=tier_num,
                tier_name=tier_name,
                location=nap_cfg["location"],
                experience_min=nap_cfg["experience_min"],
                experience_max=nap_cfg["experience_max"],
                max_results_per_title=nap_cfg["max_results_per_title"],
                max_charge_usd=nap_cfg["max_charge_usd_per_call"],
                budget_guard=budget_guard,
                category_override="naukri_pune",
            ))

        # ── JobSpy — India Remote ─────────────────────────────────────────────
        if "india_remote" in sources:
            ir_cfg = sources["india_remote"]
            logger.info(f"  JobSpy India Remote [T{tier_num}] — {len(titles)} titles")
            all_raw.extend(scrape_jobspy_source(
                titles=titles,
                tier_num=tier_num,
                tier_name=tier_name,
                sites=ir_cfg["sites"],
                location=ir_cfg["location"],
                is_remote=ir_cfg["is_remote"],
                hours_old=ir_cfg["hours_old"],
                max_results_per_title=ir_cfg["max_results_per_title"],
                country_indeed=ir_cfg.get("country_indeed"),
                category="india_remote",
            ))

        # ── JobSpy — Global Remote ────────────────────────────────────────────
        if "global_remote" in sources:
            gr_cfg = sources["global_remote"]
            logger.info(f"  JobSpy Global Remote [T{tier_num}] — {len(titles)} titles")
            all_raw.extend(scrape_jobspy_source(
                titles=titles,
                tier_num=tier_num,
                tier_name=tier_name,
                sites=gr_cfg["sites"],
                location=gr_cfg["location"],
                is_remote=gr_cfg["is_remote"],
                hours_old=gr_cfg["hours_old"],
                max_results_per_title=gr_cfg["max_results_per_title"],
                country_indeed=gr_cfg.get("country_indeed"),
                category="global_remote",
            ))

    logger.info(f"Total raw jobs scraped: {len(all_raw)}")
    return all_raw


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _location_to_category(location: str) -> str:
    loc = location.lower()
    if "mumbai" in loc:
        return "mumbai"
    if "pune" in loc:
        return "pune"
    if "navi mumbai" in loc:
        return "mumbai"
    if "thane" in loc:
        return "mumbai"
    if "india" in loc and "remote" not in loc:
        return "india_remote"
    return "global_remote"


def _tier_to_legacy(tier_num: int) -> str:
    """Map 6-tier number to legacy 2-tier role_tier string."""
    if tier_num <= 3:
        return "tier1_core_data"
    return "tier2_broader"
