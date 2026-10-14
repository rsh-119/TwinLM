"""TwinLM test console - a Streamlit UI for exercising the gateway over
plain HTTP, so every response detail (provider, tokens, request ID, cache
status, errors) is visible.

    pip install -r ui/requirements.txt
    streamlit run ui/streamlit_app.py

The gateway URL is fixed by GATEWAY_URL. Keys are entered per browser
session and are never inherited from the container environment.
"""
import json
import math
import os
import statistics
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator, List, Optional

import httpx
import streamlit as st

st.set_page_config(page_title="TwinLM Console", page_icon="🔀", layout="wide")

DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "population": {"type": "integer"}},
    "required": ["city", "population"],
}

# ---- state & sidebar -------------------------------------------------------

defaults = {
    "base_url": os.environ.get("GATEWAY_URL", "http://127.0.0.1:8000"),
    "api_key": "",
    "admin_token": "",
    "chat": [],
    "conversation_id": "",
    "new_key": None,
}
for name, value in defaults.items():
    st.session_state.setdefault(name, value)
st.session_state.base_url = defaults["base_url"].rstrip("/")


@st.cache_resource
def http() -> httpx.Client:
    """One shared, thread-safe client for every call. Constructing an
    httpx client is expensive (it builds an SSL context): a new one per
    request measured ~1.7 s on Windows versus ~30 ms reused."""
    return httpx.Client(timeout=120, limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))


def use_key(raw: str) -> None:
    st.session_state.api_key = raw


def create_console_key() -> None:
    """Sidebar shortcut: mint a key via the admin API and use it."""
    try:
        response = http().post(
            f"{st.session_state.base_url}/admin/keys",
            json={"name": "console", "project": "console"},
            headers={"Authorization": f"Bearer {st.session_state.admin_token}"},
        )
    except httpx.HTTPError as exc:
        st.session_state.key_error = f"Could not create a key: {exc}"
        return
    if response.status_code == 201:
        st.session_state.api_key = response.json()["key"]
    else:
        st.session_state.key_error = f"Could not create a key ({response.status_code}): {response.text[:200]}"


with st.sidebar:
    st.title("TwinLM Console")
    st.caption(f"Gateway: {st.session_state.base_url}")
    st.text_input("API key (gw_live_…)", key="api_key", type="password", help="Needed when AUTH_ENABLED=true")
    st.text_input("Admin token", key="admin_token", type="password", help="Needed for the admin tabs")
    if st.session_state.admin_token and not st.session_state.api_key:
        st.button("Create & use a console API key", on_click=create_console_key, type="primary", width="stretch")
    if st.session_state.get("key_error"):
        st.error(st.session_state.pop("key_error"))

    try:
        http().get(f"{st.session_state.base_url}/health", timeout=3).raise_for_status()
        ready = http().get(f"{st.session_state.base_url}/ready", timeout=3)
        if ready.status_code == 200:
            st.success("Gateway up and ready")
        else:
            st.warning(f"Gateway up but not ready ({ready.status_code})")
    except httpx.HTTPError as exc:
        st.error(f"Gateway unreachable: {exc}")


def _headers(admin: bool = False) -> dict:
    token = st.session_state.admin_token if admin else st.session_state.api_key
    return {"Authorization": f"Bearer {token}"} if token else {}


def call(method: str, path: str, *, admin: bool = False, **kwargs) -> Optional[httpx.Response]:
    """HTTP call to the gateway; shows connection errors and returns None."""
    try:
        return http().request(method, f"{st.session_state.base_url}{path}", headers=_headers(admin), **kwargs)
    except httpx.HTTPError as exc:
        st.error(f"Request failed: {exc}")
        return None


def show_error(response: httpx.Response) -> None:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    st.error(f"{response.status_code}: {detail}  ·  request {response.headers.get('x-request-id', '?')}")


