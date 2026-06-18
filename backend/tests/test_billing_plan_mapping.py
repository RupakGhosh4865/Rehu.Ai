"""Stripe plan-mapping (Prompt 0.2).

The bug: Stripe prices were mapped only to legacy plans (pilot/professional/
business), so a successful checkout for a PUBLIC plan (growth/scale/enterprise)
did not map — apply_subscription_event fell through and could reset a paying
tenant to trial.

These tests pin the corrected behaviour:
  - every public plan price id maps to its plan
  - every legacy plan price id still maps (back-compat)
  - an UNKNOWN price id never silently downgrades — it alerts and leaves the
    tenant untouched
  - cancelled / past_due subscriptions DO drop to trial (correct entitlement)
"""
import time

import pytest

from app import billing, tenants
from app.config import settings


# ── Price-id fixtures ─────────────────────────────────────────────────────────
# Configure deterministic price ids so the mapping is testable without Stripe.
GROWTH_PRICE = "price_growth_test"
SCALE_PRICE = "price_scale_test"
ENTERPRISE_PRICE = "price_enterprise_test"
PILOT_PRICE = "price_pilot_test"
PRO_PRICE = "price_pro_test"
BUSINESS_PRICE = "price_business_test"


@pytest.fixture(autouse=True)
def _configure_prices(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_PRICE_GROWTH", GROWTH_PRICE)
    monkeypatch.setattr(settings, "STRIPE_PRICE_SCALE", SCALE_PRICE)
    monkeypatch.setattr(settings, "STRIPE_PRICE_ENTERPRISE", ENTERPRISE_PRICE)
    monkeypatch.setattr(settings, "STRIPE_PRICE_PILOT", PILOT_PRICE)
    monkeypatch.setattr(settings, "STRIPE_PRICE_PROFESSIONAL", PRO_PRICE)
    monkeypatch.setattr(settings, "STRIPE_PRICE_BUSINESS", BUSINESS_PRICE)
    yield


def _sub(price_id="", status="active", subscription_id="sub_123"):
    """Minimal Stripe subscription object."""
    items = {"data": [{"price": {"id": price_id}}]} if price_id else {"data": []}
    return {"id": subscription_id, "status": status, "items": items}


# ── reconcile_subscription_plan: the pure mapping ──────────────────────────────

@pytest.mark.parametrize("price_id,expected", [
    (GROWTH_PRICE, "growth"),
    (SCALE_PRICE, "scale"),
    (ENTERPRISE_PRICE, "enterprise"),
    (PILOT_PRICE, "pilot"),
    (PRO_PRICE, "professional"),
    (BUSINESS_PRICE, "business"),
])
def test_active_subscription_maps_each_plan(price_id, expected):
    plan, problem = billing.reconcile_subscription_plan(_sub(price_id))
    assert plan == expected
    assert problem is None


def test_unknown_price_id_returns_no_plan_and_a_problem():
    plan, problem = billing.reconcile_subscription_plan(_sub("price_does_not_exist"))
    assert plan is None
    assert problem and "no known plan" in problem


def test_missing_price_id_returns_problem():
    plan, problem = billing.reconcile_subscription_plan(_sub(""))
    assert plan is None
    assert problem


@pytest.mark.parametrize("status", ["canceled", "incomplete_expired", "unpaid", "past_due"])
def test_inactive_subscription_drops_to_trial(status):
    # Even with a valid (paid) price id, an inactive status means no entitlement.
    plan, problem = billing.reconcile_subscription_plan(_sub(SCALE_PRICE, status=status))
    assert plan == "trial"
    assert problem is None


def test_plan_for_price_id_roundtrips():
    for plan in (*billing.PUBLIC_PLANS, *billing.LEGACY_PLANS):
        pid = billing.price_id_for_plan(plan)
        assert pid, f"price id should be configured for {plan}"
        assert billing.plan_for_price_id(pid) == plan


# ── apply_subscription_event: the webhook side effect on a real tenant ─────────

def _make_tenant(plan="trial"):
    email = f"billing-{int(time.time()*1000)}-{plan}@test.example"
    t = tenants.create_tenant(email=email, password="pw-123456", company_name="BillCo", plan=plan)
    return t["tenant_id"]


def _event(tenant_id, price_id, status="active", event_type="customer.subscription.updated"):
    return {
        "type": event_type,
        "data": {"object": {
            "id": "sub_evt",
            "status": status,
            "metadata": {"tenant_id": tenant_id},
            "items": {"data": [{"price": {"id": price_id}}]} if price_id else {"data": []},
        }},
    }


def test_scale_checkout_upgrades_tenant(client):
    # The original bug: a Scale checkout left the tenant on trial. Now it upgrades.
    tid = _make_tenant("trial")
    billing.apply_subscription_event(_event(tid, SCALE_PRICE))
    assert tenants.get_tenant(tid)["plan"] == "scale"


def test_growth_checkout_upgrades_tenant(client):
    tid = _make_tenant("trial")
    billing.apply_subscription_event(_event(tid, GROWTH_PRICE))
    assert tenants.get_tenant(tid)["plan"] == "growth"


def test_unknown_price_does_not_downgrade_paying_tenant(client):
    # A tenant already on 'scale' must NOT be silently reset by an unmappable event.
    tid = _make_tenant("scale")
    result = billing.apply_subscription_event(_event(tid, "price_garbage"))
    assert result is None
    assert tenants.get_tenant(tid)["plan"] == "scale"  # unchanged


def test_cancelled_subscription_downgrades_to_trial(client):
    tid = _make_tenant("scale")
    billing.apply_subscription_event(_event(tid, SCALE_PRICE, status="canceled"))
    assert tenants.get_tenant(tid)["plan"] == "trial"


def test_event_without_tenant_is_ignored(client):
    evt = {"type": "customer.subscription.updated",
           "data": {"object": {"id": "sub_x", "status": "active",
                               "items": {"data": [{"price": {"id": SCALE_PRICE}}]}}}}
    assert billing.apply_subscription_event(evt) is None
