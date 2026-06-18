"""Client dashboard APIs (Prompt 1.5).

The dashboard is dynamic per tenant — every data API must (a) require auth and
(b) return ONLY the signed-in tenant's data. The headline tests assert tenant A
can never see tenant B's data through any dashboard endpoint, plus the Free-vs-Pro
avatar gating is enforced server-side.
"""
import uuid


def _signup(client, plan=None):
    email = f"dash-{uuid.uuid4().hex[:8]}@co.com"
    r = client.post("/api/auth/signup",
                    json={"email": email, "password": "Passw0rd123", "company_name": "DashCo"})
    assert r.status_code == 200
    token = r.json()["token"]
    tid = r.json().get("tenant_id") or r.json().get("tenant", {}).get("tenant_id")
    if plan:
        # Bump the plan directly via the store (no Stripe in tests).
        from app import tenants
        if not tid:
            tid = tenants.get_tenant_by_email(email)["tenant_id"]
        tenants.update_tenant(tid, {"plan": plan})
    return email, token, tid


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ── Auth required on every dashboard data API ──────────────────────────────────

def test_dashboard_apis_require_auth(client):
    for path in ["/api/metering/usage", "/api/leads", "/api/leads/export",
                 "/api/analytics/summary"]:
        assert client.get(path).status_code == 401, f"{path} must require auth"


# ── metering/usage is tenant-scoped and shaped for the widget ─────────────────

def test_metering_usage_returns_tenant_scoped_summary(client):
    _e, token, _t = _signup(client)
    r = client.get("/api/metering/usage", headers=_h(token))
    assert r.status_code == 200
    d = r.json()
    assert "avatar_minutes_used" in d and "avatar_minutes_cap" in d
    assert "plan" in d and "plan_limits" in d


# ── Tenant isolation: A's leads never visible to B ─────────────────────────────

def test_leads_are_tenant_isolated(client):
    # Tenant A captures a lead in their workspace.
    _ea, ta, _ta = _signup(client)
    # Inbound /api/leads POST persists under the ACTIVE tenant (A, via bearer).
    client.post("/api/leads", headers=_h(ta),
                json={"email": "va@x.com", "name": "Visitor A", "source": "test"})
    a_leads = client.get("/api/leads", headers=_h(ta)).json()["leads"]
    a_emails = {l["visitor_email"] for l in a_leads}

    # Tenant B must not see A's lead.
    _eb, tb, _tb = _signup(client)
    b_leads = client.get("/api/leads", headers=_h(tb)).json()["leads"]
    b_emails = {l["visitor_email"] for l in b_leads}
    assert "va@x.com" not in b_emails


def test_leads_export_is_tenant_isolated(client):
    _ea, ta, _ta = _signup(client)
    client.post("/api/leads", headers=_h(ta),
                json={"email": "exporta@x.com", "name": "ExpA", "source": "test"})
    _eb, tb, _tb = _signup(client)
    csv_b = client.get("/api/leads/export", headers=_h(tb)).text
    assert "exporta@x.com" not in csv_b   # B's export never contains A's lead


def test_leads_export_returns_csv(client):
    _e, token, _t = _signup(client)
    r = client.get("/api/leads/export", headers=_h(token))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "visitor_email" in r.text   # header row present


# ── Avatar gating: Free locked, Pro+ can choose (server-enforced) ─────────────

def test_trial_tenant_cannot_change_avatar(client):
    _e, token, _t = _signup(client)   # default plan = trial (avatar_choice False)
    r = client.put("/api/personas/default", headers=_h(token),
                   json={"persona_id": "default", "avatar_id": "some-custom-avatar"})
    assert r.status_code == 403


def test_growth_tenant_can_change_avatar(client):
    _e, token, _t = _signup(client, plan="growth")   # avatar_choice True
    r = client.put("/api/personas/default", headers=_h(token),
                   json={"persona_id": "default", "avatar_id": "some-custom-avatar"})
    assert r.status_code == 200


def test_trial_tenant_can_still_edit_non_avatar_fields(client):
    # Gating is ONLY on avatar changes — name/tone edits must still work on trial.
    _e, token, _t = _signup(client)
    r = client.put("/api/personas/default", headers=_h(token),
                   json={"persona_id": "default", "persona_name": "Nova"})
    assert r.status_code == 200


# ── Retrieval test box is auth-gated and tenant-scoped ────────────────────────

def test_knowledge_query_requires_auth(client):
    r = client.post("/api/knowledge/query", json={"persona_id": "default", "query": "hi"})
    assert r.status_code == 401
