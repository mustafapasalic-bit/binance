# Binance Signal Trader

Signal-driven **spot** trading bot for Binance. Every number it shows comes from the exchange:
positions, fills, fees and PnL are read back from Binance REST responses and `myTrades` —
there is no simulated data, no mock prices and no placeholder dashboard.

The bot refuses to start without API credentials, starts with **trading disabled**, and never
opens a position it cannot immediately protect with a stop-loss.

## Flow

```
webhook (TradingView / Telegram bridge / own model)
  -> Signal stored (always, even if rejected)
  -> Validator    whitelist, symbol status, age, duplicate id, spread, 1h trend, confidence
  -> Risk engine  size from live equity, max notional, max open positions, daily loss cap, exchange filters
  -> Execution    MARKET entry -> OCO (stop-loss + take-profit) attached immediately
  -> Reconciler   polls Binance every 15s, books real exit price / fees / realized PnL
  -> Kill switch  manual, or automatic when the daily loss limit is breached
```

Every step writes an `audit_log` row with the decision and the reason for it.

## Quick start (Binance Spot Testnet)

1. Get keys at <https://testnet.binance.vision> (GitHub login → *Generate HMAC_SHA256 Key*).
   The testnet account is funded with test USDT.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env    # fill in BINANCE_API_KEY / BINANCE_API_SECRET / WEBHOOK_SECRET / ADMIN_TOKEN
```

2. Verify the connection, permissions and balances before anything else:

```bash
python -m scripts.preflight
```

3. Run it:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dashboard: <http://127.0.0.1:8000> — enter the `ADMIN_TOKEN`, press **Enable trading**.

4. Send a signal:

```bash
python -m scripts.send_signal BTCUSDT BUY --confidence 0.9
```

## Signal payload

`POST /api/signals/webhook`, authenticated with either
`X-Signature: hmac_sha256(WEBHOOK_SECRET, raw_body)` or `X-Webhook-Secret: <WEBHOOK_SECRET>`.

```json
{
  "source": "tradingview",
  "symbol": "BTCUSDT",
  "side": "BUY",
  "confidence": 0.85,
  "external_id": "tv-4711",
  "signal_time": "2026-01-01T12:00:00Z"
}
```

`side: SELL` closes an existing position; the bot is spot long-only and never sells what it does not hold.
`external_id` makes retries idempotent. `confidence` can only scale the position **down**, never above the cap.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | live equity, balances, PnL, limits, kill-switch state |
| GET | `/api/positions` | open positions with mark price and unrealized PnL |
| GET | `/api/trades` | trade history with realized PnL |
| GET | `/api/signals` | every signal, including rejected ones and the reason |
| GET | `/api/audit` | full audit trail |
| POST | `/api/signals/webhook` | ingest a signal |
| POST | `/api/admin/trading` | enable/disable trading |
| POST | `/api/admin/kill-switch` | stop trading and optionally market-close everything |
| POST | `/api/admin/positions/{id}/close` | market-close one position |
| POST | `/api/admin/reconcile` | force reconciliation against Binance |

Admin routes require the `X-Admin-Token` header.

## Risk controls

| Setting | Meaning |
| --- | --- |
| `RISK_PER_TRADE_PCT` | share of equity risked if the stop is hit; position size is derived from it |
| `MAX_POSITION_NOTIONAL_USDT` | hard cap per position |
| `MAX_OPEN_POSITIONS` | concurrent positions (one per symbol) |
| `MAX_DAILY_LOSS_USDT` | breach flips the kill switch automatically |
| `MAX_TRADES_PER_DAY` | overtrading brake |
| `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` | OCO bracket attached right after entry |
| `MAX_SPREAD_PCT` | rejects illiquid/wide-spread moments |
| `MAX_SIGNAL_AGE_SEC` | rejects stale signals |
| `ALLOWED_SYMBOLS` | whitelist; nothing else is ever traded |

Order size is rounded with the symbol's real `LOT_SIZE` / `PRICE_FILTER` / `NOTIONAL` filters in
`Decimal` arithmetic, so orders are not rejected for precision and no float rounding ever touches money.

## Going live

Only after the testnet run looks right:

1. Binance API key with **Enable Spot Trading** on, **withdrawals off**, **IP whitelist** on your server IP.
2. `BINANCE_ENV=live`, keys in a secrets manager or a `chmod 600 .env` — never in git.
3. Keep only trading capital on the account.
4. Start with `MAX_POSITION_NOTIONAL_USDT` at a few dollars and one symbol.
5. Run as a non-root user behind a firewall; expose the dashboard only over a VPN or SSH tunnel.
6. Configure `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` so entries, exits and kill-switch events reach you.

Binance rate limits are enforced client-side (weight/minute and orders/10s) with backoff on
HTTP 429/418, because repeated violations lead to an IP ban.

## Tests

```bash
pytest -q          # risk sizing, validator rules, fill accounting, kill switch
ruff check .
```

Unit tests never hit the network. `scripts/preflight.py` is the check that talks to the real API.
