FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Hash-locked runtime dependencies (requirements.lock is compiled from
# requirements.txt for Linux/Python 3.11; see docs/OPERATIONS.md). This
# layer stays cached unless the lock changes.
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY app ./app
COPY gateway.toml .

# Unprivileged runtime user; /app/data holds the usage outbox (and the
# SQLite file when no DATABASE_URL is given) - mount a volume there.
RUN useradd --create-home --uid 10001 twinlm \
    && mkdir -p /app/data \
    && chown twinlm:twinlm /app/data
USER twinlm

ENV DATABASE_URL=sqlite:////app/data/twinlm.db \
    USAGE_SPOOL_PATH=/app/data/usage-spool.db

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status != 200)"

# Secrets (API keys, ADMIN_TOKEN, DATABASE_URL credentials) come from the
# environment at run time, never from the image - see .env.example.
# Access logs come from the gateway's own JSON middleware.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
