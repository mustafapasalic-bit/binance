import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger(__name__)


def _default(value: Any) -> str:
    return str(value)


def log_event(
    session: Session,
    event: str,
    *,
    symbol: str | None = None,
    signal_id: int | None = None,
    trade_id: int | None = None,
    decision: str | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        event=event,
        symbol=symbol,
        signal_id=signal_id,
        trade_id=trade_id,
        decision=decision,
        reason=reason[:512] if reason else None,
        details=json.dumps(details or {}, default=_default),
    )
    session.add(entry)
    session.flush()
    logger.info("audit %s symbol=%s decision=%s reason=%s", event, symbol, decision, reason)
    return entry
