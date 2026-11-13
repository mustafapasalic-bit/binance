"""Performance statistics computed from realized, exchange-reconciled trades only."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Trade
from app.schemas import EquityPoint, PerformanceOut

ZERO = Decimal("0")


def performance(session: Session) -> PerformanceOut:
    trades = session.scalars(
        select(Trade).where(Trade.status == "closed", Trade.closed_at.is_not(None)).order_by(Trade.closed_at, Trade.id)
    ).all()
    pnls = [t.realized_pnl_quote for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins, ZERO)
    gross_loss = sum(losses, ZERO)
    total = sum(pnls, ZERO)

    curve: list[EquityPoint] = []
    cumulative = peak = max_drawdown = ZERO
    for trade in trades:
        cumulative += trade.realized_pnl_quote
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        curve.append(EquityPoint(trade_id=trade.id, closed_at=trade.closed_at, cumulative_pnl=cumulative))

    return PerformanceOut(
        closed_trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=Decimal(len(wins)) / Decimal(len(trades)) * 100 if trades else None,
        total_realized_pnl=total,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=gross_profit / -gross_loss if gross_loss < 0 else None,
        avg_win=gross_profit / len(wins) if wins else None,
        avg_loss=gross_loss / len(losses) if losses else None,
        expectancy=total / len(trades) if trades else None,
        best_trade=max(pnls) if pnls else None,
        worst_trade=min(pnls) if pnls else None,
        max_drawdown=max_drawdown,
        equity_curve=curve,
    )
