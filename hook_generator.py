from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from scorer import ScoredLead
from timing import StepTimer, ms_to_seconds, utc_now_iso_ms


@dataclass
class PitchHookPack:
    """Ready-to-use cold outreach subject, hook, email, and follow-up for one lead."""

    lead_id: str
    company_name: str
    contact_name: str
    best_email: str
    domain: str
    opportunity_score: int
    priority_tier: str
    pitch_subject: str
    pitch_hook: str
    pitch_email: str
    follow_up_line: str
    ready_to_send: bool
    hook_duration_ms: float
    hook_duration_s: float
    generated_at_utc_ms: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _greeting_name(contact_name: str) -> str:
    cleaned = (contact_name or "").strip()
    if not cleaned:
        return "there"
    first_word = cleaned.split()[0].strip(",.")
    return first_word or "there"


def _pick_primary_angle(lead: ScoredLead) -> tuple[str, str, str]:
    """Pick the strongest real finding so the opening hook feels specific and useful."""
    site_label = lead.domain or lead.company_name

    if not lead.normalized_url or not lead.domain:
        subject = f"Quick question about getting more leads for {lead.company_name}"
        hook = (
            f"I looked up {lead.company_name} and noticed you don't have a main company website "
            f"linked yet—which often makes it harder for new customers to check you out before reaching out."
        )
        angle_fix = "set up a clear landing page that turns visitors into calls or bookings"
        return subject, hook, angle_fix

    if not lead.reachable:
        if lead.timed_out:
            subject = f"Spotted a timeout on {site_label}"
            hook = (
                f"When I checked {site_label}, the page timed out after {lead.load_time_s:.2f}s "
                f"({lead.load_time_ms:.1f} ms)—if real visitors hit that, they can bounce before seeing what you offer."
            )
        else:
            subject = f"Broken link check on {site_label}"
            hook = (
                f"I tried opening {site_label} and hit a connection error—if prospects click a dead link, "
                f"they usually move on instead of trying again."
            )
        angle_fix = "get your site loading fast and reliably so visitors don't slip away"
        return subject, hook, angle_fix

    if not lead.uses_https:
        subject = f"Security warning check for {site_label}"
        hook = (
            f"I visited {site_label} and saw it isn't running on HTTPS yet, which can show a "
            f"'Not Secure' warning to people checking your site."
        )
        angle_fix = "lock in HTTPS and clean up trust signals for first-time visitors"
        return subject, hook, angle_fix

    if lead.http_status is not None and lead.http_status >= 400:
        subject = f"Page error ({lead.http_status}) on {site_label}"
        hook = (
            f"While checking {site_label}, the page returned status {lead.http_status}—catching errors "
            f"like that early saves real leads from hitting a dead end."
        )
        angle_fix = "fix the page error and keep visitors moving toward contacting you"
        return subject, hook, angle_fix

    if lead.load_time_ms >= 2000.0:
        subject = f"Page speed on {site_label} ({lead.load_time_s:.2f}s)"
        hook = (
            f"I checked {site_label} and saw it took {lead.load_time_s:.2f}s ({lead.load_time_ms:.1f} ms) "
            f"to respond—trimming slow spots usually lifts replies and inquiries."
        )
        angle_fix = "speed up the main page so visitors don't drop off waiting"
        return subject, hook, angle_fix

    if not lead.has_mobile_viewport:
        subject = f"Mobile view check for {site_label}"
        hook = (
            f"I looked at {site_label} and noticed it doesn't set a mobile viewport tag, which can "
            f"make pages look cramped or zoomed out on phones."
        )
        angle_fix = "make the mobile layout clean and easy to tap on any screen"
        return subject, hook, angle_fix

    if not lead.has_analytics_or_pixel:
        subject = f"Tracking check on {site_label}"
        hook = (
            f"I checked {site_label} and didn't see analytics or ad tracking set up on the page—"
            f"without that, it's tough to tell which traffic actually turns into paying work."
        )
        angle_fix = "set up clean tracking so you can see what brings real inquiries"
        return subject, hook, angle_fix

    if not lead.has_meta_description or not lead.has_title:
        subject = f"Search snippet idea for {site_label}"
        hook = (
            f"I checked {site_label} and spotted missing page title or meta description tags that "
            f"can make your listing look plain in search results."
        )
        angle_fix = "tighten your title and description so more people click through"
        return subject, hook, angle_fix

    subject = f"Quick idea for {lead.company_name}"
    hook = (
        f"I looked over {site_label} ({lead.load_time_s:.2f}s / {lead.load_time_ms:.1f} ms) "
        f"and put together a couple of practical ways to bring in more steady outreach replies."
    )
    angle_fix = "turn more site visitors into booked calls or direct replies"
    return subject, hook, angle_fix


def generate_pitch_hook(scored_lead: ScoredLead) -> PitchHookPack:
    """Build subject, opening hook, short outreach email, and follow-up line."""
    with StepTimer("generate_pitch_hook") as timer:
        greeting = _greeting_name(scored_lead.contact_name)
        subject, hook, angle_fix = _pick_primary_angle(scored_lead)

        pitch_email = (
            f"Hi {greeting},\n\n"
            f"{hook}\n\n"
            f"We help teams like {scored_lead.company_name} {angle_fix} without overcomplicating things. "
            f"If you're open to it, I can send over 2 quick fixes tailored to {scored_lead.domain or scored_lead.company_name}.\n\n"
            f"Worth a quick look?"
        )

        follow_up_line = (
            f"Hey {greeting}—bumping this up in case it got buried. Happy to share the short notes on "
            f"{scored_lead.domain or scored_lead.company_name} whenever timing works."
        )

        ready_to_send = bool(scored_lead.best_email and "@" in scored_lead.best_email)

    rec = timer.record
    dur_ms = rec.duration_ms if rec else timer.elapsed_ms
    dur_s = rec.duration_s if rec else ms_to_seconds(dur_ms)
    gen_at = rec.completed_at_utc_ms if rec else utc_now_iso_ms()

    return PitchHookPack(
        lead_id=scored_lead.lead_id,
        company_name=scored_lead.company_name,
        contact_name=scored_lead.contact_name,
        best_email=scored_lead.best_email,
        domain=scored_lead.domain,
        opportunity_score=scored_lead.opportunity_score,
        priority_tier=scored_lead.priority_tier,
        pitch_subject=subject,
        pitch_hook=hook,
        pitch_email=pitch_email,
        follow_up_line=follow_up_line,
        ready_to_send=ready_to_send,
        hook_duration_ms=dur_ms,
        hook_duration_s=dur_s,
        generated_at_utc_ms=gen_at,
    )
