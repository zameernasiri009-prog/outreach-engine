from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import re
from typing import Any

from config import (
    DEFAULT_TOPUP_CREDITS,
    MAX_CREDIT_ADJUSTMENT,
    get_plan_spec,
)
from ledger import CreditLedger, LedgerOperationResult
from readiness import ensure_schema_exists, ensure_user_account_row, open_sqlite_connection
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_@.-]{2,80}$")
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


@dataclass
class AccountSession:
    """Active user session info with current plan and timing."""

    user_id: str
    email: str
    plan_tier: str
    plan_display_name: str
    max_batch_size: int
    balance: int
    checked_at_utc_ms: str
    duration_ms: float
    duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LicenseActivationResult:
    """Result when a user activates a license key or switches plan tier."""

    ok: bool
    user_id: str
    license_key: str | None
    plan_tier: str
    credits_granted: int
    balance_after: int
    duration_ms: float
    duration_s: float
    timestamp_utc_ms: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitize_user_id(raw_user_id: str | None, fallback: str = "guest_user") -> str:
    """Turn a raw user ID or email into a safe, clean account ID."""
    candidate = (raw_user_id or "").strip().lower()
    if not candidate:
        return fallback
    cleaned = re.sub(r"[^a-z0-9_@.-]", "_", candidate)[:80]
    if _USER_ID_RE.match(cleaned):
        return cleaned
    return fallback


def validate_email_soft(raw_email: str | None) -> tuple[bool, str, str]:
    """Check if an email address looks valid without crashing on empty input."""
    cleaned = (raw_email or "").strip().lower()
    if not cleaned:
        return True, "", "No email entered (optional)."
    if _EMAIL_RE.match(cleaned):
        return True, cleaned, "Email format looks good."
    return False, cleaned, "Email address format does not look valid."


def generate_license_key(plan_tier: str, seed_text: str) -> str:
    """Create a clean, repeatable license key code for a plan tier."""
    spec = get_plan_spec(plan_tier)
    raw_seed = f"{spec.tier_id.upper()}|{(seed_text or 'lead_engine').strip()}|{utc_now_iso_ms()}"
    digest = hashlib.sha256(raw_seed.encode("utf-8")).hexdigest().upper()
    prefix = spec.tier_id[:3].upper()
    return f"OLE-{prefix}-{digest[:6]}-{digest[6:12]}"


