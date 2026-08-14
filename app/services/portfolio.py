"""Portfolio state and reconciliation against what Binance actually executed."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.exchange.binance_client import BinanceClient, BinanceError
from app.models import Trade
from app.schemas import PositionOut
from app.services import risk
from app.services.alerts import send_alert
from app.services.audit import log_event

logger = logging.getLogger(__name__)


def open_positions(session: Session, client: BinanceClient) -> list[PositionOut]:
    positions: list[PositionOut] = []
    for trade in risk.open_trades(session):
        mark = None
        unrealized = None
        unrealized_pct = None
        try:
            mark = Decimal(client.book_ticker(trade.symbol)["bidPrice"])
            unrealized = trade.qty * mark - trade.entry_quote_qty
            if trade.entry_quote_qty > 0:
                unrealized_pct = unrealized / trade.entry_quote_qty * Decimal("100")
        except BinanceError as exc:
            logger.warning("mark price unavailable for %s: %s", trade.symbol, exc)
        positions.append(
            PositionOut(
                trade_id=trade.id,
                symbol=trade.symbol,
                qty=trade.qty,
                entry_price=trade.entry_price,
                entry_quote_qty=trade.entry_quote_qty,
                stop_price=trade.stop_price,
                take_profit_price=trade.take_profit_price,
                mark_price=mark,
                unrealized_pnl_quote=unrealized,
                unrealized_pnl_pct=unrealized_pct,
                opened_at=trade.opened_at,
            )
        )
    return positions


def unrealized_pnl(session: Session, client: BinanceClient) -> Decimal:
    return sum((p.unrealized_pnl_quote or Decimal("0") for p in open_positions(session, client)), Decimal("0"))


def reconcile_trade(session: Session, client: BinanceClient, settings: Settings, trade: Trade) -> bool:
    """Detect stop-loss / take-profit fills that happened on the exchange side.

    Sell fills are read from /api/v3/myTrades, so the recorded exit price, fees and
    PnL are exactly what Binance charged.
    """
    if trade.status != "open":
        return False

    opened_ms = int(trade.opened_at.replace(tzinfo=trade.opened_at.tzinfo or timezone.utc).timestamp() * 1000)
    fills = [
        f
        for f in client.my_trades(trade.symbol, limit=200)
        if not f["isBuyer"] and int(f["time"]) >= opened_ms - 1000
    ]
    if not fills:
        return False

    filters = client.filters_for(trade.symbol)
    sold_qty = sum((Decimal(f["qty"]) for f in fills), Decimal("0"))
    quote_received = sum((Decimal(f["quoteQty"]) for f in fills), Decimal("0"))
    commission_quote = sum(
        (Decimal(f["commission"]) for f in fills if f["commissionAsset"] == filters.quote_asset), Decimal("0")
    )
    if sold_qty <= 0:
        return False

    remaining = trade.qty - sold_qty
    if remaining > filters.min_qty and remaining / trade.qty > Decimal("0.02"):
        logger.info("trade %s only partially exited (%s of %s)", trade.id, sold_qty, trade.qty)
        return False

    trade.exit_qty = sold_qty
    trade.exit_quote_qty = quote_received
    trade.exit_price = quote_received / sold_qty
    trade.exit_commission_quote = commission_quote
    trade.exit_reason = trade.exit_reason or _classify_exit(trade)
    trade.status = "closed"
    trade.closed_at = datetime.now(timezone.utc)
    trade.realized_pnl_quote = quote_received - trade.entry_quote_qty - trade.entry_commission_quote - commission_quote
    session.flush()

    log_event(
        session,
        "trade_reconciled",
        symbol=trade.symbol,
        trade_id=trade.id,
        decision="closed",
        reason=trade.exit_reason,
        details={"exit_price": trade.exit_price, "realized_pnl": trade.realized_pnl_quote},
    )
    send_alert(
        f"EXIT {trade.symbol} @ {trade.exit_price} reason={trade.exit_reason} "
        f"PnL={trade.realized_pnl_quote} {settings.quote_asset}"
    )
    return True


def _classify_exit(trade: Trade) -> str:
    if trade.exit_price is None or trade.take_profit_price is None or trade.stop_price is None:
        return "exchange_fill"
    midpoint = (trade.take_profit_price + trade.stop_price) / 2
    return "take_profit" if trade.exit_price >= midpoint else "stop_loss"


def reconcile_all(session: Session, client: BinanceClient, settings: Settings) -> int:
    closed = 0
    for trade in risk.open_trades(session):
        try:
            if reconcile_trade(session, client, settings, trade):
                closed += 1
        except BinanceError as exc:
            logger.error("reconciliation failed for trade %s: %s", trade.id, exc)
    return closed


def closed_trades(session: Session, limit: int = 100) -> list[Trade]:
    return list(
        session.scalars(
            select(Trade).where(Trade.status == "closed").order_by(Trade.closed_at.desc()).limit(limit)
        ).all()
    )
