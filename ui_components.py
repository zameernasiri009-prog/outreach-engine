from __future__ import annotations

from typing import Any

import streamlit as st

from batch_pipeline import BatchRunSummary, ProcessedLeadRow
from config import APP_NAME, APP_SUBTITLE, APP_VERSION, PLANS
from exporter import create_export_bundle
from ledger import BalanceSnapshot
from readiness import ReadinessReport
from timing import format_duration_precise, ms_to_seconds


def render_app_header() -> None:
    """Display the main app title and clear plain-English purpose."""
    st.title(f"{APP_NAME}")
    st.caption(f"{APP_SUBTITLE}  •  v{APP_VERSION}")


def render_top_status_bar(
    readiness: ReadinessReport,
    balance_snap: BalanceSnapshot,
) -> None:
    """Show readiness status, live credit balance, plan tier, and check speed."""
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        if readiness.ready:
            st.success("Ready to Run")
        else:
            st.error("Blocked — Fix Check")

    with c2:
        st.metric(
            label="Credit Balance",
            value=f"{balance_snap.balance} credits",
            delta=f"Spent: {balance_snap.total_spent}",
        )

    with c3:
        st.metric(
            label="Active Plan",
            value=balance_snap.plan_tier.capitalize(),
            delta=f"Account: {balance_snap.user_id}",
        )

    with c4:
        st.metric(
            label="Balance Check Speed",
            value=f"{balance_snap.check_duration_ms:.2f} ms",
            delta=f"{balance_snap.check_duration_s:.4f} s",
        )


def render_readiness_details(readiness: ReadinessReport) -> None:
    """Show clear database, schema, and network check results."""
    with st.expander("System & Database Readiness Check", expanded=not readiness.ready):
        st.write(
            f"Checked at `{readiness.checked_at_utc_ms}` in "
            f"**{format_duration_precise(readiness.duration_ms)}**"
        )
        col_a, col_b, col_c = st.columns(3)
        col_a.write(f"Database Connected: **{'Yes' if readiness.db_connected else 'No'}**")
        col_b.write(f"WAL Mode Active: **{'Yes' if readiness.wal_mode_active else 'No'}**")
        col_c.write(f"Internet DNS Reachable: **{'Yes' if readiness.dns_reachable else 'No'}**")

        if readiness.blockers:
            for item in readiness.blockers:
                st.error(f"Blocker: {item}")
        if readiness.warnings:
            for warn in readiness.warnings:
                st.warning(f"Note: {warn}")
        if not readiness.blockers and not readiness.warnings:
            st.info("All tables, columns, WAL mode, and account checks passed cleanly.")


def render_batch_summary_cards(summary: BatchRunSummary) -> None:
    """Show counts, credits used, and exact total/average timing for a batch run."""
    st.subheader("Run Summary & Timing")
    st.write(summary.message)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows Processed", f"{summary.processed_rows_count} / {summary.total_submitted_rows}")
    m2.metric("Ready to Email", f"{summary.ready_to_send_count}")
    m3.metric(
        "Credits Used",
        f"{summary.total_credits_charged}",
        delta=f"Balance: {summary.balance_before} -> {summary.balance_after}",
    )
    m4.metric(
        "Total Batch Time",
        f"{summary.total_duration_ms:.2f} ms",
        delta=f"{summary.total_duration_s:.4f} s",
    )

    t1, t2, t3, t4 = st.columns(4)
    t1.write(f"Clean: **{summary.clean_rows_count}**")
    t2.write(f"Needs Review: **{summary.review_rows_count}**")
    t3.write(f"Junk / Dupes: **{summary.junk_rows_count} / {summary.duplicate_rows_count}**")
    t4.write(
        f"Avg Per Row: **{summary.avg_row_duration_ms:.2f} ms** "
        f"({summary.avg_row_duration_s:.4f} s)"
    )

    if summary.errors:
        with st.expander(f"Row Notes & Warnings ({len(summary.errors)})", expanded=False):
            for err in summary.errors:
                st.warning(err)


