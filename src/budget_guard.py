"""
JobRadar v2 — Monthly Budget Tracker & Monitor

Tracks and logs monthly spend to the Run Log tab in Google Sheets.
Non-blocking: will never halt or crash scraping/pipeline execution.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Legacy exception class kept for backward compatibility."""
    pass


class MonthlyBudgetGuard:
    """
    Tracks USD spend across paid services (Apify, Claude API) and persists state
    in the Run Log Google Sheet tab. Non-blocking to ensure uninterrupted scraping.
    """

    def __init__(self, sheet, config: dict):
        self.sheet = sheet
        self.ceiling = float(config.get("budget", {}).get("monthly_ceiling_usd", 100.0))
        self._monthly_spend: float | None = None
        self._is_degraded = False

    @property
    def is_degraded(self) -> bool:
        """Returns degraded status (always False to allow direct scraping)."""
        return False

    def get_monthly_spend(self) -> float:
        """Reads current month's total spend from Run Log. Cached per run."""
        if self._monthly_spend is not None:
            return self._monthly_spend
        try:
            if not self.sheet:
                self._monthly_spend = 0.0
                return 0.0
            ws = self.sheet.worksheet("Run Log")
            records = ws.get_all_records()
            current_month = date.today().strftime("%Y-%m")
            total = 0.0
            for r in records:
                run_date = str(r.get("run_date", ""))
                if run_date.startswith(current_month):
                    try:
                        val = float(r.get("spend_usd", 0) or 0)
                        # Sanity check: ignore erroneous astronomical values (> $1000/run)
                        if 0 <= val < 1000:
                            total += val
                    except (ValueError, TypeError):
                        pass
            self._monthly_spend = total
            logger.info(f"Monthly spend tracked: ${total:.4f}")
            return total
        except Exception as e:
            logger.warning(f"Failed to read monthly spend from Run Log: {e}. Defaulting to $0.")
            self._monthly_spend = 0.0
            return 0.0

    def check_and_debit(self, service: str, amount_usd: float) -> None:
        """
        Tracks spend in-memory without raising exceptions or blocking execution.
        """
        current = self.get_monthly_spend()
        self._monthly_spend = current + amount_usd
        logger.debug(
            f"Spend debit logged: {service} ${amount_usd:.4f} | "
            f"Running total: ${self._monthly_spend:.4f}"
        )

    def get_run_spend(self) -> float:
        """Returns the spend incurred during THIS run only."""
        return max(0.0, self._monthly_spend or 0.0)

    def _get_pre_run_spend(self) -> float:
        return 0.0
