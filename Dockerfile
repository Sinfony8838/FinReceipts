# FinReceipts API demo image. No secrets: pass keys via env / compose env_file.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FINRECEIPTS_LLM_PROVIDER=fake \
    FINRECEIPTS_OFFLINE=1 \
    FINRECEIPTS_CACHE_DIR=/app/.cache/sec \
    FINRECEIPTS_HOST=0.0.0.0 \
    FINRECEIPTS_PORT=8000

COPY pyproject.toml README.md LICENSE DISCLAIMER.md ./
COPY src ./src
COPY examples ./examples
COPY docs ./docs
# Seed offline SEC replay cache with recorded fixtures (AAPL, NVDA, tickers map).
COPY tests/fixtures/sec /app/.cache/sec

RUN pip install --no-cache-dir -e ".[web]" \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "-m", "finreceipts.app"]
