"""FastAPI application entrypoint for TwinLM."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import chat, conversations, memories
from app.config import get_settings
from app.database.database import init_db
from app.web import routes as web_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("twinlm")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    get_settings()
    init_db()
    logger.info("TwinLM started")
    yield


app = FastAPI(
    title="TwinLM",
    description="A unified API for multiple LLM providers with shared conversation memory.",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(memories.router)
app.include_router(web_routes.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
