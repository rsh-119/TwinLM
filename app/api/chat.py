"""Chat completions endpoint.

Pipeline, in order:

    admit (model allow-list, rate limits, budget)
    -> input guardrails
    -> build context (stateful: conversation memory; stateless: as sent)
    -> cache lookup (stateless, non-streaming, nothing sensitive)
    -> route (registry, retries, circuit breakers, fallback)
    -> structured-output validation + bounded repair (response_format)
    -> output guardrails
    -> persist the exchange (stateful) / fill the cache (stateless)
    -> accounting (request log, metrics, TPM) - on every path, via finally

`body.stream=True` runs the same pipeline incrementally (`stream_chat_turn`)
and answers with Server-Sent Events. That also powers the web UI's live
"typing" effect (app/web/routes.py).
"""
import asyncio
import json
import logging
import time
from contextlib import aclosing
from typing import AsyncIterator, Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import stateful
from app.api.common import GatewayServices, admit, get_services, provider_errors
from app.auth import Principal, authenticate
from app.database import repository
from app.database.database import get_db, release_connection
from app.database.models import Conversation
from app.gateway import budgets, structured
from app.gateway.guardrails import GuardrailViolation
from app.gateway.usage import UsageRecord, finish
from app.memory.conversation_manager import ConversationManager
from app.observability import CACHE_HITS, CACHE_MISSES, STRUCTURED_OUTPUT_FAILURES, current_request_id
from app.providers.base import TokenUsage
from app.router.model_router import ModelRouter, RouteChunk, RouteResult, get_model_router
from app.schemas.chat import (
    ChatChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatResponseMessage,
    TokenUsageOut,
)

logger = logging.getLogger("twinlm.api.chat")

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    body: ChatCompletionRequest,
    response: Response = None,  # type: ignore[assignment] - FastAPI injects it
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_model_router),
    principal: Optional[Principal] = Depends(authenticate),
    services: GatewayServices = Depends(get_services),
) -> Union[ChatCompletionResponse, StreamingResponse]:
    if body.stream:
        return await _stream_chat_completions(body, db, model_router, principal, services)

    record = UsageRecord(
        current_request_id(), "chat", body.model, principal=principal, conversation_id=body.conversation_id
    )
    try:
        return await _complete(body, response, db, model_router, principal, services, record)
    except HTTPException as exc:
        if record.status == "success":
            record.fail(exc.status_code, str(exc.detail))
        raise
    except Exception as exc:
        record.fail(500, f"internal error: {type(exc).__name__}")
        raise
    finally:
        finish(db, record, services.config.pricing, services.limiter, services.usage_writer)


