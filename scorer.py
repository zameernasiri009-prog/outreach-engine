from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from config import (
    MAX_ISSUES_PER_LEAD,
    PRIORITY_HIGH_MIN_SCORE,
    PRIORITY_MEDIUM_MIN_SCORE,
    SCORE_WEIGHT_HTTP_ERROR,
    SCORE_WEIGHT_MISSING_META_DESC,
    SCORE_WEIGHT_MISSING_TITLE,
    SCORE_WEIGHT_MISSING_TRACKING,
    SCORE_WEIGHT_MISSING_VIEWPORT,
    SCORE_WEIGHT_NO_HTTPS,
    SCORE_WEIGHT_SLOW_LOAD_MODERATE,
    SCORE_WEIGHT_SLOW_LOAD_SEVERE,
    SCORE_WEIGHT_UNREACHABLE_OR_TIMEOUT,
    SLOW_LOAD_MODERATE_MS,
    SLOW_LOAD_SEVERE_MS,
)
from enricher import EnrichmentResult
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


@dataclass
class ScoredLead:
    """Scored lead with best contact info, plain issues, and priority rank."""

    lead_id: str
    company_name: str
    contact_name: str
    contact_email: str
    best_email: str
    email_source: str  # "input" | "extracted" | "none"
    extracted_emails: list[str]
    extracted_phones: list[str]
    input_website: str
    normalized_url: str
    final_url: str
    domain: str
    clean_status: str
    clean_reasons: list[str]
    reachable: bool
    timed_out: bool
    http_status: int | None
    load_time_ms: float
    load_time_s: float
    opportunity_score: int
    priority_tier: str  # "high" | "medium" | "low"
    issues: list[str]
    issues_summary: str
    score_duration_ms: float
    score_duration_s: float
    scored_at_utc_ms: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _select_best_email(
    input_email: str,
    extracted_emails: list[str],
) -> tuple[str, str]:
    """Choose the best usable email address for outreach."""
    clean_input = (input_email or "").strip().lower()
    if clean_input and "@" in clean_input:
        return clean_input, "input"

    for addr in extracted_emails or []:
        clean_ext = (addr or "").strip().lower()
        if clean_ext and "@" in clean_ext:
            return clean_ext, "extracted"

    return "", "none"


def _classify_priority(score: int) -> str:
    """Turn numeric opportunity score (0-100) into a simple priority label."""
    safe_score = max(0, min(100, int(score)))
    if safe_score >= PRIORITY_HIGH_MIN_SCORE:
        return "high"
    if safe_score >= PRIORITY_MEDIUM_MIN_SCORE:
        return "medium"
    return "low"


