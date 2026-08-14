from decimal import Decimal

from app.schemas import SignalIn
from app.services import pipeline
from app.services.state import get_state, set_trading_enabled


def test_signal_is_rejected_and_recorded_when_trading_disabled(session, settings, monkeypatch):
    monkeypatch.setattr(pipeline, "get_client", lambda *_: None)
    assert get_state(session).trading_enabled is False

    result = pipeline.process_signal(
        session, settings, SignalIn(source="tv", symbol="BTCUSDT", side="BUY", confidence=Decimal("1"))
    )
    assert result.status == "rejected"
    assert "trading disabled" in result.reason

    from app.models import AuditLog, Signal

    assert session.query(Signal).count() == 1
    assert {a.event for a in session.query(AuditLog).all()} == {"signal_received", "signal_rejected"}


def test_kill_switch_persists_reason(session, settings):
    set_trading_enabled(session, True, "manual start")
    assert get_state(session).trading_enabled is True
    set_trading_enabled(session, False, "daily loss limit breached")
    state = get_state(session)
    assert state.trading_enabled is False
    assert state.kill_switch_reason == "daily loss limit breached"


def test_sell_signal_without_position_is_rejected(session, settings, monkeypatch):
    monkeypatch.setattr(pipeline, "get_client", lambda *_: None)
    set_trading_enabled(session, True, "test")
    result = pipeline.process_signal(session, settings, SignalIn(source="tv", symbol="BTCUSDT", side="SELL"))
    assert result.status == "rejected" and "no open position" in result.reason
