from decimal import Decimal

from app.exchange.binance_client import SymbolFilters
from tests.conftest import BTC_INFO


def test_qty_rounds_down_to_step_size():
    f = SymbolFilters(BTC_INFO)
    assert f.qty_str(f.round_qty(Decimal("0.001666666"))) == "0.00166"


def test_price_rounds_to_tick():
    f = SymbolFilters(BTC_INFO)
    assert f.price_str(f.round_price(Decimal("60123.4567"))) == "60123.45"
    assert f.price_str(f.round_price(Decimal("60123.4567"), up=True)) == "60123.46"


def test_parsed_exchange_rules():
    f = SymbolFilters(BTC_INFO)
    assert (f.min_notional, f.min_qty, f.base_asset, f.quote_asset) == (
        Decimal("5"),
        Decimal("0.00001"),
        "BTC",
        "USDT",
    )
