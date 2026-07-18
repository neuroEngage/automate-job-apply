"""
JobRadar — Scraping Layer
Sources: Apify LinkedIn, Apify Naukri, JobSpy (Indeed/Google/Glassdoor/ZipRecruiter)

Each source is called TWICE per run: once for tier1_core_data titles, once for
tier2_broader titles. This ensures every incoming job is tagged with role_tier
before scoring (required by the Role Tier weight in Stage A).
"""
import logging
import os
import time
from typing import Any
from datetime import timedelta

import yaml
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
                run = client.actor(actor_id).call(run_input=run_input, wait_duration=timedelta(seconds=timeout_secs))
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    logger.info(f"Apify actor {actor_id} returned {len(items)} items")
    return items


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn scraper (valig/linkedin-jobs-scraper)
# ─────────────────────────────────────────────────────────────────────────────

def scrape_linkedin(
    titles: list[str],
    role_tier: str,
    location: str,
    date_posted: str,
    max_results_per_title: int,
    max_charge_usd: float,
    budget_guard,
) -> list[dict]:
    """
    Calls the Apify LinkedIn Jobs Scraper for each title in the list.
    Returns raw dicts tagged with source='linkedin', role_tier, category.
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
                item["_role_tier"] = role_tier
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
    role_tier: str,
    location: str,
    experience_min: int,
    experience_max: int,
    max_results_per_title: int,
    max_charge_usd: float,
    budget_guard,
) -> list[dict]:
    """
    Calls the Apify Naukri Scraper (epic-scrapers/naukri-scraper) for each title.
    Returns raw dicts tagged with source='naukri', role_tier, category='naukri'.
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
                item["_role_tier"] = role_tier
                item["_category"] = "naukri"
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
    role_tier: str,
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
    Returns raw dicts tagged with source, role_tier, category.
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
                    item["_role_tier"] = role_tier
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
    Runs all configured scrapers (LinkedIn, Naukri, JobSpy) for both title
    tiers. Returns a combined flat list of raw job dicts ready for normalisation.
    """
    profile = config["candidate_profile"]
    sources = config["sources"]
    tier1 = profile["target_titles_tier1_core_data"]
    tier2 = profile["target_titles_tier2_broader"]

    all_raw: list[dict] = []

    # ── LinkedIn — Mumbai ───────────────────────────────────────────────────
    li_cfg = sources["mumbai_local"]
    for tier_name, titles in [("tier1_core_data", tier1), ("tier2_broader", tier2)]:
        logger.info(f"Scraping LinkedIn Mumbai [{tier_name}] — {len(titles)} titles")
        all_raw.extend(scrape_linkedin(
            titles=titles,
            role_tier=tier_name,
            location=li_cfg["location"],
            date_posted=li_cfg["date_posted"],
            max_results_per_title=li_cfg["max_results_per_title"],
            max_charge_usd=li_cfg["max_charge_usd_per_call"],
            budget_guard=budget_guard,
        ))

    # ── Naukri ──────────────────────────────────────────────────────────────
    na_cfg = sources["naukri"]
    for tier_name, titles in [("tier1_core_data", tier1), ("tier2_broader", tier2)]:
        logger.info(f"Scraping Naukri [{tier_name}] — {len(titles)} titles")
        all_raw.extend(scrape_naukri(
            titles=titles,
            role_tier=tier_name,
            location=na_cfg["location"],
            experience_min=na_cfg["experience_min"],
            experience_max=na_cfg["experience_max"],
            max_results_per_title=na_cfg["max_results_per_title"],
            max_charge_usd=na_cfg["max_charge_usd_per_call"],
            budget_guard=budget_guard,
        ))

    # ── JobSpy — India Remote ────────────────────────────────────────────────
    ir_cfg = sources["india_remote"]
    for tier_name, titles in [("tier1_core_data", tier1), ("tier2_broader", tier2)]:
        logger.info(f"Scraping JobSpy India Remote [{tier_name}] — {len(titles)} titles")
        all_raw.extend(scrape_jobspy_source(
            titles=titles,
            role_tier=tier_name,
            sites=ir_cfg["sites"],
            location=ir_cfg["location"],
            is_remote=ir_cfg["is_remote"],
            hours_old=ir_cfg["hours_old"],
            max_results_per_title=ir_cfg["max_results_per_title"],
            country_indeed=ir_cfg.get("country_indeed"),
            category="india_remote",
        ))

    # ── JobSpy — Global Remote ───────────────────────────────────────────────
    gr_cfg = sources["global_remote"]
    for tier_name, titles in [("tier1_core_data", tier1), ("tier2_broader", tier2)]:
        logger.info(f"Scraping JobSpy Global Remote [{tier_name}] — {len(titles)} titles")
        all_raw.extend(scrape_jobspy_source(
            titles=titles,
            role_tier=tier_name,
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
    if "india" in loc and "remote" not in loc:
        return "india_remote"
    return "global_remote"
