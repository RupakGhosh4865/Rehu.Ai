"""Production startup hardening (Prompt 0.4).

security.check_secrets() is the single boot-time gate. In production it must
REFUSE to start with any weak/missing value; in development it logs but allows
boot. These tests pin both halves.
"""
import pytest

from app import security
from app.config import settings


# A fully-valid production config we can selectively break per test.
STRONG_JWT = "x" * 40
STRONG_SECRET = "y" * 40
STRONG_ADMIN_PW = "a-very-strong-admin-pw"
PROD_DB = "postgresql+psycopg2://u:p@db:5432/savant"


def _apply(monkeypatch, **overrides):
    base = {
        "ENVIRONMENT": "production",
        "JWT_SECRET": STRONG_JWT,
        "SECRET_KEY": STRONG_SECRET,
        "ADMIN_PASSWORD": STRONG_ADMIN_PW,
        "CORS_ORIGINS": ["https://app.savant.ai"],
        "STRIPE_WEBHOOK_SECRET": "whsec_abc123",
        "FORCE_HTTPS": True,
        "DATABASE_URL": PROD_DB,
        # Required service keys (real-looking values, not placeholders).
        "OPENAI_API_KEY": "sk-proj-realkeyvalue000",
        "LIVEAVATAR_API_KEY": "la-realkeyvalue000",
    }
    base.update(overrides)
    for k, v in base.items():
        monkeypatch.setattr(settings, k, v)


def test_valid_production_config_boots(monkeypatch):
    _apply(monkeypatch)
    assert security.check_secrets() == []   # no problems, does not raise


@pytest.mark.parametrize("override,needle", [
    ({"JWT_SECRET": "change-me-jwt-secret"}, "JWT_SECRET"),
    ({"JWT_SECRET": "short"}, "JWT_SECRET"),
    ({"SECRET_KEY": "change-me-in-production"}, "SECRET_KEY"),
    ({"ADMIN_PASSWORD": ""}, "ADMIN_PASSWORD"),
    ({"ADMIN_PASSWORD": "short"}, "ADMIN_PASSWORD"),
    ({"CORS_ORIGINS": ["*"]}, "CORS_ORIGINS"),
    ({"STRIPE_WEBHOOK_SECRET": ""}, "STRIPE_WEBHOOK_SECRET"),
    ({"FORCE_HTTPS": False}, "FORCE_HTTPS"),
    ({"DATABASE_URL": ""}, "DATABASE_URL"),
    ({"DATABASE_URL": "sqlite:///./data/savant.db"}, "DATABASE_URL"),
    # Required service keys: missing or still a placeholder default.
    ({"OPENAI_API_KEY": ""}, "OPENAI_API_KEY"),
    ({"OPENAI_API_KEY": "sk-proj-..."}, "OPENAI_API_KEY"),
    ({"LIVEAVATAR_API_KEY": ""}, "LIVEAVATAR_API_KEY"),
    ({"LIVEAVATAR_API_KEY": "..."}, "LIVEAVATAR_API_KEY"),
])
def test_production_refuses_each_bad_value(monkeypatch, override, needle):
    _apply(monkeypatch, **override)
    with pytest.raises(RuntimeError) as exc:
        security.check_secrets()
    assert needle in str(exc.value)


def test_development_allows_weak_config_without_raising(monkeypatch):
    _apply(monkeypatch, ENVIRONMENT="development",
           JWT_SECRET="change-me-jwt-secret",
           SECRET_KEY="change-me-in-production",
           ADMIN_PASSWORD="",
           CORS_ORIGINS=["*"],
           STRIPE_WEBHOOK_SECRET="",
           FORCE_HTTPS=False,
           DATABASE_URL="")
    # Dev: returns the problem list but does NOT raise.
    problems = security.check_secrets()
    assert problems   # problems are reported
    # production-only checks should NOT appear in dev
    joined = " ".join(problems)
    assert "STRIPE_WEBHOOK_SECRET" not in joined
    assert "FORCE_HTTPS" not in joined
    assert "DATABASE_URL" not in joined


def test_production_reports_all_problems_at_once(monkeypatch):
    _apply(monkeypatch, JWT_SECRET="short", STRIPE_WEBHOOK_SECRET="", FORCE_HTTPS=False)
    with pytest.raises(RuntimeError) as exc:
        security.check_secrets()
    msg = str(exc.value)
    assert "JWT_SECRET" in msg and "STRIPE_WEBHOOK_SECRET" in msg and "FORCE_HTTPS" in msg
