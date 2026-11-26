from __future__ import annotations

from dataclasses import dataclass, asdict
import re
import time
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

from cleaner import CleanedLead, clean_single_lead
from config import (
    ASSET_FILE_EXTENSIONS,
    DEFAULT_TIMEOUT_SECONDS,
    JUNK_EMAIL_PREFIXES,
    MAX_EMAILS_PER_LEAD,
    MAX_PHONES_PER_LEAD,
    MAX_REDIRECTS,
    MAX_RESPONSE_BYTES,
    MAX_RETRIES_PER_URL,
    RETRY_BACKOFF_BASE_S,
    USER_AGENT_DEFAULT,
    clamp_timeout_seconds,
)
from timing import StepTimer, ms_to_seconds, now_perf_ns, ns_to_ms, utc_now_iso_ms


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")


@dataclass
class EnrichmentResult:
    """Website check and contact extraction result for one lead."""

    lead_id: str
    company_name: str
    contact_name: str
    contact_email: str
    input_website: str
    normalized_url: str
    final_url: str
    domain: str
    clean_status: str
    clean_reasons: list[str]
    reachable: bool
    timed_out: bool
    http_status: int | None
    redirect_count: int
    load_time_ms: float
    load_time_s: float
    enrich_duration_ms: float
    enrich_duration_s: float
    uses_https: bool
    page_title: str
    has_title: bool
    has_meta_description: bool
    has_mobile_viewport: bool
    has_analytics_or_pixel: bool
    detected_trackers: list[str]
    extracted_emails: list[str]
    extracted_phones: list[str]
    technical_findings: list[str]
    error_detail: str | None
    enriched_at_utc_ms: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extract_emails_from_html(html_text: str, soup: BeautifulSoup) -> list[str]:
    """Pull clean public emails from mailto links and page text."""
    found: set[str] = set()
    if html_text:
        for match in _EMAIL_RE.finditer(html_text):
            addr = match.group(0).strip().lower().strip(".,;:'\"()[]{}<>")
            if addr:
                found.add(addr)

    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if href.lower().startswith("mailto:"):
            raw_addr = href.split(":", 1)[1].split("?", 1)[0].strip().lower()
            if "@" in raw_addr:
                found.add(raw_addr)

    filtered: list[str] = []
    for addr in sorted(found):
        if len(addr) > 160 or "@" not in addr:
            continue
        if any(addr.startswith(prefix) for prefix in JUNK_EMAIL_PREFIXES):
            continue
        if any(addr.endswith(ext) for ext in ASSET_FILE_EXTENSIONS):
            continue
        domain_part = addr.split("@", 1)[1]
        if "." not in domain_part:
            continue
        filtered.append(addr)
        if len(filtered) >= MAX_EMAILS_PER_LEAD:
            break
    return filtered


def _extract_phones_from_html(soup: BeautifulSoup) -> list[str]:
    """Pull phone numbers from tel links and visible page text safely."""
    found: list[str] = []
    seen_digits: set[str] = set()

    def _try_add_phone(raw_val: str) -> None:
        cleaned = re.sub(r"\s+", " ", (raw_val or "").strip())
        if not cleaned or len(cleaned) > 40:
            return
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) < 7 or len(digits) > 15:
            return
        if digits in seen_digits:
            return
        seen_digits.add(digits)
        found.append(cleaned)

    for tag in soup.find_all("a", href=True):
        href = str(tag["href"]).strip()
        if href.lower().startswith("tel:"):
            tel_part = href.split(":", 1)[1].split("?", 1)[0].strip()
            _try_add_phone(tel_part)
            if len(found) >= MAX_PHONES_PER_LEAD:
                return found

    visible_text = soup.get_text(" ", strip=True)
    for match in _PHONE_RE.finditer(visible_text):
        _try_add_phone(match.group(0))
        if len(found) >= MAX_PHONES_PER_LEAD:
            break

    return found


def _detect_trackers(lower_html: str) -> list[str]:
    """Check common analytics and ad pixels in page HTML."""
    trackers: list[str] = []
    checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Google Analytics / gtag", ("googletagmanager.com", "google-analytics.com", "gtag(")),
        ("Meta Pixel", ("connect.facebook.net", "fbq(")),
        ("TikTok Pixel", ("analytics.tiktok.com", "ttq.load")),
        ("LinkedIn Insight", ("snap.licdn.com", "_linkedin_partner_id")),
        ("Hotjar", ("static.hotjar.com", "hj(")),
    )
    for label, needles in checks:
        if any(needle in lower_html for needle in needles):
            trackers.append(label)
    return trackers


def _read_bounded_html(response: requests.Response) -> str:
    """Read response body up to MAX_RESPONSE_BYTES so huge pages don't stall."""
    chunks: list[bytes] = []
    total_bytes = 0
    try:
        for chunk in response.iter_content(chunk_size=16384):
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > MAX_RESPONSE_BYTES:
                remaining = MAX_RESPONSE_BYTES - (total_bytes - len(chunk))
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                break
            chunks.append(chunk)
        raw_bytes = b"".join(chunks)
        encoding = response.encoding or response.apparent_encoding or "utf-8"
        return raw_bytes.decode(encoding, errors="replace")
    except Exception:
        return response.text or ""


