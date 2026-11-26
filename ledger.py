from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import sqlite3
from typing import Any

from config import (
    COST_FULL_LEAD_RUN,
    DB_FILE_DEFAULT,
    DEFAULT_STARTER_CREDITS,
    DEFAULT_TOPUP_CREDITS,
    MAX_CREDIT_ADJUSTMENT,
    get_plan_spec,
)
from readiness import ensure_schema_exists, ensure_user_account_row, open_sqlite_connection
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


@dataclass
class BalanceSnapshot:
    """Clear current balance picture for an account at a specific millisecond."""

    user_id: str
    email: str
    plan_tier: str
    balance: int
    total_credited: int
    total_spent: int
    total_refunded: int
    last_tx_id: str | None
    checked_at_utc_ms: str
    check_duration_ms: float
    check_duration_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LedgerOperationResult:
    """Result of a credit top-up, charge, or refund operation."""

    ok: bool
    user_id: str
    tx_id: str | None
    delta: int
    balance_before: int
    balance_after: int
    reason: str
    reference_id: str | None
    target_url: str | None
    duration_ms: float
    duration_s: float
    timestamp_utc_ms: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _make_tx_id(prefix: str, user_id: str, stamp_ms: str, seq_ms: int) -> str:
    clean_user = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in user_id)[:24]
    clean_stamp = stamp_ms.replace(":", "").replace("-", "").replace(".", "").replace("Z", "")
    return f"tx_{prefix}_{clean_user}_{clean_stamp}_{seq_ms}"


def _make_event_id(user_id: str, stamp_ms: str, seq_ms: int) -> str:
    clean_user = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in user_id)[:24]
    clean_stamp = stamp_ms.replace(":", "").replace("-", "").replace(".", "").replace("Z", "")
    return f"evt_{clean_user}_{clean_stamp}_{seq_ms}"


