from decimal import Decimal
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Exchange ---------------------------------------------------------
    binance_api_key: str = ""
    binance_api_secret: str = ""
    # "testnet" -> https://testnet.binance.vision, "live" -> https://api.binance.com
    binance_env: str = "testnet"
    binance_recv_window: int = 5000
    # Explicit override, e.g. a regional endpoint. Empty = derive from binance_env.
    binance_base_url: str = ""
    # Binance answers HTTP 451 from restricted regions (e.g. US-hosted servers).
    # Route through an outbound proxy located where the account is allowed to trade.
    binance_proxy_url: str = ""

    # --- Persistence ------------------------------------------------------
    database_url: str = "sqlite:///./trader.db"

    # --- Risk limits (all enforced before any order is sent) --------------
    risk_per_trade_pct: Decimal = Decimal("1.0")
    max_position_notional_usdt: Decimal = Decimal("100")
    max_open_positions: int = 3
    max_daily_loss_usdt: Decimal = Decimal("50")
    max_trades_per_day: int = 20
    stop_loss_pct: Decimal = Decimal("1.5")
    take_profit_pct: Decimal = Decimal("3.0")
    quote_asset: str = "USDT"
    allowed_symbols: str = "BTCUSDT,ETHUSDT"
    max_spread_pct: Decimal = Decimal("0.15")
    max_signal_age_sec: int = 120

    # --- Security ---------------------------------------------------------
    webhook_secret: str = ""
    admin_token: str = ""
    trading_enabled: bool = False

    # --- Alerting ---------------------------------------------------------
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @field_validator("binance_env")
    @classmethod
    def _check_env(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"testnet", "live"}:
            raise ValueError("binance_env must be 'testnet' or 'live'")
        return v

    @property
    def base_url(self) -> str:
        if self.binance_base_url:
            return self.binance_base_url.rstrip("/")
        return "https://testnet.binance.vision" if self.binance_env == "testnet" else "https://api.binance.com"

    @property
    def symbol_whitelist(self) -> list[str]:
        return [s.strip().upper() for s in self.allowed_symbols.split(",") if s.strip()]

    @property
    def is_live(self) -> bool:
        return self.binance_env == "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
