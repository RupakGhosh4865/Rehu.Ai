"""
Stripe Billing: Checkout, Customer Portal, plan enforcement.

We hit the Stripe REST API directly via httpx so we do not pull the official
SDK into this codebase. All plan -> price_id mapping lives in settings.
"""
import logging
from typing import Optional

import httpx

from .config import settings
from . import tenants

logger = logging.getLogger(__name__)

STRIPE_BASE = "https://api.stripe.com/v1"


def _headers() -> dict:
    if not settings.STRIPE_API_KEY:
        return {}
    return {
        "Authorization": f"Bearer {settings.STRIPE_API_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


# Plans we sell self-serve today. Order matters only for display elsewhere.
PUBLIC_PLANS = ("growth", "scale", "enterprise")
# Legacy plans kept for back-compat with older Stripe products / tenant rows.
LEGACY_PLANS = ("pilot", "professional", "business")


def price_id_for_plan(plan: str) -> Optional[str]:
    """Map an internal plan id -> its configured Stripe price id (or "" if unset)."""
    return {
        # Public plans (the ones we actually sell).
        "growth": settings.STRIPE_PRICE_GROWTH,
        "scale": settings.STRIPE_PRICE_SCALE,
        "enterprise": settings.STRIPE_PRICE_ENTERPRISE,
        # Legacy aliases — preserved so existing subscriptions keep resolving.
        "pilot": settings.STRIPE_PRICE_PILOT,
        "professional": settings.STRIPE_PRICE_PROFESSIONAL,
        "business": settings.STRIPE_PRICE_BUSINESS,
    }.get(plan or "")


def plan_for_price_id(price_id: str) -> Optional[str]:
    """Reverse map a Stripe price id -> internal plan id. Public plans win over
    legacy aliases if both happen to share a price id. Returns None if unknown."""
    if not price_id:
        return None
    for plan in (*PUBLIC_PLANS, *LEGACY_PLANS):
        configured = price_id_for_plan(plan)
        if configured and configured == price_id:
            return plan
    return None


# Stripe subscription statuses that mean the customer is NOT entitled to a paid
# plan — we drop them back to trial.
_INACTIVE_STATUSES = {"canceled", "incomplete_expired", "unpaid", "past_due"}


def reconcile_subscription_plan(subscription: dict) -> tuple[Optional[str], Optional[str]]:
    """Given a Stripe subscription object, return (plan, problem).

    - plan: the internal plan id the tenant should be on, or None if it can't be
      determined (caller should NOT silently downgrade — see apply_subscription_event).
    - problem: a human-readable reason when the price id maps to no known plan, so
      the caller can alert instead of failing silently. None when all is well.
    """
    status = (subscription or {}).get("status") or ""
    if status in _INACTIVE_STATUSES:
        return "trial", None
    items = (subscription.get("items") or {}).get("data") or []
    price_id = ""
    if items:
        price_id = (items[0].get("price") or {}).get("id") or ""
    plan = plan_for_price_id(price_id)
    if plan is None:
        return None, (
            f"Stripe price id '{price_id or '(missing)'}' maps to no known plan. "
            f"Set STRIPE_PRICE_GROWTH/SCALE/ENTERPRISE to match your Stripe products."
        )
    return plan, None


def stripe_configured() -> bool:
    return bool(settings.STRIPE_API_KEY)


async def _post(path: str, data: dict) -> dict:
    if not stripe_configured():
        raise RuntimeError("Stripe is not configured")
    flat: list[tuple[str, str]] = []
    for k, v in data.items():
        if isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    for sk, sv in item.items():
                        flat.append((f"{k}[{i}][{sk}]", str(sv)))
                else:
                    flat.append((f"{k}[{i}]", str(item)))
        elif isinstance(v, dict):
            for sk, sv in v.items():
                flat.append((f"{k}[{sk}]", str(sv)))
        elif v is not None:
            flat.append((k, str(v)))
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{STRIPE_BASE}{path}", headers=_headers(), data=flat)
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {}).get("message", r.text)
        except Exception:
            err = r.text
        raise RuntimeError(f"Stripe error: {err}")
    return r.json()


async def ensure_customer(tenant: dict) -> str:
    if tenant.get("stripe_customer_id"):
        return tenant["stripe_customer_id"]
    payload = {
        "email": tenant.get("email") or "",
        "name": tenant.get("company_name") or "",
        "metadata": {
            "tenant_id": tenant.get("tenant_id") or "",
            "slug": tenant.get("slug") or "",
        },
    }
    res = await _post("/customers", payload)
    customer_id = res.get("id") or ""
    tenants.update_tenant(tenant["tenant_id"], {"stripe_customer_id": customer_id})
    return customer_id


async def create_checkout_session(tenant: dict, plan: str, return_url: str) -> dict:
    price_id = price_id_for_plan(plan)
    if not price_id:
        raise RuntimeError(f"No Stripe price configured for plan '{plan}'")
    customer_id = await ensure_customer(tenant)
    success_url = f"{return_url.rstrip('/')}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{return_url.rstrip('/')}/billing/cancel"
    payload = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {
            "tenant_id": tenant.get("tenant_id") or "",
            "plan": plan,
        },
        "allow_promotion_codes": "true",
    }
    return await _post("/checkout/sessions", payload)


async def create_portal_session(tenant: dict, return_url: str) -> dict:
    if not tenant.get("stripe_customer_id"):
        await ensure_customer(tenant)
        tenant = tenants.get_tenant(tenant["tenant_id"]) or tenant
    payload = {
        "customer": tenant["stripe_customer_id"],
        "return_url": return_url,
    }
    return await _post("/billing_portal/sessions", payload)


def apply_subscription_event(event: dict) -> Optional[dict]:
    """Map a Stripe subscription event payload onto the tenant's plan.

    Fails LOUD on an unrecognised price id: we log an error and raise an alert
    rather than silently downgrading a paying customer to trial (the original
    bug). A genuinely cancelled/unpaid subscription DOES drop to trial — that's
    the correct entitlement, handled by reconcile_subscription_plan.
    """
    obj = (event.get("data") or {}).get("object") or {}
    metadata = obj.get("metadata") or {}
    tenant_id = metadata.get("tenant_id")
    if not tenant_id:
        # Fallback to customer lookup
        customer_id = obj.get("customer")
        if not customer_id:
            return None
        for t in tenants.list_tenants():
            if t.get("stripe_customer_id") == customer_id:
                tenant_id = t["tenant_id"]
                break
    if not tenant_id:
        return None

    plan, problem = reconcile_subscription_plan(obj)
    if problem:
        # An active subscription whose price we can't map. Do NOT downgrade — that
        # would silently strip a paying customer of their plan. Alert and bail.
        logger.error("Stripe webhook: %s (tenant=%s, subscription=%s)",
                     problem, tenant_id, obj.get("id"))
        try:
            from . import audit
            audit.record("billing.unmapped_price", actor="stripe", tenant=tenant_id,
                         target=str(obj.get("id") or ""), meta={"problem": problem})
        except Exception:
            pass
        return None

    patch = {
        "stripe_subscription_id": obj.get("id") or "",
        "plan": plan,
    }
    return tenants.update_tenant(tenant_id, patch)