async def _complete(
    body: ChatCompletionRequest,
    response: Optional[Response],
    db: Session,
    model_router: ModelRouter,
    principal: Optional[Principal],
    services: GatewayServices,
    record: UsageRecord,
) -> ChatCompletionResponse:
    _reject_unsupported(body)
    _check_request_metadata(body, services, record)
    validator, json_mode = _structured_output(body)
    admit(body.model, principal, services, db, record, _estimate(body, model_router, services))

    manager, conversation, user_message, messages = _prepare_turn(body, db, principal, services, record)
    params = _checked_generation_params(body, services, record)

    cache_key = None
    semantic_key = None  # (scope, embedding) to store the answer under
    cache_ttl = services.config.cache_ttl(body.model)
    cacheable = (
        services.config.cache.enabled and conversation is None and cache_ttl != 0
        and not params.get("temperature")  # sampled replies are meant to vary
    )
    if cacheable and not services.guardrails.contains_sensitive(messages):
        cache_parts = dict(
            model=body.model,
            params=params,
            response_format=body.response_format.model_dump(by_alias=True) if body.response_format else None,
            project=principal.project if principal else None,
        )
        cache_key = services.cache.make_key(messages=messages, **cache_parts)
        cached: Optional[RouteResult] = services.cache.get(cache_key)
        if (cached is None and services.semantic_cache is not None
                and body.model in services.config.cache.semantic.models):
            semantic_key = await _semantic_key(messages, cache_parts, model_router, db, principal, services, record)
            found = services.semantic_cache.get(*semantic_key) if semantic_key else None
            if found is not None:
                cached, similarity = found
                if response is not None:
                    response.headers["X-Cache-Similarity"] = f"{similarity:.4f}"
        if response is not None:
            response.headers["X-Cache"] = "HIT" if cached else "MISS"
        if cached is not None:
            CACHE_HITS.inc()
            record.provider, record.model, record.usage, record.deployment, record.cache_hit = (
                cached.provider, cached.model, cached.usage, cached.deployment, True,
            )
            return _response(record, cached.content, cached.tool_calls, cached.finish_reason)
        CACHE_MISSES.inc()

    if json_mode:
        messages = [{"role": "system", "content": structured.instruction(validator.schema if validator else None)}, *messages]

    release_connection(db)
    with provider_errors(record):
        async with asyncio.timeout(services.config.request_timeout_seconds):
            result = await model_router.route(body.model, messages, params)
            record.served_by(result)
            content, usage = result.content, result.usage
            if json_mode and not result.tool_calls:
                content, usage = await _enforce_structure(
                    body, model_router, messages, params, validator, content, usage, services, record
                )
                record.usage = usage

    output_sensitive = services.guardrails.contains_sensitive([
        {"role": "assistant", "content": content, "tool_calls": result.tool_calls}
    ])
    try:
        content = services.guardrails.check_output(content)
    except GuardrailViolation as exc:
        record.fail(502, str(exc), "blocked")
        raise HTTPException(status_code=502, detail="response blocked by output guardrail")

    if json_mode and not result.tool_calls:
        content, error = structured.parse_and_validate(content, validator)
        if content is None:
            record.fail(502, f"output guardrail invalidated structured response: {error}")
            raise HTTPException(status_code=502, detail="output guardrail invalidated structured response")

    if conversation is not None:
        try:
            manager.save_exchange(conversation.id, conversation.version, user_message, content, result.provider, usage)
        except repository.ConversationConflictError:
            record.fail(409, "conversation changed during generation", "conflict")
            raise HTTPException(status_code=409, detail="conversation changed during generation; retry the turn")
        await stateful.maybe_summarize(manager, model_router, conversation.id, body.model, principal, services)
    elif cache_key is not None and not output_sensitive and not result.tool_calls:
        answer = result._replace(content=content, usage=usage)
        services.cache.set(cache_key, answer, cache_ttl)
        if semantic_key is not None:
            services.semantic_cache.set(*semantic_key, answer, cache_ttl)

    return _response(record, content, result.tool_calls, result.finish_reason)


async def _semantic_key(
    messages: List[dict],
    cache_parts: dict,
    model_router: ModelRouter,
    db: Session,
    principal: Optional[Principal],
    services: GatewayServices,
    record: UsageRecord,
) -> Optional[Tuple[str, List[float]]]:
    """(scope, embedding of the last user message) for the semantic cache,
    or None when the request doesn't fit (last message isn't plain user
    text) or the embeddings call fails - then the request just goes on
    uncached. The embeddings call is logged as its own request."""
    last = messages[-1]
    if last.get("role") != "user" or not isinstance(last.get("content"), str):
        return None
    scope = services.cache.make_key(semantic=True, context=messages[:-1], **cache_parts)
    embedding_model = services.config.cache.semantic.embedding_model
    embed_record = UsageRecord(f"{record.request_id}-semantic", "embeddings", embedding_model, principal=principal)
    release_connection(db)
    try:
        with provider_errors(embed_record):
            result = await model_router.embed(embedding_model, [last["content"]])
        embed_record.served_by(result)
        return scope, result.vectors[0]
    except HTTPException:
        logger.warning("semantic cache skipped, embedding failed: %s", embed_record.error)
        return None
    finally:
        finish(db, embed_record, services.config.pricing, services.limiter, services.usage_writer)


def _response(
    record: UsageRecord,
    content: str,
    tool_calls: Optional[List[dict]] = None,
    finish_reason: Optional[str] = None,
) -> ChatCompletionResponse:
    # OpenAI sends null content on a tool-calls-only turn
    message = ChatResponseMessage(content=(content or None) if tool_calls else content, tool_calls=tool_calls)
    finish_reason = finish_reason or ("tool_calls" if tool_calls else "stop")
    return ChatCompletionResponse(
        id=record.request_id,
        created=int(time.time()),
        conversation_id=record.conversation_id,
        model=record.model,
        provider=record.provider,
        choices=[ChatChoice(message=message, finish_reason=finish_reason)],
        message=message,
        usage=TokenUsageOut(**record.usage._asdict()) if record.usage else None,
    )


