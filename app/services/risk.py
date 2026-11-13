"""Risk engine: decides IF and HOW BIG. Every number comes from the live account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.exchange.binance_client import BinanceClient, SymbolFilters
from app.models import Trade


def day_start_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def realized_pnl_today(session: Session) -> Decimal:
    rows = session.scalars(
        select(Trade.realized_pnl_quote).where(Trade.status == "closed", Trade.closed_at >= day_start_utc())
    ).all()
    return sum(rows, Decimal("0"))


def trades_today(session: Session) -> int:
    return int(session.scalar(select(func.count(Trade.id)).where(Trade.opened_at >= day_start_utc())) or 0)


def open_trades(session: Session) -> list[Trade]:
    return list(session.scalars(select(Trade).where(Trade.status == "open")).all())


@dataclass
class RiskDecision:
    approved: bool
    reason: str | None = None
    qty: Decimal = Decimal("0")
    quote_to_spend: Decimal = Decimal("0")
    entry_reference_price: Decimal = Decimal("0")
    stop_price: Decimal = Decimal("0")
    take_profit_price: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    risk_amount: Decimal = Decimal("0")

    def as_dict(self) -> dict:
        return {
            "qty": self.qty,
            "quote_to_spend": self.quote_to_spend,
            "entry_reference_price": self.entry_reference_price,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "equity": self.equity,
            "risk_amount": self.risk_amount,
        }


def account_equity_quote(client: BinanceClient, settings: Settings) -> Decimal:
    """Free quote balance + mark value of every balance we can price against the quote asset."""
    account = client.account()
    equity = Decimal("0")
    for bal in account["balances"]:
        total = Decimal(bal["free"]) + Decimal(bal["locked"])
        if total <= 0:
            continue
        asset = bal["asset"]
        if asset == settings.quote_asset:
            equity += total
            continue
        symbol = f"{asset}{settings.quote_asset}"
        if symbol not in settings.symbol_whitelist:
            continue
        try:
            book = client.book_ticker(symbol)
            equity += total * Decimal(book["bidPrice"])
        except Exception:  # noqa: BLE001 - unpriceable dust must not block trading
            continue
    return equity


def evaluate(
    session: Session,
    client: BinanceClient,
    settings: Settings,
    symbol: str,
    confidence: Decimal = Decimal("1"),
) -> RiskDecision:
    filters: SymbolFilters = client.filters_for(symbol)

    open_now = open_trades(session)
    if len(open_now) >= settings.max_open_positions:
        return RiskDecision(False, f"max open positions reached ({len(open_now)}/{settings.max_open_positions})")
    if any(t.symbol == symbol for t in open_now):
        return RiskDecision(False, f"position already open on {symbol}")

    pnl_today = realized_pnl_today(session)
    if pnl_today <= -settings.max_daily_loss_usdt:
        return RiskDecision(False, f"daily loss limit hit ({pnl_today} {settings.quote_asset})")

    count_today = trades_today(session)
    if count_today >= settings.max_trades_per_day:
        return RiskDecision(False, f"daily trade cap reached ({count_today}/{settings.max_trades_per_day})")

    book = client.book_ticker(symbol)
    ask = Decimal(book["askPrice"])
    if ask <= 0:
        return RiskDecision(False, "no ask price available")

    equity = account_equity_quote(client, settings)
    free_quote = client.free_balance(settings.quote_asset)

    risk_amount = equity * settings.risk_per_trade_pct / Decimal("100")
    # Position size so that hitting the stop costs exactly `risk_amount`.
    notional = risk_amount / (settings.stop_loss_pct / Decimal("100"))
    # Confidence scales the size down only; it can never exceed the configured cap.
    notional *= max(Decimal("0.25"), min(confidence, Decimal("1")))
    notional = min(notional, settings.max_position_notional_usdt, free_quote * Decimal("0.995"))

    if notional < filters.min_notional:
        return RiskDecision(
            False,
            f"position size {notional:.2f} {settings.quote_asset} below exchange minimum {filters.min_notional}",
        )

    qty = filters.round_qty(notional / ask)
    if qty <= 0 or qty < filters.min_qty:
        return RiskDecision(False, f"quantity {qty} below exchange minQty {filters.min_qty}")
    if qty * ask < filters.min_notional:
        return RiskDecision(False, f"notional {qty * ask:.2f} below exchange minimum {filters.min_notional}")
    if qty * ask > free_quote:
        return RiskDecision(False, f"insufficient {settings.quote_asset}: need {qty * ask:.2f}, have {free_quote:.2f}")

    stop = filters.round_price(ask * (Decimal("1") - settings.stop_loss_pct / Decimal("100")))
    take_profit = filters.round_price(ask * (Decimal("1") + settings.take_profit_pct / Decimal("100")), up=True)

    return RiskDecision(
        approved=True,
        qty=qty,
        quote_to_spend=qty * ask,
        entry_reference_price=ask,
        stop_price=stop,
        take_profit_price=take_profit,
        equity=equity,
        risk_amount=risk_amount,
    )
