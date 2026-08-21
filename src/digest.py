"""
JobRadar v2 — Daily Digest

Composes and optionally delivers a categorized daily digest with 10 sections
(from the category mapping) and per-job explainable blocks.

Delivery: email (Google service account) or webhook (Slack/Telegram).
Fully optional — must not break the run if absent.
"""
import json
import logging
import os
from datetime import date

from src.explainer import generate_explanation
from src.scorer import TIER_CATEGORY_MAP

logger = logging.getLogger(__name__)

# All 10 categories in display order
ALL_CATEGORIES = [
    "Data Engineering",
    "Data/Product Analytics",
    "Core AI/ML",
    "AI + Product/Business",
    "Founder's Office/Strategy",
    "AI Startup/GTM",
    "Established Company",
    "Remote",
    "Unconventional",
]


def compose_digest(
    tracker_jobs: list[dict],
    reach_jobs: list[dict],
    stats: dict,
    config: dict,
) -> str:
    """
    Composes the full categorized daily digest.

    Returns a formatted text string with 10 sections, each containing
    job entries with their explainable score blocks.
    """
    max_per_section = config.get("digest", {}).get("max_jobs_per_section", 10)
    today = date.today()

    lines = [
        f"═══ JobRadar Daily Digest — {today.strftime('%A, %B %d, %Y')} ═══",
        "",
        f"📊 Summary: {stats.get('jobs_scraped', 0)} scraped → "
        f"{stats.get('jobs_new', 0)} new → "
        f"{stats.get('jobs_scored_stage_a', 0)} scored",
        "",
    ]

    # Group jobs by category
    category_groups = {}
    for job in tracker_jobs:
        cat = job.get("category_label", "Unconventional")
        if cat not in category_groups:
            category_groups[cat] = []
        category_groups[cat].append(job)

    # Render each of the 10 sections
    section_num = 0
    for category in ALL_CATEGORIES:
        jobs = category_groups.get(category, [])
        section_num += 1

        lines.append(f"─── {section_num}. {category} ({len(jobs)} jobs) ───")

        if not jobs:
            lines.append("  No new jobs in this category today.")
            lines.append("")
            continue

        # Sort by overall_score descending within section
        jobs_sorted = sorted(jobs, key=lambda j: j.get("overall_score", 0), reverse=True)

        for i, job in enumerate(jobs_sorted[:max_per_section]):
            lines.append(f"  [{i+1}] {job.get('title', '?')} @ {job.get('company', '?')}")
            lines.append(f"      📍 {job.get('location', 'N/A')} | 🔗 {job.get('url', 'N/A')}")

            # Add explainable block (indented)
            explanation = generate_explanation(job)
            for line in explanation.split("\n"):
                lines.append(f"      {line}")

            lines.append("")

        if len(jobs) > max_per_section:
            lines.append(f"  ... and {len(jobs) - max_per_section} more (see Google Sheet)")
            lines.append("")

    # Reach roles section
    if reach_jobs:
        lines.append(f"─── Reach Roles (>4 YOE) — {len(reach_jobs)} jobs ───")
        for job in reach_jobs[:5]:
            lines.append(f"  • {job.get('title', '?')} @ {job.get('company', '?')}")
        if len(reach_jobs) > 5:
            lines.append(f"  ... and {len(reach_jobs) - 5} more")
        lines.append("")

    # Footer
    if stats.get("errors"):
        lines.append(f"⚠️ Errors this run: {'; '.join(stats.get('errors', []))}")
    lines.append(f"💰 Spend today: ${stats.get('spend_usd', 0):.4f}")
    lines.append("")
    lines.append("═══ End of Digest ═══")

    return "\n".join(lines)


def send_digest(digest_text: str, config: dict) -> bool:
    """
    Sends the digest via configured delivery mechanism.
    Returns True if sent successfully, False otherwise.
    Never raises — failures are logged and swallowed.
    """
    # Try email first
    notify_email = os.environ.get("NOTIFY_EMAIL")
    if notify_email:
        return _send_email(digest_text, notify_email, config)

    # Try webhook
    webhook_url = os.environ.get("WEBHOOK_URL")
    if webhook_url:
        return _send_webhook(digest_text, webhook_url)

    logger.info("No digest delivery configured (set NOTIFY_EMAIL or WEBHOOK_URL)")
    return False


def _send_email(digest_text: str, recipient: str, config: dict) -> bool:
    """Sends digest via email using Google service account."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        import base64
        from email.mime.text import MIMEText

        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not sa_json:
            logger.warning("No service account for email digest")
            return False

        creds_info = json.loads(sa_json)
        creds = Credentials.from_service_account_info(
            creds_info,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

        service = build("gmail", "v1", credentials=creds)

        message = MIMEText(digest_text)
        message["to"] = recipient
        message["subject"] = f"JobRadar Daily Digest — {date.today().strftime('%b %d, %Y')}"

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()

        logger.info(f"Digest sent via email to {recipient}")
        return True
    except Exception as e:
        logger.warning(f"Email digest failed: {e}")
        return False


def _send_webhook(digest_text: str, webhook_url: str) -> bool:
    """Sends digest via Slack or Telegram webhook."""
    try:
        import requests

        # Auto-detect webhook type
        if "slack" in webhook_url.lower() or "hooks.slack.com" in webhook_url:
            payload = {"text": digest_text}
        elif "telegram" in webhook_url.lower() or "api.telegram.org" in webhook_url:
            # Telegram bot API format
            payload = {"text": digest_text, "parse_mode": "HTML"}
        else:
            # Generic webhook
            payload = {"text": digest_text}

        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code in (200, 201, 204):
            logger.info("Digest sent via webhook")
            return True
        else:
            logger.warning(f"Webhook returned HTTP {resp.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Webhook digest failed: {e}")
        return False