def render_processed_leads_table(rows: list[ProcessedLeadRow | dict[str, Any]]) -> None:
    """Render processed leads in a clean table with scores, emails, hooks, and timing."""
    if not rows:
        st.info("No processed leads yet. Run a single lead or paste a list above to get started.")
        return

    table_data: list[dict[str, Any]] = []
    for item in rows:
        d = item.to_dict() if isinstance(item, ProcessedLeadRow) else dict(item)
        proc_ms = float(d.get("process_duration_ms") or 0.0)
        load_ms = float(d.get("load_time_ms") or 0.0)
        table_data.append(
            {
                "Company": d.get("company_name", ""),
                "Domain": d.get("domain", ""),
                "Email": d.get("best_email") or d.get("contact_email") or "",
                "Status": d.get("clean_status", ""),
                "Score": int(d.get("opportunity_score") or 0),
                "Priority": str(d.get("priority_tier") or "low").upper(),
                "Load (ms / s)": f"{load_ms:.1f} ms / {ms_to_seconds(load_ms):.4f} s",
                "Row Time (ms / s)": f"{proc_ms:.2f} ms / {ms_to_seconds(proc_ms):.4f} s",
                "Subject": d.get("pitch_subject", ""),
            }
        )

    st.dataframe(table_data, use_container_width=True, hide_index=True)

    st.subheader("Outreach Pitch Hooks & Ready-to-Send Emails")
    for idx, item in enumerate(rows, start=1):
        d = item.to_dict() if isinstance(item, ProcessedLeadRow) else dict(item)
        company = d.get("company_name") or "Unknown Company"
        score = int(d.get("opportunity_score") or 0)
        tier = str(d.get("priority_tier") or "low").upper()
        email_addr = d.get("best_email") or d.get("contact_email") or "No email found"
        proc_ms = float(d.get("process_duration_ms") or 0.0)

        label = f"#{idx}  {company} — Score {score}/100 ({tier}) | {email_addr}"
        with st.expander(label, expanded=(idx == 1)):
            st.write(f"**Domain / URL:** {d.get('final_url') or d.get('normalized_url') or d.get('input_website') or 'None'}")
            st.write(f"**Contact Email:** `{email_addr}`  •  **Row Time:** `{format_duration_precise(proc_ms)}`")
            st.write(f"**What We Spotted:** {d.get('issues_summary') or 'None'}")
            st.text_input(
                f"Subject Line #{idx}",
                value=str(d.get("pitch_subject") or ""),
                key=f"subj_{d.get('run_id') or idx}_{idx}",
            )
            st.text_area(
                f"Opening Hook #{idx}",
                value=str(d.get("pitch_hook") or ""),
                height=75,
                key=f"hook_{d.get('run_id') or idx}_{idx}",
            )
            st.text_area(
                f"Ready Outreach Email #{idx}",
                value=str(d.get("pitch_email") or ""),
                height=165,
                key=f"email_{d.get('run_id') or idx}_{idx}",
            )


def render_export_controls(
    summary: BatchRunSummary,
    rows: list[ProcessedLeadRow | dict[str, Any]],
) -> None:
    """Render CSV and summary report download buttons with exact export timing."""
    if not rows:
        return

    only_ready = st.checkbox(
        "Export only leads that have a contact email",
        value=False,
        key=f"only_ready_{summary.batch_id}",
    )
    bundle = create_export_bundle(summary=summary, rows=rows, only_ready_to_send=only_ready)

    st.caption(
        f"Export package ready ({bundle.row_count} rows) in "
        f"{format_duration_precise(bundle.export_duration_ms)}."
    )

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            label=f"Download CSV ({bundle.row_count} rows)",
            data=bundle.csv_content,
            file_name=bundle.csv_filename,
            mime="text/csv",
            key=f"dl_csv_{summary.batch_id}_{only_ready}",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            label="Download Summary Report (.txt)",
            data=bundle.summary_report,
            file_name=bundle.report_filename,
            mime="text/plain",
            key=f"dl_rep_{summary.batch_id}",
            use_container_width=True,
        )

    with st.expander("Preview Summary Report", expanded=False):
        st.code(bundle.summary_report, language="text")


def render_transactions_and_plans(
    current_plan_tier: str,
    transactions: list[dict[str, Any]],
) -> None:
    """Show plan options and recent balance transactions with exact timing."""
    st.subheader("Plans & Credit Rules")
    p_cols = st.columns(len(PLANS))
    for col, spec in zip(p_cols, PLANS.values()):
        active_mark = " (Active)" if spec.tier_id == current_plan_tier else ""
        with col:
            st.markdown(f"**{spec.display_name}{active_mark}**")
            st.write(f"Price: **${spec.monthly_price_usd}/mo**")
            st.write(f"Credits: **{spec.included_credits}**  •  Max Batch: **{spec.max_batch_size}**")
            st.caption(spec.description)

    st.subheader("Recent Balance Transactions")
    if not transactions:
        st.info("No balance transactions recorded yet.")
        return

    tx_rows = [
        {
            "Time (UTC ms)": t.get("created_at_utc_ms", ""),
            "Change": int(t.get("delta") or 0),
            "Before": int(t.get("balance_before") or 0),
            "After": int(t.get("balance_after") or 0),
            "Reason": t.get("reason", ""),
            "Target": t.get("target_url") or t.get("reference_id") or "",
            "Tx Time (ms / s)": (
                f"{float(t.get('duration_ms') or 0.0):.2f} ms / "
                f"{float(t.get('duration_s') or 0.0):.4f} s"
            ),
        }
        for t in transactions
    ]
    st.dataframe(tx_rows, use_container_width=True, hide_index=True)
