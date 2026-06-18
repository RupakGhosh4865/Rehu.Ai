"""Admin dashboard (Prompt 1.6).

The global-admin console must be locked to the admin role: a tenant-role token
can NEVER reach an /api/admin route. It also must never expose decrypted visitor
transcripts (only metadata/usage). And it surfaces revenue (MRR by plan,
trial→paid conversion), per-tenant feature flags, and health.
"""
import base64
import uuid

import pytest

from app.config import settings

ADMIN_PW = "admin-test-password-123"
ADMIN_ROUTES_GET = ["/api/admin/tenants", "/api/admin/health"]
ADMIN_ROUTES_POST = ["/api/admin/tenant-plan", "/api/admin/tenant-flags"]


@pytest.fixture
def admin_on(monkeypatch):
    """Enable admin Basic-auth for the duration of a test (conftest disables it)."""
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "admin")
    yield


def _admin_headers():
    raw = base64.b64encode(b"admin:" + ADMIN_PW.encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def _tenant_token(client):
    email = f"adm-{uuid.uuid4().hex[:8]}@co.com"
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "Passw0rd123", "company_name": "AdmCo"})
    return email, r.json()["token"]


# ── Security headline: a tenant token is rejected from EVERY admin route ───────

def test_tenant_token_rejected_from_all_admin_routes(client, admin_on):
    _email, token = _tenant_token(client)
    th = {"Authorization": f"Bearer {token}"}     # tenant role, NOT admin
    for path in ADMIN_ROUTES_GET:
        assert client.get(path, headers=th).status_code == 401, f"GET {path} must reject tenant token"
    for path in ADMIN_ROUTES_POST:
        assert client.post(path, headers=th, json={}).status_code == 401, f"POST {path} must reject tenant token"


def test_no_credentials_rejected_from_admin_routes(client, admin_on):
    for path in ADMIN_ROUTES_GET:
        assert client.get(path).status_code == 401


# ── Admin (correct Basic creds) can read the console ──────────────────────────

def test_admin_can_list_tenants_with_revenue(client, admin_on):
    r = client.get("/api/admin/tenants", headers=_admin_headers())
    assert r.status_code == 200
    d = r.json()
    t = d["totals"]
    # Revenue section fields present.
    assert "mrr_usd" in t and "mrr_by_plan" in t and "trial_to_paid_conversion_pct" in t


def test_admin_health_reports_sessions_and_monitoring(client, admin_on):
    r = client.get("/api/admin/health", headers=_admin_headers())
    assert r.status_code == 200
    d = r.json()
    assert "active_sessions" in d and "error_monitoring" in d and "db_healthy" in d


# ── No raw visitor PII (transcripts) in admin responses ───────────────────────

def test_admin_tenants_exposes_no_transcripts(client, admin_on):
    r = client.get("/api/admin/tenants", headers=_admin_headers())
    body = r.text.lower()
    # Usage/metadata is fine; decrypted conversation transcripts must NOT appear.
    for row in r.json()["tenants"]:
        assert "transcript" not in row
    assert "transcript" not in body


# ── Feature flags per tenant (admin-only) ─────────────────────────────────────

def test_admin_can_set_feature_flags(client, admin_on):
    email, _token = _tenant_token(client)
    r = client.post("/api/admin/tenant-flags", headers=_admin_headers(),
                    json={"email": email, "flags": {"beta_ride_along": True}})
    assert r.status_code == 200
    assert r.json()["feature_flags"]["beta_ride_along"] is True


def test_tenant_cannot_set_own_feature_flags(client, admin_on):
    email, token = _tenant_token(client)
    r = client.post("/api/admin/tenant-flags", headers={"Authorization": f"Bearer {token}"},
                    json={"email": email, "flags": {"beta": True}})
    assert r.status_code == 401   # tenant role rejected
