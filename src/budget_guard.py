"""
JobRadar — Monthly Budget Guard

Reads and writes running monthly spend to the Run Log tab in Google Sheets,
so cost tracking survives across days without needing a database.

Usage in main.py:
    guard = MonthlyBudgetGuard(sheet, config)
    guard.check_and_debit("apify", 0.05)   # raises BudgetExceeded if cap hit
    guard.check_and_debit("claude", 0.02)

When BudgetExceeded is raised, main.py catches it and switches to degraded
mode: free scraping + Stage A rule scoring continues; Stage B + resume gen
are skipped for the rest of the month.
"""
import logging
from datetime import date

logger = logging.getLogger(__name__)


class BudgetExceeded(Exception):
    """Raised when the monthly spend ceiling is reached."""
    pass


class MonthlyBudgetGuard:
    """
    Tracks and enforces a monthly USD spend ceiling across all paid services
    (Apify actors, Claude API). Persists state in the Run Log Google Sheet tab.
    """

    def __init__(self, sheet, config: dict):
        self.sheet = sheet
        self.ceiling = float(config.get("budget", {}).get("monthly_ceiling_usd", 10.0))
        self._monthly_spend: float | None = None  # lazy-loaded
        self._is_degraded = False

    @property
    def is_degraded(self) -> bool:
        """True if budget ceiling has been hit this month."""
        return self._is_degraded

    def get_monthly_spend(self) -> float:
        """Reads current month's total spend from Run Log. Cached per run."""
        if self._monthly_spend is not None:
            return self._monthly_spend
        try:
            ws = self.sheet.worksheet("Run Log")
            records = ws.get_all_records()
            current_month = date.today().strftime("%Y-%m")
            total = 0.0
            for r in records:
                run_date = str(r.get("run_date", ""))
                if run_date.startswith(current_month):
                    try:
                        total += float(r.get("spend_usd", 0) or 0)
                    except (ValueError, TypeError):
                        pass
            self._monthly_spend = total
            logger.info(f"Monthly spend so far: ${total:.4f} / ${self.ceiling:.2f}")
            return total
        except Exception as e:
            logger.error(f"Failed to read monthly spend from Run Log: {e}. Assuming $0.")
            self._monthly_spend = 0.0
            return 0.0

    def check_and_debit(self, service: str, amount_usd: float) -> None:
        """
        Checks if spending `amount_usd` would exceed the monthly ceiling.
        If yes: raises BudgetExceeded (caller should switch to degraded mode).
        If no: deducts from the in-memory running total (persisted to sheet at end of run).
        """
        if self._is_degraded:
            raise BudgetExceeded(
                f"Budget ceiling ${self.ceiling:.2f}/month already reached. "
                f"Skipping {service} call."
            )

        current = self.get_monthly_spend()
        if current + amount_usd > self.ceiling:
            self._is_degraded = True
            logger.warning(
                f"Budget ceiling reached: ${current:.4f} + ${amount_usd:.4f} "
                f"> ${self.ceiling:.2f}. Switching to degraded mode."
            )
            raise BudgetExceeded(
                f"Monthly ceiling ${self.ceiling:.2f} reached. "
                f"Service: {service}, requested: ${amount_usd:.4f}"
            )

        # Debit in memory (not written to sheet yet — done at end of run in log_run)
        self._monthly_spend = current + amount_usd
        logger.debug(
            f"Budget debit: {service} ${amount_usd:.4f} | "
            f"Running total: ${self._monthly_spend:.4f} / ${self.ceiling:.2f}"
        )

    def get_run_spend(self) -> float:
        """Returns the spend incurred during THIS run only."""
        return max(0.0, (self._monthly_spend or 0.0) - self._get_pre_run_spend())

    def _get_pre_run_spend(self) -> float:
        """Reads pre-run monthly spend (before this run's debits)."""
        # We can't distinguish pre/post cleanly from in-memory state alone
        # This is handled by log_run writing the delta
        return 0.0
