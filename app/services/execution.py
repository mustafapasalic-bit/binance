"""Execution engine: turns an approved decision into real Binance orders.

Everything persisted here comes from Binance's own response (fills, executed
quantity, commissions) - never from an estimate.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import Settings
from app.exchange.binance_client import BinanceClient, BinanceError
from app.models import OrderRecord, Trade
from app.services.audit import log_event
from app.services.risk import RiskDecision

logger = logging.getLogger(__name__)


def client_order_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _record_order(session: Session, response: dict, trade_id: int | None = None) -> OrderRecord:
    record = OrderRecord(
        trade_id=trade_id,
        symbol=response["symbol"],
        binance_order_id=str(response.get("orderId", "")),
        client_order_id=response.get("clientOrderId"),
        order_list_id=str(response.get("orderListId")) if response.get("orderListId") not in (None, -1) else None,
        side=response.get("side", ""),
        type=response.get("type", ""),
        status=response.get("status", ""),
        price=Decimal(response.get("price", "0") or "0"),
        orig_qty=Decimal(response.get("origQty", "0") or "0"),
        executed_qty=Decimal(response.get("executedQty", "0") or "0"),
        cummulative_quote_qty=Decimal(response.get("cummulativeQuoteQty", "0") or "0"),
        raw_response=json.dumps(response),
    )
    session.add(record)
    session.flush()
    return record


def summarize_fills(
    response: dict, base_asset: str, quote_asset: str
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return (filled_qty, quote_qty, avg_price, quote_commission, base_commission).

    Commission paid in the base asset reduces the sellable quantity; commission paid
    in the quote asset reduces PnL directly. Both are returned so the caller can be exact.
    """
    filled = Decimal(response.get("executedQty", "0") or "0")
    quote = Decimal(response.get("cummulativeQuoteQty", "0") or "0")
    base_commission = Decimal("0")
    quote_commission = Decimal("0")
    for fill in response.get("fills", []):
        commission = Decimal(fill.get("commission", "0") or "0")
        asset = fill.get("commissionAsset")
        if asset == base_asset:
            base_commission += commission
        elif asset == quote_asset:
            quote_commission += commission
    avg_price = (quote / filled) if filled > 0 else Decimal("0")
    return filled, quote, avg_price, quote_commission, base_commission


def open_position(
    session: Session,
    client: BinanceClient,
    settings: Settings,
    symbol: str,
    decision: RiskDecision,
    signal_id: int | None = None,
) -> Trade:
    filters = client.filters_for(symbol)
    qty_str = filters.qty_str(decision.qty)

    response = client.market_buy(symbol, qty_str, client_order_id("entry"))
    filled, quote_spent, avg_price, quote_commission, base_commission = summarize_fills(
        response, filters.base_asset, filters.quote_asset
    )

    if filled <= 0:
        log_event(
            session,
            "entry_not_filled",
            symbol=symbol,
            signal_id=signal_id,
            decision="error",
            reason="market buy returned zero executed quantity",
            details=response,
        )
        raise BinanceError(200, None, "market buy returned zero executed quantity")

    trade = Trade(
        signal_id=signal_id,
        symbol=symbol,
        side="BUY",
        status="open",
        qty=filled - base_commission,
        entry_price=avg_price,
        entry_quote_qty=quote_spent,
        entry_order_id=str(response.get("orderId")),
        entry_commission_quote=quote_commission,
        opened_at=datetime.now(timezone.utc),
    )
    session.add(trade)
    session.flush()
    _record_order(session, response, trade.id)

    log_event(
        session,
        "entry_filled",
        symbol=symbol,
        signal_id=signal_id,
        trade_id=trade.id,
        decision="executed",
        reason=f"bought {filled} @ {avg_price}",
        details={
            "requested_qty": decision.qty,
            "filled_qty": filled,
            "quote_spent": quote_spent,
            "avg_price": avg_price,
            "base_commission": base_commission,
            "quote_commission": quote_commission,
        },
    )

    attach_protection(session, client, settings, trade, decision.stop_price, decision.take_profit_price)
    return trade


