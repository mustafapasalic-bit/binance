"""Signal -> validation -> risk -> execution, with an audit entry at every step."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.exchange.binance_client import BinanceError
from app.models import Signal
from app.schemas import SignalIn, SignalResult
from app.services import execution, risk, validator
from app.services.alerts import send_alert
from app.services.audit import log_event
from app.services.runtime import get_client
from app.services.state import get_state, set_trading_enabled

logger = logging.getLogger(__name__)


def store_signal(session: Session, payload: SignalIn) -> Signal:
    signal = Signal(
        external_id=payload.external_id,
        source=payload.source,
        symbol=payload.symbol,
        side=payload.side,
        confidence=payload.confidence,
        signal_time=payload.signal_time or datetime.now(timezone.utc),
        raw_payload=json.dumps(payload.model_dump(mode="json")),
    )
    session.add(signal)
    session.flush()
    return signal


def _reject(session: Session, signal: Signal, reason: str, details: dict | None = None) -> SignalResult:
    signal.status = "rejected"
    signal.reject_reason = reason[:255]
    log_event(
        session,
        "signal_rejected",
        symbol=signal.symbol,
        signal_id=signal.id,
        decision="rejected",
        reason=reason,
        details=details,
    )
    return SignalResult(signal_id=signal.id, status="rejected", reason=reason, symbol=signal.symbol)


def process_signal(session: Session, settings: Settings, payload: SignalIn) -> SignalResult:
    signal = store_signal(session, payload)
    log_event(
        session,
        "signal_received",
        symbol=signal.symbol,
        signal_id=signal.id,
        details=payload.model_dump(mode="json"),
    )

    state = get_state(session)
    if not state.trading_enabled:
        return _reject(session, signal, f"trading disabled ({state.kill_switch_reason or 'kill switch active'})")

    client = get_client(settings)

    # SELL signals only ever close an existing position - this bot is spot long-only.
    if payload.side == "SELL":
        open_trade = next((t for t in risk.open_trades(session) if t.symbol == signal.symbol), None)
        if open_trade is None:
            return _reject(session, signal, "sell signal but no open position")
        try:
            trade = execution.close_position(session, client, settings, open_trade, reason="signal_exit")
        except BinanceError as exc:
            signal.status = "failed"
            log_event(session, "exit_error", symbol=signal.symbol, signal_id=signal.id, reason=str(exc))
            send_alert(f"EXIT FAILED {signal.symbol}: {exc}")
            return SignalResult(signal_id=signal.id, status="failed", reason=str(exc), symbol=signal.symbol)
        signal.status = "executed"
        return SignalResult(
            signal_id=signal.id,
            status="executed",
            trade_id=trade.id,
            symbol=trade.symbol,
            qty=trade.exit_qty,
            entry_price=trade.exit_price,
        )

    verdict = validator.validate(session, client, settings, payload)
    if not verdict.ok:
        return _reject(session, signal, verdict.reason or "validation failed", verdict.details)

    decision = risk.evaluate(session, client, settings, signal.symbol, payload.confidence)
    log_event(
        session,
        "risk_evaluated",
        symbol=signal.symbol,
        signal_id=signal.id,
        decision="approved" if decision.approved else "rejected",
        reason=decision.reason,
        details=decision.as_dict(),
    )
    if not decision.approved:
        return _reject(session, signal, decision.reason or "risk rejected", decision.as_dict())

    signal.status = "accepted"
    try:
        trade = execution.open_position(session, client, settings, signal.symbol, decision, signal_id=signal.id)
    except BinanceError as exc:
        signal.status = "failed"
        signal.reject_reason = str(exc)[:255]
        log_event(
            session, "entry_error", symbol=signal.symbol, signal_id=signal.id, decision="error", reason=str(exc)
        )
        send_alert(f"ENTRY FAILED {signal.symbol}: {exc}")
        return SignalResult(signal_id=signal.id, status="failed", reason=str(exc), symbol=signal.symbol)

    signal.status = "executed"
    send_alert(
        f"ENTRY {trade.symbol} qty={trade.qty} @ {trade.entry_price} "
        f"SL={trade.stop_price} TP={trade.take_profit_price}"
    )
    return SignalResult(
        signal_id=signal.id,
        status="executed",
        trade_id=trade.id,
        symbol=trade.symbol,
        qty=trade.qty,
        entry_price=trade.entry_price,
    )


def enforce_daily_loss_limit(session: Session, settings: Settings) -> bool:
    """Flip the kill switch when the realized daily loss limit is breached."""
    pnl = risk.realized_pnl_today(session)
    if pnl <= -settings.max_daily_loss_usdt and get_state(session).trading_enabled:
        set_trading_enabled(session, False, f"daily loss limit breached: {pnl} {settings.quote_asset}")
        send_alert(f"KILL SWITCH: daily loss limit breached ({pnl} {settings.quote_asset})")
        return True
    return False
