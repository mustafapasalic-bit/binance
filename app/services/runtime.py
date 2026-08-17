import logging

from app.config import Settings, get_settings
from app.exchange.binance_client import BinanceClient

logger = logging.getLogger(__name__)

_client: BinanceClient | None = None


def get_client(settings: Settings | None = None) -> BinanceClient:
    """Process-wide Binance client. Credentials are required: the bot never fakes data."""
    global _client
    if _client is None:
        settings = settings or get_settings()
        if not settings.binance_api_key or not settings.binance_api_secret:
            raise RuntimeError(
                "BINANCE_API_KEY / BINANCE_API_SECRET are not set. "
                "This bot talks to the real Binance REST API and refuses to run without credentials."
            )
        _client = BinanceClient(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            base_url=settings.base_url,
            recv_window=settings.binance_recv_window,
            proxy_url=settings.binance_proxy_url or None,
        )
        _client.sync_time()
        _client.load_filters(settings.symbol_whitelist)
        logger.info("Binance client ready on %s", settings.base_url)
    return _client


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None
