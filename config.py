from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Final


# ==============================================================================
# 1. APPLICATION & DATABASE IDENTITY
# ==============================================================================
APP_NAME: Final[str] = "Outbound Lead Engine"
APP_SUBTITLE: Final[str] = "Lead List Cleaner, Enricher & Pitch-Hook Generator"
APP_VERSION: Final[str] = "1.0.0-complete"
APP_ENV_DEFAULT: Final[str] = "production"

DB_FILE_DEFAULT: Final[str] = os.getenv("LEAD_ENGINE_DB_PATH", "lead_engine_outbound.db")
SQLITE_CONNECT_TIMEOUT_S: Final[float] = 15.0
SQLITE_BUSY_TIMEOUT_MS: Final[int] = 5000
SQLITE_JOURNAL_MODE: Final[str] = "WAL"
SQLITE_SYNCHRONOUS_MODE: Final[str] = "NORMAL"

REQUIRED_TABLES: Final[tuple[str, ...]] = (
    "accounts",
    "balance_transactions",
    "lead_runs",
    "licenses",
    "audit_events",
)

REQUIRED_TABLE_COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "accounts": (
        "user_id",
        "email",
        "plan_tier",
        "balance",
        "total_credited",
        "total_spent",
        "total_refunded",
        "created_at_utc_ms",
        "updated_at_utc_ms",
    ),
    "balance_transactions": (
        "tx_id",
        "user_id",
        "delta",
        "balance_before",
        "balance_after",
        "reason",
        "reference_id",
        "target_url",
        "duration_ms",
        "created_at_utc_ms",
    ),
    "lead_runs": (
        "run_id",
        "user_id",
        "batch_id",
        "input_company",
        "input_website",
        "normalized_url",
        "final_url",
        "domain",
        "contact_email",
        "clean_status",
        "clean_reasons_json",
        "http_status",
        "load_time_ms",
        "process_duration_ms",
        "opportunity_score",
        "priority_tier",
        "issues_json",
        "pitch_subject",
        "pitch_hook",
        "pitch_email",
        "credits_charged",
        "balance_after",
        "processed_at_utc_ms",
    ),
    "licenses": (
        "license_key",
        "user_id",
        "plan_tier",
        "credits_granted",
        "status",
        "activated_at_utc_ms",
    ),
    "audit_events": (
        "event_id",
        "user_id",
        "event_type",
        "severity",
        "detail",
        "duration_ms",
        "created_at_utc_ms",
    ),
}


# ==============================================================================
# 2. TIMING, NETWORK & RETRY PARAMETERS
# ==============================================================================
DEFAULT_TIMEOUT_SECONDS: Final[float] = 8.0
MIN_TIMEOUT_SECONDS: Final[float] = 1.0
MAX_TIMEOUT_SECONDS: Final[float] = 25.0

MAX_RETRIES_PER_URL: Final[int] = 1
RETRY_BACKOFF_BASE_S: Final[float] = 0.25
MAX_REDIRECTS: Final[int] = 8
MAX_RESPONSE_BYTES: Final[int] = 2_000_000  # ~2 MB cap per page for fast, low-RAM safety

DNS_CHECK_HOST: Final[str] = "example.com"
DNS_CHECK_PORT: Final[int] = 443
DNS_CHECK_TIMEOUT_S: Final[float] = 3.0

USER_AGENT_DEFAULT: Final[str] = (
    "Mozilla/5.0 (compatible; OutboundLeadEngineBot/1.0; +https://example.com/bot)"
)


# ==============================================================================
# 3. BATCH LIMITS & EXTRACTION CAPS
# ==============================================================================
MAX_BATCH_ROWS_DEFAULT: Final[int] = 250
MAX_BATCH_ROWS_HARD_CAP: Final[int] = 1000
MAX_EMAILS_PER_LEAD: Final[int] = 10
MAX_PHONES_PER_LEAD: Final[int] = 8
MAX_ISSUES_PER_LEAD: Final[int] = 15
MAX_COMPANY_NAME_LEN: Final[int] = 160
MAX_CONTACT_NAME_LEN: Final[int] = 120
MAX_EMAIL_LEN: Final[int] = 180
MAX_PHONE_LEN: Final[int] = 40
MAX_URL_LEN: Final[int] = 512


# ==============================================================================
# 4. CREDIT BALANCE & ATOMIC PRICING CONSTANTS
# ==============================================================================
COST_CLEAN_ROW: Final[int] = 0
COST_ENRICH_LEAD: Final[int] = 1
COST_GENERATE_HOOK: Final[int] = 0
COST_FULL_LEAD_RUN: Final[int] = 1

DEFAULT_STARTER_CREDITS: Final[int] = 50
DEFAULT_TOPUP_CREDITS: Final[int] = 100
MAX_CREDIT_ADJUSTMENT: Final[int] = 100_000


# ==============================================================================
# 5. SCORING THRESHOLDS & WEIGHTS (0 TO 100 SCALE)
# ==============================================================================
SLOW_LOAD_SEVERE_MS: Final[float] = 3500.0
SLOW_LOAD_MODERATE_MS: Final[float] = 2000.0