@st.cache_data(ttl=15, show_spinner=False)
def _fetch_models(base_url: str, api_key: str) -> List[str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = http().get(f"{base_url}/v1/models", headers=headers, timeout=10)
    except httpx.HTTPError:
        return []
    return [m["id"] for m in response.json()["data"]] if response.status_code == 200 else []


def model_ids() -> List[str]:
    """Cached briefly: every tab needs the list and Streamlit reruns all of
    them on each interaction."""
    return _fetch_models(st.session_state.base_url, st.session_state.api_key) or ["auto"]


def meta_line(meta: dict) -> str:
    parts = [f"**{meta.get('provider', '?')}** / {meta.get('model', '?')}"]
    usage = meta.get("usage")
    if usage:
        parts.append(f"{usage['prompt_tokens']}+{usage['completion_tokens']}={usage['total_tokens']} tokens")
    if meta.get("latency_ms") is not None:
        parts.append(f"{meta['latency_ms']} ms")
    if meta.get("cache"):
        parts.append(f"cache {meta['cache']}")
    if meta.get("request_id"):
        parts.append(f"`{meta['request_id']}`")
    return " · ".join(parts)


tabs = st.tabs(["💬 Chat", "🧾 Structured output", "🔢 Embeddings", "🩺 Providers", "📊 Usage", "🔑 API keys", "⚡ Burst test"])

# ---- chat ------------------------------------------------------------------

with tabs[0]:
    left, right = st.columns([3, 1])
    with right:
        model = st.selectbox("Model", model_ids(), key="chat_model")
        stream = st.toggle("Stream", value=True)
        mode = st.radio("Mode", ["Stateless (OpenAI-style)", "Stateful (gateway memory)"])
        stateful = mode.startswith("Stateful")
        temperature = st.slider("temperature", 0.0, 2.0, 0.7, 0.1)
        max_tokens = st.number_input("max_tokens (0 = provider default)", 0, 32000, 0, 64)
        system_prompt = st.text_area("System prompt (stateless only)", "", height=80, disabled=stateful)

        if stateful:
            user_id = st.text_input("user_id", "console-user")
            if st.button("New conversation", width="stretch"):
                response = call("POST", "/v1/conversations", json={"user_id": user_id})
                if response is not None and response.status_code == 201:
                    st.session_state.conversation_id = response.json()["conversation_id"]
                    st.session_state.chat = []
                elif response is not None:
                    show_error(response)
            st.text_input("conversation_id", key="conversation_id")
            if st.button("Load history", width="stretch", disabled=not st.session_state.conversation_id):
                response = call("GET", f"/v1/conversations/{st.session_state.conversation_id}/messages")
                if response is not None and response.status_code == 200:
                    st.session_state.chat = [
                        {"role": m["role"], "content": m["content"], "meta": {"provider": m.get("model")}}
                        for m in response.json()
                    ]
                elif response is not None:
                    show_error(response)
            with st.expander("Add a long-term memory"):
                memory = st.text_input("Fact about this user", key="memory_text")
                if st.button("Save memory") and memory:
                    response = call("POST", "/v1/memories", json={"user_id": user_id, "content": memory})
                    if response is not None and response.status_code == 201:
                        st.success("Saved")
                    elif response is not None:
                        show_error(response)

        if st.button("Clear chat", width="stretch"):
            st.session_state.chat = []
            st.rerun()

    with left:
        for message in st.session_state.chat:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message.get("meta"):
                    st.caption(meta_line(message["meta"]))

        prompt = st.chat_input("Message the gateway…")
        if prompt:
            if stateful and not st.session_state.conversation_id:
                st.warning("Create or enter a conversation first.")
                st.stop()

            st.session_state.chat.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            if stateful:
                messages = [{"role": "user", "content": prompt}]
            else:
                history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat]
                messages = ([{"role": "system", "content": system_prompt}] if system_prompt.strip() else []) + history
            payload = {"model": model, "messages": messages, "stream": stream, "temperature": temperature}
            if max_tokens:
                payload["max_tokens"] = int(max_tokens)
            if stateful:
                payload["conversation_id"] = st.session_state.conversation_id

            meta: dict = {}
            started = time.perf_counter()
            with st.chat_message("assistant"):
                if stream:
                    holder: dict = {}

                    def events() -> Iterator[str]:
                        with http().stream(
                            "POST", f"{st.session_state.base_url}/v1/chat/completions",
                            json=payload, headers=_headers(),
                        ) as response:
                            holder["response"] = response
                            if response.status_code != 200:
                                response.read()
                                return
                            meta["request_id"] = response.headers.get("x-request-id")
                            for line in response.iter_lines():
                                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                                    continue
                                event = json.loads(line[5:])
                                if "ttft_ms" not in meta:
                                    meta["ttft_ms"] = int((time.perf_counter() - started) * 1000)
                                meta.update(provider=event.get("provider"), model=event.get("model"))
                                if event.get("usage"):
                                    meta["usage"] = event["usage"]
                                if event.get("delta"):
                                    yield event["delta"]

                    try:
                        content = st.write_stream(events())
                    except httpx.HTTPError as exc:
                        st.error(f"Request failed: {exc}")
                        content = None
                    response = holder.get("response")
                    if response is not None and response.status_code != 200:
                        show_error(response)
                        content = None
                else:
                    response = call("POST", "/v1/chat/completions", json=payload)
                    content = None
                    if response is not None and response.status_code == 200:
                        body = response.json()
                        content = body["choices"][0]["message"]["content"]
                        meta = {
                            "provider": body.get("provider"),
                            "model": body.get("model"),
                            "usage": body.get("usage"),
                            "request_id": response.headers.get("x-request-id"),
                            "cache": response.headers.get("x-cache"),
                        }
                        st.markdown(content)
                    elif response is not None:
                        show_error(response)

                if content is not None:
                    meta["latency_ms"] = int((time.perf_counter() - started) * 1000)
                    st.caption(meta_line(meta) + (f" · TTFT {meta['ttft_ms']} ms" if meta.get("ttft_ms") else ""))
                    st.session_state.chat.append({"role": "assistant", "content": content or "", "meta": meta})
                else:
                    st.session_state.chat.pop()  # keep history consistent with what the gateway stored

