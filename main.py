"""
JobRadar v2 — Pipeline Orchestrator

Sequential, deterministic pipeline. Run daily via GitHub Actions cron
at 2:00 UTC (7:30 AM IST). Manually triggerable via workflow_dispatch.

v2 pipeline steps:
  1.  Load config + authenticate sheets
  2.  Scrape all sources (6 tiers × 6 sources)
  2.5 Run company watchlist checks
  3.  Normalize raw jobs
  4.  Dedup against SeenJobs ledger
  5.  Score Stage A (9-component formula, free, 100% of jobs)
  6.  Validate company pages (for high-scoring jobs)
  7.  Score Stage B (Claude Haiku, paid, top jobs only)
  8.  Generate ATS resumes (for top-scoring jobs)
  9.  Read existing Applied status (before writing)
  10. Write to Job Tracker (sorted: priority_tier ASC, overall_score DESC)
  11. Write to Reach Roles (>4 YOE)
  12. Update SeenJobs ledger
  13. Update Weekly Quota tracker
  14. Archive stale jobs
  15. Compose & send daily digest
  16. Log run to Run Log
"""
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
import yaml

from src.budget_guard import BudgetExceeded, MonthlyBudgetGuard
from src.company_watchlist import run_watchlist_checks
from src.dedup import filter_new_jobs, load_seen_ids, append_seen_ids
from src.digest import compose_digest, send_digest
from src.normalizer import normalize_all
from src.quota_tracker import compute_quota, write_quota
from src.resume_generator import generate_resumes
from src.scorer import compute_stage_a, score_all_stage_a, stage_b_score_batch
from src.scraper import run_all_scrapers
from src.sheets import (
    connect_sheet,
    ensure_tabs,
    append_job_rows,
    log_run,
    read_existing_applied_status,
)
from src.validator import validate_jobs

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────
LOG_FILE = "jobradar_run.log"

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("jobradar")


