"""
JobRadar v2 — Google Sheets Integration

Manages all Google Sheet tabs:
  1. Job Tracker   — live sortable job list (main view)
  2. SeenJobs      — permanent dedup ledger (hidden)
  3. Archive       — jobs older than 30 days
  4. Reach Roles   — jobs requiring >4 YOE
  5. Run Log       — one row per run (operational dashboard)
  6. Weekly Quota  — (v2) trailing 7-day application tracking by tier

v2 changes:
  - New columns: Priority Tier, Category Label, AI Exposure, Company Opp.,
    Score Breakdown, Applied
  - Applied column preserved on re-write (never clobber user input)
  - Sort order: priority_tier ASC, overall_score DESC
  - Weekly Quota tab

Authentication: Google service account JSON from GOOGLE_SERVICE_ACCOUNT_JSON env var.
"""
import json
import logging
import os
from datetime import date

import gspread

from src.explainer import generate_compact_explanation

logger = logging.getLogger(__name__)



# ─────────────────────────────────────────────────────────────────────────────
# Column definition for Job Tracker tab (v2 — 36 columns)
# ─────────────────────────────────────────────────────────────────────────────
JOB_TRACKER_COLUMNS = [
    "#",
    "job_id",
    "Priority Tier",
    "Category Label",
    "Role Tier",
    "Job Title",
    "Company",
    "Location",
    "Region",
    "Posted Date",
    "Days Old",
    "Recency Bucket",
    "Exp. Required",
    "Exp. Gate",
    "Startup?",
    "Pay",
    "Currency",
    "Skill Match",
    "Experience Fit",
    "AI Exposure",
    "Company Opp.",
    "Location Fit",
    "Freshness",
    "Product/Biz",
    "Startup/Own",
    "Stage A Score",
    "Stage B Score",
    "Overall Score",
    "Score Breakdown",
    "Apply Link",
    "Resume Link",
    "Validation",
    "Fit Note",
    "Red Flags",
    "Source",
    "First Seen",
    "Applied",
]

SEEN_JOBS_COLUMNS = ["job_id", "first_seen_date", "last_seen_date"]
ARCHIVE_COLUMNS = JOB_TRACKER_COLUMNS  # same schema
REACH_ROLES_COLUMNS = JOB_TRACKER_COLUMNS
RUN_LOG_COLUMNS = [
    "run_date",
    "run_timestamp",
    "jobs_scraped",
    "jobs_new",
    "jobs_scored_stage_a",
    "jobs_scored_stage_b",
    "resumes_generated",
    "reach_roles_added",
    "archived",
    "spend_usd",
    "budget_degraded",
    "errors",
    "notes",
]
WEEKLY_QUOTA_COLUMNS = [
    "week_ending",
    "tier_1_applied",
    "tier_1_target",
    "tier_2_applied",
    "tier_2_target",
    "tier_3_applied",
    "tier_3_target",
    "tiers_4_5_6_applied",
    "tiers_4_5_6_target",
    "total_applied",
    "notes",
]


# ─────────────────────────────────────────────────────────────────────────────
# Auth & sheet connection
# ─────────────────────────────────────────────────────────────────────────────

