# Verification evidence, commands, assumptions, limitations

Audit date 2026-10-14, user timezone Asia/Calcutta. Workspace `D:\projects\TwinLM`. HEAD `a2a04efcc2a5390130c7a33ec2e1f0e9d1c7e7fa`; findings apply to the **working tree**, not that commit alone. Initial `git status --short` showed substantial preexisting staged/unstaged changes and untracked .github/, LICENSE, CODEBASE_WALKTHROUGH.md. Those are user work. No source/test/config fixes were made. Only audit reports are new. Final staging is limited to `git add audit/`; existing staging is retained.

## Evidence labels

**Verified:** directly inspected source/config or observed isolated check. **Inferred:** consequence logically suggested by source, not demonstrated in deployment. **Requires Validation:** unknown provider/runtime/traffic/operational behavior. High confidence applies to test output and reproduced local defects; production impact/capacity depends on assumptions. No coverage, CVE scan, CPU/memory utilization, query latency, image size, availability, restore success or current load capacity was measured.

## Commands and scope

| Command/check | Result / boundary |
|---|---|
| Get-Content on both supplied Pasted text.txt attachments | Read full request and development summary; summary location available, no clarification needed |
| `Get-ChildItem -Force`, `git ls-files`, `git status --short` | Inventoried working tree and tracked sources; many modifications existed before audit |
| `rg --files --hidden -g AGENTS.md -g CLAUDE.md -g '!.git' -g '!.venv'`; Test-Path/Get-Content on D:\AGENTS.md, D:\projects\AGENTS.md and workspace root guidance | No matching project/ancestor instruction files found; no delegation instruction |
| Numbered Get-Content and targeted rg of app/API/router/providers/gateway/database/memory/observability | File/function/line evidence in reports; no `.env` or existing DB row contents read |
| Reads of README, CODEBASE_WALKTHROUGH, docs, tests, CI, Docker/Compose, monitoring, UI, examples/demo/load harness | Compared historical claims/configured behavior to current source; large batched output sometimes truncated and targeted follow-up reads used |
| `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider` with explicit safe env | 198 passed, 0 failed, 0 skipped, 1 warning in 6.17s |
| `.venv\Scripts\python.exe -m pip check` | No broken requirements found; not a vulnerability scan |
| importlib.metadata/sys.version local inventory | Python 3.13.2; FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.5, SQLAlchemy 2.0.52, httpx 0.28.1, jsonschema 4.26.0, referencing 0.37.0, pytest 9.1.1, pytest-asyncio 1.4.0, prometheus-client 0.26.0 |
| AST parse all .py in app/tests/ui/loadtest/examples/scripts | 58 files parsed successfully; no bytecode output |
| `docker --version` | Docker CLI 29.6.2, build dfc4efb; not runtime verification |
| `docker compose --env-file .env.example config --quiet` | Exit 0, no service start or resolved secrets displayed |
| Report inventory, nonempty-file and relative Markdown-link checks | All 13 requested reports present, nonempty, with valid relative report links |
| `git rev-parse HEAD` | Baseline revision above |
| Isolated in-memory/fake-provider probes below | Confirmed F01/F02/F03/F07/F08/F09/F11; no actual network requests |
| Read installed jsonschema/validators.py | Default loader implementation uses urllib urlopen (`:108-132,238`) |
| Public documentation read via browser search tool | [Official jsonschema referencing documentation](https://python-jsonschema.readthedocs.io/en/stable/referencing/); supports explicit registry solution, no app account access |

Execution environment: first sandbox shell failed before process creation with helper_unknown_error/setup refresh errors. Read/check commands were retried through approved escalation. No auto-review rejection blocked the work. An rg wildcard `app/templates/*` failed under Windows path handling; directory search used subsequently. `rg --files` instruction search exit 1 means no matching files, not an application failure. No destructive test, cloud cost, external attack, infrastructure modification, commit/push/PR or unrelated staging performed.

Test isolation was enforced with process-level values:

```powershell
$env:DATABASE_URL='sqlite:///:memory:'
$env:AUTH_ENABLED='false'
$env:WEB_UI_ENABLED='true'
$env:PYTHONDONTWRITEBYTECODE='1'
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

These assignments apply to the shell subprocess; no `.env` file changed. conftest uses setdefault, so explicitly setting DATABASE_URL is important. Existing tests intentionally write temporary SQLite fixtures under tmp_path. No coverage option was enabled and the preexisting coverage file was not evidence.

## Exact local probe observations

| Probe | Setup | Observed output |
|---|---|---|
| F03 memory guardrail bypass | In-memory SQLite/StaticPool, TestClient DB/router/services overrides, FakeProvider capture, input secrets=block; recognized synthetic AWS-format token stored as memory | memory_status=201; chat_status=200; synthetic_secret_forwarded=true |
| F02 repeated request ID | Same isolated app; send two chat requests with X-Request-ID=duplicate-probe | statuses=[200,200]; stored request_logs rows for ID=1 |
| F02 batch rollback | Fresh in-memory schema; production UsageWriter._write called with duplicate IDs plus unrelated valid row | submitted=3; stored=0; dropped_counter=0 |
| F01 schema reference | validator_for `$ref=http://schema.invalid/probe`; urllib.request.urlopen replaced with an in-memory response, no socket access | intercepted_fetches=1; validated=true; network_used=false |
| F11 actual upstream 400 | OpenAICompatibleProvider AsyncClient MockTransport returns HTTP 400; execute under provider_errors | mapped HTTP status=502 |
| F07 mid-stream outage | FakeProvider unavailable_mid_stream after partial text; TestClient SSE | HTTP=200; log_status=success; stop_finish=true; done_sentinel=true |
| F08 post-redaction invalid schema | FakeProvider valid JSON with synthetic credential; schema const requires original; output secrets=redact | HTTP=200; returned_schema_valid=false |
| F09 missing TOML startup | Fresh subprocess cached settings gateway_config_path set to nonexistent-audit-probe.toml; no services/router dependency touched | health=200; ready=200; config_loaded=false |

No new tests were written into the suite. Probes were inline Python passed through PowerShell stdin; log output disabled to avoid value-bearing diagnostics. Private databases were created/dropped only in memory. The batch check directly exercised `_write`, proving transaction rollback independently of scheduling; a future integration regression should exercise the full queue under concurrent traffic as well.

## Reproduction snippets (offline, proposed for regression tests)

Run in a fresh process with safe env above. The following captures the unusual loader/accounting behavior without external I/O:

```python
import io, warnings, urllib.request
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database.database import Base
from app.database import models  # register tables
from app.gateway.usage import UsageWriter
from app.gateway.structured import validator_for, parse_and_validate

engine = create_engine('sqlite:///:memory:',
    connect_args={'check_same_thread': False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine)
row = dict(id='same-id', endpoint='chat', requested_model='auto',
    status='success', http_status=200, latency_ms=1, stream=False,
    fallback_used=False, cache_hit=False)
writer = UsageWriter(factory)
writer._write([row, row, {**row, 'id': 'unrelated-valid'}])
with factory() as session:
    print(session.execute(text('SELECT count(*) FROM request_logs')).scalar())
# Current output: 0. A future corrected implementation should preserve valid events.

retrieval = []
def intercept(request, *args, **kwargs):
    retrieval.append(request)
    return io.BytesIO(b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}')
with patch.object(urllib.request, 'urlopen', intercept), warnings.catch_warnings():
    warnings.simplefilter('ignore')
    v = validator_for({'$ref': 'http://schema.invalid/probe'})
    print(parse_and_validate('{}', v), len(retrieval))
# Current: normalized {}, no validation error, one intercepted loader call.
engine.dispose()
```

API probes use the existing fixture pattern (`tests/conftest.py:140`) with override_get_db yielding a fresh session, router containing FakeProvider, services from build_services. To reproduce F03, create a conversation, POST a synthetic recognized credential through /v1/memories, capture provider messages on the next stateful chat under secrets=block. For F07, use FakeProvider(behavior='unavailable_mid_stream', chunks=['partial']) and compare SSE terminal fields with RequestLog.status. For F08 use a fake JSON reply containing a credential and a schema const for that value, turn on output redaction, then validate the **returned** content. F09 must run in a fresh process with WEB_UI_ENABLED=false and no service cache, to avoid previous test wiring masking lazy initialization.

## Assumptions and exclusions

Owner requirements question was raised during work: users/trust model, expected concurrency, latency/availability, hosting/monthly budget and sensitivity. No answer was available when drafting. Conditional target assumes small private pilot, one gateway worker, no regulated data, PostgreSQL available, operator access private. Hard-budget and public/shared UI decisions remain launch gates. Historical perf/Compose/SDK reports are documentation evidence only; no fresh operational proof. Live Gemini tools and other compatible providers, PostgreSQL runtime, image build, GHCR workflow, provider prices and production data volume are Requires Validation.

Public documentation lookup was limited to schema-library behavior. Current provider pricing/model availability was deliberately not asserted or researched as a verified product recommendation; config prices are examples. All capacity/HA claims remain conditional. Source review included all application module families and test/config/deployment surfaces; generated dependencies, real secrets, binary databases, and .git internals were not audited. Repository versions can change after this snapshot; line references describe this working tree.
