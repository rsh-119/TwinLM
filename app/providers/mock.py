"""Mock provider: no network, configurable latency and failure rate. For
load tests and for running the gateway locally without provider keys
(`type = "mock"` in the gateway config). Its replies say so - it never
pretends to be a real model."""
import asyncio
import hashlib
import random
from typing import AsyncIterator, Dict, List, Optional

from app.providers.base import (
    EmbeddingResult,
    GenerationParams,
    GenerationResult,
    LLMProvider,
    ProviderUnavailableError,
    StreamEvent,
    TokenUsage,
    text_of,
)

_EMBEDDING_DIMENSIONS = 8


class MockProvider(LLMProvider):
    def __init__(
        self,
        name: str = "mock",
        default_model: str = "mock-1",
        latency_seconds: float = 0.0,
        failure_rate: float = 0.0,
    ) -> None:
        self.name = name
        self.default_model = default_model
        self._latency = latency_seconds
        self._failure_rate = failure_rate

    def _reply(self, messages: List[Dict[str, str]]) -> str:
        last = next((text_of(m.get("content")) for m in reversed(messages) if m["role"] == "user"), "")
        return f"[mock reply from {self.name}] You said: {last[:200]}"

    @staticmethod
    def _usage(messages: List[Dict[str, str]], reply: str) -> TokenUsage:
        # ponytail: whitespace word count stands in for tokens - mock only.
        prompt = sum(len(text_of(m.get("content")).split()) for m in messages)
        completion = len(reply.split())
        return TokenUsage(prompt, completion, prompt + completion)

    async def _simulate(self) -> None:
        if self._latency:
            await asyncio.sleep(self._latency)
        if self._failure_rate and random.random() < self._failure_rate:
            raise ProviderUnavailableError(f"{self.name} simulated failure")

    async def generate(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> GenerationResult:
        await self._simulate()
        reply = self._reply(messages)
        return GenerationResult(content=reply, usage=self._usage(messages, reply))

    async def generate_stream(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> AsyncIterator[StreamEvent]:
        await self._simulate()
        reply = self._reply(messages)
        words = reply.split(" ")
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            if self._latency and not is_last:
                await asyncio.sleep(self._latency / 10)
            yield StreamEvent(
                delta=word if is_last else word + " ",
                usage=self._usage(messages, reply) if is_last else None,
            )

    async def embed(self, texts: List[str], model: str) -> EmbeddingResult:
        await self._simulate()
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vectors.append([b / 255 for b in digest[:_EMBEDDING_DIMENSIONS]])
        tokens = sum(len(t.split()) for t in texts)
        return EmbeddingResult(vectors=vectors, usage=TokenUsage(tokens, 0, tokens))
