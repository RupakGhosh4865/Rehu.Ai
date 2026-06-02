"""
Outbound notifications: transactional email + webhook delivery for leads,
meetings, and finished sessions.

Designed so customers can wire any CRM / Slack / inbox without us writing a
per-vendor integration. Native integrations (HubSpot, Google Calendar) live in
their own modules.
"""
import asyncio
import hmac
import hashlib
import json
import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import httpx

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)


def _notifications_file() -> Path:
    return tenants.tenant_dir(tenants.active_tenant_id()) / "notification_settings.json"

DEFAULT_SETTINGS = {
    "lead_email": "",
    "meeting_email": "",
    "from_email": "",
    "from_name": "Savant.ai",
    "webhook_url": "",
    "webhook_secret": "",
    "events": {
        "lead_captured": True,
        "meeting_requested": True,
        "session_ended": False,
    },
}


def _load() -> dict:
    path = _notifications_file()
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f) or {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update(saved)
        events = dict(DEFAULT_SETTINGS["events"])
        events.update(saved.get("events") or {})
        merged["events"] = events
        return merged
    except Exception as e:
        logger.warning("Could not load notification settings: %s", e)
        return dict(DEFAULT_SETTINGS)


def _save(data: dict) -> dict:
    path = _notifications_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = _load()
    current.update(data or {})
    if "events" in (data or {}):
        events = dict(current.get("events") or {})
        events.update(data["events"] or {})
        current["events"] = events
    with open(path, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current


def get_settings() -> dict:
    return _load()


def save_settings(payload: dict) -> dict:
    return _save(payload or {})


# ─────────────────────────── Email ───────────────────────────────────────────

def _smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_PORT)


def _send_email_sync(to: str, subject: str, body_text: str, body_html: str = "") -> bool:
    if not to or not _smtp_configured():
        if not _smtp_configured():
            logger.info("SMTP not configured; skipping email to %s", to or "<empty>")
        return False

    settings_data = _load()
    from_email = settings_data.get("from_email") or settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
    from_name = settings_data.get("from_name") or "Savant.ai"
    if not from_email:
        logger.warning("Email skipped: no from_email configured")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to
    msg.set_content(body_text or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    try:
        if settings.SMTP_USE_SSL:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=ctx, timeout=15) as smtp:
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                if settings.SMTP_USE_TLS:
                    smtp.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg)
        logger.info("Sent notification email to %s (%s)", to, subject)
        return True
    except Exception as e:
        logger.warning("Email send failed (%s): %s", subject, e)
        return False


async def send_email(to: str, subject: str, body_text: str, body_html: str = "") -> bool:
    return await asyncio.to_thread(_send_email_sync, to, subject, body_text, body_html)


# ─────────────────────────── Webhook ─────────────────────────────────────────

async def send_webhook(event: str, payload: dict, *, webhook_url: str = "", webhook_secret: str = "") -> bool:
    cfg = _load()
    url = webhook_url or cfg.get("webhook_url") or ""
    secret = webhook_secret or cfg.get("webhook_secret") or ""
    if not url:
        return False
    body = json.dumps({"event": event, "data": payload}, ensure_ascii=False, default=str)
    headers = {"Content-Type": "application/json", "X-Savant-Event": event}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["X-Savant-Signature"] = f"sha256={sig}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, content=body, headers=headers)
        ok = 200 <= r.status_code < 300
        if not ok:
            logger.warning("Webhook %s -> %s returned %s", event, url, r.status_code)
        return ok
    except Exception as e:
        logger.warning("Webhook %s -> %s failed: %s", event, url, e)
        return False


# ─────────────────────────── Event helpers ───────────────────────────────────

def _enabled(event: str) -> bool:
    cfg = _load()
    events = cfg.get("events") or {}
    return bool(events.get(event, False))