def connect_sheet(sheet_id: str) -> gspread.Spreadsheet:
    """Authenticate with service account and open the Google Sheet by ID."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        # Check if a service account json file exists locally
        from pathlib import Path
        for f in Path(".").glob("*.json"):
            if "client" in f.name or "service" in f.name or "account" in f.name:
                try:
                    with open(f, encoding="utf-8") as jf:
                        data = json.load(jf)
                        if data.get("type") == "service_account":
                            sa_json = json.dumps(data)
                            logger.info(f"Loaded service account credentials from {f.name}")
                            break
                except Exception:
                    pass

    if not sa_json:
        raise EnvironmentError("GOOGLE_SERVICE_ACCOUNT_JSON env var not set and no local service account JSON found")

    creds_info = json.loads(sa_json)
    # gspread v6+: use service_account_from_dict() — gspread.authorize() was removed
    gc = gspread.service_account_from_dict(creds_info)
    sheet = gc.open_by_key(sheet_id)
    logger.info(f"Connected to Google Sheet: {sheet.title}")
    return sheet



def ensure_tabs(sheet: gspread.Spreadsheet, config: dict) -> None:
    """Creates all required tabs with headers if they don't already exist."""
    tab_config = {
        config.get("sheets", {}).get("job_tracker_tab", "Job Tracker"): JOB_TRACKER_COLUMNS,
        config.get("sheets", {}).get("seen_jobs_tab", "SeenJobs"): SEEN_JOBS_COLUMNS,
        config.get("sheets", {}).get("archive_tab", "Archive"): ARCHIVE_COLUMNS,
        config.get("sheets", {}).get("reach_roles_tab", "Reach Roles (5yr+)"): REACH_ROLES_COLUMNS,
        config.get("sheets", {}).get("run_log_tab", "Run Log"): RUN_LOG_COLUMNS,
        config.get("sheets", {}).get("weekly_quota_tab", "Weekly Quota"): WEEKLY_QUOTA_COLUMNS,
    }

    existing_titles = {ws.title for ws in sheet.worksheets()}

    for tab_name, columns in tab_config.items():
        if tab_name not in existing_titles:
            ws = sheet.add_worksheet(title=tab_name, rows=1000, cols=len(columns) + 5)
            ws.append_row(columns, value_input_option="RAW")
            logger.info(f"Created tab: {tab_name}")
        else:
            logger.debug(f"Tab already exists: {tab_name}")


# ─────────────────────────────────────────────────────────────────────────────
# Applied status preservation (v2 — item #10)
# ─────────────────────────────────────────────────────────────────────────────