def enrich_lead(
    lead: CleanedLead | dict[str, Any],
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = MAX_RETRIES_PER_URL,
) -> EnrichmentResult:
    """Enrich one cleaned lead record with live site checks and timing."""
    cleaned = lead if isinstance(lead, CleanedLead) else clean_single_lead(lead)
    safe_timeout = clamp_timeout_seconds(timeout_s)
    safe_retries = max(0, min(3, int(max_retries)))

    reachable = False
    timed_out = False
    http_status: int | None = None
    redirect_count = 0
    load_time_ms = 0.0
    final_url = cleaned.normalized_url or cleaned.input_website
    uses_https = (cleaned.normalized_url or "").startswith("https://")
    page_title = ""
    has_title = False
    has_meta_description = False
    has_mobile_viewport = False
    detected_trackers: list[str] = []
    extracted_emails: list[str] = []
    extracted_phones: list[str] = []
    technical_findings: list[str] = []
    error_detail: str | None = None

    with StepTimer("enrich_lead") as timer:
        if not cleaned.normalized_url or not cleaned.domain:
            error_detail = "No valid website URL available to visit."
            technical_findings.append("Skipped live site fetch because no valid domain was found.")
        elif cleaned.is_social_or_non_business_domain:
            error_detail = "Domain is a social or non-business platform."
            technical_findings.append("Skipped live site fetch because URL is a social/non-business domain.")
        else:
            session = requests.Session()
            session.max_redirects = MAX_REDIRECTS
            headers = {
                "User-Agent": USER_AGENT_DEFAULT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }

            try:
                attempts = safe_retries + 1
                for attempt in range(1, attempts + 1):
                    req_start_ns = now_perf_ns()
                    try:
                        resp = session.get(
                            cleaned.normalized_url,
                            headers=headers,
                            timeout=safe_timeout,
                            allow_redirects=True,
                            stream=True,
                        )
                        load_time_ms = ns_to_ms(now_perf_ns() - req_start_ns, decimals=3)
                        http_status = int(resp.status_code)
                        final_url = str(resp.url or cleaned.normalized_url)
                        redirect_count = len(resp.history or [])
                        uses_https = final_url.lower().startswith("https://")
                        reachable = True
                        timed_out = False
                        error_detail = None

                        html_text = _read_bounded_html(resp)
                        resp.close()

                        soup = BeautifulSoup(html_text, "html.parser")
                        title_el = soup.find("title")
                        if title_el:
                            title_val = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True)).strip()
                            if title_val:
                                page_title = title_val[:180]
                                has_title = True

                        meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
                        if meta_desc and str(meta_desc.get("content") or "").strip():
                            has_meta_description = True

                        viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
                        if viewport and str(viewport.get("content") or "").strip():
                            has_mobile_viewport = True

                        detected_trackers = _detect_trackers(html_text.lower())
                        extracted_emails = _extract_emails_from_html(html_text, soup)
                        extracted_phones = _extract_phones_from_html(soup)

                        if not uses_https:
                            technical_findings.append("Final URL does not use HTTPS.")
                        if http_status >= 400:
                            technical_findings.append(f"Page returned HTTP status {http_status}.")
                        if not has_title:
                            technical_findings.append("Missing page <title> tag.")
                        if not has_meta_description:
                            technical_findings.append("Missing meta description tag.")
                        if not has_mobile_viewport:
                            technical_findings.append("Missing mobile viewport tag.")
                        if not detected_trackers:
                            technical_findings.append("No analytics or ad pixel detected.")
                        break

                    except requests.Timeout as exc:
                        load_time_ms = ns_to_ms(now_perf_ns() - req_start_ns, decimals=3)
                        timed_out = True
                        reachable = False
                        error_detail = f"Timeout after {safe_timeout}s ({exc})"
                        if attempt < attempts:
                            time.sleep(RETRY_BACKOFF_BASE_S * attempt)
                        else:
                            technical_findings.append(f"Website timed out after {safe_timeout}s.")

                    except requests.RequestException as exc:
                        load_time_ms = ns_to_ms(now_perf_ns() - req_start_ns, decimals=3)
                        reachable = False
                        error_detail = f"Request failed: {exc}"
                        if attempt < attempts:
                            time.sleep(RETRY_BACKOFF_BASE_S * attempt)
                        else:
                            technical_findings.append(f"Website could not be reached ({type(exc).__name__}).")
            finally:
                try:
                    session.close()
                except Exception:
                    pass

    rec = timer.record
    enrich_ms = rec.duration_ms if rec else timer.elapsed_ms
    enrich_s = rec.duration_s if rec else ms_to_seconds(enrich_ms)
    enriched_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

    return EnrichmentResult(
        lead_id=cleaned.lead_id,
        company_name=cleaned.company_name,
        contact_name=cleaned.contact_name,
        contact_email=cleaned.contact_email,
        input_website=cleaned.input_website,
        normalized_url=cleaned.normalized_url,
        final_url=final_url,
        domain=cleaned.domain,
        clean_status=cleaned.clean_status,
        clean_reasons=list(cleaned.clean_reasons),
        reachable=reachable,
        timed_out=timed_out,
        http_status=http_status,
        redirect_count=redirect_count,
        load_time_ms=load_time_ms,
        load_time_s=ms_to_seconds(load_time_ms),
        enrich_duration_ms=enrich_ms,
        enrich_duration_s=enrich_s,
        uses_https=uses_https,
        page_title=page_title,
        has_title=has_title,
        has_meta_description=has_meta_description,
        has_mobile_viewport=has_mobile_viewport,
        has_analytics_or_pixel=len(detected_trackers) > 0,
        detected_trackers=detected_trackers,
        extracted_emails=extracted_emails,
        extracted_phones=extracted_phones,
        technical_findings=technical_findings,
        error_detail=error_detail,
        enriched_at_utc_ms=enriched_at,
    )
