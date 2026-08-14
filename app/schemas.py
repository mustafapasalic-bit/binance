from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SignalIn(BaseModel):
    """Normalized signal. TradingView/Telegram/Discord payloads map onto this."""

    source: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=5, max_length=32)
    side: Literal["BUY", "SELL"]
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    external_id: str | None = Field(default=None, max_length=128)
    signal_time: datetime | None = None
    price: Decimal | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.upper().strip()
            return {"LONG": "BUY", "BUY": "BUY", "SHORT": "SELL", "SELL": "SELL", "CLOSE": "SELL"}.get(v, v)
        return v


class SignalResult(BaseModel):
    signal_id: int
    status: str
    reason: str | None = None
    trade_id: int | None = None
    symbol: str | None = None
    qty: Decimal | None = None
    entry_price: Decimal | None = None


class PositionOut(BaseModel):
    trade_id: int
    symbol: str
    qty: Decimal
    entry_price: Decimal
    entry_quote_qty: Decimal
    stop_price: Decimal | None
    take_profit_price: Decimal | None
    mark_price: Decimal | None = None
    unrealized_pnl_quote: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None
    opened_at: datetime


class TradeOut(BaseModel):
    id: int
    symbol: str
    side: str
    status: str
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal | None
    realized_pnl_quote: Decimal
    exit_reason: str | None
    opened_at: datetime
    closed_at: datetime | None


class StatusOut(BaseModel):
    environment: str
    base_url: str
    trading_enabled: bool
    kill_switch_reason: str | None
    server_time_offset_ms: int
    quote_asset: str
    free_quote_balance: Decimal
    account_equity_quote: Decimal
    open_positions: int
    max_open_positions: int
    realized_pnl_today: Decimal
    unrealized_pnl: Decimal
    trades_today: int
    max_trades_per_day: int
    daily_loss_limit: Decimal
    allowed_symbols: list[str]
