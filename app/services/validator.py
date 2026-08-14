"""Signal validation: nothing reaches the risk engine unless it passes every check."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.exchange.binance_client import BinanceClient
from app.models import Signal
from app.schemas import SignalIn


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None
    details: dict | None = None


def check_duplicate(session: Session, signal: SignalIn) -> bool:
    if not signal.external_id:
        return False
    return session.scalar(select(Signal.id).where(Signal.external_id == signal.external_id)) is not None


def spread_pct(book: dict) -> Decimal:
    bid, ask = Decimal(book["bidPrice"]), Decimal(book["askPrice"])
    if bid <= 0 or ask <= 0:
        return Decimal("100")
    return (ask - bid) / ((ask + bid) / 2) * 100


def trend_is_up(
    client: BinanceClient, symbol: str, interval: str = "1h", period: int = 50
) -> tuple[bool, Decimal, Decimal]:
    """Higher-timeframe filter: last close vs SMA(period) on `interval` candles."""
    candles = client.klines(symbol, interval, limit=period + 1)
    closes = [Decimal(c[4]) for c in candles]
    sma = sum(closes[-period:]) / Decimal(period)
    last = closes[-1]
    return last >= sma, last, sma


def validate(
    session: Session,
    client: BinanceClient,
    settings: Settings,
    signal: SignalIn,
    *,
    require_trend: bool = True,
    min_confidence: Decimal = Decimal("0.5"),
) -> ValidationResult:
    if signal.symbol not in settings.symbol_whitelist:
        return ValidationResult(False, f"symbol {signal.symbol} not in whitelist")

    filters = client.filters_for(signal.symbol)
    if filters.status != "TRADING":
        return ValidationResult(False, f"symbol status is {filters.status}")
    if filters.quote_asset != settings.quote_asset:
        return ValidationResult(False, f"quote asset {filters.quote_asset} != {settings.quote_asset}")

    signal_time = signal.signal_time or datetime.now(timezone.utc)
    if signal_time.tzinfo is None:
        signal_time = signal_time.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - signal_time
    if age > timedelta(seconds=settings.max_signal_age_sec):
        return ValidationResult(False, f"signal too old ({int(age.total_seconds())}s)")
    if age < timedelta(seconds=-60):
        return ValidationResult(False, "signal timestamp is in the future")

    if signal.confidence < min_confidence:
        return ValidationResult(False, f"confidence {signal.confidence} below {min_confidence}")

    if check_duplicate(session, signal):
        return ValidationResult(False, f"duplicate external_id {signal.external_id}")

    book = client.book_ticker(signal.symbol)
    spread = spread_pct(book)
    if spread > settings.max_spread_pct:
        return ValidationResult(False, f"spread {spread:.4f}% above limit {settings.max_spread_pct}%")

    details = {"spread_pct": spread, "bid": book["bidPrice"], "ask": book["askPrice"]}

    if require_trend and signal.side == "BUY":
        up, last, sma = trend_is_up(client, signal.symbol)
        details |= {"last_close": last, "sma50_1h": sma}
        if not up:
            return ValidationResult(False, f"1h trend down (close {last} < SMA50 {sma})", details)

    return ValidationResult(True, None, details)
