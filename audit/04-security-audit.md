# Defensive security audit

No real secrets were printed, no `.env` values or stored application data were read, and no external attacks/provider calls occurred. Findings distinguish verified execution from inferred reachability/impact. No confirmed Critical finding; High findings below are launch gates in their stated threat model. Likelihood and business impact depend on deployment/users/data sensitivity.

## F01 — High: untrusted JSON Schema reference retrieval (P0)

**Verified, high confidence.** `app/gateway/structured.py:25-33` constructs Draft202012Validator from caller schemas without an explicit registry; `:60` evaluates refs synchronously. Installed jsonschema 4.26.0 `validators.py:108-132,238` uses a default remote retriever invoking urllib.request.urlopen. A harmless `$ref` to a `.invalid` host was resolved through a monkeypatched loader: **one intercepted fetch, successful validation, zero actual network access**. This is an authenticated SSRF primitive (unauthenticated when auth disabled), not a demonstration of credential theft. Actual private-network reachability remains untested.

Impact: requests to internal destinations and unbounded blocking I/O on the event loop; references/schema complexity can also consume CPU. Fix: explicit offline `referencing.Registry`, support local `$defs` and preloaded resources only, reject external `$ref`/`$dynamicRef`, bound schema nesting/size/validation work, and safely map reference errors. Do not rely on deprecation warnings or network egress alone. The library documents custom registries and warns about untrusted schema retrieval: [JSON Schema referencing documentation](https://python-jsonschema.readthedocs.io/en/stable/referencing/). Actual installed default behavior was verified locally rather than inferred from general documentation.

## F02 — High: caller correlation ID controls usage primary key (P0)

**Verified, high confidence.** `observability.py:94-96` accepts any sane X-Request-ID. `usage.py:97` stores it in `RequestLog.id`, a primary key (`models.py:101`). Two fake-provider requests using the same ID returned 200 with only one stored log. Production `_write` with two duplicate IDs plus an unrelated valid row stored **zero of three**, and `writer.dropped` stayed zero. A caller can corrupt accounting availability for unrelated requests in the same batch; this does not modify other tenants' conversations. Fix server-generated execution IDs, separate correlation column, independent bad-row isolation and visible/recoverable failures. Idempotency must be a separate authenticated contract, not inferred from X-Request-ID.

## F03 — High: stored memory bypasses outbound guardrails (P0)

**Verified, high confidence.** `memories.py:31-39` stores arbitrary text without guardrails. Stateful `_prepare_turn` checks only latest user message (`chat.py:466`), then `_load_context_messages` loads facts (`:488`) and `build_context` elevates them into system content (`context_builder.py:42-46`). A synthetic recognized credential stored through memory was forwarded to a fake provider under secrets=block; both requests succeeded. No real credential was used.

Fix validate fact writes, check the complete outbound request (summary/history/memory/schema/tools) and distinguish untrusted context from instructions. Define acceptable transformations and limits; preserve role boundaries where possible. Stored cross-project leakage was not observed: scoping tests pass. Within-project fact injection affects other consumers intentionally sharing the same user_id, requiring a trust decision.

## F04/F05 — High: accounting loss and ineffective hard spend controls

**Verified code, high confidence; financial exposure inferred.** `_write` discards database exceptions; queue overflow drops rows; `sum_cost_since` sums persisted non-null costs (`usage.py:155-161,203-209`, `repository.py:190`). Admission has no pending reservation (`common.py:81`). Concurrent requests can all pass before any finishes; queue lag/backlog/outage further expands exposure. Unlimited/unpriced models and Gemini embedding unknown usage do not contribute to cost sums. This is advisory accounting, not a hard financial limit. Fix unique ledger events, reliable persistence, reservations, conservative unknown-price behavior and upstream account limits. Do not promise a fixed 0.5-second exposure bound.

## F10 — High if exposed: operator console privilege and URL forwarding

**Verified configuration; no external exploit performed.** Compose injects global ADMIN_TOKEN into Streamlit (`docker-compose.yml:69-73`). Every new session initializes it (`ui/streamlit_app.py:35-43`), can mint a key (`:58-70`), and can change base_url (`:77`) used in server-side HTTP calls with that token (`:101-104`). A reachable console grants admin capabilities and can forward credentials to a caller-selected destination/perform internal HTTP access. Loopback default is an effective mitigation for the default local topology; changing UI_BIND to public is unsafe. Keep private, allowlist the gateway origin, remove token inheritance for shared use, and authenticate/authorize operators. This is distinct from the gateway's trusted TOML base_url setting.

Built-in web UI has no authenticate dependency and passes principal=None (`app/web/routes.py:send_message_ui`, `send_message_stream_ui`). Enabling it with auth on still allows public inference in the default project and bypasses per-key limits. Off by default. Require an authenticated perimeter or enforce exclusive local-only mode. Cross-site form submission is also relevant if this unauthenticated UI is enabled; API bearer authentication does not protect those routes.

## Additional findings

| Severity | Evidence/status | Risk and practical recommendation |
|---|---|---|
| Medium | `main.py:77-81` includes jsonable_encoder(exc.errors()); Verified source | Pydantic error entries can include offending `input`, echoed into responses. Return curated validation fields; clients may log errors. No separate secret leak to another caller demonstrated |
| Medium | `structured.py:60-63`, `chat.py:225,228`; Verified source | Schema error.message can contain generated values and is logged/returned; DB usage error strings do not pass log redactor. Minimize value-bearing errors and redact persisted diagnostics |
| Medium | `guardrails.py:175-213`, `chat.py:137,379`; Verified | Tool arguments only count for size/cache sensitivity, not block/redaction; tool definitions/schema/image URLs unscanned. Stream output arrives before checks. Define and test a complete payload policy; disable streaming for strict blocking workflows |
| Medium | `schemas/chat.py:88-99`, `memories.py:16`, `context_builder.py:42`; Verified absence of bounds | No complete body/output/memory quota; authenticated resource exhaustion. Add byte/count limits and in-flight admission |
| Medium | `docker-compose.yml:27-30,43-45,65-66`; Verified | All generated secrets readable through common mounted volume; UI reads DB password too. Separate service-specific secrets and least-privilege mounts |
| Medium | `requirements.txt:1-17`, `.github/workflows/ci.yml:13-18`; Verified | Unpinned dependencies/actions, no vulnerability scan. Lock/hash builds, scan runtime+UI+images and pin releases. No CVE was confirmed by this audit |
| Medium | `docker-compose.yml:52-53`, Dockerfile CMD; Verified | Gateway HTTP has no built-in TLS ingress. Keep loopback/private or add TLS termination; restrict internal metrics/admin separately |
| Low/conditional | `main.py:111`, Grafana anonymous Viewer; Verified | Metrics expose models/activity; anonymous dashboard exposes usage. Internal-only access and bounded model labels |
| Low/conditional | `auth.py:105`, `models.py:75`; Verified | Global static admin token, no key expiry/roles. Adequate single operator primitive; add rotation and separate roles only if required |

## OWASP coverage and non-findings

Authentication: entropy and hash storage appropriate for random keys; SHA-256 is not recommended here for human passwords, which this app does not support. Admin comparison uses constant-time hmac. No refresh-token/JWT-login implementation to audit. Authorization: project isolation verified by tests; keys within a project have shared access to arbitrary supplied user_id. If tenant means individual end user, isolation is incomplete; otherwise document project as the boundary. Alias allowlists do not constrain resolved targets after operator config changes.

SQL injection: ORM bound filters and static group_by map; startup DDL interpolation uses constant internal identifiers. No attacker-controlled raw SQL path found. Command injection/unsafe deserialization: no client-driven shell execution, pickle or eval found. Docker shell commands interpolate trusted deployment variables/secrets, not request data. File uploads: no upload endpoint; image data/URLs are JSON payloads. Gateway does not itself fetch remote image URLs in adapters, though downstream providers may; native Gemini accepts data URLs. Gateway schema refs and Streamlit URL are the identified SSRF surfaces.

XSS: `app/web/formatting.py:render_message` uses nh3 allowlists and safe link relation; 12 formatting tests cover scripts/handlers/javascript links. This supports tested cases, not exhaustive browser security. CORS: no permissive CORS middleware found; absence does not secure server-side calls or web forms. CSRF: bearer API uses no ambient auth cookie; unauthenticated form UI requires separate protection/perimeter. Container: gateway/UI non-root; no privileged/host-network setting found. Persistence: prompt text and memories are stored in clear application-level form; no retention/encryption-at-rest/restore policy verified. Encrypt disks/backups and agree provider data processing before sensitive use.