# ---- structured output -----------------------------------------------------

with tabs[1]:
    st.subheader("JSON / JSON-Schema output")
    st.caption("The gateway instructs the model, validates the reply, and asks it to repair invalid JSON before giving up with 502.")
    so_model = st.selectbox("Model", model_ids(), key="so_model")
    so_mode = st.radio("response_format", ["json_schema", "json_object"], horizontal=True)
    so_prompt = st.text_area("Prompt", "Name a city in India and its approximate population.")
    schema_text = st.text_area("JSON Schema", json.dumps(DEFAULT_SCHEMA, indent=2), height=200, disabled=so_mode != "json_schema")
    if st.button("Generate", type="primary", key="so_go"):
        response_format = {"type": so_mode}
        if so_mode == "json_schema":
            try:
                response_format["json_schema"] = {"name": "response", "schema": json.loads(schema_text)}
            except ValueError as exc:
                st.error(f"Schema is not valid JSON: {exc}")
                st.stop()
        response = call(
            "POST", "/v1/chat/completions",
            json={"model": so_model, "messages": [{"role": "user", "content": so_prompt}], "response_format": response_format},
        )
        if response is not None and response.status_code == 200:
            body = response.json()
            st.json(json.loads(body["choices"][0]["message"]["content"]))
            st.caption(meta_line({**body, "request_id": response.headers.get("x-request-id")}))
        elif response is not None:
            show_error(response)

# ---- embeddings ------------------------------------------------------------

with tabs[2]:
    st.subheader("Embeddings")
    models = model_ids()
    emb_model = st.selectbox("Model", models, index=models.index("embed") if "embed" in models else 0, key="emb_model")
    texts_raw = st.text_area("Inputs (one per line)", "The cat sat on the mat\nA kitten rested on the rug\nStock markets fell today")
    if st.button("Embed", type="primary"):
        texts = [t for t in texts_raw.splitlines() if t.strip()]
        response = call("POST", "/v1/embeddings", json={"model": emb_model, "input": texts})
        if response is not None and response.status_code == 200:
            body = response.json()
            vectors = [d["embedding"] for d in body["data"]]
            st.write(f"**{body['provider']} / {body['model']}** · {len(vectors)} vectors × {len(vectors[0])} dims · usage {body['usage']}")

            def cosine(a, b):
                dot = sum(x * y for x, y in zip(a, b))
                return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)

            st.write("Cosine similarity")
            labels = [t[:30] for t in texts]
            st.dataframe(
                {labels[j]: [round(cosine(vectors[i], vectors[j]), 3) for i in range(len(vectors))] for j in range(len(vectors))},
                hide_index=False,
            )
        elif response is not None:
            show_error(response)