def read_existing_applied_status(sheet: gspread.Spreadsheet, tab_name: str = "Job Tracker") -> dict:
    """
    Reads the Applied column for existing rows in Job Tracker.
    Returns {job_id: applied_value} so we never clobber user input.
    """
    try:
        ws = sheet.worksheet(tab_name)
        all_rows = ws.get_all_values()
        if not all_rows:
            return {}
        header = all_rows[0]
        if "job_id" not in header or "Applied" not in header:
            return {}
        id_col = header.index("job_id")
        applied_col = header.index("Applied")
        result = {}
        for row in all_rows[1:]:
            if len(row) > max(id_col, applied_col):
                jid = row[id_col]
                applied = row[applied_col]
                if jid and applied:
                    result[jid] = applied
        return result
    except Exception as e:
        logger.warning(f"Could not read existing Applied status: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Recency bucket helper
# ─────────────────────────────────────────────────────────────────────────────

def _recency_bucket(days_old: int | None) -> str:
    if days_old is None:
        return "Unknown"
    if days_old <= 3:
        return "🔥 0-3 days"
    if days_old <= 7:
        return "✅ 4-7 days"
    if days_old <= 14:
        return "🔵 1-2 weeks"
    if days_old <= 30:
        return "🟡 2-4 weeks"
    return "⚪ >1 month"


# ─────────────────────────────────────────────────────────────────────────────
# Job row serializer (v2 — 37 columns)
# ─────────────────────────────────────────────────────────────────────────────

def _job_to_row(job: dict, row_num: int, applied_status: dict | None = None) -> list:
    """Converts a scored job dict to a list matching JOB_TRACKER_COLUMNS."""
    days_old = job.get("days_old")
    jid = job.get("job_id", "")

    # Preserve existing Applied status if user has set it
    applied = ""
    if applied_status and jid in applied_status:
        applied = applied_status[jid]

    # Generate compact score breakdown
    try:
        breakdown = generate_compact_explanation(job)
    except Exception:
        breakdown = ""

    return [
        row_num,                                             # #
        jid,                                                 # job_id
        job.get("priority_tier", ""),                        # Priority Tier
        job.get("category_label", ""),                       # Category Label
        job.get("role_tier", ""),                            # Role Tier
        job.get("title", ""),                                # Job Title
        job.get("company", ""),                              # Company
        job.get("location", ""),                             # Location
        job.get("region", ""),                               # Region
        job.get("posted_date", ""),                          # Posted Date
        str(days_old) if days_old is not None else "",       # Days Old
        _recency_bucket(days_old),                           # Recency Bucket
        job.get("experience_required_text", ""),             # Exp. Required
        job.get("experience_gate_label", ""),                # Exp. Gate
        "Yes" if job.get("is_startup") else "No",           # Startup?
        job.get("salary_text", ""),                          # Pay
        job.get("salary_currency", ""),                      # Currency
        job.get("skill_match_score", ""),                    # Skill Match
        job.get("experience_fit_score", ""),                 # Experience Fit
        job.get("ai_exposure_score", ""),                    # AI Exposure
        job.get("company_opportunity_score", ""),            # Company Opp.
        job.get("location_fit_score", ""),                   # Location Fit
        job.get("freshness_score", ""),                      # Freshness
        job.get("product_business_score", ""),               # Product/Biz
        job.get("startup_ownership_score", ""),              # Startup/Own
        job.get("stage_a_score", ""),                        # Stage A Score
        job.get("stage_b_refined_score", ""),                # Stage B Score
        job.get("overall_score", job.get("stage_a_score", "")),  # Overall Score
        breakdown,                                           # Score Breakdown
        job.get("url", ""),                                  # Apply Link
        job.get("resume_link", ""),                          # Resume Link
        job.get("validation_status", ""),                    # Validation
        job.get("fit_note", ""),                             # Fit Note
        job.get("red_flags", ""),                            # Red Flags
        job.get("source", ""),                               # Source
        job.get("first_seen_date", ""),                      # First Seen
        applied,                                             # Applied
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Write functions
# ─────────────────────────────────────────────────────────────────────────────

def append_job_rows(
    sheet: gspread.Spreadsheet,
    jobs: list[dict],
    tab_name: str = "Job Tracker",
    applied_status: dict | None = None,
) -> int:
    """Appends new job rows to the specified tab. Returns number of rows written."""
    if not jobs:
        return 0
    try:
        ws = sheet.worksheet(tab_name)
        # Determine current last row number for the # column
        existing = ws.get_all_values()
        start_num = len(existing)  # header is row 1

        rows = [_job_to_row(job, start_num + i, applied_status) for i, job in enumerate(jobs)]
        ws.append_rows(rows, value_input_option="RAW")
        logger.info(f"Appended {len(rows)} rows to '{tab_name}'")
        return len(rows)
    except Exception as e:
        logger.error(f"append_job_rows failed for tab '{tab_name}': {e}")
        return 0


def log_run(
    sheet: gspread.Spreadsheet,
    stats: dict,
    tab_name: str = "Run Log",
) -> None:
    """Appends one row to the Run Log tab."""
    try:
        ws = sheet.worksheet(tab_name)
        today = date.today()
        row = [
            str(today),                              # run_date
            stats.get("timestamp", ""),              # run_timestamp
            stats.get("jobs_scraped", 0),            # jobs_scraped
            stats.get("jobs_new", 0),                # jobs_new
            stats.get("jobs_scored_stage_a", 0),     # jobs_scored_stage_a
            stats.get("jobs_scored_stage_b", 0),     # jobs_scored_stage_b
            stats.get("resumes_generated", 0),       # resumes_generated
            stats.get("reach_roles_added", 0),       # reach_roles_added
            stats.get("archived", 0),                # archived
            round(stats.get("spend_usd", 0.0), 4),  # spend_usd
            "Yes" if stats.get("budget_degraded") else "No",  # budget_degraded
            "; ".join(stats.get("errors", [])),      # errors
            stats.get("notes", ""),                  # notes
        ]
        ws.append_row(row, value_input_option="RAW")
        logger.info(f"Logged run to Run Log: {stats}")
    except Exception as e:
        logger.error(f"log_run failed: {e}")
