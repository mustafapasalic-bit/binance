"""Pre-flight check against the real Binance API.

Verifies credentials, clock drift, permissions (trading yes / withdrawals no),
symbol filters and live balances. Run this before enabling trading.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from app.config import get_settings
from app.exchange.binance_client import BinanceError
from app.services.risk import account_equity_quote
from app.services.runtime import get_client


def main() -> int:
    settings = get_settings()
    print(f"environment : {settings.binance_env}  ({settings.base_url})")
    try:
        client = get_client(settings)
    except RuntimeError as exc:
        print(f"FAIL: {exc}")
        return 1

    try:
        client.ping()
        offset = client.sync_time()
        print(f"clock offset: {offset} ms")
        if abs(offset) > 1000:
            print("WARN: clock drift above 1s - orders may be rejected with -1021")

        account = client.account()
        perms = account.get("permissions", [])
        print(f"account type: {account.get('accountType')}  permissions={perms}")
        print(f"canTrade={account['canTrade']} canWithdraw={account['canWithdraw']} canDeposit={account['canDeposit']}")
        if not account["canTrade"]:
            print("FAIL: API key cannot trade")
            return 1
        if account["canWithdraw"]:
            print("WARN: this API key HAS withdrawal permission - disable it in Binance settings")

        balances = [b for b in account["balances"] if Decimal(b["free"]) + Decimal(b["locked"]) > 0]
        print("balances:")
        for b in sorted(balances, key=lambda x: x["asset"]):
            print(f"  {b['asset']:<8} free={b['free']:<20} locked={b['locked']}")

        filters = client.load_filters(settings.symbol_whitelist)
        print("symbol filters:")
        for symbol in settings.symbol_whitelist:
            f = filters[symbol]
            book = client.book_ticker(symbol)
            print(
                f"  {symbol:<10} status={f.status} step={f.step_size} tick={f.tick_size} "
                f"minNotional={f.min_notional} bid={book['bidPrice']} ask={book['askPrice']}"
            )

        equity = account_equity_quote(client, settings)
        print(f"equity      : {equity:.2f} {settings.quote_asset}")
        risk_amount = equity * settings.risk_per_trade_pct / Decimal("100")
        notional = min(risk_amount / (settings.stop_loss_pct / Decimal("100")), settings.max_position_notional_usdt)
        print(f"next trade  : risk {risk_amount:.2f} -> notional {notional:.2f} {settings.quote_asset}")
        print("OK")
        return 0
    except BinanceError as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