def _reject_unsupported(body: ChatCompletionRequest) -> None:
    """400 for what the gateway would otherwise silently get wrong."""
    if body.n is not None and body.n > 1:
        raise HTTPException(status_code=400, detail="n > 1 is not supported")
    if body.conversation_id is None:
        return
    if body.tools or body.tool_choice:
        raise HTTPException(status_code=400, detail="tools are only supported in stateless mode")
    if any(isinstance(m.content, list) and any(p.type != "text" for p in m.content) for m in body.messages):
        raise HTTPException(status_code=400, detail="image content is only supported in stateless mode")


def _structured_output(body: ChatCompletionRequest):
    """(validator or None, whether JSON output is required)."""
    fmt = body.response_format
    if fmt is None or fmt.type == "text":
        return None, False
    if body.stream:
        raise HTTPException(status_code=400, detail="response_format is not supported with stream=true")
    if fmt.type == "json_schema":
        if fmt.json_schema is None:
            raise HTTPException(status_code=400, detail="response_format.json_schema is required")
        try:
            return structured.validator_for(fmt.json_schema.schema_), True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return None, True


async def _enforce_structure(
    body: ChatCompletionRequest,
    model_router: ModelRouter,
    messages: List[Dict[str, str]],
    params: dict,
    validator,
    content: str,
    usage: Optional[TokenUsage],
    services: GatewayServices,
    record: UsageRecord,
) -> Tuple[str, Optional[TokenUsage]]:
    repairs = 0
    while True:
        normalized, error = structured.parse_and_validate(content, validator)
        if normalized is not None:
            return normalized, usage
        STRUCTURED_OUTPUT_FAILURES.inc()
        logger.warning("structured output invalid: attempt=%d error=%s", repairs + 1, error)
        if repairs >= services.config.structured_output.max_repair_attempts:
            record.fail(502, f"structured output validation failed: {error}")
            raise HTTPException(status_code=502, detail=f"model output failed validation: {error}")
        repairs += 1
        messages = [*messages, {"role": "assistant", "content": content}, structured.repair_message(error)]
        with provider_errors(record):
            result = await model_router.route(body.model, messages, params)
        record.bill(result.provider, result.model, result.usage)
        content = result.content
        usage = _add_usage(usage, result.usage)


def _add_usage(a: Optional[TokenUsage], b: Optional[TokenUsage]) -> Optional[TokenUsage]:
    if a is None or b is None:
        return a or b
    return TokenUsage(*(x + y for x, y in zip(a, b)))


async def _stream_chat_completions(
    body: ChatCompletionRequest,
    db: Session,
    model_router: ModelRouter,
    principal: Optional[Principal],
    services: GatewayServices,
) -> StreamingResponse:
    """The `"stream": true` branch. Each SSE event is an OpenAI
    `chat.completion.chunk` that also carries TwinLM's original
    `provider`/`delta`/`done` fields, so both kinds of client can read it.
    Stateless (OpenAI-style) requests end with OpenAI's `data: [DONE]`
    sentinel; stateful ones don't, since existing clients parse every
    `data:` line as JSON.

    All the actual work is `stream_chat_turn`; this just primes it (see
    `prime_stream`) so a setup failure still turns into a normal
    400/404/429/503, then formats the rest of the chunks onto the wire.
    """
    request_id = current_request_id()
    created = int(time.time())
    chunks = await prime_stream(
        stream_chat_turn(body, db, model_router, request_id=request_id, principal=principal, services=services)
    )
    include_usage = bool(body.stream_options and body.stream_options.include_usage)

    async def _sse() -> AsyncIterator[str]:
        first = True
        model, usage = body.model, None
        interrupted = False
        async with aclosing(chunks):
            async for chunk in chunks:
                delta = {"role": "assistant"} if first else {}
                if chunk.delta:
                    delta["content"] = chunk.delta
                if chunk.tool_calls:
                    delta["tool_calls"] = chunk.tool_calls
                first = False
                model = chunk.model
                interrupted = chunk.interrupted
                finish_reason = (chunk.finish_reason or "stop") if chunk.done else None
                payload = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": chunk.model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
                    "provider": chunk.provider,
                    "delta": chunk.delta,
                    "done": chunk.done,
                }
                if chunk.interrupted:
                    payload["interrupted"] = True
                if chunk.usage:
                    usage = payload["usage"] = chunk.usage._asdict()
                yield f"data: {json.dumps(payload)}\n\n"
        if interrupted:
            yield 'event: error\ndata: {"error":{"message":"stream interrupted","type":"upstream_error"}}\n\n'
            return
        if include_usage:
            # OpenAI's stream_options.include_usage: a last, choice-less chunk
            payload = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": usage,
            }
            yield f"data: {json.dumps(payload)}\n\n"
        if body.conversation_id is None:
            yield "data: [DONE]\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


