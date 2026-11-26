from __future__ import annotations

from dataclasses import dataclass, asdict
import re
from typing import Any
from urllib.parse import urlparse

from config import (
    ASSET_FILE_EXTENSIONS,
    COMMON_EMAIL_TYPO_DOMAINS,
    FREE_EMAIL_DOMAINS,
    JUNK_EMAIL_PREFIXES,
    MAX_COMPANY_NAME_LEN,
    MAX_CONTACT_NAME_LEN,
    MAX_EMAIL_LEN,
    MAX_URL_LEN,
    NON_BUSINESS_DOMAINS,
)
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_WHITESPACE_RE = re.compile(r"\s+")
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass
class CleanedLead:
    """Cleaned, checked lead record ready for scoring, enrichment, and hooks."""

    lead_id: str
    company_name: str
    contact_name: str
    contact_email: str
    email_domain: str
    is_free_email: bool
    is_junk_email: bool
    email_typo_fixed: bool
    input_website: str
    normalized_url: str
    domain: str
    is_social_or_non_business_domain: bool
    clean_status: str  # "clean" | "needs_review" | "junk"
    clean_reasons: list[str]
    dedupe_key: str
    is_duplicate: bool
    duplicate_of_lead_id: str | None
    clean_duration_ms: float
    clean_duration_s: float
    cleaned_at_utc_ms: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean_whitespace(raw_text: Any, max_len: int = 200) -> str:
    """Collapse extra spaces/newlines cleanly and enforce safe length."""
    if raw_text is None:
        return ""
    text = _WHITESPACE_RE.sub(" ", str(raw_text)).strip()
    if len(text) > max_len:
        return text[:max_len].strip()
    return text


def clean_email_address(raw_email: Any) -> tuple[str, str, bool, bool, list[str]]:
    """Normalize email, fix obvious domain typos, and check free/junk flags."""
    reasons: list[str] = []
    addr = clean_whitespace(raw_email, max_len=MAX_EMAIL_LEN).lower().strip(" ,;\"'<>()[]{}")
    if not addr:
        return "", "", False, False, []

    if "@" not in addr or addr.count("@") != 1:
        return addr, "", False, True, ["Email format is invalid (missing or extra '@')."]

    local_part, domain_part = addr.split("@", 1)
    local_part = local_part.strip(".")
    domain_part = domain_part.strip(".")

    typo_fixed = False
    if domain_part in COMMON_EMAIL_TYPO_DOMAINS:
        fixed_domain = COMMON_EMAIL_TYPO_DOMAINS[domain_part]
        addr = f"{local_part}@{fixed_domain}"
        domain_part = fixed_domain
        typo_fixed = True
        reasons.append(f"Fixed common email domain typo to '{fixed_domain}'.")

    if not _EMAIL_PATTERN.match(addr):
        reasons.append("Email address did not pass standard format check.")
        return addr, domain_part, False, True, reasons

    is_junk = any(addr.startswith(prefix) for prefix in JUNK_EMAIL_PREFIXES)
    if is_junk:
        reasons.append("Email uses a generic/automated prefix (such as noreply or abuse).")

    is_free = domain_part in FREE_EMAIL_DOMAINS
    if is_free:
        reasons.append(f"Email uses a free webmail provider ({domain_part}).")

    return addr, domain_part, is_free, is_junk, reasons


