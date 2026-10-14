"""Bound request bodies before FastAPI parses JSON or allocates model inputs."""
import threading

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                payload = b'{"detail":"request body too large"}'
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": payload})
                return
            if not message.get("more_body", False):
                break

        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class ConcurrencyLimitMiddleware:
    """Reject overload immediately so waiting requests cannot grow without bound.
    A slot is freed when the response body is complete - not after
    dependency teardown - so a client's next request is not rejected
    while the previous one is still cleaning up."""

    def __init__(self, app: ASGIApp, max_requests: int = 100) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be positive")
        self.app = app
        self._slots = threading.BoundedSemaphore(max_requests)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in {"/health", "/ready", "/metrics"}:
            await self.app(scope, receive, send)
            return
        if not self._slots.acquire(blocking=False):
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json"), (b"retry-after", b"1")]})
            await send({"type": "http.response.body", "body": b'{"detail":"server busy"}'})
            return
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self._slots.release()

        async def send_and_release(message: Message) -> None:
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                release()

        try:
            await self.app(scope, receive, send_and_release)
        finally:
            release()
