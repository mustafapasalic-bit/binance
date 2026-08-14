from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import Signal
from app.schemas import SignalIn
from app.services import validator


def signal(**kwargs) -> SignalIn:
    payload = {"source": "tv", "symbol": "BTCUSDT", "side": "BUY", "confidence": Decimal("1")} | kwargs
    return SignalIn(**payload)


def test_accepts_fresh_in_trend_signal(session, client, settings):
    assert validator.validate(session, client, settings, signal()).ok


def test_rejects_symbol_outside_whitelist(session, client, settings):
    result = validator.validate(session, client, settings, signal(symbol="DOGEUSDT"))
    assert not result.ok and "whitelist" in result.reason


def test_rejects_stale_signal(session, client, settings):
    stale = signal(signal_time=datetime.now(timezone.utc) - timedelta(seconds=600))
    result = validator.validate(session, client, settings, stale)
    assert not result.ok and "too old" in result.reason


def test_rejects_wide_spread(session, client, settings):
    client.ask = "60600.00"  # 1% spread
    result = validator.validate(session, client, settings, signal())
    assert not result.ok and "spread" in result.reason


def test_rejects_duplicate_external_id(session, client, settings):
    session.add(Signal(external_id="abc", source="tv", symbol="BTCUSDT", side="BUY"))
    session.flush()
    result = validator.validate(session, client, settings, signal(external_id="abc"))
    assert not result.ok and "duplicate" in result.reason


def test_rejects_counter_trend_buy(session, client, settings):
    client.closes = [Decimal("70000")] * 50 + [Decimal("60000")]
    result = validator.validate(session, client, settings, signal())
    assert not result.ok and "trend" in result.reason


def test_rejects_low_confidence(session, client, settings):
    result = validator.validate(session, client, settings, signal(confidence=Decimal("0.1")))
    assert not result.ok and "confidence" in result.reason


def test_long_alias_is_normalized_to_buy():
    assert SignalIn(source="tv", symbol="btcusdt", side="long").side == "BUY"
    assert SignalIn(source="tv", symbol="btcusdt", side="long").symbol == "BTCUSDT"