def attach_protection(
    session: Session,
    client: BinanceClient,
    settings: Settings,
    trade: Trade,
    stop_price: Decimal,
    take_profit_price: Decimal,
) -> None:
    """Place the OCO bracket. If it cannot be placed, the position is closed immediately:
    an unprotected position is never left running."""
    filters = client.filters_for(trade.symbol)
    sellable = filters.round_qty(min(trade.qty, client.free_balance(filters.base_asset)))
    stop_limit = filters.round_price(stop_price * Decimal("0.999"))

    try:
        response = client.oco_sell(
            symbol=trade.symbol,
            quantity=filters.qty_str(sellable),
            take_profit_price=filters.price_str(take_profit_price),
            stop_price=filters.price_str(stop_price),
            stop_limit_price=filters.price_str(stop_limit),
            list_client_order_id=client_order_id("prot")[:36],
        )
    except BinanceError as exc:
        log_event(
            session,
            "protection_failed",
            symbol=trade.symbol,
            trade_id=trade.id,
            decision="emergency_exit",
            reason=str(exc),
        )
        close_position(session, client, settings, trade, reason="protection_failed")
        return

    trade.stop_price = stop_price
    trade.take_profit_price = take_profit_price
    trade.protection_order_list_id = str(response.get("orderListId"))
    session.flush()
    for report in response.get("orderReports", []) or response.get("orders", []):
        _record_order(session, {**report, "symbol": trade.symbol}, trade.id)

    log_event(
        session,
        "protection_placed",
        symbol=trade.symbol,
        trade_id=trade.id,
        decision="ok",
        reason=f"SL {stop_price} / TP {take_profit_price}",
        details={"orderListId": trade.protection_order_list_id},
    )


def cancel_protection(client: BinanceClient, trade: Trade) -> None:
    if not trade.protection_order_list_id:
        return
    try:
        client.cancel_order_list(trade.symbol, trade.protection_order_list_id)
    except BinanceError as exc:
        logger.warning("could not cancel protection for trade %s: %s", trade.id, exc)


def close_position(
    session: Session,
    client: BinanceClient,
    settings: Settings,
    trade: Trade,
    reason: str,
) -> Trade:
    """Market-exit whatever is actually still held, then book the realized PnL."""
    filters = client.filters_for(trade.symbol)
    cancel_protection(client, trade)

    held = client.free_balance(filters.base_asset)
    qty = filters.round_qty(min(trade.qty, held))
    if qty <= 0 or qty < filters.min_qty:
        trade.status = "closed"
        trade.exit_reason = f"{reason}:nothing_to_sell"
        trade.closed_at = datetime.now(timezone.utc)
        session.flush()
        log_event(session, "exit_skipped", symbol=trade.symbol, trade_id=trade.id, reason="no sellable balance")
        return trade

    response = client.market_sell(trade.symbol, filters.qty_str(qty), client_order_id("exit"))
    filled, quote_received, avg_price, quote_commission, _base_commission = summarize_fills(
        response, filters.base_asset, filters.quote_asset
    )
    _record_order(session, response, trade.id)

    trade.exit_price = avg_price
    trade.exit_qty = filled
    trade.exit_quote_qty = quote_received
    trade.exit_order_id = str(response.get("orderId"))
    trade.exit_commission_quote = quote_commission
    trade.exit_reason = reason
    trade.status = "closed"
    trade.closed_at = datetime.now(timezone.utc)
    trade.realized_pnl_quote = (
        quote_received - trade.entry_quote_qty - trade.entry_commission_quote - quote_commission
    )
    session.flush()

    log_event(
        session,
        "exit_filled",
        symbol=trade.symbol,
        trade_id=trade.id,
        decision="closed",
        reason=reason,
        details={
            "exit_qty": filled,
            "exit_price": avg_price,
            "quote_received": quote_received,
            "realized_pnl": trade.realized_pnl_quote,
        },
    )
    return trade
