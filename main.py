"""
JobRadar — Main Orchestrator

Runs the full pipeline in order:
  1. Load config + init budget guard
  2. Scrape (LinkedIn Apify, Naukri Apify, JobSpy)
  3. Normalize → common schema
  4. Dedup against SeenJobs ledger
  5. Append new job_ids to SeenJobs (immediately, before scoring)
  6. Stage A scoring (free, rule-based) → route to tracker / reach / skip
  7. Company page validation (for jobs above score threshold)
  8. Stage B scoring (Claude Haiku, batched, score >= 6 only) [if budget allows]
  9. ATS resume generation (score >= 7.5, ≤5/day) [if budget allows]
  10. Write scored jobs to Job Tracker tab
  11. Write reach-role jobs to Reach Roles tab
  12. Update last_seen for re-sighted jobs
  13. Archive stale rows (>30 days, no re-sighting)
  14. Log run stats to Run Log tab

Anti-regression rules enforced:
  - Every step wrapped in try/except — failures logged and run continues
  - Budget guard checked before every paid API call
  - SeenJobs appended before scoring (crash-safe dedup)
  - Resume gen gated by resume_base.json sentinel
  - No auto-apply: system surfaces, human submits
"""
import logging
import os
import sys
from datetime import datetime, date

import anthropic
import yaml

