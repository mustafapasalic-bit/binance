from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Trade
from app.services.stats import performance


def _closed(session, pnl: str, minutes: int) -> Trade:
    trade = Trade(
        symbol="BTCUSDT",
        side="BUY",
        status="closed",
        realized_pnl_quote=Decimal(pnl),
        closed_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes),
    )
    session.add(trade)
    session.flush()
    return trade


def test_empty_history_has_no_ratios(session):
    out = performance(session)
    assert out.closed_trades == 0
    assert out.win_rate_pct is None and out.profit_factor is None and out.expectancy is None
    assert out.max_drawdown == Decimal("0") and out.equity_curve == []


def test_performance_from_realized_pnl(session):
    for minutes, pnl in enumerate(["10", "-4", "-6", "20"]):
        _closed(session, pnl, minutes)
    session.add(Trade(symbol="BTCUSDT", side="BUY", status="open", realized_pnl_quote=Decimal("999")))
    session.flush()

    out = performance(session)
    assert (out.closed_trades, out.wins, out.losses) == (4, 2, 2)
    assert out.win_rate_pct == Decimal("50")
    assert out.total_realized_pnl == Decimal("20")
    assert out.profit_factor == Decimal("3")
    assert out.avg_win == Decimal("15") and out.avg_loss == Decimal("-5")
    assert out.expectancy == Decimal("5")
    assert (out.best_trade, out.worst_trade) == (Decimal("20"), Decimal("-6"))
    assert out.max_drawdown == Decimal("10")
    assert [p.cumulative_pnl for p in out.equity_curve] == [Decimal(v) for v in ("10", "6", "0", "20")]


def test_all_winners_have_no_profit_factor(session):
    _closed(session, "5", 0)
    assert performance(session).profit_factor is None