# ---- admin: providers --------------------------------------------------------

with tabs[3]:
    st.subheader("Provider health (this gateway instance)")
    if not st.session_state.admin_token:
        st.info("Set the admin token in the sidebar.")
    else:
        st.button("Refresh", key="prov_refresh")
        response = call("GET", "/admin/providers", admin=True)
        if response is not None and response.status_code == 200:
            icon = {"closed": "🟢", "half_open": "🟡", "open": "🔴"}
            st.dataframe(
                [{**p, "state": f"{icon.get(p['state'], '')} {p['state']}"} for p in response.json()],
                width="stretch",
            )
        elif response is not None:
            show_error(response)

        st.subheader("Model registry")
        response = call("GET", "/admin/models", admin=True)
        if response is not None and response.status_code == 200:
            st.dataframe(
                [
                    {
                        "model": m["id"],
                        "type": m["type"],
                        "targets": " → ".join(f"{t['provider']}/{t['model']}" for t in m["targets"]),
                        "pricing (in/out per M)": ", ".join(
                            f"{t['pricing']['input_per_million']}/{t['pricing']['output_per_million']}"
                            for t in m["targets"] if t["pricing"]
                        ) or "—",
                    }
                    for m in response.json()
                ],
                width="stretch",
            )

# ---- admin: usage ----------------------------------------------------------

with tabs[4]:
    st.subheader("Usage & cost")
    if not st.session_state.admin_token:
        st.info("Set the admin token in the sidebar.")
    else:
        c1, c2 = st.columns(2)
        hours = c1.select_slider("Window (hours)", [1, 6, 24, 72, 168], value=24)
        group_by = c2.selectbox("Group by", ["provider", "model", "project", "api_key_id"])
        response = call("GET", "/admin/usage", admin=True, params={"hours": hours, "group_by": group_by})
        if response is not None and response.status_code == 200:
            body = response.json()
            total = body["total"]
            cols = st.columns(6)
            cols[0].metric("Requests", total["requests"])
            cols[1].metric("Errors", total["errors"])
            cols[2].metric("Fallbacks", total["fallbacks"])
            cols[3].metric("Cache hit rate", f"{total['cache_hit_rate']:.1%}")
            cols[4].metric("Tokens", f"{total['total_tokens']:,}")
            cols[5].metric("Cost (USD)", f"{total['cost_usd']:.6f}" if total["cost_usd"] is not None else "—")
            st.dataframe(body["groups"], width="stretch")
        elif response is not None:
            show_error(response)

# ---- admin: keys -----------------------------------------------------------

