from __future__ import annotations

from dataclasses import dataclass, asdict
import os
import socket
import sqlite3
from typing import Any

from config import (
    DB_FILE_DEFAULT,
    DNS_CHECK_HOST,
    DNS_CHECK_PORT,
    DNS_CHECK_TIMEOUT_S,
    REQUIRED_TABLE_COLUMNS,
    REQUIRED_TABLES,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_CONNECT_TIMEOUT_S,
    SQLITE_JOURNAL_MODE,
    SQLITE_SYNCHRONOUS_MODE,
    get_plan_spec,
)
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


@dataclass
class ReadinessReport:
    """Full pre-flight check result before running any action."""

    ready: bool
    user_id: str
    db_path: str
    checked_at_utc_ms: str
    duration_ms: float
    duration_s: float
    db_connected: bool
    wal_mode_active: bool
    tables_ready: bool
    columns_ready: bool
    account_ready: bool
    dns_reachable: bool
    plan_tier: str
    balance: int
    missing_tables: list[str]
    missing_columns: dict[str, list[str]]
    blockers: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def open_sqlite_connection(db_path: str = DB_FILE_DEFAULT) -> sqlite3.Connection:
    """Open a SQLite connection configured for safe multi-step writes."""
    target_path = (db_path or "").strip() or DB_FILE_DEFAULT
    parent_dir = os.path.dirname(os.path.abspath(target_path))
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(
        target_path,
        timeout=SQLITE_CONNECT_TIMEOUT_S,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE};")
    conn.execute(f"PRAGMA synchronous={SQLITE_SYNCHRONOUS_MODE};")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def ensure_schema_exists(conn: sqlite3.Connection) -> None:
    """Create required tables if they are not there yet."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL DEFAULT '',
                plan_tier TEXT NOT NULL DEFAULT 'starter',
                balance INTEGER NOT NULL DEFAULT 50,
                total_credited INTEGER NOT NULL DEFAULT 50,
                total_spent INTEGER NOT NULL DEFAULT 0,
                total_refunded INTEGER NOT NULL DEFAULT 0,
                created_at_utc_ms TEXT NOT NULL,
                updated_at_utc_ms TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_transactions (
                tx_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                delta INTEGER NOT NULL,
                balance_before INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL,
                reference_id TEXT,
                target_url TEXT,
                duration_ms REAL NOT NULL,
                created_at_utc_ms TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES accounts(user_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_runs (
                run_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                input_company TEXT NOT NULL DEFAULT '',
                input_website TEXT NOT NULL DEFAULT '',
                normalized_url TEXT NOT NULL DEFAULT '',
                final_url TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT '',
                contact_email TEXT NOT NULL DEFAULT '',
                clean_status TEXT NOT NULL DEFAULT 'pending',
                clean_reasons_json TEXT NOT NULL DEFAULT '[]',
                http_status INTEGER,
                load_time_ms REAL NOT NULL DEFAULT 0.0,
                process_duration_ms REAL NOT NULL DEFAULT 0.0,
                opportunity_score INTEGER NOT NULL DEFAULT 0,
                priority_tier TEXT NOT NULL DEFAULT 'low',
                issues_json TEXT NOT NULL DEFAULT '[]',
                pitch_subject TEXT NOT NULL DEFAULT '',
                pitch_hook TEXT NOT NULL DEFAULT '',
                pitch_email TEXT NOT NULL DEFAULT '',
                credits_charged INTEGER NOT NULL DEFAULT 0,
                balance_after INTEGER NOT NULL DEFAULT 0,
                processed_at_utc_ms TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES accounts(user_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS licenses (
                license_key TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                plan_tier TEXT NOT NULL,
                credits_granted INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                activated_at_utc_ms TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                detail TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                created_at_utc_ms TEXT NOT NULL
            );
            """
        )
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise


def ensure_user_account_row(
    conn: sqlite3.Connection,
    user_id: str,
    email: str = "",
    plan_tier: str = "starter",
) -> tuple[str, int]:
    """Make sure the user has a starting balance row in accounts."""
    clean_user = (user_id or "").strip()
    if not clean_user:
        raise ValueError("User ID cannot be empty.")

    spec = get_plan_spec(plan_tier)
    now_ms = utc_now_iso_ms()
    clean_email = (email or "").strip()

    conn.execute("BEGIN IMMEDIATE;")
    try:
        row = conn.execute(
            "SELECT plan_tier, balance FROM accounts WHERE user_id = ?;",
            (clean_user,),
        ).fetchone()
        if row is None:
            starting_credits = spec.included_credits
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
                    starting_credits,
                    starting_credits,
                    now_ms,
                    now_ms,
                ),
            )
            tx_id = f"tx_init_{clean_user}_{now_ms.replace(':', '').replace('.', '').replace('-', '')}"
            conn.execute(
                """
                INSERT INTO balance_transactions (
                    tx_id, user_id, delta, balance_before, balance_after,
                    reason, reference_id, target_url, duration_ms, created_at_utc_ms
                )
                VALUES (?, ?, ?, 0, ?, 'INITIAL_STARTER_BALANCE', 'readiness_init', NULL, 0.0, ?);
                """,
                (tx_id, clean_user, starting_credits, starting_credits, now_ms),
            )
            tier_out = spec.tier_id
            bal_out = starting_credits
        else:
            tier_out = str(row["plan_tier"])
            bal_out = int(row["balance"])
        conn.execute("COMMIT;")
        return tier_out, bal_out
    except Exception:
        conn.execute("ROLLBACK;")
        raise