def normalize_website_and_domain(raw_website: Any) -> tuple[str, str, str, bool, list[str]]:
    """Clean and normalize a website/domain string without crashing on messy input."""
    reasons: list[str] = []
    raw_clean = clean_whitespace(raw_website, max_len=MAX_URL_LEN).strip(" ,;\"'<>()[]{}")
    if not raw_clean:
        return "", "", "", False, ["No website or domain provided."]

    if any(raw_clean.lower().startswith(scheme) for scheme in ("mailto:", "tel:", "javascript:")):
        return raw_clean, "", "", False, ["Input is a mailto/tel/script link, not a website URL."]

    candidate = raw_clean
    if not candidate.lower().startswith(("http://", "https://")):
        candidate = "https://" + candidate

    try:
        parsed = urlparse(candidate)
    except Exception as exc:
        return raw_clean, "", "", False, [f"Could not parse website URL ({exc})."]

    host = (parsed.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host_no_www = host[4:]
    else:
        host_no_www = host

    if not host_no_www or "." not in host_no_www:
        return raw_clean, "", "", False, [f"Website host '{host or raw_clean}' is missing a valid domain."]

    labels = host_no_www.split(".")
    if any(not _DOMAIN_LABEL_RE.match(lbl) for lbl in labels):
        return raw_clean, "", "", False, [f"Domain '{host_no_www}' has invalid characters or formatting."]

    path_lower = (parsed.path or "").lower()
    if any(path_lower.endswith(ext) for ext in ASSET_FILE_EXTENSIONS):
        return raw_clean, "", host_no_www, False, [f"URL points to a file/asset ({path_lower}), not a web page."]

    scheme = (parsed.scheme or "https").lower()
    if scheme not in ("http", "https"):
        scheme = "https"

    clean_path = parsed.path if parsed.path else "/"
    clean_query = f"?{parsed.query}" if parsed.query else ""
    normalized_url = f"{scheme}://{host_no_www}{clean_path}{clean_query}"

    is_non_biz = host_no_www in NON_BUSINESS_DOMAINS or host in NON_BUSINESS_DOMAINS
    if is_non_biz:
        reasons.append(f"Domain '{host_no_www}' is a social/directory/non-business site rather than a company website.")

    return raw_clean, normalized_url, host_no_www, is_non_biz, reasons


def infer_company_name(raw_company: Any, domain: str, email_domain: str) -> str:
    """Use provided company name or fall back cleanly to domain name."""
    cleaned = clean_whitespace(raw_company, max_len=MAX_COMPANY_NAME_LEN)
    if cleaned:
        return cleaned

    source_domain = domain or email_domain
    if source_domain and "." in source_domain:
        root_label = source_domain.split(".")[0]
        words = [w.capitalize() for w in re.split(r"[-_]+", root_label) if w]
        if words:
            return " ".join(words)[:MAX_COMPANY_NAME_LEN]

    return "Unknown Company"


def clean_single_lead(
    raw_lead: dict[str, Any],
    lead_index: int = 1,
    seen_keys: dict[str, str] | None = None,
) -> CleanedLead:
    """Clean one lead row, check duplicates against seen_keys, and time the step."""
    with StepTimer("clean_single_lead") as timer:
        safe_idx = max(1, int(lead_index))
        lead_id = clean_whitespace(raw_lead.get("lead_id"), max_len=64) or f"lead_{safe_idx:04d}"

        contact_name = clean_whitespace(raw_lead.get("contact_name"), max_len=MAX_CONTACT_NAME_LEN)
        email_addr, email_domain, is_free_email, is_junk_email, email_reasons = clean_email_address(
            raw_lead.get("contact_email") or raw_lead.get("email")
        )
        input_website, normalized_url, domain, is_non_biz, url_reasons = normalize_website_and_domain(
            raw_lead.get("website") or raw_lead.get("url") or raw_lead.get("domain")
        )
        company_name = infer_company_name(
            raw_lead.get("company_name") or raw_lead.get("company"),
            domain=domain,
            email_domain=email_domain,
        )

        reasons: list[str] = []
        reasons.extend(url_reasons)
        reasons.extend(email_reasons)

        has_valid_url = bool(normalized_url and domain and not is_non_biz)
        has_valid_email = bool(email_addr and "@" in email_addr and not is_junk_email)

        if not has_valid_url and not has_valid_email:
            clean_status = "junk"
            reasons.append("Row has neither a valid company website nor a usable contact email.")
        elif is_junk_email and not has_valid_url:
            clean_status = "junk"
            reasons.append("Email is automated/junk and no valid company website is present.")
        elif not has_valid_url or is_non_biz or is_free_email:
            clean_status = "needs_review"
        else:
            clean_status = "clean"

        dedupe_key = f"{domain or email_domain or company_name.lower()}|{email_addr}"
        is_duplicate = False
        duplicate_of_lead_id: str | None = None

        if seen_keys is not None:
            if dedupe_key in seen_keys:
                is_duplicate = True
                duplicate_of_lead_id = seen_keys[dedupe_key]
                reasons.append(f"Duplicate of earlier lead '{duplicate_of_lead_id}'.")
                if clean_status == "clean":
                    clean_status = "needs_review"
            else:
                seen_keys[dedupe_key] = lead_id

    rec = timer.record
    dur_ms = rec.duration_ms if rec else timer.elapsed_ms
    dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
    cleaned_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

    return CleanedLead(
        lead_id=lead_id,
        company_name=company_name,
        contact_name=contact_name,
        contact_email=email_addr,
        email_domain=email_domain,
        is_free_email=is_free_email,
        is_junk_email=is_junk_email,
        email_typo_fixed=any("Fixed common email domain typo" in r for r in email_reasons),
        input_website=input_website,
        normalized_url=normalized_url,
        domain=domain,
        is_social_or_non_business_domain=is_non_biz,
        clean_status=clean_status,
        clean_reasons=reasons,
        dedupe_key=dedupe_key,
        is_duplicate=is_duplicate,
        duplicate_of_lead_id=duplicate_of_lead_id,
        clean_duration_ms=dur_ms,
        clean_duration_s=dur_s,
        cleaned_at_utc_ms=cleaned_at,
    )


def clean_lead_list(raw_leads: list[dict[str, Any]]) -> tuple[list[CleanedLead], dict[str, Any]]:
    """Clean a full list of raw lead rows and return cleaned items plus summary stats."""
    with StepTimer("clean_lead_list") as timer:
        seen_keys: dict[str, str] = {}
        cleaned_items: list[CleanedLead] = []
        clean_count = 0
        review_count = 0
        junk_count = 0
        duplicate_count = 0

        for idx, row in enumerate(raw_leads or [], start=1):
            item = clean_single_lead(row if isinstance(row, dict) else {}, lead_index=idx, seen_keys=seen_keys)
            cleaned_items.append(item)
            if item.clean_status == "clean":
                clean_count += 1
            elif item.clean_status == "needs_review":
                review_count += 1
            else:
                junk_count += 1
            if item.is_duplicate:
                duplicate_count += 1

    rec = timer.record
    total_ms = rec.duration_ms if rec else timer.elapsed_ms
    total_s = rec.duration_s if rec else ms_to_seconds(total_ms)
    summary = {
        "total_input_rows": len(cleaned_items),
        "clean_count": clean_count,
        "needs_review_count": review_count,
        "junk_count": junk_count,
        "duplicate_count": duplicate_count,
        "clean_batch_duration_ms": total_ms,
        "clean_batch_duration_s": total_s,
        "completed_at_utc_ms": rec.completed_at_utc_ms if rec else utc_now_iso_ms(),
    }
    return cleaned_items, summary

