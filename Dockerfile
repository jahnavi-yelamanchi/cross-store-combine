# syntax=docker/dockerfile:1.7
# xstore image: proposals API (default), CRM stub, Dagster code location.

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd --system --gid 10001 xstore \
    && useradd --system --uid 10001 --gid xstore --home-dir /app --shell /usr/sbin/nologin xstore \
    && mkdir -p /data && chown xstore:xstore /data
WORKDIR /app
COPY --from=builder --chown=xstore:xstore /app /app
COPY --chown=xstore:xstore policy ./policy
COPY --chown=xstore:xstore stores ./stores
COPY --chown=xstore:xstore cdc ./cdc
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    STORE_BACKEND=local \
    MODEL_PROVIDER=mock \
    PORT=8040 \
    HEALTH_PATH=/healthz
USER 10001:10001
EXPOSE 8040
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request as u;u.urlopen('http://127.0.0.1:'+os.environ['PORT']+os.environ['HEALTH_PATH'],timeout=2)"]
CMD ["sh", "-c", "exec uvicorn xstore.proposals.app:app --host 0.0.0.0 --port \"$PORT\""]

# Dagster webserver/daemon: runtime plus the UI package pinned to the locked dagster version.
FROM builder AS dagster-builder
ARG DAGSTER_WEBSERVER_VERSION=1.13.28
RUN uv pip install --python /app/.venv/bin/python "dagster-webserver==${DAGSTER_WEBSERVER_VERSION}"

FROM runtime AS dagster
COPY --from=dagster-builder --chown=xstore:xstore /app/.venv /app/.venv
ENV DAGSTER_HOME=/data/dagster PORT=3000 HEALTH_PATH=/server_info
USER root
RUN mkdir -p /data/dagster && touch /data/dagster/dagster.yaml && chown -R xstore:xstore /data/dagster
USER 10001:10001
EXPOSE 3000
CMD ["sh", "-c", "exec dagster-webserver -h 0.0.0.0 -p \"$PORT\" -m xstore.orchestration.definitions"]
