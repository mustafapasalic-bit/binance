import json
from decimal import Decimal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, settings_dep, verify_webhook
from app.config import Settings
from app.db import get_session
from app.exchange.binance_client import BinanceClient, BinanceError
from app.models import AuditLog, Signal, Trade
from app.schemas import PositionOut, SignalIn, SignalResult, StatusOut, TradeOut
from app.services import execution, pipeline, portfolio, risk
from app.services.runtime import get_client
from app.services.state import get_state, set_trading_enabled

router = APIRouter()


def optional_client(settings: Settings) -> tuple[BinanceClient | None, str | None]:
    """Client for read-only views, which stay usable while Binance is unreachable."""
    try:
        return get_client(settings), None
    except (BinanceError, RuntimeError, httpx.HTTPError) as exc:
        return None, str(exc)


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/status", response_model=StatusOut)
def status(session: Session = Depends(get_session), settings: Settings = Depends(settings_dep)) -> StatusOut:
    state = get_state(session)
    client, exchange_error = optional_client(settings)
    positions = portfolio.open_positions(session, client)
    out = StatusOut(
        environment=settings.binance_env,
        base_url=settings.base_url,
        trading_enabled=state.trading_enabled,
        kill_switch_reason=state.kill_switch_reason,
        quote_asset=settings.quote_asset,
        open_positions=len(positions),
        max_open_positions=settings.max_open_positions,
        realized_pnl_today=risk.realized_pnl_today(session),
        trades_today=risk.trades_today(session),
        max_trades_per_day=settings.max_trades_per_day,
        daily_loss_limit=settings.max_daily_loss_usdt,
        allowed_symbols=settings.symbol_whitelist,
        exchange_error=exchange_error,
    )
    if client is None:
        return out

    # Exchange-sourced figures are left empty rather than guessed when Binance is down.
    out.server_time_offset_ms = client._time_offset_ms  # noqa: SLF001 - diagnostic value
    out.unrealized_pnl = sum((p.unrealized_pnl_quote or Decimal("0") for p in positions), Decimal("0"))
    try:
        out.free_quote_balance = client.free_balance(settings.quote_asset)
        out.account_equity_quote = risk.account_equity_quote(client, settings)
    except (BinanceError, httpx.HTTPError) as exc:
        out.exchange_error = str(exc)
    return out


@router.post("/signals/webhook", response_model=SignalResult)
def signal_webhook(
    body: bytes = Depends(verify_webhook),
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> SignalResult:
    try:
        payload = SignalIn.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json())) from exc
    return pipeline.process_signal(session, settings, payload)


@router.get("/positions", response_model=list[PositionOut])
def positions(session: Session = Depends(get_session), settings: Settings = Depends(settings_dep)):
    return portfolio.open_positions(session, optional_client(settings)[0])


@router.get("/trades", response_model=list[TradeOut])
def trades(limit: int = 100, session: Session = Depends(get_session)):
    return list(session.scalars(select(Trade).order_by(Trade.id.desc()).limit(limit)).all())


@router.get("/signals")
def signals(limit: int = 100, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Signal).order_by(Signal.id.desc()).limit(limit)).all()
    return [
        {
            "id": s.id,
            "source": s.source,
            "symbol": s.symbol,
            "side": s.side,
            "confidence": str(s.confidence),
            "status": s.status,
            "reject_reason": s.reject_reason,
            "received_at": s.received_at,
        }
        for s in rows
    ]


@router.get("/audit")
def audit(limit: int = 200, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    return [
        {
            "id": a.id,
            "created_at": a.created_at,
            "event": a.event,
            "symbol": a.symbol,
            "trade_id": a.trade_id,
            "signal_id": a.signal_id,
            "decision": a.decision,
            "reason": a.reason,
            "details": json.loads(a.details or "{}"),
        }
        for a in rows
    ]


@router.post("/admin/trading", dependencies=[Depends(require_admin)])
def toggle_trading(
    enabled: bool = Body(embed=True),
    reason: str = Body(default="manual", embed=True),
    session: Session = Depends(get_session),
) -> dict:
    state = set_trading_enabled(session, enabled, reason)
    return {"trading_enabled": state.trading_enabled, "reason": state.kill_switch_reason}


@router.post("/admin/kill-switch", dependencies=[Depends(require_admin)])
def kill_switch(
    close_positions: bool = Body(default=False, embed=True),
    reason: str = Body(default="manual kill switch", embed=True),
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> dict:
    set_trading_enabled(session, False, reason)
    closed = []
    if close_positions:
        client = get_client(settings)
        for trade in risk.open_trades(session):
            execution.close_position(session, client, settings, trade, reason="kill_switch")
            closed.append(trade.id)
    return {"trading_enabled": False, "reason": reason, "closed_trades": closed}


@router.post("/admin/positions/{trade_id}/close", dependencies=[Depends(require_admin)])
def close_trade(
    trade_id: int,
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> dict:
    trade = session.get(Trade, trade_id)
    if trade is None or trade.status != "open":
        raise HTTPException(404, "open trade not found")
    execution.close_position(session, get_client(settings), settings, trade, reason="manual_close")
    return {"trade_id": trade.id, "status": trade.status, "realized_pnl": str(trade.realized_pnl_quote)}


@router.post("/admin/reconcile", dependencies=[Depends(require_admin)])
def reconcile(session: Session = Depends(get_session), settings: Settings = Depends(settings_dep)) -> dict:
    closed = portfolio.reconcile_all(session, get_client(settings), settings)
    pipeline.enforce_daily_loss_limit(session, settings)
    return {"closed": closed}
