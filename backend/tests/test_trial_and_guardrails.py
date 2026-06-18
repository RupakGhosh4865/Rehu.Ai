"""Self-serve trial + credit-exhaustion guardrail.

Trial contract: every signup gets 20 REAL avatar minutes on their own tenant,
hard-capped. Guardrail contract: when LiveAvatar refuses sessions for credits,
the founder is alerted (throttled) and visitors get a graceful message.
"""
import asyncio
import time
import uuid


# ── Trial plan: 5 real minutes, then graceful chat fallback ──────────────────

def test_trial_plan_is_5_real_minutes():
    from app import tenants
    limits = tenants.plan_limits("trial")
    assert limits["minute_limit"] == 5
    assert limits["production_avatar"] is True   # real avatar, not sandbox


def test_exhausted_pool_falls_back_to_chat():
    from app import metering
    t = {"tenant_id": "t_x", "plan": "trial", "minutes_used": 4,
         "minutes_period_start": time.time()}
    assert metering.evaluate_avatar(t).allowed is True
    t["minutes_used"] = 5
    d = metering.evaluate_avatar(t)
    assert d.allowed is False
    assert d.fallback == "chat"


def test_signup_lands_on_trial_with_5_min_cap(client):
    email = f"trial-{uuid.uuid4().hex[:8]}@co.com"
    r = client.post("/api/auth/signup", json={"email": email, "password": "Passw0rd123",
                                              "company_name": "TrialCo"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    usage = client.get("/api/usage", headers=h).json()
    assert usage["plan"] == "trial"
    assert usage["avatar_minutes_cap"] == 5
    assert usage["avatar_minutes_used"] == 0
    assert usage["production_avatar"] is True


def test_exhausted_trial_gets_chat_session_not_an_error(client):
    """The end-to-end promise: pool used up -> session still works, as chat."""
    from app import tenants
    email = f"chat-{uuid.uuid4().hex[:8]}@co.com"
    r = client.post("/api/auth/signup", json={"email": email, "password": "Passw0rd123",
                                              "company_name": "ChatCo"})
    token = r.json()["token"]
    tid = tenants.get_tenant_by_email(email)["tenant_id"]
    tenants.add_minutes_used(tid, 5.0)   # burn the whole trial pool

    s = client.post("/api/sessions", headers={"Authorization": f"Bearer {token}"},
                    json={"persona_id": "default", "language": "en"})
    assert s.status_code == 200
    body = s.json()
    assert body["mode"] == "chat"
    assert body["cap_exhausted"] is True
    assert body["usage"]["status"] == "exhausted"
    assert body["opening_text"]            # she still greets, in text
    assert not body.get("livekit_url")     # and costs no avatar minutes

    # The chat brain answers (no OpenAI key in tests -> graceful fallback reply).
    sid = body["session_id"]
    resp = client.post(f"/api/sessions/{sid}/respond", json={"text": "What do you cost?"})
    assert resp.status_code == 200
    assert resp.json().get("reply")


# ── Credit-exhaustion alert ───────────────────────────────────────────────────

def _fire_alert(monkeypatch):
    # The alert fn + its throttle state live in core.py (extracted from main.py
    # in the router split). Target core so the monkeypatched throttle takes effect.
    from app import core, notifications
    sent = []

    async def fake_send_email(to, subject, body, html=""):
        sent.append({"to": to, "subject": subject, "body": body})
        return True
    monkeypatch.setattr(notifications, "send_email", fake_send_email)

    async def run():
        core._alert_avatar_credits_exhausted("Session start failed: Insufficient credits")
        await asyncio.sleep(0)   # let the created task run
    return core, sent, run


def test_credit_alert_emails_the_founder(monkeypatch):
    core, sent, run = _fire_alert(monkeypatch)
    monkeypatch.setattr(core, "_last_credit_alert_at", 0.0)
    asyncio.run(run())
    assert len(sent) == 1
    assert "credits exhausted" in sent[0]["subject"].lower()
    assert "Insufficient credits" in sent[0]["body"]
    from app.config import settings
    assert sent[0]["to"] == settings.ALERT_EMAIL


def test_credit_alert_is_throttled_to_one_per_hour(monkeypatch):
    core, sent, run = _fire_alert(monkeypatch)
    monkeypatch.setattr(core, "_last_credit_alert_at", 0.0)
    asyncio.run(run())
    asyncio.run(run())   # immediately again — must NOT email twice
    assert len(sent) == 1


# ── Admin overview ────────────────────────────────────────────────────────────

def test_admin_overview_includes_usage_and_activity(client):
    # Dev/test env has admin auth disabled, so the endpoint is reachable.
    r = client.get("/api/admin/tenants")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == len(data["tenants"])
    if data["tenants"]:
        row = data["tenants"][0]
        for key in ("email", "plan", "plan_label", "minutes_used", "minutes_cap",
                    "usage_status", "sessions", "last_session_at", "created_at"):
            assert key in row, f"missing {key}"


def test_session_stats_groups_by_tenant(client):
    from app import db, session_log_store, tenants
    tid = "t_stats_" + uuid.uuid4().hex[:6]
    prev = tenants.active_tenant_id()
    tenants.set_active_tenant(tid)
    try:
        session_log_store.finalize_session({
            "session_id": "s_" + uuid.uuid4().hex[:8],
            "started_at": "2026-06-11T10:00:00+00:00",
            "ended_at": "2026-06-11T10:05:00+00:00",
            "transcript": [],
        })
    finally:
        tenants.set_active_tenant(prev)
    stats = db.session_stats_by_tenant()
    assert tid in stats
    assert stats[tid]["sessions"] == 1
    assert stats[tid]["last_session_at"].startswith("2026-06-11")
