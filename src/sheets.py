"""
JobRadar — Google Sheets Integration

Manages all 5 Google Sheet tabs:
  1. Job Tracker   — live sortable job list (main view)
  2. SeenJobs      — permanent dedup ledger (hidden)
  3. Archive       — jobs older than 30 days
  4. Reach Roles   — jobs requiring >4 YOE
  5. Run Log       — one row per run (operational dashboard)

Authentication: Google service account JSON from GOOGLE_SERVICE_ACCOUNT_JSON env var.
"""
import json
import logging
import os
from datetime import date

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─────────────────────────────────────────────────────────────────────────────
# Column definition for Job Tracker tab (exact order = sheet columns)
# ─────────────────────────────────────────────────────────────────────────────
JOB_TRACKER_COLUMNS = [
    "#",
    "job_id",
    "Category",
    "Role Tier",
    "Job Title",
    "Company",
    "Location",
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
    "Comp Fit",
    "Recency Bonus",
    "Region Bonus",
    "Stage A Score",
    "Stage B Score",
    "Overall Score",
    "Apply Link",
    "Resume Link",
    "Validation",
    "Fit Note",
    "Red Flags",
    "Source",
    "First Seen",
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


# ─────────────────────────────────────────────────────────────────────────────
# Auth & sheet connection
# ─────────────────────────────────────────────────────────────────────────────

def connect_sheet(sheet_id: str) -> gspread.Spreadsheet:
    """Authenticate with service account and open the Google Sheet by ID."""
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise EnvironmentError("GOOGLE_SERVICE_ACCOUNT_JSON env var not set")

    creds_info = json.loads(sa_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    gc = gspread.authorize(creds)
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
# Job row serializer
# ─────────────────────────────────────────────────────────────────────────────

def _job_to_row(job: dict, row_num: int) -> list:
    """Converts a scored job dict to a list matching JOB_TRACKER_COLUMNS."""
    days_old = job.get("days_old")
    return [
        row_num,                                             # #
        job.get("job_id", ""),                               # job_id
        job.get("category", ""),                             # Category
        job.get("role_tier", ""),                            # Role Tier
        job.get("title", ""),                                # Job Title
        job.get("company", ""),                              # Company
        job.get("location", ""),                             # Location
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
        job.get("comp_fit_score", ""),                       # Comp Fit
        job.get("recency_bonus", ""),                        # Recency Bonus
        job.get("region_bonus", ""),                         # Region Bonus
        job.get("stage_a_score", ""),                        # Stage A Score
        job.get("stage_b_refined_score", ""),                # Stage B Score
        job.get("overall_score", job.get("stage_a_score", "")),  # Overall Score
        job.get("url", ""),                                  # Apply Link
        job.get("resume_link", ""),                          # Resume Link
        job.get("validation_status", ""),                    # Validation
        job.get("fit_note", ""),                             # Fit Note
        job.get("red_flags", ""),                            # Red Flags
        job.get("source", ""),                               # Source
        job.get("first_seen_date", ""),                      # First Seen
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Write functions
# ─────────────────────────────────────────────────────────────────────────────

def append_job_rows(sheet: gspread.Spreadsheet, jobs: list[dict], tab_name: str = "Job Tracker") -> int:
    """Appends new job rows to the specified tab. Returns number of rows written."""
    if not jobs:
        return 0
    try:
        ws = sheet.worksheet(tab_name)
        # Determine current last row number for the # column
        existing = ws.get_all_values()
        start_num = len(existing)  # header is row 1

        rows = [_job_to_row(job, start_num + i) for i, job in enumerate(jobs)]
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
