from __future__ import annotations

import csv
import io
import json
from typing import Any

import streamlit as st

from auth_licensing import AuthLicensingService, sanitize_user_id
from batch_pipeline import OutboundBatchPipeline, ProcessedLeadRow
from config import (
    APP_NAME,
    APP_VERSION,
    DB_FILE_DEFAULT,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_TOPUP_CREDITS,
    MAX_BATCH_ROWS_HARD_CAP,
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    PLANS,
    clamp_batch_limit,
    clamp_timeout_seconds,
)
from ledger import CreditLedger
from readiness import verify_system_readiness
from timing import format_duration_precise
from ui_components import (
    render_app_header,
    render_batch_summary_cards,
    render_export_controls,
    render_processed_leads_table,
    render_readiness_details,
    render_top_status_bar,
    render_transactions_and_plans,
)


SAMPLE_LEADS_TEXT = """Acme Dental Clinic | https://example.com | Dr. Maya Lin | maya@example.com
Northstar Roofing | northstar-roofing.example.org | Sam Patel | sam@northstar.example.org
Bad Typos Studio | gmial.com | | bad@gmial.com
Duplicate Row Check | https://example.com | Dr. Maya Lin | maya@example.com
"""


def _parse_pasted_leads(raw_text: str) -> list[dict[str, Any]]:
    """Parse pasted lead lines ('Company | Website | Contact | Email') safely."""
    leads: list[dict[str, Any]] = []
    lines = (raw_text or "").splitlines()
    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
        else:
            parts = [line]

        company = parts[0] if len(parts) >= 1 else ""
        website = parts[1] if len(parts) >= 2 else ""
        contact_name = parts[2] if len(parts) >= 3 else ""
        contact_email = parts[3] if len(parts) >= 4 else ""

        # If first column looks like a URL and second is empty, treat first as website.
        if not website and ("." in company) and (" " not in company):
            website = company
            company = ""

        leads.append(
            {
                "lead_id": f"paste_{idx:04d}",
                "company_name": company,
                "website": website,
                "contact_name": contact_name,
                "contact_email": contact_email,
            }
        )
    return leads


