"""Resource admission tests without provider or database dependencies."""
import asyncio

from app.request_limits import ConcurrencyLimitMiddleware


async def test_concurrency_limit_rejects_overload_without_queueing():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_app(scope, receive, send):
        entered.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = ConcurrencyLimitMiddleware(slow_app, max_requests=1)
    scope = {"type": "http", "path": "/v1/chat/completions", "method": "POST"}
    first_output, second_output = [], []

    async def first_send(message):
        first_output.append(message)

    async def second_send(message):
        second_output.append(message)

    first = asyncio.create_task(middleware(scope, receive, first_send))
    await entered.wait()
    await middleware(scope, receive, second_send)
    assert second_output[0]["status"] == 503
    release.set()
    await first
    assert first_output[0]["status"] == 200


async def test_slot_is_freed_when_the_response_body_is_complete():
    finished_body = asyncio.Event()
    teardown = asyncio.Event()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
        finished_body.set()
        await teardown.wait()  # e.g. dependency cleanup after the client has its answer

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    middleware = ConcurrencyLimitMiddleware(app, max_requests=1)
    scope = {"type": "http", "path": "/v1/chat/completions", "method": "POST"}
    second = []

    async def collect(message):
        second.append(message)

    async def ignore(message):
        pass

    first = asyncio.create_task(middleware(scope, receive, ignore))
    await finished_body.wait()
    teardown.set()
    await middleware(scope, receive, collect)
    await first
    assert second[0]["status"] == 200