class AuthLicensingService:
    """Manages user accounts, plan tiers, credit top-ups, and license keys."""

    def __init__(self, ledger: CreditLedger | None = None) -> None:
        self.ledger = ledger if ledger is not None else CreditLedger()
        self.db_path = self.ledger.db_path

    def switch_or_login_account(
        self,
        raw_user_id: str,
        raw_email: str = "",
        default_plan: str = "starter",
    ) -> AccountSession:
        """Load or create a user account and return current session details."""
        with StepTimer("auth_switch_account") as timer:
            user_id = sanitize_user_id(raw_user_id, fallback="guest_user")
            _, clean_email, _ = validate_email_soft(raw_email)
            spec = get_plan_spec(default_plan)

            snap = self.ledger.ensure_account(
                user_id=user_id,
                email=clean_email,
                plan_tier=spec.tier_id,
                starting_credits=spec.included_credits,
            )

        rec = timer.record
        dur_ms = rec.duration_ms if rec else timer.elapsed_ms
        dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
        checked_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()
        active_spec = get_plan_spec(snap.plan_tier)

        return AccountSession(
            user_id=snap.user_id,
            email=snap.email,
            plan_tier=active_spec.tier_id,
            plan_display_name=active_spec.display_name,
            max_batch_size=active_spec.max_batch_size,
            balance=snap.balance,
            checked_at_utc_ms=checked_at,
            duration_ms=dur_ms,
            duration_s=dur_s,
        )

    def top_up_credits(
        self,
        user_id: str,
        amount: int = DEFAULT_TOPUP_CREDITS,
        reason: str = "MANUAL_CREDIT_TOP_UP",
        reference_id: str | None = None,
    ) -> LedgerOperationResult:
        """Add credits directly to the user's account balance."""
        clean_user = sanitize_user_id(user_id, fallback="")
        if not clean_user:
            raise ValueError("Cannot add credits: user account ID is missing.")
        amt = int(amount)
        if amt <= 0 or amt > MAX_CREDIT_ADJUSTMENT:
            raise ValueError(f"Credit top-up must be between 1 and {MAX_CREDIT_ADJUSTMENT}.")
        return self.ledger.add_credits(
            user_id=clean_user,
            amount=amt,
            reason=reason,
            reference_id=reference_id,
        )

    def apply_plan_and_credits(
        self,
        user_id: str,
        plan_tier: str,
        license_key: str | None = None,
        grant_included_credits: bool = True,
    ) -> LicenseActivationResult:
        """Activate or switch a plan tier, log the license if given, and grant credits."""
        clean_user = sanitize_user_id(user_id, fallback="")
        if not clean_user:
            raise ValueError("Cannot activate plan: user account ID is missing.")

        spec = get_plan_spec(plan_tier)
        clean_key = (license_key or "").strip().upper()
        if not clean_key:
            clean_key = generate_license_key(spec.tier_id, clean_user)

        with StepTimer("auth_apply_plan_and_credits") as timer:
            conn = open_sqlite_connection(self.db_path)
            try:
                ensure_schema_exists(conn)
                ensure_user_account_row(conn, user_id=clean_user, plan_tier=spec.tier_id)
                now_ms = utc_now_iso_ms()

                existing_lic = conn.execute(
                    "SELECT license_key, status FROM licenses WHERE license_key = ?;",
                    (clean_key,),
                ).fetchone()
                if existing_lic is not None and str(existing_lic["status"]).lower() == "revoked":
                    raise ValueError(f"License key '{clean_key}' has been revoked.")

                credits_to_grant = spec.included_credits if grant_included_credits else 0

                conn.execute("BEGIN IMMEDIATE;")
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO licenses (
                            license_key, user_id, plan_tier, credits_granted, status, activated_at_utc_ms
                        )
                        VALUES (?, ?, ?, ?, 'active', ?);
                        """,
                        (clean_key, clean_user, spec.tier_id, credits_to_grant, now_ms),
                    )
                    conn.execute(
                        """
                        UPDATE accounts
                        SET plan_tier = ?, updated_at_utc_ms = ?
                        WHERE user_id = ?;
                        """,
                        (spec.tier_id, now_ms, clean_user),
                    )
                    conn.execute("COMMIT;")
                except Exception:
                    conn.execute("ROLLBACK;")
                    raise
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            if credits_to_grant > 0:
                tx_res = self.ledger.add_credits(
                    user_id=clean_user,
                    amount=credits_to_grant,
                    reason=f"PLAN_ACTIVATED_{spec.tier_id.upper()}",
                    reference_id=clean_key,
                    plan_tier=spec.tier_id,
                )
                balance_after = tx_res.balance_after
            else:
                snap = self.ledger.get_balance(clean_user)
                balance_after = snap.balance

        rec = timer.record
        dur_ms = rec.duration_ms if rec else timer.elapsed_ms
        dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
        stamp_out = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

        return LicenseActivationResult(
            ok=True,
            user_id=clean_user,
            license_key=clean_key,
            plan_tier=spec.tier_id,
            credits_granted=credits_to_grant,
            balance_after=balance_after,
            duration_ms=dur_ms,
            duration_s=dur_s,
            timestamp_utc_ms=stamp_out,
            message=(
                f"Plan '{spec.display_name}' active with key {clean_key}. "
                f"Granted {credits_to_grant} credits. Balance: {balance_after} credits."
            ),
        )

    def list_licenses(self, user_id: str) -> list[dict[str, Any]]:
        """Return licenses linked to this user account."""
        clean_user = sanitize_user_id(user_id, fallback="")
        if not clean_user:
            return []
        conn = open_sqlite_connection(self.db_path)
        try:
            ensure_schema_exists(conn)
            rows = conn.execute(
                """
                SELECT license_key, user_id, plan_tier, credits_granted, status, activated_at_utc_ms
                FROM licenses
                WHERE user_id = ?
                ORDER BY activated_at_utc_ms DESC, rowid DESC;
                """,
                (clean_user,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            try:
                conn.close()
            except Exception:
                pass
