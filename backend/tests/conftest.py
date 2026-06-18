"""Test config — isolate all data into a throwaway dir so tests never touch dev data.

Env is set BEFORE any app module is imported (pytest loads conftest first), so the
settings singleton picks up the test paths.
"""
import os
import shutil
import pathlib

_TESTDATA = pathlib.Path(__file__).parent / "_testdata"
shutil.rmtree(_TESTDATA, ignore_errors=True)
_TESTDATA.mkdir(parents=True, exist_ok=True)

# Default DB URL derives from CHROMA_PERSIST_DIR.parent -> isolated sqlite + audit log + tenant dirs.
os.environ["CHROMA_PERSIST_DIR"] = str(_TESTDATA / "chromadb")
os.environ["OPENAI_API_KEY"] = ""          # never make live LLM calls in tests
# Rate limiting OFF by default in tests so heavy signup/session fixtures don't trip
# the shared limiter. The limiter has its own dedicated test (test_rate_limit.py)
# that re-enables it locally and asserts 429s fire.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["ENVIRONMENT"] = "development"
os.environ["DEBUG"] = "true"               # dev mode: tolerate empty admin password / default secrets
# Tests must not depend on a developer's local backend/.env. Pin auth/secret
# config so the suite is reproducible regardless of what .env happens to contain.
os.environ["ADMIN_PASSWORD"] = ""          # admin auth disabled in tests (matches assertions)
os.environ["JWT_SECRET"] = "test-jwt-secret-not-used-in-prod-000000"
os.environ["SECRET_KEY"] = "test-secret-key-not-used-in-prod-000000"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app
    with TestClient(app) as c:   # context manager triggers lifespan (init_db, migrate)
        yield c


@pytest.fixture(autouse=True)
def _clean_cookies(client):
    """Each test starts unauthenticated; tests that need auth pass explicit Bearer
    headers. (TestClient otherwise persists cookies across tests.)"""
    client.cookies.clear()
    yield
    client.cookies.clear()
