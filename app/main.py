"""FastAPI application entrypoint for the LLM Gateway."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import chat, conversations, memories
from app.config import get_settings
from app.database.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("llm_gateway")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Touch settings once at startup so misconfiguration fails fast and loudly.
    get_settings()
    init_db()
    logger.info("LLM Gateway started")
    yield


app = FastAPI(
    title="LLM Gateway",
    description="A unified API for multiple LLM providers with shared conversation memory.",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(memories.router)


@app.get("/health")
async def health() -> dict:
    """Basic liveness check for the gateway itself."""
    return {"status": "ok"}
