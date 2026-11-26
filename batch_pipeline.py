from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from typing import Any

from auth_licensing import sanitize_user_id
from cleaner import CleanedLead, clean_single_lead
from config import (
    COST_CLEAN_ROW,
    COST_ENRICH_LEAD,
    COST_FULL_LEAD_RUN,
    COST_GENERATE_HOOK,
    DB_FILE_DEFAULT,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_RETRIES_PER_URL,
    clamp_batch_limit,
    clamp_timeout_seconds,
)
from enricher import EnrichmentResult, enrich_lead
from hook_generator import PitchHookPack, generate_pitch_hook
from ledger import CreditLedger
from readiness import verify_system_readiness
from scorer import ScoredLead, rank_scored_leads, score_enriched_lead
from timing import BatchStopwatch, StepTimer, ms_to_seconds, utc_now_iso_ms


@dataclass
class ProcessedLeadRow:
    """Complete output for one lead across cleaning, enrichment, scoring, and hooks."""

    run_id: str
    batch_id: str
    lead_id: str
    company_name: str
    contact_name: str
    contact_email: str
    best_email: str
    email_source: str
    extracted_emails: list[str]
    extracted_phones: list[str]
    input_website: str
    normalized_url: str
    final_url: str
    domain: str
    clean_status: str
    clean_reasons: list[str]
    is_duplicate: bool
    duplicate_of_lead_id: str | None
    reachable: bool
    timed_out: bool
    http_status: int | None
    load_time_ms: float
    load_time_s: float
    opportunity_score: int
    priority_tier: str
    issues: list[str]
    issues_summary: str
    pitch_subject: str
    pitch_hook: str
    pitch_email: str
    follow_up_line: str
    ready_to_send: bool
    credits_charged: int
    balance_after: int
    process_duration_ms: float
    process_duration_s: float
    processed_at_utc_ms: str
    row_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BatchRunSummary:
    """Full summary of a single or multi-lead run with exact timing and balance."""

    ok: bool
    batch_id: str
    user_id: str
    db_path: str
    total_submitted_rows: int
    processed_rows_count: int
    clean_rows_count: int
    review_rows_count: int
    junk_rows_count: int
    duplicate_rows_count: int
    enriched_rows_count: int
    failed_rows_count: int
    high_priority_count: int
    medium_priority_count: int
    low_priority_count: int
    ready_to_send_count: int
    total_credits_charged: int
    balance_before: int
    balance_after: int
    total_duration_ms: float
    total_duration_s: float
    avg_row_duration_ms: float
    avg_row_duration_s: float
    started_at_utc_ms: str
    completed_at_utc_ms: str
    rows: list[ProcessedLeadRow]
    errors: list[str]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _make_batch_id(user_id: str, stamp_ms: str) -> str:
    clean_user = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in user_id)[:20]
    clean_stamp = stamp_ms.replace(":", "").replace("-", "").replace(".", "").replace("Z", "")
    return f"batch_{clean_user}_{clean_stamp}"


def _make_run_id(batch_id: str, index: int) -> str:
    return f"{batch_id}_r{int(index):04d}"


