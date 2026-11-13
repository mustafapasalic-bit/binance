import hashlib
import hmac

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings


def settings_dep() -> Settings:
    return get_settings()


def require_admin(
    x_admin_token: str = Header(default=""),
    settings: Settings = Depends(settings_dep),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ADMIN_TOKEN is not configured")
    if not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin token")


async def verify_webhook(
    request: Request,
    x_signature: str = Header(default=""),
    x_webhook_secret: str = Header(default=""),
    settings: Settings = Depends(settings_dep),
) -> bytes:
    """Accept either a shared secret header or an HMAC-SHA256 signature of the raw body."""
    if not settings.webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "WEBHOOK_SECRET is not configured")
    body = await request.body()
    if x_signature:
        expected = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(x_signature.lower(), expected):
            return body
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    if x_webhook_secret and hmac.compare_digest(x_webhook_secret, settings.webhook_secret):
        return body
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid webhook credentials")
