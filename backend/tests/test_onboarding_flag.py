"""Server-side onboarding flag (Prompt 1.2).

Onboarding completion used to live only in localStorage (per-browser, bypassable
via Google OAuth). It is now a tenant field that gates /onboarding vs /dashboard
on the server. These tests pin: fresh tenant sees onboarding; completed tenant
skips it; the flag is set server-side via /api/onboarding/complete.
"""
import uuid


def _signup(client):
    email = f"onb-{uuid.uuid4().hex[:8]}@co.com"
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "Passw0rd123", "company_name": "OnbCo"})
    assert r.status_code == 200
    return email, r.json()["token"]


def test_fresh_tenant_defaults_to_not_completed(client):
    _email, token = _signup(client)
    h = {"Authorization": f"Bearer {token}"}
    acct = client.get("/api/account", headers=h).json()
    assert acct["tenant"]["onboarding_completed"] is False


def test_fresh_tenant_dashboard_redirects_to_onboarding(client):
    _email, token = _signup(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/dashboard", headers=h, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/onboarding"


def test_fresh_tenant_can_open_onboarding(client):
    _email, token = _signup(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/onboarding", headers=h, follow_redirects=False)
    assert r.status_code == 200   # served, not redirected


def test_complete_endpoint_sets_flag(client):
    _email, token = _signup(client)
    h = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/onboarding/complete", headers=h)
    assert r.status_code == 200
    assert r.json()["onboarding_completed"] is True
    # And it persists on the account.
    acct = client.get("/api/account", headers=h).json()
    assert acct["tenant"]["onboarding_completed"] is True


def test_completed_tenant_skips_onboarding(client):
    _email, token = _signup(client)
    h = {"Authorization": f"Bearer {token}"}
    client.post("/api/onboarding/complete", headers=h)
    # /onboarding now bounces to /dashboard ...
    r1 = client.get("/onboarding", headers=h, follow_redirects=False)
    assert r1.status_code == 302 and r1.headers["location"] == "/dashboard"
    # ... and /dashboard serves directly.
    r2 = client.get("/dashboard", headers=h, follow_redirects=False)
    assert r2.status_code == 200


def test_complete_requires_auth(client):
    # No token -> cannot mark onboarding complete.
    r = client.post("/api/onboarding/complete")
    assert r.status_code == 401


def test_anonymous_pages_are_not_gated(client):
    # An anonymous visitor (no tenant) just gets the static pages, no redirect loop.
    assert client.get("/onboarding", follow_redirects=False).status_code == 200
    assert client.get("/dashboard", follow_redirects=False).status_code == 200
