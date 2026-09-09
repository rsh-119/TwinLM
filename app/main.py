"""FastAPI application entrypoint for the LLM Gateway."""
import logging

from fastapi import FastAPI

from app.config import get_settings
from app.database.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("llm_gateway")

app = FastAPI(
    title="LLM Gateway",
    description="A unified API for multiple LLM providers with shared conversation memory.",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict:
    """Basic liveness check for the gateway itself."""
    return {"status": "ok"}


@app.on_event("startup")
async def on_startup() -> None:
    # Touch settings once at startup so misconfiguration fails fast and loudly.
    get_settings()
    init_db()
    logger.info("LLM Gateway started")