class CreditLedger:
    """Atomic SQLite credit/balance ledger with full transaction and run history."""

    def __init__(self, db_path: str = DB_FILE_DEFAULT) -> None:
        self.db_path = (db_path or "").strip() or DB_FILE_DEFAULT
        self._bootstrap_database()

    def _bootstrap_database(self) -> None:
        conn = open_sqlite_connection(self.db_path)
        try:
            ensure_schema_exists(conn)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _write_audit_event(
        self,
        conn: sqlite3.Connection,
        user_id: str,
        event_type: str,
        severity: str,
        detail: str,
        duration_ms: float,
        stamp_ms: str,
    ) -> None:
        seq_ms = int(round(max(0.0, duration_ms) * 1000.0))
        event_id = _make_event_id(user_id or "system", stamp_ms, seq_ms)
        conn.execute(
            """
            INSERT INTO audit_events (
                event_id, user_id, event_type, severity, detail, duration_ms, created_at_utc_ms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                event_id,
                user_id or "system",
                event_type,
                severity,
                detail[:1000],
                round(max(0.0, float(duration_ms)), 3),
                stamp_ms,
            ),
        )

    def ensure_account(
        self,
        user_id: str,
        email: str = "",
        plan_tier: str = "starter",
        starting_credits: int | None = None,
    ) -> BalanceSnapshot:
        """Ensure a user account row exists and return its exact balance."""
        clean_user = (user_id or "").strip()
        if not clean_user:
            raise ValueError("User ID cannot be empty.")

        clean_email = (email or "").strip()
        spec = get_plan_spec(plan_tier)
        initial_bal = (
            DEFAULT_STARTER_CREDITS
            if starting_credits is None
            else int(starting_credits)
        )
        if initial_bal < 0 or initial_bal > MAX_CREDIT_ADJUSTMENT:
            raise ValueError(f"Invalid starting credit balance: {initial_bal}")

        with StepTimer("ledger_ensure_account") as timer:
            conn = open_sqlite_connection(self.db_path)
            try:
                ensure_schema_exists(conn)
                now_ms = utc_now_iso_ms()
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    row = conn.execute(
                        "SELECT user_id FROM accounts WHERE user_id = ?;",
                        (clean_user,),
                    ).fetchone()
                    if row is None:
                        conn.execute(
                            """
                            INSERT INTO accounts (
                                user_id, email, plan_tier, balance, total_credited,
                                total_spent, total_refunded, created_at_utc_ms, updated_at_utc_ms
                            )
                            VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?);
                            """,
                            (
                                clean_user,
                                clean_email,
                                spec.tier_id,
                                initial_bal,
                                initial_bal,
                                now_ms,
                                now_ms,
                            ),
                        )
                        tx_id = _make_tx_id("init", clean_user, now_ms, 1)
                        conn.execute(
                            """
                            INSERT INTO balance_transactions (
                                tx_id, user_id, delta, balance_before, balance_after,
                                reason, reference_id, target_url, duration_ms, created_at_utc_ms
                            )
                            VALUES (?, ?, ?, 0, ?, 'ACCOUNT_CREATED', 'ledger_init', NULL, 0.0, ?);
                            """,
                            (tx_id, clean_user, initial_bal, initial_bal, now_ms),
                        )
                    elif clean_email:
                        conn.execute(
                            """
                            UPDATE accounts
                            SET email = CASE WHEN email = '' THEN ? ELSE email END,
                                updated_at_utc_ms = ?
                            WHERE user_id = ?;
                            """,
                            (clean_email, now_ms, clean_user),
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

        return self.get_balance(clean_user)

    def get_balance(self, user_id: str) -> BalanceSnapshot:
        """Read exact current balance, totals, and last transaction ID with timing."""
        clean_user = (user_id or "").strip()
        if not clean_user:
            raise ValueError("User ID cannot be empty.")

        with StepTimer("ledger_get_balance") as timer:
            conn = open_sqlite_connection(self.db_path)
            try:
                ensure_schema_exists(conn)
                ensure_user_account_row(conn, user_id=clean_user)
                acc = conn.execute(
                    """
                    SELECT user_id, email, plan_tier, balance,
                           total_credited, total_spent, total_refunded
                    FROM accounts
                    WHERE user_id = ?;
                    """,
                    (clean_user,),
                ).fetchone()
                if acc is None:
                    raise RuntimeError(f"Account '{clean_user}' could not be loaded.")

                last_tx = conn.execute(
                    """
                    SELECT tx_id
                    FROM balance_transactions
                    WHERE user_id = ?
                    ORDER BY created_at_utc_ms DESC, rowid DESC
                    LIMIT 1;
                    """,
                    (clean_user,),
                ).fetchone()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        rec = timer.record
        dur_ms = rec.duration_ms if rec else timer.elapsed_ms
        dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
        checked_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

        return BalanceSnapshot(
            user_id=str(acc["user_id"]),
            email=str(acc["email"] or ""),
            plan_tier=str(acc["plan_tier"] or "starter"),
            balance=int(acc["balance"]),
            total_credited=int(acc["total_credited"]),
            total_spent=int(acc["total_spent"]),
            total_refunded=int(acc["total_refunded"]),
            last_tx_id=str(last_tx["tx_id"]) if last_tx and last_tx["tx_id"] else None,
            checked_at_utc_ms=checked_at,
            check_duration_ms=dur_ms,
            check_duration_s=dur_s,
        )

    def can_afford(self, user_id: str, required_credits: int) -> tuple[bool, int, str]:
        """Check whether the user has enough credits before starting a paid step."""
        clean_user = (user_id or "").strip()
        needed = max(0, int(required_credits))
        if not clean_user:
            return False, 0, "No user account specified."

        snap = self.get_balance(clean_user)
        if snap.balance < needed:
            return (
                False,
                snap.balance,
                f"Not enough balance: you have {snap.balance} credits, need {needed} credits.",
            )
        return (
            True,
            snap.balance,
            f"Balance verified: {snap.balance} credits available ({needed} required).",
        )

    def add_credits(
        self,
        user_id: str,
        amount: int = DEFAULT_TOPUP_CREDITS,
        reason: str = "CREDIT_TOP_UP",
        reference_id: str | None = None,
        plan_tier: str | None = None,
    ) -> LedgerOperationResult:
        """Add credits atomically and log the transaction with exact timing."""
        clean_user = (user_id or "").strip()
        credits_to_add = int(amount)
        if not clean_user:
            raise ValueError("User ID cannot be empty.")
        if credits_to_add <= 0 or credits_to_add > MAX_CREDIT_ADJUSTMENT:
            raise ValueError(
                f"Credit amount must be between 1 and {MAX_CREDIT_ADJUSTMENT}, got {credits_to_add}."
            )

        clean_reason = (reason or "").strip() or "CREDIT_TOP_UP"
        clean_ref = (reference_id or "").strip() or None
        clean_tier = plan_tier.strip().lower() if plan_tier else None

        with StepTimer("ledger_add_credits") as timer:
            conn = open_sqlite_connection(self.db_path)
            try:
                ensure_schema_exists(conn)
                ensure_user_account_row(conn, user_id=clean_user)
                now_ms = utc_now_iso_ms()

                conn.execute("BEGIN IMMEDIATE;")
                try:
                    row = conn.execute(
                        "SELECT balance, total_credited, plan_tier FROM accounts WHERE user_id = ?;",
                        (clean_user,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(f"Account '{clean_user}' not found during credit top-up.")

                    before_bal = int(row["balance"])
                    after_bal = before_bal + credits_to_add
                    new_total_credited = int(row["total_credited"]) + credits_to_add
                    resolved_tier = (
                        get_plan_spec(clean_tier).tier_id
                        if clean_tier
                        else str(row["plan_tier"] or "starter")
                    )

                    conn.execute(
                        """
                        UPDATE accounts
                        SET balance = ?,
                            total_credited = ?,
                            plan_tier = ?,
                            updated_at_utc_ms = ?
                        WHERE user_id = ?;
                        """,
                        (after_bal, new_total_credited, resolved_tier, now_ms, clean_user),
                    )

                    tx_id = _make_tx_id("add", clean_user, now_ms, credits_to_add)
                    op_ms = timer.elapsed_ms
                    conn.execute(
                        """
                        INSERT INTO balance_transactions (
                            tx_id, user_id, delta, balance_before, balance_after,
                            reason, reference_id, target_url, duration_ms, created_at_utc_ms
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?);
                        """,
                        (
                            tx_id,
                            clean_user,
                            credits_to_add,
                            before_bal,
                            after_bal,
                            clean_reason,
                            clean_ref,
                            op_ms,
                            now_ms,
                        ),
                    )

                    self._write_audit_event(
                        conn=conn,
                        user_id=clean_user,
                        event_type="CREDITS_ADDED",
                        severity="info",
                        detail=f"Added {credits_to_add} credits ({clean_reason}). Balance: {before_bal} -> {after_bal}.",
                        duration_ms=op_ms,
                        stamp_ms=now_ms,
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

        rec = timer.record
        dur_ms = rec.duration_ms if rec else timer.elapsed_ms
        dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
        stamp_out = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

        return LedgerOperationResult(
            ok=True,
            user_id=clean_user,
            tx_id=tx_id,
            delta=credits_to_add,
            balance_before=before_bal,
            balance_after=after_bal,
            reason=clean_reason,
            reference_id=clean_ref,
            target_url=None,
            duration_ms=dur_ms,
            duration_s=dur_s,
            timestamp_utc_ms=stamp_out,
            message=f"Added {credits_to_add} credits. New balance: {after_bal} credits.",
        )

    def spend_credits(
        self,
        user_id: str,
        cost: int = COST_FULL_LEAD_RUN,
        reason: str = "LEAD_ENRICHMENT_RUN",
        reference_id: str | None = None,
        target_url: str | None = None,
    ) -> LedgerOperationResult:
        """Deduct credits atomically only when balance is sufficient."""
        clean_user = (user_id or "").strip()
        charge = int(cost)
        if not clean_user:
            raise ValueError("User ID cannot be empty.")
        if charge < 0 or charge > MAX_CREDIT_ADJUSTMENT:
            raise ValueError(f"Invalid credit cost: {charge}")

        clean_reason = (reason or "").strip() or "LEAD_ENRICHMENT_RUN"
        clean_ref = (reference_id or "").strip() or None
        clean_url = (target_url or "").strip() or None

        with StepTimer("ledger_spend_credits") as timer:
            conn = open_sqlite_connection(self.db_path)
            try:
                ensure_schema_exists(conn)
                ensure_user_account_row(conn, user_id=clean_user)
                now_ms = utc_now_iso_ms()

                conn.execute("BEGIN IMMEDIATE;")
                try:
                    row = conn.execute(
                        "SELECT balance, total_spent FROM accounts WHERE user_id = ?;",
                        (clean_user,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(f"Account '{clean_user}' not found during charge.")

                    before_bal = int(row["balance"])
                    if before_bal < charge:
                        raise ValueError(
                            f"Insufficient credits: balance is {before_bal}, required is {charge}."
                        )

                    after_bal = before_bal - charge
                    new_spent = int(row["total_spent"]) + charge

                    conn.execute(
                        """
                        UPDATE accounts
                        SET balance = ?,
                            total_spent = ?,
                            updated_at_utc_ms = ?
                        WHERE user_id = ?;
                        """,
                        (after_bal, new_spent, now_ms, clean_user),
                    )

                    tx_id = _make_tx_id("spend", clean_user, now_ms, max(1, charge))
                    op_ms = timer.elapsed_ms
                    conn.execute(
                        """
                        INSERT INTO balance_transactions (
                            tx_id, user_id, delta, balance_before, balance_after,
                            reason, reference_id, target_url, duration_ms, created_at_utc_ms
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            tx_id,
                            clean_user,
                            -charge,
                            before_bal,
                            after_bal,
                            clean_reason,
                            clean_ref,
                            clean_url,
                            op_ms,
                            now_ms,
                        ),
                    )

                    self._write_audit_event(
                        conn=conn,
                        user_id=clean_user,
                        event_type="CREDITS_SPENT",
                        severity="info",
                        detail=f"Charged {charge} credits ({clean_reason}). Balance: {before_bal} -> {after_bal}.",
                        duration_ms=op_ms,
                        stamp_ms=now_ms,
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

        rec = timer.record
        dur_ms = rec.duration_ms if rec else timer.elapsed_ms
        dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
        stamp_out = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

        return LedgerOperationResult(
            ok=True,
            user_id=clean_user,
            tx_id=tx_id,
            delta=-charge,
            balance_before=before_bal,
            balance_after=after_bal,
            reason=clean_reason,
            reference_id=clean_ref,
            target_url=clean_url,
            duration_ms=dur_ms,
            duration_s=dur_s,
            timestamp_utc_ms=stamp_out,
            message=f"Used {charge} credits. Remaining balance: {after_bal} credits.",
        )

    def refund_credits(
        self,
        user_id: str,
        amount: int,
        reason: str = "RUN_ERROR_REFUND",
        reference_id: str | None = None,
        target_url: str | None = None,
    ) -> LedgerOperationResult:
        """Return credits atomically if a charged step fails or needs reversal."""
        clean_user = (user_id or "").strip()
        refund_amt = int(amount)
        if not clean_user:
            raise ValueError("User ID cannot be empty.")
        if refund_amt <= 0 or refund_amt > MAX_CREDIT_ADJUSTMENT:
            raise ValueError(f"Invalid refund credit amount: {refund_amt}")

        clean_reason = (reason or "").strip() or "RUN_ERROR_REFUND"
        clean_ref = (reference_id or "").strip() or None
        clean_url = (target_url or "").strip() or None

        with StepTimer("ledger_refund_credits") as timer:
            conn = open_sqlite_connection(self.db_path)
            try:
                ensure_schema_exists(conn)
                ensure_user_account_row(conn, user_id=clean_user)
                now_ms = utc_now_iso_ms()

                conn.execute("BEGIN IMMEDIATE;")
                try:
                    row = conn.execute(
                        "SELECT balance, total_refunded FROM accounts WHERE user_id = ?;",
                        (clean_user,),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(f"Account '{clean_user}' not found during refund.")

                    before_bal = int(row["balance"])
                    after_bal = before_bal + refund_amt
                    new_refunded = int(row["total_refunded"]) + refund_amt

                    conn.execute(
                        """
                        UPDATE accounts
                        SET balance = ?,
                            total_refunded = ?,
                            updated_at_utc_ms = ?
                        WHERE user_id = ?;
                        """,
                        (after_bal, new_refunded, now_ms, clean_user),
                    )

                    tx_id = _make_tx_id("ref", clean_user, now_ms, refund_amt)
                    op_ms = timer.elapsed_ms
                    conn.execute(
                        """
                        INSERT INTO balance_transactions (
                            tx_id, user_id, delta, balance_before, balance_after,
                            reason, reference_id, target_url, duration_ms, created_at_utc_ms
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            tx_id,
                            clean_user,
                            refund_amt,
                            before_bal,
                            after_bal,
                            clean_reason,
                            clean_ref,
                            clean_url,
                            op_ms,
                            now_ms,
                        ),
                    )

                    self._write_audit_event(
                        conn=conn,
                        user_id=clean_user,
                        event_type="CREDITS_REFUNDED",
                        severity="warning",
                        detail=f"Refunded {refund_amt} credits ({clean_reason}). Balance: {before_bal} -> {after_bal}.",
                        duration_ms=op_ms,
                        stamp_ms=now_ms,
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

        rec = timer.record
        dur_ms = rec.duration_ms if rec else timer.elapsed_ms
        dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
        stamp_out = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

        return LedgerOperationResult(
            ok=True,
            user_id=clean_user,
            tx_id=tx_id,
            delta=refund_amt,
            balance_before=before_bal,
            balance_after=after_bal,
            reason=clean_reason,
            reference_id=clean_ref,
            target_url=clean_url,
            duration_ms=dur_ms,
            duration_s=dur_s,
            timestamp_utc_ms=stamp_out,
            message=f"Refunded {refund_amt} credits. Current balance: {after_bal} credits.",
        )

    def record_lead_run(self, user_id: str, run_payload: dict[str, Any]) -> None:
        """Save a processed lead row into lead_runs atomically."""
        clean_user = (user_id or "").strip()
        if not clean_user:
            raise ValueError("User ID cannot be empty.")

        now_ms = utc_now_iso_ms()
        run_id = str(run_payload.get("run_id") or f"run_{now_ms}")
        batch_id = str(run_payload.get("batch_id") or "single_run")

        conn = open_sqlite_connection(self.db_path)
        try:
            ensure_schema_exists(conn)
            ensure_user_account_row(conn, user_id=clean_user)
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO lead_runs (
                        run_id, user_id, batch_id, input_company, input_website,
                        normalized_url, final_url, domain, contact_email, clean_status,
                        clean_reasons_json, http_status, load_time_ms, process_duration_ms,
                        opportunity_score, priority_tier, issues_json, pitch_subject,
                        pitch_hook, pitch_email, credits_charged, balance_after,
                        processed_at_utc_ms
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        run_id,
                        clean_user,
                        batch_id,
                        str(run_payload.get("company_name") or ""),
                        str(run_payload.get("input_website") or ""),
                        str(run_payload.get("normalized_url") or ""),
                        str(run_payload.get("final_url") or ""),
                        str(run_payload.get("domain") or ""),
                        str(run_payload.get("contact_email") or ""),
                        str(run_payload.get("clean_status") or "clean"),
                        json.dumps(run_payload.get("clean_reasons") or []),
                        run_payload.get("http_status"),
                        float(run_payload.get("load_time_ms") or 0.0),
                        float(run_payload.get("process_duration_ms") or 0.0),
                        int(run_payload.get("opportunity_score") or 0),
                        str(run_payload.get("priority_tier") or "low"),
                        json.dumps(run_payload.get("issues") or []),
                        str(run_payload.get("pitch_subject") or ""),
                        str(run_payload.get("pitch_hook") or ""),
                        str(run_payload.get("pitch_email") or ""),
                        int(run_payload.get("credits_charged") or 0),
                        int(run_payload.get("balance_after") or 0),
                        str(run_payload.get("processed_at_utc_ms") or now_ms),
                    ),
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

    def get_transactions(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        """Return recent balance transactions in newest-first order."""
        clean_user = (user_id or "").strip()
        if not clean_user:
            return []
        safe_limit = max(1, min(500, int(limit)))
        conn = open_sqlite_connection(self.db_path)
        try:
            ensure_schema_exists(conn)
            rows = conn.execute(
                """
                SELECT tx_id, user_id, delta, balance_before, balance_after,
                       reason, reference_id, target_url, duration_ms, created_at_utc_ms
                FROM balance_transactions
                WHERE user_id = ?
                ORDER BY created_at_utc_ms DESC, rowid DESC
                LIMIT ?;
                """,
                (clean_user, safe_limit),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["duration_s"] = ms_to_seconds(float(d.get("duration_ms") or 0.0))
                result.append(d)
            return result
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_recent_lead_runs(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return saved processed leads for the user."""
        clean_user = (user_id or "").strip()
        if not clean_user:
            return []
        safe_limit = max(1, min(1000, int(limit)))
        conn = open_sqlite_connection(self.db_path)
        try:
            ensure_schema_exists(conn)
            rows = conn.execute(
                """
                SELECT run_id, user_id, batch_id, input_company, input_website,
                       normalized_url, final_url, domain, contact_email, clean_status,
                       clean_reasons_json, http_status, load_time_ms, process_duration_ms,
                       opportunity_score, priority_tier, issues_json, pitch_subject,
                       pitch_hook, pitch_email, credits_charged, balance_after,
                       processed_at_utc_ms
                FROM lead_runs
                WHERE user_id = ?
                ORDER BY processed_at_utc_ms DESC, rowid DESC
                LIMIT ?;
                """,
                (clean_user, safe_limit),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                item["load_time_s"] = ms_to_seconds(float(item.get("load_time_ms") or 0.0))
                item["process_duration_s"] = ms_to_seconds(float(item.get("process_duration_ms") or 0.0))
                try:
                    item["clean_reasons"] = json.loads(item.pop("clean_reasons_json") or "[]")
                except Exception:
                    item["clean_reasons"] = []
                try:
                    item["issues"] = json.loads(item.pop("issues_json") or "[]")
                except Exception:
                    item["issues"] = []
                items.append(item)
            return items
        finally:
            try:
                conn.close()
            except Exception:
                pass

