from decimal import Decimal

from app.services.execution import summarize_fills

BUY_RESPONSE = {
    "symbol": "BTCUSDT",
    "orderId": 1,
    "status": "FILLED",
    "executedQty": "0.00200000",
    "cummulativeQuoteQty": "120.01200000",
    "fills": [
        {"price": "60000.00", "qty": "0.00100000", "commission": "0.00000100", "commissionAsset": "BTC"},
        {"price": "60012.00", "qty": "0.00100000", "commission": "0.06000000", "commissionAsset": "USDT"},
    ],
}


def test_average_price_comes_from_actual_fills():
    filled, quote, avg, quote_fee, base_fee = summarize_fills(BUY_RESPONSE, "BTC", "USDT")
    assert filled == Decimal("0.002")
    assert quote == Decimal("120.012")
    assert avg == Decimal("60006")
    assert quote_fee == Decimal("0.06")
    assert base_fee == Decimal("0.000001")


def test_unfilled_order_reports_zero():
    filled, quote, avg, _, _ = summarize_fills(
        {"executedQty": "0", "cummulativeQuoteQty": "0", "fills": []}, "BTC", "USDT"
    )
    assert (filled, quote, avg) == (Decimal("0"), Decimal("0"), Decimal("0"))
