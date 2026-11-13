"""Send a signed signal to the running bot.

Usage:
    python -m scripts.send_signal BTCUSDT BUY [--confidence 0.9] [--url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import httpx

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("side", choices=["BUY", "SELL"])
    parser.add_argument("--source", default="manual")
    parser.add_argument("--confidence", default="1.0")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    settings = get_settings()
    payload = {
        "source": args.source,
        "symbol": args.symbol.upper(),
        "side": args.side,
        "confidence": args.confidence,
        "external_id": uuid.uuid4().hex,
        "signal_time": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()

    response = httpx.post(
        f"{args.url}/api/signals/webhook",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": signature},
        timeout=60.0,
    )
    print(response.status_code, json.dumps(response.json(), indent=2))


if __name__ == "__main__":
    main()
