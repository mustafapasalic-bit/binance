"""Thin, explicit Binance Spot REST client.

Only what a trading bot needs, with the parts that matter for real money handled
properly: HMAC signing, server time drift, weight-aware rate limiting, retry with
backoff on 429/418/5xx, and exchange filter rounding done in Decimal.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

WEIGHT_LIMIT_PER_MINUTE = 1200
ORDER_LIMIT_PER_10S = 50


class BinanceError(RuntimeError):
    def __init__(self, status_code: int, code: int | None, message: str):
        super().__init__(f"HTTP {status_code} code={code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


class RateLimiter:
    """Client-side guard so we stop before Binance answers 429 and bans the IP."""

    def __init__(self, weight_limit: int = WEIGHT_LIMIT_PER_MINUTE, order_limit: int = ORDER_LIMIT_PER_10S):
        self._weight_limit = weight_limit
        self._order_limit = order_limit
        self._weights: list[tuple[float, int]] = []
        self._orders: list[float] = []
        self._lock = threading.Lock()

    def acquire(self, weight: int, is_order: bool) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._weights = [(t, w) for t, w in self._weights if now - t < 60]
                self._orders = [t for t in self._orders if now - t < 10]
                used = sum(w for _, w in self._weights)
                wait = 0.0
                if used + weight > self._weight_limit:
                    wait = max(wait, 60 - (now - self._weights[0][0]))
                if is_order and len(self._orders) + 1 > self._order_limit:
                    wait = max(wait, 10 - (now - self._orders[0]))
                if wait <= 0:
                    self._weights.append((now, weight))
                    if is_order:
                        self._orders.append(now)
                    return
            logger.warning("local rate limit reached, sleeping %.2fs", wait)
            time.sleep(min(wait, 60))


class SymbolFilters:
    """LOT_SIZE / PRICE_FILTER / NOTIONAL rules for one symbol."""

    def __init__(self, info: dict[str, Any]):
        self.symbol: str = info["symbol"]
        self.base_asset: str = info["baseAsset"]
        self.quote_asset: str = info["quoteAsset"]
        self.status: str = info["status"]
        self.step_size = Decimal("0")
        self.min_qty = Decimal("0")
        self.max_qty: Decimal | None = None
        self.tick_size = Decimal("0")
        self.min_notional = Decimal("0")
        for f in info.get("filters", []):
            ftype = f["filterType"]
            if ftype == "LOT_SIZE":
                self.step_size = Decimal(f["stepSize"])
                self.min_qty = Decimal(f["minQty"])
                self.max_qty = Decimal(f["maxQty"])
            elif ftype == "PRICE_FILTER":
                self.tick_size = Decimal(f["tickSize"])
            elif ftype in ("NOTIONAL", "MIN_NOTIONAL"):
                value = f.get("minNotional")
                if value is not None:
                    self.min_notional = Decimal(value)

    @staticmethod
    def _round_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
        if step == 0:
            return value
        return (value / step).quantize(Decimal("1"), rounding=rounding) * step

    def round_qty(self, qty: Decimal) -> Decimal:
        return self._round_step(qty, self.step_size, ROUND_DOWN).normalize() + Decimal("0")

    def round_price(self, price: Decimal, up: bool = False) -> Decimal:
        return self._round_step(price, self.tick_size, ROUND_UP if up else ROUND_DOWN)

    @staticmethod
    def _decimals(step: Decimal) -> int:
        """Significant decimals of a step, e.g. 0.00001000 -> 5."""
        if step == 0:
            return 8
        return max(0, -step.normalize().as_tuple().exponent)

    def qty_str(self, qty: Decimal) -> str:
        return format(qty.quantize(Decimal(1).scaleb(-self._decimals(self.step_size))), "f")

    def price_str(self, price: Decimal) -> str:
        return format(price.quantize(Decimal(1).scaleb(-self._decimals(self.tick_size))), "f")


class BinanceClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str,
        recv_window: int = 5000,
        timeout: float = 10.0,
        max_retries: int = 4,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret.encode()
        self.base_url = base_url.rstrip("/")
        self._recv_window = recv_window
        self._max_retries = max_retries
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            proxy=proxy_url or None,
            headers={"X-MBX-APIKEY": api_key, "User-Agent": "binance-signal-trader/1.0"},
        )
        self._limiter = RateLimiter()
        self._time_offset_ms = 0
        self._filters: dict[str, SymbolFilters] = {}
        self._filters_fetched_at = 0.0

    # -- plumbing ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict[str, Any]) -> str:
        query = urlencode(params, doseq=True)
        signature = hmac.new(self._api_secret, query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={signature}"

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        weight: int = 1,
        is_order: bool = False,
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            self._limiter.acquire(weight, is_order)
            if signed:
                params["timestamp"] = self._timestamp()
                params["recvWindow"] = self._recv_window
                url = f"{path}?{self._sign(params)}"
                request_kwargs: dict[str, Any] = {}
            else:
                url = path
                request_kwargs = {"params": params}
            try:
                response = self._client.request(method, url, **request_kwargs)
            except httpx.HTTPError as exc:
                attempt += 1
                if attempt > self._max_retries:
                    raise BinanceError(0, None, f"network error: {exc}") from exc
                time.sleep(min(2**attempt, 20))
                continue

            if response.status_code in (429, 418):
                retry_after = float(response.headers.get("Retry-After", 2**attempt))
                attempt += 1
                logger.error("rate limited by Binance (%s), backing off %.1fs", response.status_code, retry_after)
                if attempt > self._max_retries:
                    raise BinanceError(response.status_code, None, "rate limited by Binance")
                time.sleep(min(retry_after, 120))
                continue

            if response.status_code >= 500:
                attempt += 1
                if attempt > self._max_retries:
                    raise BinanceError(response.status_code, None, response.text[:300])
                time.sleep(min(2**attempt, 20))
                continue

            if response.status_code == 451:
                raise BinanceError(
                    451,
                    None,
                    "Binance blocks this server's location (HTTP 451). Run the bot from a permitted "
                    "region or set BINANCE_PROXY_URL to an outbound proxy in one.",
                )

            if response.status_code >= 400:
                try:
                    body = response.json()
                    code, msg = body.get("code"), body.get("msg", response.text[:300])
                except ValueError:
                    code, msg = None, response.text[:300]
                if code == -1021 and attempt < self._max_retries:  # timestamp outside recvWindow
                    attempt += 1
                    self.sync_time()
                    continue
                raise BinanceError(response.status_code, code, msg)

            return response.json()

    # -- public endpoints -------------------------------------------------
    def ping(self) -> dict:
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> int:
        return int(self._request("GET", "/api/v3/time")["serverTime"])

    def sync_time(self) -> int:
        local_before = int(time.time() * 1000)
        server = self.server_time()
        local_after = int(time.time() * 1000)
        self._time_offset_ms = server - (local_before + local_after) // 2
        logger.info("clock offset vs Binance: %d ms", self._time_offset_ms)
        return self._time_offset_ms

    def exchange_info(self, symbols: list[str] | None = None) -> dict:
        params = {"symbols": "[" + ",".join(f'"{s}"' for s in symbols) + "]"} if symbols else None
        return self._request("GET", "/api/v3/exchangeInfo", params, weight=20)

    def load_filters(self, symbols: list[str], ttl_sec: int = 3600) -> dict[str, SymbolFilters]:
        fresh = self._filters and time.monotonic() - self._filters_fetched_at < ttl_sec
        if fresh and all(s in self._filters for s in symbols):
            return self._filters
        info = self.exchange_info(symbols)
        self._filters = {s["symbol"]: SymbolFilters(s) for s in info["symbols"]}
        self._filters_fetched_at = time.monotonic()
        return self._filters

    def filters_for(self, symbol: str) -> SymbolFilters:
        if symbol not in self._filters:
            self.load_filters([symbol])
        return self._filters[symbol]

    def book_ticker(self, symbol: str) -> dict:
        return self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol})

    def klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        return self._request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    # -- signed endpoints -------------------------------------------------
    def account(self) -> dict:
        return self._request("GET", "/api/v3/account", signed=True, weight=20)

    def free_balance(self, asset: str) -> Decimal:
        for bal in self.account()["balances"]:
            if bal["asset"] == asset:
                return Decimal(bal["free"])
        return Decimal("0")

    def new_order(self, **params: Any) -> dict:
        return self._request("POST", "/api/v3/order", params, signed=True, is_order=True)

    def market_buy(self, symbol: str, quantity: str, client_order_id: str | None = None) -> dict:
        return self.new_order(
            symbol=symbol, side="BUY", type="MARKET", quantity=quantity, newClientOrderId=client_order_id
        )

    def market_sell(self, symbol: str, quantity: str, client_order_id: str | None = None) -> dict:
        return self.new_order(
            symbol=symbol, side="SELL", type="MARKET", quantity=quantity, newClientOrderId=client_order_id
        )

    def oco_sell(
        self,
        symbol: str,
        quantity: str,
        take_profit_price: str,
        stop_price: str,
        stop_limit_price: str,
        list_client_order_id: str | None = None,
    ) -> dict:
        """Protective bracket: take-profit limit + stop-loss limit, one cancels the other."""
        modern = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": quantity,
            "aboveType": "LIMIT_MAKER",
            "abovePrice": take_profit_price,
            "belowType": "STOP_LOSS_LIMIT",
            "belowPrice": stop_limit_price,
            "belowStopPrice": stop_price,
            "belowTimeInForce": "GTC",
            "listClientOrderId": list_client_order_id,
        }
        try:
            return self._request("POST", "/api/v3/orderList/oco", modern, signed=True, is_order=True)
        except BinanceError as exc:
            if exc.status_code not in (400, 404):
                raise
            logger.warning("orderList/oco unavailable (%s), using legacy order/oco", exc)
            legacy = {
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "price": take_profit_price,
                "stopPrice": stop_price,
                "stopLimitPrice": stop_limit_price,
                "stopLimitTimeInForce": "GTC",
                "listClientOrderId": list_client_order_id,
            }
            return self._request("POST", "/api/v3/order/oco", legacy, signed=True, is_order=True)

    def get_order(self, symbol: str, order_id: str | int) -> dict:
        return self._request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True, weight=4)

    def get_order_list(self, order_list_id: str | int) -> dict:
        return self._request("GET", "/api/v3/orderList", {"orderListId": order_list_id}, signed=True, weight=4)

    def open_orders(self, symbol: str | None = None) -> list:
        return self._request("GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True, weight=6)

    def cancel_order(self, symbol: str, order_id: str | int) -> dict:
        return self._request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    def cancel_order_list(self, symbol: str, order_list_id: str | int) -> dict:
        return self._request(
            "DELETE", "/api/v3/orderList", {"symbol": symbol, "orderListId": order_list_id}, signed=True
        )

    def cancel_open_orders(self, symbol: str) -> list:
        return self._request("DELETE", "/api/v3/openOrders", {"symbol": symbol}, signed=True)

    def my_trades(self, symbol: str, order_id: str | int | None = None, limit: int = 50) -> list:
        return self._request(
            "GET", "/api/v3/myTrades", {"symbol": symbol, "orderId": order_id, "limit": limit}, signed=True, weight=20
        )
