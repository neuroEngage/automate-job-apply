"""
Utility to clear all previously scraped jobs from Google Sheets and local caches,
then prepare for a completely fresh pipeline run.
"""
import os
import sys
import json
import logging
from pathlib import Path
import yaml
import gspread

from src.sheets import (
    JOB_TRACKER_COLUMNS,
    SEEN_JOBS_COLUMNS,
    ARCHIVE_COLUMNS,
    REACH_ROLES_COLUMNS,
    WEEKLY_QUOTA_COLUMNS,
    connect_sheet,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
logger = logging.getLogger("reset")

def clear_all():
    # 1. Clear local caches
    local_files = [
        ".watchlist_cache.json",
        "jobradar_run.log",
    ]
    for fname in local_files:
        p = Path(fname)
        if p.exists():
            try:
                p.unlink()
                logger.info(f"Removed local file: {fname}")
            except Exception as e:
                logger.warning(f"Could not remove {fname}: {e}")

    # 2. Check for Google Service Account credentials
    sa_json_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sa_file = None
    if not sa_json_env:
        # Check for service account file in root
        for f in Path(".").glob("*.json"):
            if "client" in f.name or "service" in f.name or "account" in f.name:
                try:
                    with open(f, encoding="utf-8") as jf:
                        data = json.load(jf)
                        if data.get("type") == "service_account":
                            sa_file = str(f)
                            os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"] = json.dumps(data)
                            logger.info(f"Using service account key from file: {f.name}")
                            break
                except Exception:
                    pass

    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        # Check config or prompt
        logger.info("GOOGLE_SHEET_ID not set in env. If you want to clear Google Sheets, ensure GOOGLE_SHEET_ID is set.")
    else:
        try:
            sheet = connect_sheet(sheet_id)
            tabs_to_reset = {
                "Job Tracker": JOB_TRACKER_COLUMNS,
                "SeenJobs": SEEN_JOBS_COLUMNS,
                "Archive": ARCHIVE_COLUMNS,
                "Reach Roles (5yr+)": REACH_ROLES_COLUMNS,
                "Weekly Quota": WEEKLY_QUOTA_COLUMNS,
            }
            existing_tabs = {ws.title: ws for ws in sheet.worksheets()}
            for tab_name, headers in tabs_to_reset.items():
                if tab_name in existing_tabs:
                    ws = existing_tabs[tab_name]
                    ws.clear()
                    ws.append_row(headers, value_input_option="RAW")
                    logger.info(f"Reset tab in Google Sheets: '{tab_name}'")
                else:
                    ws = sheet.add_worksheet(title=tab_name, rows=1000, cols=len(headers) + 5)
                    ws.append_row(headers, value_input_option="RAW")
                    logger.info(f"Created fresh tab in Google Sheets: '{tab_name}'")
            logger.info("Google Sheets successfully cleared and reset with fresh headers!")
        except Exception as e:
            logger.error(f"Error resetting Google Sheets: {e}")

if __name__ == "__main__":
    clear_all()
