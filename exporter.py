from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
import io
import json
from typing import Any

from batch_pipeline import BatchRunSummary, ProcessedLeadRow
from config import EXPORT_COLUMNS
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


_DANGEROUS_CSV_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class ExportBundle:
    """Complete export package with CSV text, summary report, and exact timing."""

    ok: bool
    batch_id: str
    row_count: int
    ready_to_send_count: int
    csv_filename: str
    csv_content: str
    report_filename: str
    summary_report: str
    export_duration_ms: float
    export_duration_s: float
    exported_at_utc_ms: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_csv_cell(value: Any) -> str:
    """Format a value safely for CSV without breaking spreadsheet formulas or newlines."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        text = " | ".join(str(x).strip() for x in value if str(x).strip())
    elif isinstance(value, dict):
        text = json.dumps(value, default=str)
    else:
        text = str(value)

    normalized = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
    if normalized.startswith(_DANGEROUS_CSV_PREFIXES):
        normalized = "'" + normalized
    return normalized


def _row_to_flat_dict(row: ProcessedLeadRow | dict[str, Any]) -> dict[str, Any]:
    """Normalize a ProcessedLeadRow or plain dict into a consistent key map."""
    if isinstance(row, ProcessedLeadRow):
        data = row.to_dict()
    elif isinstance(row, dict):
        data = dict(row)
    else:
        data = {}

    contact_email = str(data.get("best_email") or data.get("contact_email") or "").strip()
    load_ms = float(data.get("load_time_ms") or 0.0)
    proc_ms = float(data.get("process_duration_ms") or 0.0)

    return {
        "lead_id": data.get("lead_id", ""),
        "batch_id": data.get("batch_id", ""),
        "company_name": data.get("company_name", ""),
        "contact_name": data.get("contact_name", ""),
        "contact_email": contact_email,
        "input_website": data.get("input_website", ""),
        "normalized_url": data.get("normalized_url", ""),
        "final_url": data.get("final_url", ""),
        "domain": data.get("domain", ""),
        "clean_status": data.get("clean_status", ""),
        "clean_reasons": data.get("clean_reasons", []),
        "http_status": data.get("http_status") if data.get("http_status") is not None else "",
        "load_time_ms": round(load_ms, 3),
        "load_time_s": ms_to_seconds(load_ms, decimals=4),
        "process_duration_ms": round(proc_ms, 3),
        "process_duration_s": ms_to_seconds(proc_ms, decimals=4),
        "opportunity_score": int(data.get("opportunity_score") or 0),
        "priority_tier": data.get("priority_tier", "low"),
        "extracted_emails": data.get("extracted_emails", []),
        "extracted_phones": data.get("extracted_phones", []),
        "issues_summary": data.get("issues_summary", ""),
        "pitch_subject": data.get("pitch_subject", ""),
        "pitch_hook": data.get("pitch_hook", ""),
        "pitch_email": data.get("pitch_email", ""),
        "credits_charged": int(data.get("credits_charged") or 0),
        "balance_after": int(data.get("balance_after") or 0),
        "processed_at_utc_ms": data.get("processed_at_utc_ms", ""),
    }


def build_leads_csv(
    rows: list[ProcessedLeadRow | dict[str, Any]],
    only_ready_to_send: bool = False,
) -> tuple[str, int]:
    """Build clean CSV text using EXPORT_COLUMNS and return (csv_text, exported_row_count)."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(list(EXPORT_COLUMNS))

    exported_count = 0
    for raw_item in rows or []:
        flat = _row_to_flat_dict(raw_item)
        email_val = str(flat.get("contact_email") or "").strip()
        if only_ready_to_send and ("@" not in email_val):
            continue

        row_cells = [_safe_csv_cell(flat.get(col, "")) for col in EXPORT_COLUMNS]
        writer.writerow(row_cells)
        exported_count += 1

    return output.getvalue(), exported_count


