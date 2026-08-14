import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def send_alert(text: str) -> bool:
    """Telegram alert. Never raises: alerting must not break trading or vice versa."""
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("ALERT (telegram not configured): %s", text)
        return False
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10.0,
        )
        response.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 - alerting is best effort
        logger.error("telegram alert failed: %s", exc)
        return False