def _format_lead_email(payload: dict) -> tuple[str, str, str]:
    visitor = payload.get("visitor_name") or "Unknown visitor"
    company = payload.get("company_name") or ""
    email = payload.get("visitor_email") or "—"
    persona = payload.get("persona_name") or "Aiza"
    summary = (payload.get("lead_summary") or {})
    score = summary.get("lead_score", "")
    nba = summary.get("next_best_action", "")
    interests = ", ".join(payload.get("product_interests") or []) or "—"
    transcript_excerpt = ""
    for line in (payload.get("transcript") or [])[-6:]:
        role = "Visitor" if line.get("role") in ("user", "me") else persona
        transcript_excerpt += f"{role}: {line.get('text','')}\n"

    subject = f"[Savant] New lead: {visitor}"
    if company:
        subject += f" — {company}"

    text = (
        f"New lead captured by {persona}.\n\n"
        f"Visitor: {visitor}\n"
        f"Company: {company or '—'}\n"
        f"Email: {email}\n"
        f"Lead score: {score}\n"
        f"Next best action: {nba}\n"
        f"Interests: {interests}\n\n"
        f"Recent transcript:\n{transcript_excerpt}\n"
        f"Session ID: {payload.get('session_id','')}\n"
    )
    html = text.replace("\n", "<br>")
    return subject, text, html


def _format_meeting_email(meeting: dict) -> tuple[str, str, str]:
    visitor = meeting.get("visitor_name") or "Unknown visitor"
    email = meeting.get("visitor_email") or "—"
    company = meeting.get("company_name") or ""
    preferred = meeting.get("preferred_time") or "—"
    topic = meeting.get("topic") or "—"
    subject = f"[Savant] Meeting request from {visitor}"
    if company:
        subject += f" — {company}"
    text = (
        f"New meeting request via Aiza.\n\n"
        f"Visitor: {visitor}\n"
        f"Email: {email}\n"
        f"Company: {company or '—'}\n"
        f"Preferred time: {preferred}\n"
        f"Topic: {topic}\n"
        f"Notes: {meeting.get('notes') or '—'}\n"
        f"Meeting ID: {meeting.get('meeting_id','')}\n"
    )
    return subject, text, text.replace("\n", "<br>")


async def _push_to_hubspot_if_connected(payload: dict) -> None:
    try:
        from . import hubspot as _hubspot
        active_id = tenants.active_tenant_id()
        tenant = tenants.get_tenant(active_id)
        if not tenant:
            return
        await _hubspot.push_contact_from_lead(tenant, payload)
    except Exception:
        logger.exception("HubSpot push failed for active tenant")


async def _push_to_smartsheet_if_connected(payload: dict) -> None:
    try:
        from . import smartsheet as _smartsheet
        active_id = tenants.active_tenant_id()
        tenant = tenants.get_tenant(active_id)
        if not tenant:
            return
        await _smartsheet.push_row_from_lead(tenant, payload)
    except Exception:
        logger.exception("Smartsheet push failed for active tenant")


async def notify_lead_captured(payload: dict) -> None:
    """Fire-and-forget: send email + webhook + native CRM push when a lead is captured."""
    if not _enabled("lead_captured"):
        return
    cfg = _load()
    to = cfg.get("lead_email") or ""
    tasks = []
    if to:
        subject, text, html = _format_lead_email(payload)
        tasks.append(send_email(to, subject, text, html))
    tasks.append(send_webhook("lead_captured", payload))
    tasks.append(_push_to_hubspot_if_connected(payload))
    tasks.append(_push_to_smartsheet_if_connected(payload))
    await asyncio.gather(*tasks, return_exceptions=True)


async def notify_meeting_requested(meeting: dict) -> None:
    if not _enabled("meeting_requested"):
        return
    cfg = _load()
    to = cfg.get("meeting_email") or cfg.get("lead_email") or ""
    tasks = []
    if to:
        subject, text, html = _format_meeting_email(meeting)
        tasks.append(send_email(to, subject, text, html))
    tasks.append(send_webhook("meeting_requested", meeting))
    tasks.append(_push_to_hubspot_if_connected(meeting))
    tasks.append(_push_to_smartsheet_if_connected(meeting))
    await asyncio.gather(*tasks, return_exceptions=True)


async def notify_session_ended(record: dict) -> None:
    if not _enabled("session_ended"):
        return
    cfg = _load()
    to = cfg.get("lead_email") or ""
    tasks = []
    if to and record.get("visitor_email"):
        subject = f"[Savant] Session ended — {record.get('visitor_name') or 'visitor'}"
        text = json.dumps(record, indent=2, default=str)[:6000]
        tasks.append(send_email(to, subject, text))
    tasks.append(send_webhook("session_ended", record))
    await asyncio.gather(*tasks, return_exceptions=True)


def schedule(task_coro) -> None:
    """Schedule a notification coroutine without blocking the request handler."""
    try:
        asyncio.get_event_loop().create_task(task_coro)
    except RuntimeError:
        asyncio.run(task_coro)