def check_dns_reachability(
    host: str = DNS_CHECK_HOST,
    port: int = DNS_CHECK_PORT,
    timeout_s: float = DNS_CHECK_TIMEOUT_S,
) -> tuple[bool, str | None]:
    """Check if outbound DNS and socket reachability are working."""
    target_host = (host or "").strip() or DNS_CHECK_HOST
    safe_timeout = max(0.5, float(timeout_s))
    try:
        socket.setdefaulttimeout(safe_timeout)
        addr_info = socket.getaddrinfo(target_host, port, type=socket.SOCK_STREAM)
        if not addr_info:
            return False, f"No network address returned for {target_host}."
        return True, None
    except OSError as exc:
        return False, f"Network check failed for {target_host}:{port} ({exc})."


def verify_system_readiness(
    user_id: str,
    db_path: str = DB_FILE_DEFAULT,
    auto_create_schema: bool = True,
    auto_create_account: bool = True,
    email: str = "",
    plan_tier: str = "starter",
    check_network: bool = True,
) -> ReadinessReport:
    """Run all pre-flight checks and return a clear pass/block report."""
    clean_user = (user_id or "").strip()
    clean_db = (db_path or "").strip() or DB_FILE_DEFAULT

    db_connected = False
    wal_mode_active = False
    tables_ready = False
    columns_ready = False
    account_ready = False
    dns_reachable = False
    resolved_tier = "starter"
    resolved_balance = 0

    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    blockers: list[str] = []
    warnings: list[str] = []

    with StepTimer("system_readiness_check") as timer:
        if not clean_user:
            blockers.append("No active user account selected.")

        conn: sqlite3.Connection | None = None
        try:
            conn = open_sqlite_connection(clean_db)
            conn.execute("SELECT 1;").fetchone()
            db_connected = True

            mode_row = conn.execute("PRAGMA journal_mode;").fetchone()
            mode_val = str(mode_row[0]).lower() if mode_row and mode_row[0] else ""
            wal_mode_active = mode_val == SQLITE_JOURNAL_MODE.lower()
            if not wal_mode_active:
                warnings.append(f"Database mode is '{mode_val or 'unknown'}' instead of WAL.")

            if auto_create_schema and db_connected:
                ensure_schema_exists(conn)

            existing_tables = {
                str(r["name"])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            }
            missing_tables = [t for t in REQUIRED_TABLES if t not in existing_tables]
            tables_ready = len(missing_tables) == 0
            if not tables_ready:
                blockers.append(f"Missing database tables: {', '.join(missing_tables)}.")

            for tbl_name, req_cols in REQUIRED_TABLE_COLUMNS.items():
                if tbl_name not in existing_tables:
                    missing_columns[tbl_name] = list(req_cols)
                    continue
                pragma_rows = conn.execute(f"PRAGMA table_info({tbl_name});").fetchall()
                present_cols = {str(col["name"]) for col in pragma_rows}
                absent = [c for c in req_cols if c not in present_cols]
                if absent:
                    missing_columns[tbl_name] = absent

            columns_ready = len(missing_columns) == 0
            if not columns_ready:
                blockers.append("Database table columns do not match expected layout.")

            if clean_user and tables_ready and columns_ready:
                acc_row = conn.execute(
                    "SELECT plan_tier, balance FROM accounts WHERE user_id = ?;",
                    (clean_user,),
                ).fetchone()
                if acc_row is None and auto_create_account:
                    resolved_tier, resolved_balance = ensure_user_account_row(
                        conn=conn,
                        user_id=clean_user,
                        email=email,
                        plan_tier=plan_tier,
                    )
                    account_ready = True
                elif acc_row is not None:
                    resolved_tier = str(acc_row["plan_tier"])
                    resolved_balance = int(acc_row["balance"])
                    account_ready = True
                else:
                    blockers.append(f"Account '{clean_user}' does not exist in database.")

        except Exception as exc:
            blockers.append(f"Database connection error: {exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        if check_network:
            dns_reachable, dns_err = check_dns_reachability()
            if not dns_reachable and dns_err:
                warnings.append(dns_err)
        else:
            dns_reachable = True

    rec = timer.record
    elapsed_ms = rec.duration_ms if rec else timer.elapsed_ms
    elapsed_s = rec.duration_s if rec else ms_to_seconds(elapsed_ms)
    checked_at = rec.started_at_utc_ms if rec else utc_now_iso_ms()

    is_ready = (
        db_connected
        and tables_ready
        and columns_ready
        and account_ready
        and len(blockers) == 0
    )

    return ReadinessReport(
        ready=is_ready,
        user_id=clean_user,
        db_path=clean_db,
        checked_at_utc_ms=checked_at,
        duration_ms=elapsed_ms,
        duration_s=elapsed_s,
        db_connected=db_connected,
        wal_mode_active=wal_mode_active,
        tables_ready=tables_ready,
        columns_ready=columns_ready,
        account_ready=account_ready,
        dns_reachable=dns_reachable,
        plan_tier=resolved_tier,
        balance=resolved_balance,
        missing_tables=missing_tables,
        missing_columns=missing_columns,
        blockers=blockers,
        warnings=warnings,
    )
