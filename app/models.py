from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Money(TypeDecorator):
    """Exact decimal storage. Stored as text so SQLite never rounds through float."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return format(Decimal(str(value)), "f")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


class Base(DeclarativeBase):
    pass


class Signal(Base):
    """Every received signal is stored, whether it is traded or rejected."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[Decimal] = mapped_column(Money, default=Decimal("1"))
    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw_payload: Mapped[str] = mapped_column(Text, default="{}")

    # received | rejected | accepted | executed | failed
    status: Mapped[str] = mapped_column(String(24), default="received", index=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255))

    trades: Mapped[list["Trade"]] = relationship(back_populates="signal")


class Trade(Base):
    """One round trip: entry fill, protective orders, exit fill, realized PnL."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))  # BUY (spot long only)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)  # open|closed|error

    qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    entry_price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    entry_quote_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    entry_order_id: Mapped[str | None] = mapped_column(String(64))
    entry_commission_quote: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    stop_price: Mapped[Decimal | None] = mapped_column(Money)
    take_profit_price: Mapped[Decimal | None] = mapped_column(Money)
    protection_order_list_id: Mapped[str | None] = mapped_column(String(64))

    exit_price: Mapped[Decimal | None] = mapped_column(Money)
    exit_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    exit_quote_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    exit_order_id: Mapped[str | None] = mapped_column(String(64))
    exit_commission_quote: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    exit_reason: Mapped[str | None] = mapped_column(String(64))

    realized_pnl_quote: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    signal: Mapped["Signal"] = relationship(back_populates="trades")
    orders: Mapped[list["OrderRecord"]] = relationship(back_populates="trade")


class OrderRecord(Base):
    """Raw record of every order Binance accepted, exactly as it answered."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    binance_order_id: Mapped[str] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    order_list_id: Mapped[str | None] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), index=True)
    price: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    orig_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    executed_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    cummulative_quote_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    raw_response: Mapped[str] = mapped_column(Text, default="{}")

    trade: Mapped["Trade"] = relationship(back_populates="orders")


class AuditLog(Base):
    """Append-only record of every decision the bot made and why."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer)
    trade_id: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[str] = mapped_column(Text, default="{}")


class BotState(Base):
    """Single-row runtime state: kill switch and the reason it was flipped."""

    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    trading_enabled: Mapped[bool] = mapped_column(default=False)
    kill_switch_reason: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