SCORE_WEIGHT_NO_HTTPS: Final[int] = 20
SCORE_WEIGHT_HTTP_ERROR: Final[int] = 35
SCORE_WEIGHT_SLOW_LOAD_SEVERE: Final[int] = 20
SCORE_WEIGHT_SLOW_LOAD_MODERATE: Final[int] = 10
SCORE_WEIGHT_MISSING_TITLE: Final[int] = 10
SCORE_WEIGHT_MISSING_META_DESC: Final[int] = 10
SCORE_WEIGHT_MISSING_VIEWPORT: Final[int] = 15
SCORE_WEIGHT_MISSING_TRACKING: Final[int] = 10
SCORE_WEIGHT_UNREACHABLE_OR_TIMEOUT: Final[int] = 40

PRIORITY_HIGH_MIN_SCORE: Final[int] = 60
PRIORITY_MEDIUM_MIN_SCORE: Final[int] = 30


# ==============================================================================
# 6. CLEANING & DOMAIN / EMAIL FILTER SETS
# ==============================================================================
FREE_EMAIL_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "ymail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "mail.ru",
        "bk.ru",
        "inbox.ru",
        "list.ru",
        "yandex.ru",
        "yandex.com",
        "gmx.com",
        "zoho.com",
    }
)

COMMON_EMAIL_TYPO_DOMAINS: Final[dict[str, str]] = {
    "gmial.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmil.com": "gmail.com",
    "gnail.com": "gmail.com",
    "yaho.com": "yahoo.com",
    "yahooo.com": "yahoo.com",
    "hotmial.com": "hotmail.com",
    "outlok.com": "outlook.com",
    "iclod.com": "icloud.com",
}

JUNK_EMAIL_PREFIXES: Final[tuple[str, ...]] = (
    "noreply@",
    "no-reply@",
    "donotreply@",
    "do-not-reply@",
    "mailer-daemon@",
    "postmaster@",
    "abuse@",
    "security@",
    "spam@",
    "unsubscribe@",
    "bounce@",
)

ASSET_FILE_EXTENSIONS: Final[tuple[str, ...]] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tiff",
    ".pdf",
    ".zip",
    ".mp4",
    ".mp3",
    ".css",
    ".js",
)

NON_BUSINESS_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "twitter.com",
        "x.com",
        "www.x.com",
        "tiktok.com",
        "www.tiktok.com",
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "linkedin.com",
        "www.linkedin.com",
        "pinterest.com",
        "www.pinterest.com",
        "wikipedia.org",
        "en.wikipedia.org",
    }
)


# ==============================================================================
# 7. EXPORT SCHEMA & PLAN TIER DEFINITIONS
# ==============================================================================
EXPORT_COLUMNS: Final[tuple[str, ...]] = (
    "lead_id",
    "batch_id",
    "company_name",
    "contact_name",
    "contact_email",
    "input_website",
    "normalized_url",
    "final_url",
    "domain",
    "clean_status",
    "clean_reasons",
    "http_status",
    "load_time_ms",
    "load_time_s",
    "process_duration_ms",
    "process_duration_s",
    "opportunity_score",
    "priority_tier",
    "extracted_emails",
    "extracted_phones",
    "issues_summary",
    "pitch_subject",
    "pitch_hook",
    "pitch_email",
    "credits_charged",
    "balance_after",
    "processed_at_utc_ms",
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
        included_credits=DEFAULT_STARTER_CREDITS,
        max_batch_size=50,
        description="Includes 50 credits for testing cleaning, enrichment, scoring, and pitch hooks.",
    ),
    "pro": PlanSpec(
        tier_id="pro",
        display_name="Pro Outbound",
        monthly_price_usd=29,
        included_credits=500,
        max_batch_size=250,
        description="500 monthly credits and up to 250 leads per batch for freelancers and outbound teams.",
    ),
    "agency": PlanSpec(
        tier_id="agency",
        display_name="Agency Scale",
        monthly_price_usd=79,
        included_credits=2000,
        max_batch_size=MAX_BATCH_ROWS_HARD_CAP,
        description="2,000 monthly credits and up to 1,000 leads per batch for high-volume agencies.",
    ),
}


def get_plan_spec(tier_id: str | None) -> PlanSpec:
    normalized = (tier_id or "starter").strip().lower()
    return PLANS.get(normalized, PLANS["starter"])


def clamp_timeout_seconds(timeout_s: float | int | None) -> float:
    if timeout_s is None:
        return DEFAULT_TIMEOUT_SECONDS
    val = float(timeout_s)
    if val < MIN_TIMEOUT_SECONDS:
        return MIN_TIMEOUT_SECONDS
    if val > MAX_TIMEOUT_SECONDS:
        return MAX_TIMEOUT_SECONDS
    return round(val, 3)


def clamp_batch_limit(requested_limit: int | None, plan_tier: str = "starter") -> int:
    spec = get_plan_spec(plan_tier)
    cap = min(spec.max_batch_size, MAX_BATCH_ROWS_HARD_CAP)
    if requested_limit is None:
        return min(MAX_BATCH_ROWS_DEFAULT, cap)
    if requested_limit < 1:
        return 1
    if requested_limit > cap:
        return cap
    return int(requested_limit)
