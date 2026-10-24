FROM python:3.12-slim-bookworm
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src src
RUN --mount=type=cache,target=/root/.cache/pip pip install '.[worker]' && useradd -r -u 10001 worker && mkdir -p /artifacts /data/sec && chown worker:worker /artifacts /data/sec
COPY tests/fixtures/sec /fixtures/sec
USER 10001
ENTRYPOINT ["python","-m","finreceipts.platform.worker"]