def main():
    run_start = time.time()
    timestamp = datetime.now().isoformat()
    errors: list[str] = []
    stats = {
        "timestamp": timestamp,
        "jobs_scraped": 0,
        "jobs_new": 0,
        "jobs_scored_stage_a": 0,
        "jobs_scored_stage_b": 0,
        "resumes_generated": 0,
        "reach_roles_added": 0,
        "archived": 0,
        "spend_usd": 0.0,
        "budget_degraded": False,
        "errors": errors,
        "notes": "",
    }

    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    # ── Step 1: Load config ──────────────────────────────────────────────────
    logger.info("═══ JobRadar v2 Pipeline Starting ═══")
    with open("config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Connect to Google Sheets (skip if dry-run without creds)
    sheet = None
    if not dry_run or os.environ.get("GOOGLE_SHEET_ID"):
        try:
            sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
            if sheet_id:
                sheet = connect_sheet(sheet_id)
                ensure_tabs(sheet, config)
        except Exception as e:
            logger.error(f"Sheet connection failed: {e}")
            errors.append(f"Sheet connection: {e}")

    # Budget guard
    budget_guard = MonthlyBudgetGuard(sheet, config) if sheet else _MockBudgetGuard()

    # ── Step 2: Scrape all sources ───────────────────────────────────────────
    logger.info("─── Step 2: Scraping ───")
    all_raw = []
    try:
        all_raw = run_all_scrapers(config, budget_guard)
        stats["jobs_scraped"] = len(all_raw)
        logger.info(f"Scraped {len(all_raw)} raw jobs")
    except BudgetExceeded as e:
        logger.warning(f"Budget exceeded during scraping: {e}")
        stats["budget_degraded"] = True
        errors.append(f"Budget: {e}")
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        errors.append(f"Scraping: {e}")

    # ── Step 2.5: Company watchlist checks ───────────────────────────────────
    logger.info("─── Step 2.5: Company Watchlist ───")
    try:
        watchlist_jobs = run_watchlist_checks(config)
        all_raw.extend(watchlist_jobs)
        logger.info(f"Watchlist added {len(watchlist_jobs)} jobs")
    except Exception as e:
        logger.error(f"Watchlist check failed: {e}")
        errors.append(f"Watchlist: {e}")

    if not all_raw:
        logger.warning("No jobs scraped or found. Logging run and exiting.")
        if sheet:
            log_run(sheet, stats)
        return

    # ── Step 3: Normalize ────────────────────────────────────────────────────
    logger.info("─── Step 3: Normalizing ───")
    normalized = normalize_all(all_raw, config=config)
    logger.info(f"Normalized: {len(normalized)} jobs")

    # ── Step 4: Dedup ────────────────────────────────────────────────────────
    logger.info("─── Step 4: Deduplication ───")
    seen_ids = load_seen_ids(sheet) if sheet else set()
    new_jobs, re_sighted = filter_new_jobs(normalized, seen_ids)
    stats["jobs_new"] = len(new_jobs)
    logger.info(f"New: {len(new_jobs)} | Re-sighted: {len(re_sighted)}")

    if not new_jobs:
        logger.info("No new jobs after dedup. Logging run and exiting.")
        if sheet:
            log_run(sheet, stats)
        return

    # ── Step 5: Stage A Scoring ──────────────────────────────────────────────
    logger.info("─── Step 5: Stage A Scoring (9-component formula) ───")
    tracker_jobs, reach_jobs, skipped = score_all_stage_a(new_jobs, config)
    stats["jobs_scored_stage_a"] = len(tracker_jobs) + len(reach_jobs)
    logger.info(
        f"Stage A: {len(tracker_jobs)} tracker | {len(reach_jobs)} reach | {len(skipped)} skipped"
    )

    # ── Step 6: Validation ───────────────────────────────────────────────────
    logger.info("─── Step 6: Validation ───")
    try:
        min_val_score = config.get("validation", {}).get("min_score_to_validate", 50.0)
        tracker_jobs = validate_jobs(tracker_jobs, config, min_val_score)
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        errors.append(f"Validation: {e}")

    # ── Step 7: Stage B Scoring (paid, optional) ─────────────────────────────
    if not budget_guard.is_degraded:
        logger.info("─── Step 7: Stage B Scoring (Claude Haiku) ───")
        try:
            client = anthropic.Anthropic()
            tracker_jobs = stage_b_score_batch(tracker_jobs, client, budget_guard, config)
            stage_b_count = sum(1 for j in tracker_jobs if "stage_b_refined_score" in j)
            stats["jobs_scored_stage_b"] = stage_b_count
        except BudgetExceeded as e:
            logger.warning(f"Budget hit during Stage B: {e}")
            stats["budget_degraded"] = True
        except Exception as e:
            logger.error(f"Stage B failed: {e}")
            errors.append(f"Stage B: {e}")
    else:
        logger.info("─── Step 7: Skipping Stage B (budget degraded) ───")
        for job in tracker_jobs:
            job["overall_score"] = job.get("stage_a_score", 0)

    # Ensure overall_score is set for all jobs
    for job in tracker_jobs:
        if "overall_score" not in job:
            job["overall_score"] = job.get("stage_a_score", 0)

    # ── Sort: priority_tier ASC, overall_score DESC ──────────────────────────
    tracker_jobs.sort(key=lambda j: (j.get("priority_tier", 6), -j.get("overall_score", 0)))
    reach_jobs.sort(key=lambda j: -j.get("overall_score", 0))

    # ── Step 8: Resume generation (paid, optional) ───────────────────────────
    if not budget_guard.is_degraded:
        logger.info("─── Step 8: Resume Generation ───")
        try:
            client = anthropic.Anthropic()
            tracker_jobs = generate_resumes(tracker_jobs, client, budget_guard, config)
            stats["resumes_generated"] = sum(1 for j in tracker_jobs if j.get("resume_link"))
        except BudgetExceeded as e:
            logger.warning(f"Budget hit during resume gen: {e}")
            stats["budget_degraded"] = True
        except Exception as e:
            logger.error(f"Resume gen failed: {e}")
            errors.append(f"Resume gen: {e}")

    # ── Step 9: Read existing Applied status ─────────────────────────────────
    applied_status = {}
    if sheet:
        logger.info("─── Step 9: Reading Applied Status ───")
        applied_status = read_existing_applied_status(sheet)
        logger.info(f"Preserved {len(applied_status)} existing Applied entries")

    # ── Step 10–12: Sheet writes (skip if dry-run) ───────────────────────────
    if not dry_run and sheet:
        logger.info("─── Step 10: Writing to Job Tracker ───")
        tab_config = config.get("sheets", {})

        # Write tracker jobs
        written = append_job_rows(
            sheet, tracker_jobs,
            tab_name=tab_config.get("job_tracker_tab", "Job Tracker"),
            applied_status=applied_status,
        )
        logger.info(f"Wrote {written} jobs to Job Tracker")

        # Write reach roles
        logger.info("─── Step 11: Writing to Reach Roles ───")
        reach_written = append_job_rows(
            sheet, reach_jobs,
            tab_name=tab_config.get("reach_roles_tab", "Reach Roles (5yr+)"),
            applied_status=applied_status,
        )
        stats["reach_roles_added"] = reach_written

        # Update SeenJobs ledger
        logger.info("─── Step 12: Updating SeenJobs ───")
        all_new_ids = [j["job_id"] for j in tracker_jobs + reach_jobs]
        append_seen_ids(sheet, all_new_ids)
        logger.info(f"Added {len(all_new_ids)} IDs to SeenJobs")

        # ── Step 13: Weekly Quota ────────────────────────────────────────────
        if config.get("weekly_quota", {}).get("enabled", True):
            logger.info("─── Step 13: Weekly Quota ───")
            try:
                quota = compute_quota(
                    sheet, config,
                    tab_name=tab_config.get("job_tracker_tab", "Job Tracker"),
                )
                write_quota(
                    sheet, quota,
                    tab_name=tab_config.get("weekly_quota_tab", "Weekly Quota"),
                )
            except Exception as e:
                logger.error(f"Quota tracker failed: {e}")
                errors.append(f"Quota: {e}")
    else:
        logger.info("─── Dry run — skipping sheet writes ───")

    # ── Step 14: Archive stale jobs (TODO — placeholder) ─────────────────────
    # Archive logic unchanged from v1 — runs independently

    # ── Step 15: Daily Digest ────────────────────────────────────────────────
    logger.info("─── Step 15: Daily Digest ───")
    stats["spend_usd"] = budget_guard.get_run_spend() if hasattr(budget_guard, 'get_run_spend') else 0
    stats["budget_degraded"] = budget_guard.is_degraded if hasattr(budget_guard, 'is_degraded') else False

    try:
        digest_text = compose_digest(tracker_jobs, reach_jobs, stats, config)
        logger.info("Digest composed:")
        logger.info(digest_text[:500] + "..." if len(digest_text) > 500 else digest_text)

        if not dry_run:
            sent = send_digest(digest_text, config)
            if sent:
                stats["notes"] = "Digest sent"
    except Exception as e:
        logger.error(f"Digest failed: {e}")
        errors.append(f"Digest: {e}")

    # ── Step 16: Log run ─────────────────────────────────────────────────────
    if sheet:
        log_run(sheet, stats)

    elapsed = round(time.time() - run_start, 1)
    logger.info(f"═══ Pipeline complete in {elapsed}s ═══")
    logger.info(
        f"Summary: {stats['jobs_scraped']} scraped → {stats['jobs_new']} new → "
        f"{stats['jobs_scored_stage_a']} scored → {stats['resumes_generated']} resumes"
    )


class _MockBudgetGuard:
    """Mock budget guard for dry runs without sheet access."""
    is_degraded = False
    def check_and_debit(self, service, amount):
        pass
    def get_run_spend(self):
        return 0.0
    def get_monthly_spend(self):
        return 0.0


if __name__ == "__main__":
    main()
