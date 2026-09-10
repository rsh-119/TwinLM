FROM python:3.11-slim

WORKDIR /app

# Install dependencies first so this layer is cached unless requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# Configuration (API keys, DATABASE_URL, ...) is supplied at run time via
# --env-file or -e, never baked into the image - see .env.example.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
