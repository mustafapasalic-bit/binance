"""Public market snapshot for the dashboard. Read-only, keyless, never used for order decisions."""

from __future__ import annotations

from decimal import Decimal

from app.exchange.binance_client import BinanceClient
from app.schemas import MarketTickerOut


def to_ticker(row: dict) -> MarketTickerOut:
    bid, ask = Decimal(row["bidPrice"]), Decimal(row["askPrice"])
    mid = (bid + ask) / 2
    return MarketTickerOut(
        symbol=row["symbol"],
        last_price=Decimal(row["lastPrice"]),
        change_pct=Decimal(row["priceChangePercent"]),
        high=Decimal(row["highPrice"]),
        low=Decimal(row["lowPrice"]),
        quote_volume=Decimal(row["quoteVolume"]),
        bid=bid,
        ask=ask,
        spread_pct=(ask - bid) / mid * Decimal("100") if mid > 0 else None,
    )


def snapshot(client: BinanceClient, symbols: list[str]) -> list[MarketTickerOut]:
    if not symbols:
        return []
    rows = {r["symbol"]: r for r in client.ticker_24h(symbols)}
    return [to_ticker(rows[s]) for s in symbols if s in rows]
