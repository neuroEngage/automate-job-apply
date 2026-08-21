"""
JobRadar v2 — Weekly Category-Quota Tracker

Tallies Applied-marked jobs by priority tier over the trailing 7 days
against target allocation from config.yaml.

Default allocation:
  40% Priority 1 (Data Engineering)
  30% Priority 2 (Analytics)
  20% Priority 3 (Core AI/ML)
  10% Priorities 4–6 combined

Writes summary to the Weekly Quota tab in Google Sheets.
"""
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def compute_quota(
    sheet,
    config: dict,
    tab_name: str = "Job Tracker",
) -> dict:
    """
    Reads Applied-marked jobs from Job Tracker and computes trailing 7-day
    quota distribution by priority tier.

    Returns:
        dict with keys:
        - tier_1_applied: int
        - tier_2_applied: int
        - tier_3_applied: int
        - tiers_4_5_6_applied: int
        - total_applied: int
        - tier_1_target: float (percentage)
        - tier_2_target: float
        - tier_3_target: float
        - tiers_4_5_6_target: float
        - week_ending: str (date)
    """
    quota_config = config.get("weekly_quota", {})
    allocation = quota_config.get("allocation", {})

    today = date.today()
    week_start = today - timedelta(days=7)

    # Read Job Tracker
    try:
        ws = sheet.worksheet(tab_name)
        all_rows = ws.get_all_values()
    except Exception as e:
        logger.error(f"Failed to read Job Tracker for quota: {e}")
        return _empty_quota(today, allocation)

    if not all_rows:
        return _empty_quota(today, allocation)

    header = all_rows[0]
    data_rows = all_rows[1:]

    # Find column indices
    try:
        applied_col = header.index("Applied")
        tier_col = header.index("Priority Tier")
        first_seen_col = header.index("First Seen")
    except ValueError as e:
        logger.warning(f"Required column not found for quota: {e}")
        return _empty_quota(today, allocation)

    # Count applied jobs by tier in trailing 7 days
    tier_counts = {1: 0, 2: 0, 3: 0, "4_5_6": 0}

    for row in data_rows:
        if len(row) <= max(applied_col, tier_col, first_seen_col):
            continue

        applied_val = row[applied_col].strip().lower()
        if applied_val not in ("yes", "true", "applied", "✓", "✅"):
            continue

        # Check date range
        first_seen = row[first_seen_col]
        try:
            seen_date = date.fromisoformat(first_seen)
            if seen_date < week_start:
                continue
        except (ValueError, TypeError):
            continue  # can't parse date, include it anyway

        # Get tier
        try:
            tier = int(row[tier_col])
        except (ValueError, TypeError):
            continue

        if tier == 1:
            tier_counts[1] += 1
        elif tier == 2:
            tier_counts[2] += 1
        elif tier == 3:
            tier_counts[3] += 1
        elif tier >= 4:
            tier_counts["4_5_6"] += 1

    total = sum(tier_counts.values())

    return {
        "tier_1_applied": tier_counts[1],
        "tier_2_applied": tier_counts[2],
        "tier_3_applied": tier_counts[3],
        "tiers_4_5_6_applied": tier_counts["4_5_6"],
        "total_applied": total,
        "tier_1_target": allocation.get("tier_1", 0.40),
        "tier_2_target": allocation.get("tier_2", 0.30),
        "tier_3_target": allocation.get("tier_3", 0.20),
        "tiers_4_5_6_target": allocation.get("tiers_4_5_6", 0.10),
        "week_ending": str(today),
    }


def write_quota(sheet, quota: dict, tab_name: str = "Weekly Quota") -> None:
    """Writes one row to the Weekly Quota tab."""
    try:
        ws = sheet.worksheet(tab_name)
        total = quota.get("total_applied", 0)
        row = [
            quota.get("week_ending", ""),
            quota.get("tier_1_applied", 0),
            f"{quota.get('tier_1_target', 0.40):.0%}",
            quota.get("tier_2_applied", 0),
            f"{quota.get('tier_2_target', 0.30):.0%}",
            quota.get("tier_3_applied", 0),
            f"{quota.get('tier_3_target', 0.20):.0%}",
            quota.get("tiers_4_5_6_applied", 0),
            f"{quota.get('tiers_4_5_6_target', 0.10):.0%}",
            total,
            _quota_notes(quota),
        ]
        ws.append_row(row, value_input_option="RAW")
        logger.info(f"Wrote weekly quota: {total} total applied")
    except Exception as e:
        logger.error(f"Failed to write weekly quota: {e}")


def _quota_notes(quota: dict) -> str:
    """Generates notes about quota adherence."""
    total = quota.get("total_applied", 0)
    if total == 0:
        return "No applications this week"

    notes = []
    for tier, key_applied, key_target in [
        (1, "tier_1_applied", "tier_1_target"),
        (2, "tier_2_applied", "tier_2_target"),
        (3, "tier_3_applied", "tier_3_target"),
    ]:
        actual = quota.get(key_applied, 0) / total if total > 0 else 0
        target = quota.get(key_target, 0)
        if actual < target * 0.5:
            notes.append(f"T{tier} under-represented ({actual:.0%} vs {target:.0%} target)")
        elif actual > target * 1.5:
            notes.append(f"T{tier} over-represented ({actual:.0%} vs {target:.0%} target)")

    return "; ".join(notes) if notes else "Allocation on track"


def _empty_quota(today: date, allocation: dict) -> dict:
    return {
        "tier_1_applied": 0,
        "tier_2_applied": 0,
        "tier_3_applied": 0,
        "tiers_4_5_6_applied": 0,
        "total_applied": 0,
        "tier_1_target": allocation.get("tier_1", 0.40),
        "tier_2_target": allocation.get("tier_2", 0.30),
        "tier_3_target": allocation.get("tier_3", 0.20),
        "tiers_4_5_6_target": allocation.get("tiers_4_5_6", 0.10),
        "week_ending": str(today),
    }