class OutboundBatchPipeline:
    """Runs cleaning, enrichment, scoring, pitch hooks, credit billing, and saving."""

    def __init__(
        self,
        db_path: str = DB_FILE_DEFAULT,
        ledger: CreditLedger | None = None,
    ) -> None:
        self.db_path = (db_path or "").strip() or DB_FILE_DEFAULT
        self.ledger = ledger if ledger is not None else CreditLedger(db_path=self.db_path)

    def _should_charge_enrichment(
        self,
        cleaned: CleanedLead,
        run_enrichment: bool,
        skip_junk_and_duplicates: bool,
    ) -> tuple[bool, int, str]:
        """Decide whether this row should spend enrichment credits."""
        if not run_enrichment:
            return False, COST_CLEAN_ROW, "ENRICHMENT_DISABLED"

        if skip_junk_and_duplicates:
            if cleaned.clean_status == "junk":
                return False, 0, "SKIPPED_JUNK_ROW"
            if cleaned.is_duplicate:
                return False, 0, "SKIPPED_DUPLICATE_ROW"
            if not cleaned.normalized_url or not cleaned.domain:
                return False, 0, "SKIPPED_NO_VALID_URL"
            if cleaned.is_social_or_non_business_domain:
                return False, 0, "SKIPPED_NON_BUSINESS_DOMAIN"

        cost = COST_FULL_LEAD_RUN if COST_ENRICH_LEAD > 0 else 0
        return cost > 0, cost, "LEAD_ENRICHMENT_AND_SCORING"

    def _fallback_enriched_without_fetch(self, cleaned: CleanedLead, reason_text: str) -> EnrichmentResult:
        """Build a clean EnrichmentResult when no live site fetch is performed."""
        now_ms = utc_now_iso_ms()
        return EnrichmentResult(
            lead_id=cleaned.lead_id,
            company_name=cleaned.company_name,
            contact_name=cleaned.contact_name,
            contact_email=cleaned.contact_email,
            input_website=cleaned.input_website,
            normalized_url=cleaned.normalized_url,
            final_url=cleaned.normalized_url or cleaned.input_website,
            domain=cleaned.domain,
            clean_status=cleaned.clean_status,
            clean_reasons=list(cleaned.clean_reasons),
            reachable=False,
            timed_out=False,
            http_status=None,
            redirect_count=0,
            load_time_ms=0.0,
            load_time_s=0.0,
            enrich_duration_ms=0.0,
            enrich_duration_s=0.0,
            uses_https=(cleaned.normalized_url or "").startswith("https://"),
            page_title="",
            has_title=False,
            has_meta_description=False,
            has_mobile_viewport=False,
            has_analytics_or_pixel=False,
            detected_trackers=[],
            extracted_emails=[],
            extracted_phones=[],
            technical_findings=[reason_text],
            error_detail=reason_text,
            enriched_at_utc_ms=now_ms,
        )

    def run_batch(
        self,
        user_id: str,
        raw_leads: list[dict[str, Any]],
        run_enrichment: bool = True,
        generate_hooks: bool = True,
        skip_junk_and_duplicates: bool = True,
        sort_by_score: bool = True,
        timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES_PER_URL,
        batch_limit: int | None = None,
        save_to_db: bool = True,
    ) -> BatchRunSummary:
        """Process 1 or many lead rows with strict readiness, credits, timing, and error safety."""
        clean_user = sanitize_user_id(user_id, fallback="guest_user")
        safe_timeout = clamp_timeout_seconds(timeout_s)
        safe_retries = max(0, min(3, int(max_retries)))
        started_at = utc_now_iso_ms()
        batch_id = _make_batch_id(clean_user, started_at)
        stopwatch = BatchStopwatch(batch_name=batch_id)

        readiness = verify_system_readiness(
            user_id=clean_user,
            db_path=self.db_path,
            auto_create_schema=True,
            auto_create_account=True,
            check_network=bool(run_enrichment),
        )
        balance_before = readiness.balance
        current_balance = balance_before

        if not readiness.ready:
            total_ms = stopwatch.total_elapsed_ms()
            return BatchRunSummary(
                ok=False,
                batch_id=batch_id,
                user_id=clean_user,
                db_path=self.db_path,
                total_submitted_rows=len(raw_leads or []),
                processed_rows_count=0,
                clean_rows_count=0,
                review_rows_count=0,
                junk_rows_count=0,
                duplicate_rows_count=0,
                enriched_rows_count=0,
                failed_rows_count=0,
                high_priority_count=0,
                medium_priority_count=0,
                low_priority_count=0,
                ready_to_send_count=0,
                total_credits_charged=0,
                balance_before=balance_before,
                balance_after=current_balance,
                total_duration_ms=total_ms,
                total_duration_s=ms_to_seconds(total_ms),
                avg_row_duration_ms=0.0,
                avg_row_duration_s=0.0,
                started_at_utc_ms=started_at,
                completed_at_utc_ms=utc_now_iso_ms(),
                rows=[],
                errors=list(readiness.blockers),
                message="Pre-flight check blocked this run: " + "; ".join(readiness.blockers),
            )

        max_allowed = clamp_batch_limit(batch_limit, plan_tier=readiness.plan_tier)
        submitted_rows = [r for r in (raw_leads or []) if isinstance(r, dict)][:max_allowed]

        seen_keys: dict[str, str] = {}
        processed_rows: list[ProcessedLeadRow] = []
        errors: list[str] = []

        clean_rows_count = 0
        review_rows_count = 0
        junk_rows_count = 0
        duplicate_rows_count = 0
        enriched_rows_count = 0
        failed_rows_count = 0
        total_credits_charged = 0

        for idx, raw_row in enumerate(submitted_rows, start=1):
            run_id = _make_run_id(batch_id, idx)
            row_error: str | None = None
            row_credits_charged = 0

            with StepTimer(f"row_{idx:04d}") as row_timer:
                try:
                    cleaned = clean_single_lead(raw_row, lead_index=idx, seen_keys=seen_keys)

                    if cleaned.clean_status == "clean":
                        clean_rows_count += 1
                    elif cleaned.clean_status == "needs_review":
                        review_rows_count += 1
                    else:
                        junk_rows_count += 1

                    if cleaned.is_duplicate:
                        duplicate_rows_count += 1

                    should_charge, charge_cost, charge_reason = self._should_charge_enrichment(
                        cleaned=cleaned,
                        run_enrichment=run_enrichment,
                        skip_junk_and_duplicates=skip_junk_and_duplicates,
                    )

                    if should_charge and charge_cost > 0:
                        can_pay, live_bal, pay_msg = self.ledger.can_afford(clean_user, charge_cost)
                        current_balance = live_bal
                        if not can_pay:
                            row_error = pay_msg
                            errors.append(f"Row {idx} ({cleaned.company_name}): {pay_msg}")
                            enriched = self._fallback_enriched_without_fetch(
                                cleaned=cleaned,
                                reason_text=f"Skipped enrichment: {pay_msg}",
                            )
                        else:
                            spend_res = self.ledger.spend_credits(
                                user_id=clean_user,
                                cost=charge_cost,
                                reason=charge_reason,
                                reference_id=run_id,
                                target_url=cleaned.normalized_url or cleaned.input_website,
                            )
                            row_credits_charged = charge_cost
                            total_credits_charged += charge_cost
                            current_balance = spend_res.balance_after

                            try:
                                enriched = enrich_lead(
                                    lead=cleaned,
                                    timeout_s=safe_timeout,
                                    max_retries=safe_retries,
                                )
                                enriched_rows_count += 1
                            except Exception as enrich_exc:
                                refund_res = self.ledger.refund_credits(
                                    user_id=clean_user,
                                    amount=charge_cost,
                                    reason="ENRICHMENT_ERROR_REFUND",
                                    reference_id=run_id,
                                    target_url=cleaned.normalized_url or cleaned.input_website,
                                )
                                row_credits_charged = 0
                                total_credits_charged = max(0, total_credits_charged - charge_cost)
                                current_balance = refund_res.balance_after
                                row_error = f"Enrichment error (credits refunded): {enrich_exc}"
                                errors.append(f"Row {idx} ({cleaned.company_name}): {row_error}")
                                failed_rows_count += 1
                                enriched = self._fallback_enriched_without_fetch(
                                    cleaned=cleaned,
                                    reason_text=row_error,
                                )
                    else:
                        enriched = self._fallback_enriched_without_fetch(
                            cleaned=cleaned,
                            reason_text=f"No enrichment run ({charge_reason.lower().replace('_', ' ')}).",
                        )

                    scored: ScoredLead = score_enriched_lead(enriched)

                    if generate_hooks:
                        hook_pack: PitchHookPack = generate_pitch_hook(scored)
                        pitch_subject = hook_pack.pitch_subject
                        pitch_hook = hook_pack.pitch_hook
                        pitch_email = hook_pack.pitch_email
                        follow_up_line = hook_pack.follow_up_line
                        ready_to_send = hook_pack.ready_to_send
                    else:
                        pitch_subject = ""
                        pitch_hook = ""
                        pitch_email = ""
                        follow_up_line = ""
                        ready_to_send = bool(scored.best_email and "@" in scored.best_email)

                except Exception as row_exc:
                    failed_rows_count += 1
                    row_error = f"Row processing error: {row_exc}"
                    errors.append(f"Row {idx}: {row_error}")
                    fallback_clean = clean_single_lead(raw_row, lead_index=idx, seen_keys=seen_keys)
                    fallback_enrich = self._fallback_enriched_without_fetch(fallback_clean, row_error)
                    scored = score_enriched_lead(fallback_enrich)
                    pitch_subject = ""
                    pitch_hook = ""
                    pitch_email = ""
                    follow_up_line = ""
                    ready_to_send = False

            rec = row_timer.record
            row_ms = rec.duration_ms if rec else row_timer.elapsed_ms
            row_s = rec.duration_s if rec else ms_to_seconds(row_ms)
            row_done_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()
            row_started_at = rec.started_at_utc_ms if rec else row_done_at

            stopwatch.add_item_timing(
                label=run_id,
                duration_ms=row_ms,
                started_at_utc_ms=row_started_at,
                completed_at_utc_ms=row_done_at,
                succeeded=(row_error is None),
                error_message=row_error,
            )

            row_obj = ProcessedLeadRow(
                run_id=run_id,
                batch_id=batch_id,
                lead_id=scored.lead_id,
                company_name=scored.company_name,
                contact_name=scored.contact_name,
                contact_email=scored.contact_email,
                best_email=scored.best_email,
                email_source=scored.email_source,
                extracted_emails=list(scored.extracted_emails),
                extracted_phones=list(scored.extracted_phones),
                input_website=scored.input_website,
                normalized_url=scored.normalized_url,
                final_url=scored.final_url,
                domain=scored.domain,
                clean_status=scored.clean_status,
                clean_reasons=list(scored.clean_reasons),
                is_duplicate=bool(raw_row.get("_is_duplicate", False) or any("Duplicate of earlier lead" in r for r in scored.clean_reasons)),
                duplicate_of_lead_id=next(
                    (r.split("'", 2)[1] for r in scored.clean_reasons if r.startswith("Duplicate of earlier lead '")),
                    None,
                ),
                reachable=scored.reachable,
                timed_out=scored.timed_out,
                http_status=scored.http_status,
                load_time_ms=scored.load_time_ms,
                load_time_s=scored.load_time_s,
                opportunity_score=scored.opportunity_score,
                priority_tier=scored.priority_tier,
                issues=list(scored.issues),
                issues_summary=scored.issues_summary,
                pitch_subject=pitch_subject,
                pitch_hook=pitch_hook,
                pitch_email=pitch_email,
                follow_up_line=follow_up_line,
                ready_to_send=ready_to_send,
                credits_charged=row_credits_charged,
                balance_after=current_balance,
                process_duration_ms=row_ms,
                process_duration_s=row_s,
                processed_at_utc_ms=row_done_at,
                row_error=row_error,
            )
            processed_rows.append(row_obj)

            if save_to_db:
                try:
                    self.ledger.record_lead_run(
                        user_id=clean_user,
                        run_payload={
                            "run_id": row_obj.run_id,
                            "batch_id": row_obj.batch_id,
                            "company_name": row_obj.company_name,
                            "input_website": row_obj.input_website,
                            "normalized_url": row_obj.normalized_url,
                            "final_url": row_obj.final_url,
                            "domain": row_obj.domain,
                            "contact_email": row_obj.best_email or row_obj.contact_email,
                            "clean_status": row_obj.clean_status,
                            "clean_reasons": row_obj.clean_reasons,
                            "http_status": row_obj.http_status,
                            "load_time_ms": row_obj.load_time_ms,
                            "process_duration_ms": row_obj.process_duration_ms,
                            "opportunity_score": row_obj.opportunity_score,
                            "priority_tier": row_obj.priority_tier,
                            "issues": row_obj.issues,
                            "pitch_subject": row_obj.pitch_subject,
                            "pitch_hook": row_obj.pitch_hook,
                            "pitch_email": row_obj.pitch_email,
                            "credits_charged": row_obj.credits_charged,
                            "balance_after": row_obj.balance_after,
                            "processed_at_utc_ms": row_obj.processed_at_utc_ms,
                        },
                    )
                except Exception as save_exc:
                    errors.append(f"Row {idx} DB save warning: {save_exc}")

        if sort_by_score and processed_rows:
            processed_rows = sorted(
                processed_rows,
                key=lambda r: (
                    -int(r.opportunity_score),
                    0 if r.ready_to_send else 1,
                    str(r.company_name).lower(),
                    str(r.lead_id),
                ),
            )

        high_count = sum(1 for r in processed_rows if r.priority_tier == "high")
        med_count = sum(1 for r in processed_rows if r.priority_tier == "medium")
        low_count = sum(1 for r in processed_rows if r.priority_tier == "low")
        ready_count = sum(1 for r in processed_rows if r.ready_to_send)

        sw_summary = stopwatch.summary()
        total_ms = float(sw_summary["total_elapsed_ms"])
        total_s = float(sw_summary["total_elapsed_s"])
        avg_ms = float(sw_summary["avg_item_ms"])
        avg_s = float(sw_summary["avg_item_s"])
        completed_at = str(sw_summary["ended_at_utc_ms"])

        final_snap = self.ledger.get_balance(clean_user)
        current_balance = final_snap.balance

        msg = (
            f"Processed {len(processed_rows)} of {len(submitted_rows)} rows in "
            f"{total_ms:.3f} ms ({total_s:.4f} s). "
            f"Credits used: {total_credits_charged}. Balance: {current_balance} credits."
        )

        return BatchRunSummary(
            ok=True,
            batch_id=batch_id,
            user_id=clean_user,
            db_path=self.db_path,
            total_submitted_rows=len(submitted_rows),
            processed_rows_count=len(processed_rows),
            clean_rows_count=clean_rows_count,
            review_rows_count=review_rows_count,
            junk_rows_count=junk_rows_count,
            duplicate_rows_count=duplicate_rows_count,
            enriched_rows_count=enriched_rows_count,
            failed_rows_count=failed_rows_count,
            high_priority_count=high_count,
            medium_priority_count=med_count,
            low_priority_count=low_count,
            ready_to_send_count=ready_count,
            total_credits_charged=total_credits_charged,
            balance_before=balance_before,
            balance_after=current_balance,
            total_duration_ms=total_ms,
            total_duration_s=total_s,
            avg_row_duration_ms=avg_ms,
            avg_row_duration_s=avg_s,
            started_at_utc_ms=started_at,
            completed_at_utc_ms=completed_at,
            rows=processed_rows,
            errors=errors,
            message=msg,
        )


def rows_to_json_records(rows: list[ProcessedLeadRow | dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert ProcessedLeadRow objects into plain JSON-safe dictionaries."""
    out: list[dict[str, Any]] = []
    for item in rows or []:
        if isinstance(item, ProcessedLeadRow):
            out.append(json.loads(json.dumps(item.to_dict(), default=str)))
        elif isinstance(item, dict):
            out.append(json.loads(json.dumps(item, default=str)))
    return out
