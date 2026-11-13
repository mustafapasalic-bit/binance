from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BotState
from app.services.audit import log_event


def get_state(session: Session) -> BotState:
    state = session.scalar(select(BotState).where(BotState.id == 1))
    if state is None:
        state = BotState(id=1, trading_enabled=get_settings().trading_enabled)
        session.add(state)
        session.flush()
    return state


def set_trading_enabled(session: Session, enabled: bool, reason: str) -> BotState:
    state = get_state(session)
    state.trading_enabled = enabled
    state.kill_switch_reason = None if enabled else reason
    session.flush()
    log_event(
        session,
        "kill_switch" if not enabled else "trading_enabled",
        decision="enabled" if enabled else "disabled",
        reason=reason,
    )
    return state
