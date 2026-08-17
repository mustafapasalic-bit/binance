import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.db import init_db, session_scope
from app.services import pipeline, portfolio
from app.services.runtime import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
RECONCILE_INTERVAL_SEC = 15


async def reconcile_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(RECONCILE_INTERVAL_SEC)
            client = get_client(settings)
            with session_scope() as session:
                portfolio.reconcile_all(session, client, settings)
                pipeline.enforce_daily_loss_limit(session, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive transient API errors
            logger.exception("reconcile loop iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    logger.info("starting on %s (%s)", settings.base_url, settings.binance_env)
    if settings.is_live:
        logger.warning("LIVE TRADING MODE - real funds are at risk")
    task = asyncio.create_task(reconcile_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Binance Signal Trader", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
