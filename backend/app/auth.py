"""
HTTP Basic Auth for Admin panel and mutating API routes.
When ADMIN_PASSWORD is empty, auth is skipped (local development).
"""
import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import settings

_security = HTTPBasic(auto_error=False)


def admin_auth_enabled() -> bool:
    return bool(settings.ADMIN_PASSWORD)


def verify_admin(
    credentials: Optional[HTTPBasicCredentials] = Depends(_security),
) -> Optional[str]:
    if not admin_auth_enabled():
        return None
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "Basic realm=Savant Admin"},
        )
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.ADMIN_USERNAME.encode("utf-8"),
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.ADMIN_PASSWORD.encode("utf-8"),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic realm=Savant Admin"},
        )
    return credentials.username
