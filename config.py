from __future__ import annotations

from dataclasses import dataclass
from typing import Final


APP_NAME: Final[str] = "Outbound Lead Engine"
APP_VERSION: Final[str] = "1.0.0-complete"
DB_FILE_DEFAULT: Final[str] = "lead_engine_outbound.db"

# Precision & timing defaults
DEFAULT_TIMEOUT_SECONDS: Final[float] = 8.0
MAX_TIMEOUT_SECONDS: Final[float] = 25.0
MIN_TIMEOUT_SECONDS: Final[float] = 1.0
MAX_RETRIES_PER_URL: Final[int] = 1
BACKOFF_BASE_SECONDS: Final[float] = 0.25
SQLITE_BUSY_TIMEOUT_MS: Final[int] = 5000
SQLITE_CONNECT_TIMEOUT_S: Final[float] = 10.0

# Batch & input limits
MAX_BATCH_ROWS_DEFAULT: Final[int] = 250
MAX_BATCH_ROWS_HARD_CAP: Final[int] = 1000
MAX_EMAILS_PER_LEAD: Final[int] = 10
MAX_PHONES_PER_LEAD: Final[int] = 8

# Credit / balance costs (atomic per action)
COST_CLEAN_ROW: Final[int] = 0
COST_ENRICH_LEAD: Final[int] = 1
COST_GENERATE_HOOK: Final[int] = 0
COST_FULL_LEAD_RUN: Final[int] = 1
DEFAULT_STARTER_CREDITS: Final[int] = 50
DEFAULT_TOPUP_CREDITS: Final[int] = 100

# Revenue-leak / opportunity scoring weights (0 - 100 cap)
SCORE_WEIGHT_NO_HTTPS: Final[int] = 20
SCORE_WEIGHT_HTTP_ERROR: Final[int] = 35
SCORE_WEIGHT_SLOW_LOAD_SEVERE: Final[int] = 20
SCORE_WEIGHT_SLOW_LOAD_MODERATE: Final[int] = 10
SCORE_WEIGHT_MISSING_TITLE: Final[int] = 10
SCORE_WEIGHT_MISSING_META_DESC: Final[int] = 10
SCORE_WEIGHT_MISSING_VIEWPORT: Final[int] = 15
SCORE_WEIGHT_MISSING_TRACKING: Final[int] = 10
SCORE_WEIGHT_UNREACHABLE_OR_TIMEOUT: Final[int] = 40

SLOW_LOAD_SEVERE_MS: Final[float] = 3500.0
SLOW_LOAD_MODERATE_MS: float = 2000.0

# Required SQLite tables
REQUIRED_TABLES: Final[tuple[str, ...]] = (
    "accounts",
    "balance_transactions",
    "lead_runs",
    "licenses",
)

# Standard output column order for exports & tables
EXPORT_COLUMNS: Final[tuple[str, ...]] = (
    "lead_id",
    "company_name",
    "input_website",
    "normalized_url",
    "final_url",
    "domain",
    "contact_name",
    "contact_email",
    "extracted_emails",
    "extracted_phones",
    "clean_status",
    "clean_reasons",
    "http_status",
    "load_time_ms",
    "load_time_s",
    "process_duration_ms",
    "process_duration_s",
    "opportunity_score",
    "priority_tier",
    "issues_summary",
    "pitch_subject",
    "pitch_hook",
    "pitch_email",
    "credits_charged",
    "balance_after",
    "processed_at_utc_ms",
)

FREE_EMAIL_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "mail.ru",
        "yandex.ru",
        "live.com",
        "msn.com",
    }
)

JUNK_EMAIL_PREFIXES: Final[tuple[str, ...]] = (
    "noreply@",
    "no-reply@",
    "donotreply@",
    "mailer-daemon@",
    "postmaster@",
    "abuse@",
    "privacy@",
    "unsubscribe@",
)

USER_AGENT_DEFAULT: Final[str] = (
    "Mozilla/5.0 (compatible; OutboundLeadEngine/1.0; +https://example.com/bot)"
)


@dataclass(frozen=True)
class PlanSpec:
    tier_id: str
    display_name: str
    monthly_price_usd: int
    included_credits: int
    max_batch_size: int
    description: str


PLANS: Final[dict[str, PlanSpec]] = {
    "starter": PlanSpec(
        tier_id="starter",
        display_name="Starter",
        monthly_price_usd=0,
        included_credits=50,
        max_batch_size=50,
        description="Test and run verified lead cleaning, enrichment, and pitch hooks.",
    ),
    "pro": PlanSpec(
        tier_id="pro",
        display_name="Pro Outbound",
        monthly_price_usd=29,
        included_credits=500,
        max_batch_size=250,
        description="For freelancers and outbound reps running regular prospect lists.",
    ),
    "agency": PlanSpec(
        tier_id="agency",
        display_name="Agency Scale",
        monthly_price_usd=79,
        included_credits=2000,
        max_batch_size=1000,
        description="High-volume lead cleaning, enrichment, hooks, and CSV exports.",
    ),
}


def get_plan_spec(tier_id: str) -> PlanSpec:
    key = (tier_id or "starter").strip().lower()
    return PLANS.get(key, PLANS["starter"])