def _parse_uploaded_file(uploaded_file: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Read uploaded CSV or JSON lead file safely without crashing on bad rows."""
    if uploaded_file is None:
        return [], None

    filename = str(getattr(uploaded_file, "name", "") or "").lower()
    raw_bytes = uploaded_file.getvalue()
    try:
        text = raw_bytes.decode("utf-8-sig", errors="replace")
    except Exception as exc:
        return [], f"Could not read uploaded file text: {exc}"

    if filename.endswith(".json"):
        try:
            payload = json.loads(text)
            if isinstance(payload, list):
                rows = [r for r in payload if isinstance(r, dict)]
                return rows, None
            return [], "JSON file must contain a list of lead objects."
        except Exception as exc:
            return [], f"Invalid JSON file: {exc}"

    try:
        reader = csv.DictReader(io.StringIO(text))
        parsed: list[dict[str, Any]] = []
        for idx, row in enumerate(reader, start=1):
            if not isinstance(row, dict):
                continue
            normalized_row = {
                str(k).strip().lower(): (str(v).strip() if v is not None else "")
                for k, v in row.items()
                if k is not None
            }
            parsed.append(
                {
                    "lead_id": normalized_row.get("lead_id") or f"csv_{idx:04d}",
                    "company_name": (
                        normalized_row.get("company_name")
                        or normalized_row.get("company")
                        or ""
                    ),
                    "website": (
                        normalized_row.get("website")
                        or normalized_row.get("url")
                        or normalized_row.get("domain")
                        or ""
                    ),
                    "contact_name": (
                        normalized_row.get("contact_name")
                        or normalized_row.get("name")
                        or ""
                    ),
                    "contact_email": (
                        normalized_row.get("contact_email")
                        or normalized_row.get("email")
                        or ""
                    ),
                }
            )
        return parsed, None
    except Exception as exc:
        return [], f"Could not parse CSV file: {exc}"


def _init_session_state() -> None:
    """Set safe default values in Streamlit session state."""
    if "active_user_id" not in st.session_state:
        st.session_state["active_user_id"] = "founder_main"
    if "active_email" not in st.session_state:
        st.session_state["active_email"] = ""
    if "db_path" not in st.session_state:
        st.session_state["db_path"] = DB_FILE_DEFAULT
    if "last_batch_summary" not in st.session_state:
        st.session_state["last_batch_summary"] = None
    if "last_batch_rows" not in st.session_state:
        st.session_state["last_batch_rows"] = []
    if "paste_leads_input" not in st.session_state:
        st.session_state["paste_leads_input"] = SAMPLE_LEADS_TEXT


def _render_sidebar(
    ledger: CreditLedger,
    auth: AuthLicensingService,
) -> tuple[str, float, bool, bool, bool, int]:
    """Render account, plan/credits, speed, and run options in the sidebar."""
    with st.sidebar:
        st.header("Account & Credits")

        user_input = st.text_input(
            "Account ID",
            value=st.session_state["active_user_id"],
            key="sidebar_user_id",
        )
        email_input = st.text_input(
            "Email (optional)",
            value=st.session_state["active_email"],
            key="sidebar_email",
        )
        db_input = st.text_input(
            "SQLite Database File",
            value=st.session_state["db_path"],
            key="sidebar_db_path",
        )

        if st.button("Switch / Load Account", use_container_width=True):
            sess = auth.switch_or_login_account(
                raw_user_id=user_input,
                raw_email=email_input,
            )
            st.session_state["active_user_id"] = sess.user_id
            st.session_state["active_email"] = sess.email
            st.session_state["db_path"] = (db_input or "").strip() or DB_FILE_DEFAULT
            st.success(
                f"Loaded '{sess.user_id}' ({sess.balance} credits) in "
                f"{format_duration_precise(sess.duration_ms)}."
            )
            st.rerun()

        current_user = sanitize_user_id(user_input or st.session_state["active_user_id"], "founder_main")
        st.session_state["active_user_id"] = current_user
        st.session_state["db_path"] = (db_input or "").strip() or DB_FILE_DEFAULT

        st.divider()
        st.subheader("Add Credits or Activate Plan")

        topup_amt = st.number_input(
            "Top-Up Credits",
            min_value=1,
            max_value=10000,
            value=DEFAULT_TOPUP_CREDITS,
            step=10,
            key="sidebar_topup_amt",
        )
        if st.button("Add Credits Now", use_container_width=True):
            res = auth.top_up_credits(
                user_id=current_user,
                amount=int(topup_amt),
                reason="MANUAL_TOP_UP",
            )
            st.success(
                f"{res.message} ({format_duration_precise(res.duration_ms)})"
            )
            st.rerun()

        plan_options = list(PLANS.keys())
        chosen_plan = st.selectbox(
            "Plan Tier",
            options=plan_options,
            format_func=lambda k: f"{PLANS[k].display_name} (${PLANS[k].monthly_price_usd}/mo, {PLANS[k].included_credits} cr)",
            key="sidebar_plan_select",
        )
        lic_key_input = st.text_input(
            "License Key (leave blank to auto-generate)",
            value="",
            key="sidebar_license_key",
        )
        grant_plan_credits = st.checkbox(
            "Grant plan credits upon activation",
            value=True,
            key="sidebar_grant_credits",
        )
        if st.button("Activate Plan & Key", use_container_width=True):
            lic_res = auth.apply_plan_and_credits(
                user_id=current_user,
                plan_tier=chosen_plan,
                license_key=lic_key_input,
                grant_included_credits=grant_plan_credits,
            )
            st.success(
                f"{lic_res.message} ({format_duration_precise(lic_res.duration_ms)})"
            )
            st.rerun()

        st.divider()
        st.subheader("Run Settings")
        timeout_s = st.slider(
            "Site Timeout (seconds)",
            min_value=float(MIN_TIMEOUT_SECONDS),
            max_value=float(MAX_TIMEOUT_SECONDS),
            value=float(DEFAULT_TIMEOUT_SECONDS),
            step=0.5,
            key="sidebar_timeout_s",
        )
        run_enrichment = st.checkbox("Visit & audit websites (1 credit/lead)", value=True, key="sidebar_enrich")
        generate_hooks = st.checkbox("Write cold-email hooks & pitches", value=True, key="sidebar_hooks")
        skip_junk_dupes = st.checkbox("Skip junk & duplicate rows (save credits)", value=True, key="sidebar_skip_junk")

        snap = ledger.get_balance(current_user)
        max_rows = clamp_batch_limit(MAX_BATCH_ROWS_HARD_CAP, plan_tier=snap.plan_tier)
        batch_cap = st.number_input(
            "Max Rows Per Batch",
            min_value=1,
            max_value=max_rows,
            value=min(50, max_rows),
            step=1,
            key="sidebar_batch_cap",
        )

    return (
        current_user,
        clamp_timeout_seconds(timeout_s),
        bool(run_enrichment),
        bool(generate_hooks),
        bool(skip_junk_dupes),
        int(batch_cap),
    )


def main() -> None:
    st.set_page_config(
        page_title=f"{APP_NAME} v{APP_VERSION}",
        layout="wide",
    )
    _init_session_state()

    db_path = st.session_state["db_path"]
    ledger = CreditLedger(db_path=db_path)
    auth = AuthLicensingService(ledger=ledger)
    pipeline = OutboundBatchPipeline(db_path=db_path, ledger=ledger)

    render_app_header()

    (
        current_user,
        safe_timeout,
        run_enrichment,
        generate_hooks,
        skip_junk_dupes,
        batch_cap,
    ) = _render_sidebar(ledger=ledger, auth=auth)

    readiness = verify_system_readiness(
        user_id=current_user,
        db_path=db_path,
        auto_create_schema=True,
        auto_create_account=True,
        check_network=run_enrichment,
    )
    balance_snap = ledger.get_balance(current_user)

    render_top_status_bar(readiness=readiness, balance_snap=balance_snap)
    render_readiness_details(readiness=readiness)

    tab_single, tab_batch, tab_history = st.tabs(
        ["Single Lead Check", "Batch Lead List", "Transactions & Saved Runs"]
    )

    with tab_single:
        st.subheader("Check One Lead Fast")
        s_col1, s_col2 = st.columns(2)
        with s_col1:
            s_company = st.text_input("Company Name", value="", placeholder="Example Dental", key="single_company")
            s_website = st.text_input("Website or Domain", value="", placeholder="https://example.com", key="single_website")
        with s_col2:
            s_contact = st.text_input("Contact Name (optional)", value="", placeholder="Alex Rivera", key="single_contact")
            s_email = st.text_input("Contact Email (optional)", value="", placeholder="alex@example.com", key="single_email")

        if st.button("Clean, Enrich & Write Hook", type="primary", use_container_width=True, key="btn_run_single"):
            single_row = [
                {
                    "lead_id": "single_0001",
                    "company_name": s_company,
                    "website": s_website,
                    "contact_name": s_contact,
                    "contact_email": s_email,
                }
            ]
            summary = pipeline.run_batch(
                user_id=current_user,
                raw_leads=single_row,
                run_enrichment=run_enrichment,
                generate_hooks=generate_hooks,
                skip_junk_and_duplicates=skip_junk_dupes,
                sort_by_score=True,
                timeout_s=safe_timeout,
                batch_limit=1,
                save_to_db=True,
            )
            st.session_state["last_batch_summary"] = summary
            st.session_state["last_batch_rows"] = summary.rows
            st.rerun()

    with tab_batch:
        st.subheader("Process a Lead List (Paste or Upload CSV / JSON)")
        st.caption("Format per line: `Company | Website | Contact Name | Email`")

        uploaded = st.file_uploader(
            "Upload CSV or JSON file (optional)",
            type=["csv", "json"],
            key="batch_file_uploader",
        )
        pasted_text = st.text_area(
            "Or Paste Lead Lines Here",
            value=st.session_state["paste_leads_input"],
            height=150,
            key="batch_paste_area",
        )

        if st.button("Run Full Lead Batch", type="primary", use_container_width=True, key="btn_run_batch"):
            st.session_state["paste_leads_input"] = pasted_text
            file_rows, file_err = _parse_uploaded_file(uploaded)
            if file_err:
                st.error(file_err)
            else:
                pasted_rows = _parse_pasted_leads(pasted_text)
                combined_rows = file_rows + pasted_rows
                if not combined_rows:
                    st.warning("Please enter or upload at least one lead row first.")
                else:
                    summary = pipeline.run_batch(
                        user_id=current_user,
                        raw_leads=combined_rows,
                        run_enrichment=run_enrichment,
                        generate_hooks=generate_hooks,
                        skip_junk_and_duplicates=skip_junk_dupes,
                        sort_by_score=True,
                        timeout_s=safe_timeout,
                        batch_limit=batch_cap,
                        save_to_db=True,
                    )
                    st.session_state["last_batch_summary"] = summary
                    st.session_state["last_batch_rows"] = summary.rows
                    st.rerun()

    last_summary = st.session_state.get("last_batch_summary")
    last_rows: list[ProcessedLeadRow] = st.session_state.get("last_batch_rows") or []

    if last_summary is not None:
        st.divider()
        render_batch_summary_cards(summary=last_summary)
        render_export_controls(summary=last_summary, rows=last_rows)
        render_processed_leads_table(rows=last_rows)

    with tab_history:
        tx_list = ledger.get_transactions(user_id=current_user, limit=30)
        render_transactions_and_plans(
            current_plan_tier=balance_snap.plan_tier,
            transactions=tx_list,
        )

        st.subheader("Saved Processed Leads in Database")
        saved_runs = ledger.get_recent_lead_runs(user_id=current_user, limit=30)
        if not saved_runs:
            st.info("No saved lead runs in the database yet.")
        else:
            st.dataframe(saved_runs, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
