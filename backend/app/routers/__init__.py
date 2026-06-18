"""FastAPI routers (Prompt 1.4 — split out of the main.py god module).

Each module owns one tag group's routes and nothing else: route definitions +
thin glue only. Shared runtime/state/helpers live in app/core.py; domain logic
lives in the domain modules (knowledge, billing, metering, …). No router imports
main — that is what keeps the import graph acyclic.
"""
from . import (
    system, sessions, personas, knowledge, auth, billing,
    admin, leads, integrations, meetings,
)

# Order preserved for clarity; FastAPI include order doesn't affect routing.
all_routers = [
    system.router,
    sessions.router,
    personas.router,
    knowledge.router,
    auth.router,
    billing.router,
    admin.router,
    leads.router,
    integrations.router,
    meetings.router,
]
