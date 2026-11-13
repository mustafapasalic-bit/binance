from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.exchange.binance_client import SymbolFilters

BTC_INFO = {
    "symbol": "BTCUSDT",
    "baseAsset": "BTC",
    "quoteAsset": "USDT",
    "status": "TRADING",
    "filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00001000", "minQty": "0.00001000", "maxQty": "9000.00000000"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
        {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
    ],
}


class FakeClient:
    """Stands in for BinanceClient in unit tests: no network, deterministic numbers."""

    def __init__(self, bid="60000.00", ask="60006.00", balances=None, closes=None):
        self.bid, self.ask = bid, ask
        self.balances = balances or {"USDT": Decimal("10000"), "BTC": Decimal("0")}
        self.closes = closes or [Decimal("59000")] * 50 + [Decimal("60000")]
        self._filters = {"BTCUSDT": SymbolFilters(BTC_INFO)}
        self.orders: list[dict] = []

    def filters_for(self, symbol):
        return self._filters[symbol]

    def book_ticker(self, symbol):
        return {"symbol": symbol, "bidPrice": self.bid, "askPrice": self.ask}

    def klines(self, symbol, interval, limit=100):
        return [[0, "0", "0", "0", format(c, "f"), "0"] for c in self.closes[-limit:]]

    def account(self):
        return {
            "canTrade": True,
            "balances": [{"asset": a, "free": format(v, "f"), "locked": "0"} for a, v in self.balances.items()],
        }

    def free_balance(self, asset):
        return self.balances.get(asset, Decimal("0"))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        binance_api_key="k",
        binance_api_secret="s",
        binance_env="testnet",
        allowed_symbols="BTCUSDT",
        risk_per_trade_pct=Decimal("1.0"),
        stop_loss_pct=Decimal("1.5"),
        take_profit_pct=Decimal("3.0"),
        max_position_notional_usdt=Decimal("100"),
        max_open_positions=2,
        max_daily_loss_usdt=Decimal("50"),
        max_trades_per_day=5,
        max_spread_pct=Decimal("0.15"),
    )


@pytest.fixture
def session():
    from app.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()