with tabs[5]:
    st.subheader("API keys")
    if not st.session_state.admin_token:
        st.info("Set the admin token in the sidebar.")
    else:
        with st.form("create_key"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Name", "console")
            project = c2.text_input("Project", "console")
            allowed = st.multiselect("Allowed models (empty = all)", model_ids())
            c3, c4, c5 = st.columns(3)
            rpm = c3.number_input("Requests/min (0 = none)", 0, 100000, 0)
            tpm = c4.number_input("Tokens/min (0 = none)", 0, 10_000_000, 0)
            budget = c5.number_input("Daily budget USD (0 = none)", 0.0, 100000.0, 0.0, 0.5)
            if st.form_submit_button("Create key", type="primary"):
                body = {"name": name, "project": project, "allowed_models": allowed or None}
                if rpm:
                    body["rpm_limit"] = int(rpm)
                if tpm:
                    body["tpm_limit"] = int(tpm)
                if budget:
                    body["daily_budget_usd"] = float(budget)
                response = call("POST", "/admin/keys", admin=True, json=body)
                if response is not None and response.status_code == 201:
                    st.session_state.new_key = response.json()["key"]
                elif response is not None:
                    show_error(response)

        if st.session_state.new_key:
            st.success("Key created - it is shown only once:")
            st.code(st.session_state.new_key)
            st.button("Use this key in the console", on_click=use_key, args=(st.session_state.new_key,))

        response = call("GET", "/admin/keys", admin=True)
        if response is not None and response.status_code == 200:
            keys = response.json()
            st.dataframe(keys, width="stretch")
            active = [k for k in keys if not k["revoked_at"]]
            if active:
                c1, c2 = st.columns([3, 1])
                to_revoke = c1.selectbox("Revoke", active, format_func=lambda k: f"{k['name']} ({k['key_prefix']}…, {k['id']})")
                if c2.button("Revoke", type="secondary", width="stretch"):
                    revoked = call("DELETE", f"/admin/keys/{to_revoke['id']}", admin=True)
                    if revoked is not None and revoked.status_code == 200:
                        st.rerun()
                    elif revoked is not None:
                        show_error(revoked)

        st.divider()
        if st.button("Clear response cache"):
            response = call("DELETE", "/admin/cache", admin=True)
            if response is not None and response.status_code == 200:
                st.success(f"Cleared {response.json()['cleared']} entries")

# ---- burst test --------------------------------------------------------------

with tabs[6]:
    st.subheader("Burst test")
    st.caption(
        "Fire concurrent requests to see rate limits (429), fallback, circuit breaking and caching in action. "
        "Use mock providers (loadtest/gateway.mock.toml) to avoid spending provider quota."
    )
    c1, c2, c3 = st.columns(3)
    burst_model = c1.selectbox("Model", model_ids(), key="burst_model")
    total_requests = c2.number_input("Requests", 1, 2000, 50)
    concurrency = c3.number_input("Concurrency", 1, 200, 10)
    unique = st.toggle("Unique prompts (off = identical prompts, exercises the cache)", value=True)
    burst_prompt = st.text_input("Prompt", "Say hello in one word.")

    if st.button("Run burst", type="primary"):
        base_url, headers, client = st.session_state.base_url, _headers(), http()

        def one(i: int):
            content = f"{burst_prompt} [{uuid.uuid4().hex[:8]}]" if unique else burst_prompt
            started = time.perf_counter()
            try:
                r = client.post(
                    f"{base_url}/v1/chat/completions",
                    json={"model": burst_model, "messages": [{"role": "user", "content": content}]},
                    headers=headers,
                )
                provider = r.json().get("provider") if r.status_code == 200 else None
                return r.status_code, provider, r.headers.get("x-cache"), time.perf_counter() - started
            except httpx.HTTPError as exc:
                return type(exc).__name__, None, None, time.perf_counter() - started

        progress = st.progress(0.0)
        results = []
        wall = time.perf_counter()
        with ThreadPoolExecutor(max_workers=int(concurrency)) as pool:
            for n, result in enumerate(pool.map(one, range(int(total_requests))), start=1):
                results.append(result)
                progress.progress(n / total_requests)
        wall = time.perf_counter() - wall

        latencies = sorted(r[3] * 1000 for r in results)
        q = statistics.quantiles(latencies, n=100, method="inclusive") if len(latencies) > 1 else [latencies[0]] * 99
        cols = st.columns(5)
        cols[0].metric("Throughput", f"{len(results) / wall:.1f} req/s")
        cols[1].metric("p50", f"{q[49]:.0f} ms")
        cols[2].metric("p95", f"{q[94]:.0f} ms")
        cols[3].metric("Success", f"{sum(1 for r in results if r[0] == 200) / len(results):.0%}")
        cols[4].metric("Cache hits", sum(1 for r in results if r[2] == "HIT"))
        c1, c2 = st.columns(2)
        c1.write("Status codes")
        c1.bar_chart({str(k): v for k, v in Counter(r[0] for r in results).items()})
        c2.write("Answered by provider")
        c2.bar_chart({str(k): v for k, v in Counter(r[1] for r in results if r[1]).items()} or {"none": 0})
