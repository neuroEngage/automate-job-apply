"""
JobRadar — Deduplication Layer

Maintains a permanent SeenJobs ledger in Google Sheets (hidden tab).
Zero-repeat guarantee: a job_id once seen is never re-added to the main
sheet, even if the source re-issues it with a new listing ID (our content-
hash-based job_id is immune to source ID rotation).

Workflow (must follow this exact order in main.py):
  1. load_seen_ids(sheet)
  2. filter_new_jobs(jobs, seen_ids)
  3. append_seen_ids(sheet, new_ids, today)  ← immediately, before scoring
  4. [score / resume gen / sheet write]
  5. update_last_seen(sheet, re_sighted_ids, today)
  6. archive_old_jobs(sheet, config)
"""
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def load_seen_ids(sheet) -> set[str]:
    """
    Reads all job_ids from the SeenJobs hidden tab.
    Returns a set of strings for O(1) lookup.
    """
    try:
        ws = sheet.worksheet("SeenJobs")
        records = ws.get_all_records()
        return {str(r["job_id"]) for r in records if r.get("job_id")}
    except Exception as e:
        logger.error(f"Failed to load SeenJobs: {e}")
        return set()


def filter_new_jobs(jobs: list[dict], seen_ids: set[str]) -> tuple[list[dict], list[str]]:
    """
    Splits incoming normalised jobs into:
      - new_jobs: not in seen_ids → need scoring + sheet append
      - re_sighted_ids: already seen → only update last_seen_date

    Returns (new_jobs, re_sighted_ids).
    """
    new_jobs = []
    re_sighted = []
    for job in jobs:
        jid = job["job_id"]
        if jid in seen_ids:
            re_sighted.append(jid)
        else:
            new_jobs.append(job)
    logger.info(
        f"Dedup: {len(new_jobs)} new, {len(re_sighted)} already seen (re-sighted skipped)"
    )
    return new_jobs, re_sighted


def append_seen_ids(sheet, job_ids: list[str], today: date) -> None:
    """
    Appends new job_ids to SeenJobs immediately after scrape — BEFORE scoring.
    This ensures a mid-run crash never causes a duplicate re-add next day.
    """
    if not job_ids:
        return
    try:
        ws = sheet.worksheet("SeenJobs")
        rows = [[jid, str(today), str(today)] for jid in job_ids]
        ws.append_rows(rows, value_input_option="RAW")
        logger.info(f"Appended {len(job_ids)} job_ids to SeenJobs")
    except Exception as e:
        logger.error(f"Failed to append to SeenJobs: {e}. Continuing (no data lost yet).")


def update_last_seen(sheet, re_sighted_ids: list[str], today: date) -> None:
    """
    Updates the last_seen_date for re-sighted jobs in SeenJobs.
    This is used by archive logic to determine if a job is truly stale.
    Runs best-effort — a failure here does not break the pipeline.
    """
    if not re_sighted_ids:
        return
    try:
        ws = sheet.worksheet("SeenJobs")
        records = ws.get_all_records()
        id_to_row = {r["job_id"]: i + 2 for i, r in enumerate(records)}  # 1-indexed, row 1 = header
        today_str = str(today)
        for jid in re_sighted_ids:
            row_num = id_to_row.get(jid)
            if row_num:
                ws.update_cell(row_num, 3, today_str)  # column 3 = last_seen_date
    except Exception as e:
        logger.warning(f"update_last_seen partial failure: {e}")


def archive_old_jobs(sheet, archive_after_days: int = 30) -> int:
    """
    Moves rows from Job Tracker to Archive if:
      - days_old > archive_after_days, AND
      - last_seen_date in SeenJobs is also > archive_after_days ago (no re-sighting)

    Returns the number of rows archived.
    """
    try:
        tracker_ws = sheet.worksheet("Job Tracker")
        archive_ws = sheet.worksheet("Archive")
        seen_ws = sheet.worksheet("SeenJobs")

        today = date.today()
        cutoff = today - timedelta(days=archive_after_days)

        # Build last_seen lookup from SeenJobs
        seen_records = seen_ws.get_all_records()
        last_seen_map = {
            r["job_id"]: date.fromisoformat(r["last_seen_date"])
            for r in seen_records
            if r.get("job_id") and r.get("last_seen_date")
        }

        all_rows = tracker_ws.get_all_values()
        if not all_rows:
            return 0

        header = all_rows[0]
        data_rows = all_rows[1:]

        # Identify job_id column index
        try:
            id_col = header.index("job_id")
        except ValueError:
            logger.warning("No 'job_id' column in Job Tracker — skipping archive")
            return 0

        to_archive = []
        to_keep = []
        for row in data_rows:
            jid = row[id_col] if len(row) > id_col else ""
            last_seen = last_seen_map.get(jid)
            if last_seen and last_seen < cutoff:
                to_archive.append(row)
            else:
                to_keep.append(row)

        if to_archive:
            # Append archived rows to Archive tab
            archive_ws.append_rows(to_archive, value_input_option="RAW")
            # Rewrite Job Tracker without archived rows
            tracker_ws.clear()
            tracker_ws.append_row(header, value_input_option="RAW")
            if to_keep:
                tracker_ws.append_rows(to_keep, value_input_option="RAW")
            logger.info(f"Archived {len(to_archive)} stale rows")

        return len(to_archive)

    except Exception as e:
        logger.error(f"archive_old_jobs failed: {e}")
        return 0