def score_enriched_lead(enriched: EnrichmentResult) -> ScoredLead:
    """Calculate opportunity score, plain-English issues, and priority tier."""
    with StepTimer("score_enriched_lead") as timer:
        score = 0
        issues: list[str] = []

        def _add_issue(points: int, plain_reason: str) -> None:
            nonlocal score
            score += int(points)
            if plain_reason and len(issues) < MAX_ISSUES_PER_LEAD:
                issues.append(plain_reason)

        best_email, email_source = _select_best_email(
            input_email=enriched.contact_email,
            extracted_emails=enriched.extracted_emails,
        )

        if not enriched.normalized_url or not enriched.domain:
            _add_issue(
                SCORE_WEIGHT_MISSING_TITLE,
                "Missing a clean company website URL to audit.",
            )
        elif enriched.clean_status == "junk":
            _add_issue(
                SCORE_WEIGHT_MISSING_TITLE,
                "Row was flagged as junk during lead cleaning.",
            )
        elif not enriched.reachable:
            if enriched.timed_out:
                _add_issue(
                    SCORE_WEIGHT_UNREACHABLE_OR_TIMEOUT,
                    f"Website timed out after {enriched.load_time_s:.2f}s ({enriched.load_time_ms:.1f} ms) — site may be down or very slow.",
                )
            else:
                _add_issue(
                    SCORE_WEIGHT_UNREACHABLE_OR_TIMEOUT,
                    "Website could not be reached — visitors hitting a dead or broken link cannot buy.",
                )
        else:
            if not enriched.uses_https:
                _add_issue(
                    SCORE_WEIGHT_NO_HTTPS,
                    "Site does not use HTTPS — visitors see a 'Not Secure' warning.",
                )

            if enriched.http_status is not None and enriched.http_status >= 400:
                _add_issue(
                    SCORE_WEIGHT_HTTP_ERROR,
                    f"Page returned HTTP status {enriched.http_status} instead of a healthy page.",
                )

            if enriched.load_time_ms >= SLOW_LOAD_SEVERE_MS:
                _add_issue(
                    SCORE_WEIGHT_SLOW_LOAD_SEVERE,
                    f"Slow page load ({enriched.load_time_s:.2f}s / {enriched.load_time_ms:.1f} ms) — slow sites lose impatient visitors.",
                )
            elif enriched.load_time_ms >= SLOW_LOAD_MODERATE_MS:
                _add_issue(
                    SCORE_WEIGHT_SLOW_LOAD_MODERATE,
                    f"Moderate page load ({enriched.load_time_s:.2f}s / {enriched.load_time_ms:.1f} ms) — speeding it up helps conversions.",
                )

            if not enriched.has_mobile_viewport:
                _add_issue(
                    SCORE_WEIGHT_MISSING_VIEWPORT,
                    "Missing mobile viewport setting — layout can look broken or zoomed on phones.",
                )

            if not enriched.has_title:
                _add_issue(
                    SCORE_WEIGHT_MISSING_TITLE,
                    "Missing a clear page title tag on the home page.",
                )

            if not enriched.has_meta_description:
                _add_issue(
                    SCORE_WEIGHT_MISSING_META_DESC,
                    "Missing meta description — search result snippets look incomplete.",
                )

            if not enriched.has_analytics_or_pixel:
                _add_issue(
                    SCORE_WEIGHT_MISSING_TRACKING,
                    "No analytics or ad pixel found — hard to see what brings in leads.",
                )

        if not best_email and not enriched.extracted_phones:
            _add_issue(
                SCORE_WEIGHT_MISSING_META_DESC,
                "No direct email or phone number found yet — needs extra contact lookup.",
            )

        final_score = max(0, min(100, score))
        priority_tier = _classify_priority(final_score)
        issues_summary = " | ".join(issues) if issues else "No major website or contact issues spotted."

    rec = timer.record
    dur_ms = rec.duration_ms if rec else timer.elapsed_ms
    dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
    scored_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

    return ScoredLead(
        lead_id=enriched.lead_id,
        company_name=enriched.company_name,
        contact_name=enriched.contact_name,
        contact_email=enriched.contact_email,
        best_email=best_email,
        email_source=email_source,
        extracted_emails=list(enriched.extracted_emails),
        extracted_phones=list(enriched.extracted_phones),
        input_website=enriched.input_website,
        normalized_url=enriched.normalized_url,
        final_url=enriched.final_url,
        domain=enriched.domain,
        clean_status=enriched.clean_status,
        clean_reasons=list(enriched.clean_reasons),
        reachable=enriched.reachable,
        timed_out=enriched.timed_out,
        http_status=enriched.http_status,
        load_time_ms=enriched.load_time_ms,
        load_time_s=enriched.load_time_s,
        opportunity_score=final_score,
        priority_tier=priority_tier,
        issues=issues,
        issues_summary=issues_summary,
        score_duration_ms=dur_ms,
        score_duration_s=dur_s,
        scored_at_utc_ms=scored_at,
    )


def rank_scored_leads(leads: list[ScoredLead]) -> list[ScoredLead]:
    """Sort scored leads from highest opportunity score to lowest."""
    return sorted(
        list(leads or []),
        key=lambda item: (-int(item.opportunity_score), str(item.company_name).lower(), str(item.lead_id)),
    )