from src.budget_guard import MonthlyBudgetGuard, BudgetExceeded
from src.dedup import (
    load_seen_ids,
    filter_new_jobs,
    append_seen_ids,
    update_last_seen,
    archive_old_jobs,
)
from src.normalizer import normalize_all
from src.resume_generator import generate_resumes
from src.scorer import score_all_stage_a, stage_b_score_batch
from src.scraper import run_all_scrapers
from src.sheets import connect_sheet, ensure_tabs, append_job_rows, log_run
from src.validator import validate_jobs

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("jobradar_run.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("jobradar.main")


# ─────────────────────────────────────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    run_start = datetime.now()
    logger.info("=" * 60)
    logger.info(f"JobRadar run started at {run_start.isoformat()}")
    logger.info("=" * 60)

    # Collect run stats for the Run Log
    stats = {
        "timestamp": run_start.isoformat(),
        "jobs_scraped": 0,
        "jobs_new": 0,
        "jobs_scored_stage_a": 0,
        "jobs_scored_stage_b": 0,
        "resumes_generated": 0,
        "reach_roles_added": 0,
        "archived": 0,
        "spend_usd": 0.0,
        "budget_degraded": False,
        "errors": [],
        "notes": "",
    }

    # ── Load config ────────────────────────────────────────────────────────
    config = load_config()
    sheet_cfg = config.get("sheets", {})
    today = date.today()

    # ── Connect to Google Sheet ────────────────────────────────────────────
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        logger.error("GOOGLE_SHEET_ID env var not set — cannot proceed")
        sys.exit(1)

    sheet = connect_sheet(sheet_id)
    ensure_tabs(sheet, config)

    # ── Init budget guard ──────────────────────────────────────────────────
    budget_guard = MonthlyBudgetGuard(sheet, config)

    # ── Init Claude client ─────────────────────────────────────────────────
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    claude_client = anthropic.Anthropic(api_key=anthropic_key) if anthropic_key else None

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: Scrape
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 1: Scraping ──")
    raw_jobs = []
    try:
        raw_jobs = run_all_scrapers(config, budget_guard)
        stats["jobs_scraped"] = len(raw_jobs)
        logger.info(f"Scraped {len(raw_jobs)} raw job records")
    except BudgetExceeded as e:
        logger.warning(f"Budget exceeded during scraping: {e}")
        stats["budget_degraded"] = True
        stats["errors"].append(f"Budget exceeded during scraping: {e}")
    except Exception as e:
        logger.error(f"Scraping failed: {e}", exc_info=True)
        stats["errors"].append(f"Scraping error: {e}")

    if not raw_jobs:
        logger.warning("No jobs scraped — run ending early")
        _finalize(sheet, stats, budget_guard)
        return

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: Normalize
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 2: Normalizing ──")
    normalized = []
    try:
        normalized = normalize_all(raw_jobs, today)
        logger.info(f"Normalized: {len(normalized)} records")
    except Exception as e:
        logger.error(f"Normalization failed: {e}", exc_info=True)
        stats["errors"].append(f"Normalization error: {e}")
        _finalize(sheet, stats, budget_guard)
        return

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: Dedup
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 3: Deduplication ──")
    new_jobs = normalized
    re_sighted_ids = []
    try:
        seen_ids = load_seen_ids(sheet)
        new_jobs, re_sighted_ids = filter_new_jobs(normalized, seen_ids)
        stats["jobs_new"] = len(new_jobs)
        logger.info(f"New jobs: {len(new_jobs)}, re-sighted: {len(re_sighted_ids)}")
    except Exception as e:
        logger.error(f"Dedup failed: {e}", exc_info=True)
        stats["errors"].append(f"Dedup error: {e}")
        new_jobs = normalized  # proceed without dedup to avoid losing data

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: Append to SeenJobs IMMEDIATELY (before any scoring)
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 4: Appending to SeenJobs ledger ──")
    try:
        new_ids = [j["job_id"] for j in new_jobs]
        append_seen_ids(sheet, new_ids, today)
    except Exception as e:
        logger.error(f"SeenJobs append failed: {e}")
        stats["errors"].append(f"SeenJobs append error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Stage A Scoring
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 5: Stage A Scoring (free) ──")
    tracker_jobs, reach_jobs, skipped_jobs = [], [], []
    try:
        tracker_jobs, reach_jobs, skipped_jobs = score_all_stage_a(new_jobs, config)
        stats["jobs_scored_stage_a"] = len(tracker_jobs)
        stats["reach_roles_added"] = len(reach_jobs)
        logger.info(
            f"Stage A: {len(tracker_jobs)} tracker, {len(reach_jobs)} reach, {len(skipped_jobs)} skipped"
        )
    except Exception as e:
        logger.error(f"Stage A scoring failed: {e}", exc_info=True)
        stats["errors"].append(f"Stage A error: {e}")
        tracker_jobs = new_jobs  # fallback: send everything to tracker unscored

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: Company Page Validation
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 6: Company Page Validation ──")
    try:
        min_score_to_validate = config.get("validation", {}).get("min_score_to_validate", 5.0)
        validate_jobs(tracker_jobs, config, min_score_to_validate)
        logger.info("Validation complete")
    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        stats["errors"].append(f"Validation error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: Stage B Scoring (Claude Haiku)
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 7: Stage B Scoring (Claude Haiku) ──")
    if claude_client and not budget_guard.is_degraded:
        try:
            tracker_jobs = stage_b_score_batch(tracker_jobs, claude_client, budget_guard, config)
            scored_b = sum(1 for j in tracker_jobs if "stage_b_refined_score" in j)
            stats["jobs_scored_stage_b"] = scored_b
            logger.info(f"Stage B: {scored_b} jobs refined")
        except BudgetExceeded as e:
            logger.warning(f"Budget exceeded during Stage B: {e}")
            stats["budget_degraded"] = True
            stats["errors"].append(f"Budget exceeded at Stage B: {e}")
        except Exception as e:
            logger.error(f"Stage B failed: {e}", exc_info=True)
            stats["errors"].append(f"Stage B error: {e}")
    else:
        if not claude_client:
            logger.warning("ANTHROPIC_API_KEY not set — skipping Stage B")
        else:
            logger.warning("Budget degraded — skipping Stage B")
        # Ensure overall_score is set from Stage A
        for job in tracker_jobs:
            job.setdefault("overall_score", job.get("stage_a_score", 0.0))

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 8: ATS Resume Generation
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 8: ATS Resume Generation ──")
    if claude_client and not budget_guard.is_degraded:
        try:
            tracker_jobs = generate_resumes(tracker_jobs, claude_client, budget_guard, config)
            resumes_made = sum(1 for j in tracker_jobs if j.get("resume_link"))
            stats["resumes_generated"] = resumes_made
            logger.info(f"Resumes generated: {resumes_made}")
        except BudgetExceeded as e:
            logger.warning(f"Budget exceeded during resume gen: {e}")
            stats["budget_degraded"] = True
        except Exception as e:
            logger.error(f"Resume generation failed: {e}", exc_info=True)
            stats["errors"].append(f"Resume gen error: {e}")
    else:
        logger.info("Skipping resume generation (no Claude client or budget degraded)")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 9: Write to Google Sheet
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 9: Writing to Google Sheet ──")
    tracker_tab = sheet_cfg.get("job_tracker_tab", "Job Tracker")
    reach_tab = sheet_cfg.get("reach_roles_tab", "Reach Roles (5yr+)")

    try:
        # Sort tracker jobs by overall_score desc before writing
        tracker_jobs.sort(key=lambda j: j.get("overall_score", 0), reverse=True)
        written = append_job_rows(sheet, tracker_jobs, tab_name=tracker_tab)
        logger.info(f"Wrote {written} rows to '{tracker_tab}'")
    except Exception as e:
        logger.error(f"Sheet write (Job Tracker) failed: {e}", exc_info=True)
        stats["errors"].append(f"Sheet write error: {e}")

    try:
        reach_written = append_job_rows(sheet, reach_jobs, tab_name=reach_tab)
        logger.info(f"Wrote {reach_written} rows to '{reach_tab}'")
    except Exception as e:
        logger.error(f"Sheet write (Reach Roles) failed: {e}")
        stats["errors"].append(f"Reach Roles write error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 10: Update last_seen + Archive
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("── Step 10: Update last_seen + Archive stale rows ──")
    try:
        update_last_seen(sheet, re_sighted_ids, today)
    except Exception as e:
        logger.warning(f"update_last_seen failed: {e}")

    try:
        archive_days = sheet_cfg.get("archive_after_days", 30)
        archived = archive_old_jobs(sheet, archive_days)
        stats["archived"] = archived
    except Exception as e:
        logger.error(f"Archive step failed: {e}")
        stats["errors"].append(f"Archive error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 11: Log run
    # ─────────────────────────────────────────────────────────────────────────
    _finalize(sheet, stats, budget_guard)

    run_end = datetime.now()
    duration = (run_end - run_start).seconds
    logger.info(f"JobRadar run completed in {duration}s")
    logger.info(f"Summary: {stats}")


def _finalize(sheet, stats: dict, budget_guard: MonthlyBudgetGuard) -> None:
    """Always called at end of run — logs to Run Log even on failure."""
    try:
        stats["spend_usd"] = budget_guard.get_monthly_spend()
        stats["budget_degraded"] = budget_guard.is_degraded
        log_run(sheet, stats, tab_name="Run Log")
    except Exception as e:
        logger.error(f"Failed to write Run Log: {e}")


if __name__ == "__main__":
    main()
