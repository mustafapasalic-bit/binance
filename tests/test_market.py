from decimal import Decimal

from app.services import market

ROW = {
    "symbol": "BTCUSDT",
    "lastPrice": "60000.00",
    "priceChangePercent": "-2.500",
    "highPrice": "62000.00",
    "lowPrice": "59000.00",
    "quoteVolume": "1000000.00",
    "bidPrice": "59999.00",
    "askPrice": "60001.00",
}


class TickerClient:
    def ticker_24h(self, symbols):
        return [ROW]


def test_snapshot_keeps_whitelist_order_and_skips_unknown_symbols():
    out = market.snapshot(TickerClient(), ["ETHUSDT", "BTCUSDT"])
    assert [t.symbol for t in out] == ["BTCUSDT"]
    ticker = out[0]
    assert ticker.change_pct == Decimal("-2.5")
    assert ticker.spread_pct == Decimal("2") / Decimal("60000") * 100


def test_snapshot_without_symbols_makes_no_request():
    assert market.snapshot(None, []) == []
