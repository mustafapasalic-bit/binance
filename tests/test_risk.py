from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Trade
from app.services import risk


def _open_trade(session, symbol="ETHUSDT"):
    trade = Trade(symbol=symbol, side="BUY", status="open", qty=Decimal("1"), entry_price=Decimal("1"))
    session.add(trade)
    session.flush()
    return trade


def test_position_size_matches_risk_and_stop_distance(session, client, settings):
    # equity 10 000, 1% risk = 100 USDT at a 1.5% stop -> 6 666 notional, capped at 100.
    decision = risk.evaluate(session, client, settings, "BTCUSDT")
    assert decision.approved
    assert decision.quote_to_spend <= settings.max_position_notional_usdt
    assert decision.equity == Decimal("10000")
    assert decision.risk_amount == Decimal("100")
    entry = decision.entry_reference_price
    assert decision.stop_price < entry < decision.take_profit_price
    assert abs((entry - decision.stop_price) / entry * 100 - settings.stop_loss_pct) < Decimal("0.01")


def test_uncapped_size_uses_risk_formula(session, client, settings):
    settings.max_position_notional_usdt = Decimal("100000")
    decision = risk.evaluate(session, client, settings, "BTCUSDT")
    # 1% of 10 000 = 100 risked; a 1.5% stop implies a 6 666.67 position.
    assert Decimal("6600") < decision.quote_to_spend < Decimal("6700")


def test_low_confidence_shrinks_position(session, client, settings):
    settings.max_position_notional_usdt = Decimal("100000")
    full = risk.evaluate(session, client, settings, "BTCUSDT", Decimal("1"))
    half = risk.evaluate(session, client, settings, "BTCUSDT", Decimal("0.5"))
    assert half.quote_to_spend < full.quote_to_spend


def test_rejects_when_max_open_positions_reached(session, client, settings):
    _open_trade(session, "ETHUSDT")
    _open_trade(session, "BNBUSDT")
    decision = risk.evaluate(session, client, settings, "BTCUSDT")
    assert not decision.approved and "max open positions" in decision.reason


def test_rejects_duplicate_symbol_position(session, client, settings):
    _open_trade(session, "BTCUSDT")
    decision = risk.evaluate(session, client, settings, "BTCUSDT")
    assert not decision.approved and "already open" in decision.reason


def test_rejects_after_daily_loss_limit(session, client, settings):
    session.add(
        Trade(
            symbol="BTCUSDT",
            side="BUY",
            status="closed",
            realized_pnl_quote=Decimal("-60"),
            closed_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    decision = risk.evaluate(session, client, settings, "BTCUSDT")
    assert not decision.approved and "daily loss limit" in decision.reason


def test_yesterdays_loss_does_not_block_today(session, client, settings):
    session.add(
        Trade(
            symbol="BTCUSDT",
            side="BUY",
            status="closed",
            realized_pnl_quote=Decimal("-60"),
            closed_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    session.flush()
    assert risk.evaluate(session, client, settings, "BTCUSDT").approved


def test_rejects_when_balance_too_small(session, client, settings):
    client.balances["USDT"] = Decimal("3")
    decision = risk.evaluate(session, client, settings, "BTCUSDT")
    assert not decision.approved and "below exchange minimum" in decision.reason
