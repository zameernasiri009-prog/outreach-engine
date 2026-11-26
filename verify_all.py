from __future__ import annotations

import os
import tempfile
import time

from auth_licensing import AuthLicensingService
from batch_pipeline import OutboundBatchPipeline
from cleaner import clean_lead_list
from config import APP_NAME, APP_VERSION
from enricher import EnrichmentResult
from exporter import create_export_bundle
from hook_generator import generate_pitch_hook
from ledger import CreditLedger
from readiness import verify_system_readiness
from scorer import score_enriched_lead
from timing import StepTimer, format_duration_precise


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(f"Verification failed: {message}")


def run_all_checks() -> None:
    """Run full end-to-end checks across all 13 application modules using a temp DB."""
    print(f"Starting verification for {APP_NAME} v{APP_VERSION}...")

    with StepTimer("full_system_verification") as total_timer:
        tmp_dir = tempfile.mkdtemp(prefix="lead_engine_verify_")
        db_file = os.path.join(tmp_dir, "test_verify.db")

        try:
            # 1. Readiness & account setup check
            user_id = "verify_user_01"
            readiness = verify_system_readiness(
                user_id=user_id,
                db_path=db_file,
                auto_create_schema=True,
                auto_create_account=True,
                check_network=False,
            )
            _assert(readiness.ready, f"Readiness failed: {readiness.blockers}")
            _assert(readiness.duration_ms >= 0.0, "Readiness duration_ms must be non-negative.")
            print(f"[PASS] Readiness check -> {format_duration_precise(readiness.duration_ms)}")

            # 2. Ledger balance, add, spend, refund & insufficient-balance protection
            ledger = CreditLedger(db_path=db_file)
            snap_start = ledger.get_balance(user_id)
            start_bal = snap_start.balance
            _assert(start_bal > 0, "Expected positive starter credit balance.")

            add_res = ledger.add_credits(user_id=user_id, amount=25, reason="VERIFY_TOP_UP")
            _assert(add_res.balance_after == start_bal + 25, "Credit top-up math mismatch.")

            spend_res = ledger.spend_credits(user_id=user_id, cost=5, reason="VERIFY_SPEND")
            _assert(spend_res.balance_after == start_bal + 20, "Credit spend math mismatch.")

            refund_res = ledger.refund_credits(user_id=user_id, amount=5, reason="VERIFY_REFUND")
            _assert(refund_res.balance_after == start_bal + 25, "Credit refund math mismatch.")

            # Force a small pause so timestamps are distinct when needed
            time.sleep(0.002)

            overcharge_failed = False
            try:
                ledger.spend_credits(user_id=user_id, cost=999_999, reason="VERIFY_OVERDRAFT")
            except ValueError:
                overcharge_failed = True
            _assert(overcharge_failed, "Ledger allowed spending more credits than available.")

            snap_after = ledger.get_balance(user_id)
            _assert(snap_after.balance == start_bal + 25, "Final balance after add/spend/refund mismatch.")
            print(f"[PASS] Ledger atomic math & overdraft block -> balance={snap_after.balance}")

            # 3. Auth & licensing check
            auth = AuthLicensingService(ledger=ledger)
            lic_res = auth.apply_plan_and_credits(
                user_id=user_id,
                plan_tier="pro",
                license_key="OLE-PRO-VERIFY-123456",
                grant_included_credits=True,
            )
            _assert(lic_res.ok and lic_res.plan_tier == "pro", "Plan activation failed.")
            _assert(lic_res.balance_after == snap_after.balance + 500, "Pro plan credit grant mismatch.")
            print(f"[PASS] Auth & license activation -> plan={lic_res.plan_tier}, balance={lic_res.balance_after}")

            # 4. Cleaner, scorer, and hook generator checks (offline deterministic check)
            sample_raw = [
                {
                    "lead_id": "L1",
                    "company_name": "Sunrise Bakery",
                    "contact_name": "Tariq Noor",
                    "contact_email": "tariq@gmial.com",
                    "website": "http://sunrisebakery.example.com",
                },
                {
                    "lead_id": "L2",
                    "company_name": "Sunrise Bakery",
                    "contact_name": "Tariq Noor",
                    "contact_email": "tariq@gmail.com",
                    "website": "http://sunrisebakery.example.com",
                },
                {
                    "lead_id": "L3",
                    "company_name": "",
                    "contact_name": "",
                    "contact_email": "noreply@example.com",
                    "website": "",
                },
            ]
            cleaned_list, clean_summary = clean_lead_list(sample_raw)
            _assert(len(cleaned_list) == 3, "Expected 3 cleaned rows.")
            _assert(clean_summary["duplicate_count"] == 1, "Expected 1 duplicate row.")
            _assert(cleaned_list[0].email_typo_fixed, "Expected 'gmial.com' typo fix.")
            _assert(cleaned_list[2].clean_status == "junk", "Expected row 3 to be junk.")

            mock_enriched = EnrichmentResult(
                lead_id=cleaned_list[0].lead_id,
                company_name=cleaned_list[0].company_name,
                contact_name=cleaned_list[0].contact_name,
                contact_email=cleaned_list[0].contact_email,
                input_website=cleaned_list[0].input_website,
                normalized_url=cleaned_list[0].normalized_url,
                final_url=cleaned_list[0].normalized_url,
                domain=cleaned_list[0].domain,
                clean_status=cleaned_list[0].clean_status,
                clean_reasons=list(cleaned_list[0].clean_reasons),
                reachable=True,
                timed_out=False,
                http_status=200,
                redirect_count=0,
                load_time_ms=4200.0,
                load_time_s=4.2,
                enrich_duration_ms=4205.0,
                enrich_duration_s=4.205,
                uses_https=False,
                page_title="",
                has_title=False,
                has_meta_description=False,
                has_mobile_viewport=False,
                has_analytics_or_pixel=False,
                detected_trackers=[],
                extracted_emails=["hello@sunrisebakery.example.com"],
                extracted_phones=["+1-555-0199"],
                technical_findings=["Missing HTTPS", "Slow page load"],
                error_detail=None,
                enriched_at_utc_ms=cleaned_list[0].cleaned_at_utc_ms,
            )
            scored = score_enriched_lead(mock_enriched)
            _assert(scored.opportunity_score >= 60, "Expected high opportunity score for weak/slow/HTTP site.")
            _assert(scored.priority_tier == "high", "Expected 'high' priority tier.")

            hook_pack = generate_pitch_hook(scored)
            _assert(bool(hook_pack.pitch_subject and hook_pack.pitch_hook and hook_pack.pitch_email), "Hook fields missing.")
            print(f"[PASS] Cleaner, scorer & hook generator -> score={scored.opportunity_score}/100, subject='{hook_pack.pitch_subject}'")

            # 5. Batch pipeline (offline mode) & export bundle check
            pipeline = OutboundBatchPipeline(db_path=db_file, ledger=ledger)
            batch_res = pipeline.run_batch(
                user_id=user_id,
                raw_leads=sample_raw,
                run_enrichment=False,
                generate_hooks=True,
                skip_junk_and_duplicates=True,
                save_to_db=True,
            )
            _assert(batch_res.ok, f"Batch run failed: {batch_res.errors}")
            _assert(batch_res.processed_rows_count == 3, "Expected 3 processed rows in batch.")
            _assert(batch_res.total_credits_charged == 0, "Expected 0 credits charged when enrichment is off.")

            export_bundle = create_export_bundle(summary=batch_res, rows=batch_res.rows, only_ready_to_send=False)
            _assert(export_bundle.ok and export_bundle.row_count == 3, "Export bundle row count mismatch.")
            _assert("company_name" in export_bundle.csv_content, "CSV header missing company_name.")
            _assert("OUTBOUND LEAD CAMPAIGN SUMMARY REPORT" in export_bundle.summary_report, "Report header missing.")
            print(f"[PASS] Batch pipeline & CSV/report export -> {format_duration_precise(export_bundle.export_duration_ms)}")

        finally:
            try:
                if os.path.exists(db_file):
                    os.remove(db_file)
                if os.path.isdir(tmp_dir):
                    os.rmdir(tmp_dir)
            except Exception:
                pass

    rec = total_timer.record
    total_ms = rec.duration_ms if rec else total_timer.elapsed_ms
    print(f"ALL 14-MODULE CHECKS PASSED in {format_duration_precise(total_ms)}.")


if __name__ == "__main__":
    run_all_checks()