def build_campaign_summary_report(
    summary: BatchRunSummary | dict[str, Any],
    rows: list[ProcessedLeadRow | dict[str, Any]],
) -> str:
    """Create a plain, easy-to-read summary report for campaign review or client sharing."""
    if isinstance(summary, BatchRunSummary):
        s_data = summary.to_dict()
    elif isinstance(summary, dict):
        s_data = dict(summary)
    else:
        s_data = {}

    flat_rows = [_row_to_flat_dict(r) for r in (rows or [])]
    top_leads = sorted(
        flat_rows,
        key=lambda item: (-int(item.get("opportunity_score") or 0), str(item.get("company_name") or "").lower()),
    )[:10]

    total_ms = float(s_data.get("total_duration_ms") or 0.0)
    total_s = float(s_data.get("total_duration_s") or ms_to_seconds(total_ms))
    avg_ms = float(s_data.get("avg_row_duration_ms") or 0.0)
    avg_s = float(s_data.get("avg_row_duration_s") or ms_to_seconds(avg_ms))

    lines: list[str] = [
        "OUTBOUND LEAD CAMPAIGN SUMMARY REPORT",
        "====================================",
        f"Batch ID:            {s_data.get('batch_id', 'n/a')}",
        f"Account:             {s_data.get('user_id', 'n/a')}",
        f"Started (UTC ms):    {s_data.get('started_at_utc_ms', 'n/a')}",
        f"Completed (UTC ms):  {s_data.get('completed_at_utc_ms', 'n/a')}",
        f"Total Run Time:      {total_ms:.3f} ms ({total_s:.4f} s)",
        f"Avg Time Per Row:    {avg_ms:.3f} ms ({avg_s:.4f} s)",
        "",
        "COUNTS & CREDITS",
        "----------------",
        f"Submitted Rows:      {s_data.get('total_submitted_rows', len(flat_rows))}",
        f"Clean Leads:         {s_data.get('clean_rows_count', 0)}",
        f"Needs Review:        {s_data.get('review_rows_count', 0)}",
        f"Junk Rows:           {s_data.get('junk_rows_count', 0)}",
        f"Duplicate Rows:      {s_data.get('duplicate_rows_count', 0)}",
        f"Sites Enriched:      {s_data.get('enriched_rows_count', 0)}",
        f"Ready to Email:      {s_data.get('ready_to_send_count', 0)}",
        f"High Priority:       {s_data.get('high_priority_count', 0)}",
        f"Medium Priority:     {s_data.get('medium_priority_count', 0)}",
        f"Low Priority:        {s_data.get('low_priority_count', 0)}",
        f"Credits Charged:     {s_data.get('total_credits_charged', 0)}",
        f"Balance Before:      {s_data.get('balance_before', 0)}",
        f"Balance After:       {s_data.get('balance_after', 0)}",
        "",
        "TOP PRIORITY LEADS",
        "----------------",
    ]

    if not top_leads:
        lines.append("No processed leads in this batch.")
    else:
        for idx, item in enumerate(top_leads, start=1):
            company = item.get("company_name") or "Unknown Company"
            score = item.get("opportunity_score") or 0
            tier = str(item.get("priority_tier") or "low").upper()
            email = item.get("contact_email") or "No email found"
            domain = item.get("domain") or "No domain"
            hook = item.get("pitch_hook") or ""
            lines.append(f"{idx}. {company} ({domain}) — Score: {score}/100 [{tier}] | Contact: {email}")
            if hook:
                lines.append(f"   Hook: {hook}")

    errs = s_data.get("errors") or []
    if errs:
        lines.extend(["", "NOTES & ERRORS", "--------------"])
        for err in errs:
            lines.append(f"- {err}")

    return "\n".join(lines)


def create_export_bundle(
    summary: BatchRunSummary | dict[str, Any],
    rows: list[ProcessedLeadRow | dict[str, Any]],
    only_ready_to_send: bool = False,
) -> ExportBundle:
    """Generate CSV content, summary text report, filenames, and exact export timing."""
    with StepTimer("create_export_bundle") as timer:
        if isinstance(summary, BatchRunSummary):
            batch_id = summary.batch_id
        elif isinstance(summary, dict):
            batch_id = str(summary.get("batch_id") or "outbound_batch")
        else:
            batch_id = "outbound_batch"

        safe_batch = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in batch_id)[:60]
        suffix = "_ready_to_send" if only_ready_to_send else "_all_leads"
        csv_filename = f"{safe_batch}{suffix}.csv"
        report_filename = f"{safe_batch}_summary_report.txt"

        csv_content, exported_count = build_leads_csv(
            rows=rows,
            only_ready_to_send=only_ready_to_send,
        )
        summary_report = build_campaign_summary_report(summary=summary, rows=rows)

        ready_total = 0
        for item in rows or []:
            flat = _row_to_flat_dict(item)
            if "@" in str(flat.get("contact_email") or ""):
                ready_total += 1

    rec = timer.record
    dur_ms = rec.duration_ms if rec else timer.elapsed_ms
    dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
    exported_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

    return ExportBundle(
        ok=True,
        batch_id=batch_id,
        row_count=exported_count,
        ready_to_send_count=ready_total,
        csv_filename=csv_filename,
        csv_content=csv_content,
        report_filename=report_filename,
        summary_report=summary_report,
        export_duration_ms=dur_ms,
        export_duration_s=dur_s,
        exported_at_utc_ms=exported_at,
    )