async def stream_chat_turn(
    body: ChatCompletionRequest,
    db: Session,
    model_router: ModelRouter,
    request_id: Optional[str] = None,
    principal: Optional[Principal] = None,
    services: Optional[GatewayServices] = None,
) -> AsyncIterator[RouteChunk]:
    """Streaming counterpart to `chat_completions` - shared by the JSON SSE
    branch and the web UI's streaming endpoint (app/web/routes.py).

    Setup failures (admission, unknown conversation, guardrails, no
    reachable provider) raise HTTPException on the *first* item this
    yields, before anything has gone out over the wire - see
    `prime_stream`, which every caller should route through for that
    reason. A failure after that point can't change the response anymore
    (the 200 status is already on its way), so it's logged and the stream
    just ends.

    If the client disconnects, the generator is closed: the router tears
    down the upstream request, the request is accounted as
    `client_disconnected`, and the partial reply is *not* saved to the
    conversation.
    """
    services = services or get_services()
    record = UsageRecord(
        request_id or current_request_id(),
        "chat",
        body.model,
        stream=True,
        principal=principal,
        conversation_id=body.conversation_id,
    )
    completed = False
    try:
        _reject_unsupported(body)
        _check_request_metadata(body, services, record)
        _structured_output(body)
        admit(body.model, principal, services, db, record, _estimate(body, model_router, services))
        manager, conversation, user_message, messages = _prepare_turn(body, db, principal, services, record)

        collected: List[str] = []
        first_chunk_yielded = False
        terminal_chunk: Optional[RouteChunk] = None
        release_connection(db)
        try:
            with provider_errors(record):
                async with aclosing(
                    model_router.route_stream(body.model, messages, _checked_generation_params(body, services, record))
                ) as route_chunks:
                    async for chunk in route_chunks:
                        if chunk.delta:
                            collected.append(chunk.delta)
                        if chunk.done:
                            record.provider, record.model, record.usage = chunk.provider, chunk.model, chunk.usage
                            record.fallback, record.ttft, record.deployment = chunk.fallback, chunk.ttft, chunk.deployment
                            record.bill(chunk.provider, chunk.model, chunk.usage)
                            terminal_chunk = chunk
                            continue
                        elif not first_chunk_yielded:
                            record.provider, record.model = chunk.provider, chunk.model
                        yield chunk
                        first_chunk_yielded = True
        except HTTPException:
            if first_chunk_yielded:
                logger.error("stream failed after first chunk: status=%s", record.http_status)
                completed = True
                yield RouteChunk(record.provider or "unknown", record.model or body.model, "", done=True,
                                 finish_reason="error", interrupted=True)
                return
            raise
        completed = True

        if terminal_chunk is None:
            record.fail(502, "upstream stream ended without a terminal chunk", "interrupted")
            yield RouteChunk(record.provider or "unknown", record.model or body.model, "", done=True,
                             finish_reason="error", interrupted=True)
            return
        if terminal_chunk.interrupted:
            record.fail(502, "upstream stream interrupted", "interrupted")
            yield terminal_chunk
            return

        full_content = "".join(collected)
        try:
            full_content = services.guardrails.check_output(full_content)
        except GuardrailViolation as exc:
            # already streamed - can only be recorded, not blocked
            record.fail(502, f"after-stream output guardrail: {exc}", "blocked")
            logger.warning("output guardrail finding on an already-streamed reply: %s", exc)
            yield terminal_chunk._replace(finish_reason="error", interrupted=True)
            return

        if conversation is None:
            yield terminal_chunk
            return
        try:
            manager.save_exchange(
                conversation.id, conversation.version, user_message, full_content,
                record.provider, record.usage,
            )
        except repository.ConversationConflictError:
            record.fail(409, "conversation changed during streamed generation", "conflict")
            logger.warning("streamed exchange not persisted because conversation changed: %s", conversation.id)
            yield terminal_chunk._replace(finish_reason="error", interrupted=True)
            return
        except Exception:  # noqa: BLE001 - the reply already reached the client either way
            record.fail(500, "failed to persist streamed exchange")
            logger.exception("failed to persist streamed exchange: conversation_id=%s", conversation.id)
            yield terminal_chunk._replace(finish_reason="error", interrupted=True)
            return
        yield terminal_chunk
        await stateful.maybe_summarize(manager, model_router, conversation.id, body.model, principal, services)
    except HTTPException as exc:
        if record.status == "success":
            record.fail(exc.status_code, str(exc.detail))
        raise
    except Exception as exc:
        record.fail(500, f"internal error: {type(exc).__name__}")
        raise
    except BaseException:
        if not completed:
            record.fail(499, "client disconnected", "client_disconnected")
        raise
    finally:
        finish(db, record, services.config.pricing, services.limiter, services.usage_writer)


