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


def price_id_for_plan(plan: str) -> Optional[str]:
    return {
        "pilot": settings.STRIPE_PRICE_PILOT,
        "professional": settings.STRIPE_PRICE_PROFESSIONAL,
        "business": settings.STRIPE_PRICE_BUSINESS,
    }.get(plan or "")


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
    """Map a Stripe subscription event payload onto the tenant's plan."""
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

    status = obj.get("status") or ""
    items = (obj.get("items") or {}).get("data") or []
    price_id = ""
    if items:
        price_id = (items[0].get("price") or {}).get("id") or ""
    plan = "trial"
    for candidate in ("pilot", "professional", "business"):
        if price_id_for_plan(candidate) and price_id == price_id_for_plan(candidate):
            plan = candidate
            break
    if status in {"canceled", "incomplete_expired", "unpaid"}:
        plan = "trial"
    patch = {
        "stripe_subscription_id": obj.get("id") or "",
        "plan": plan,
    }
    return tenants.update_tenant(tenant_id, patch)