async def prime_stream(gen: AsyncIterator[RouteChunk]) -> AsyncIterator[RouteChunk]:
    """Pulls the first item out of `gen` right now, before the caller
    constructs a StreamingResponse. `stream_chat_turn`'s setup work all
    happens on that first pull, so an HTTPException it raises still turns
    into a normal status code - once a StreamingResponse has sent its
    headers (status 200), that's no longer possible.

    Returns an iterator that replays the peeked-at item followed by the
    rest of `gen`, so the caller never notices the peek happened.
    """
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        first = None

    async def _replay() -> AsyncIterator[RouteChunk]:
        try:
            if first is not None:
                yield first
            async for item in gen:
                yield item
        finally:
            await gen.aclose()

    return _replay()


def _prepare_turn(
    body: ChatCompletionRequest,
    db: Session,
    principal: Optional[Principal],
    services: GatewayServices,
    record: UsageRecord,
) -> Tuple[Optional[ConversationManager], Optional[Conversation], str, List[dict]]:
    """Validates the turn, applies input guardrails, and returns
    (manager, conversation, user message, messages to send). Stateless
    requests (no `conversation_id`) send the client's messages as-is and
    touch no conversation data; stateful ones go through app/api/stateful.py."""
    if body.conversation_id is not None:
        return stateful.prepare_turn(body, db, principal, services, record)
    if body.latest_user_text() is None:
        raise HTTPException(status_code=400, detail="messages must include at least one user message")
    try:
        messages = services.guardrails.check_input([m.model_dump(exclude_none=True) for m in body.messages])
    except GuardrailViolation as exc:
        record.fail(400, str(exc), "blocked")
        raise HTTPException(status_code=400, detail=str(exc))
    return None, None, _latest_user_content(messages), messages


def _check_request_metadata(body: ChatCompletionRequest, services: GatewayServices, record: UsageRecord) -> None:
    if body.response_format is None:
        return
    try:
        services.guardrails.check_auxiliary(body.response_format.model_dump(by_alias=True, exclude_none=True))
    except GuardrailViolation as exc:
        record.fail(400, str(exc), "blocked")
        raise HTTPException(status_code=400, detail=str(exc))


def _estimate(body: ChatCompletionRequest, model_router: ModelRouter, services: GatewayServices) -> float:
    """Worst-case cost reserved against budgets while the request runs."""
    try:
        targets = model_router.resolve(body.model)
    except ValueError:
        return 0.0  # unknown model: routing rejects it
    cap = services.config.output_cap(body.model)
    requested = body.generation_params().get("max_tokens")
    max_output = min(requested or cap, cap) if cap else requested
    chars = sum(
        len(m.content) if isinstance(m.content, str)
        else sum(len(p.text) for p in m.content or [] if p.type == "text")
        for m in body.messages
    )
    return budgets.worst_case_cost(targets, services.config.pricing, chars, max_output)


def _checked_generation_params(
    body: ChatCompletionRequest, services: GatewayServices, record: UsageRecord
) -> dict:
    params = body.generation_params()
    cap = services.config.output_cap(body.model)
    if cap is not None:
        # unbounded or oversized completions are the common silent cost;
        # a clamped reply ends with finish_reason "length"
        params["max_tokens"] = min(params.get("max_tokens", cap), cap)
    if not params.get("tools"):
        return params
    try:
        params["tools"] = services.guardrails.check_auxiliary(params["tools"])
    except GuardrailViolation as exc:
        record.fail(400, str(exc), "blocked")
        raise HTTPException(status_code=400, detail=str(exc))
    return params


def _latest_user_content(messages: List[Dict[str, str]]) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "user")
