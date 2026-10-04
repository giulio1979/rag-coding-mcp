# syntax=docker/dockerfile:1

# --- ui build stage: compile the admin UI ------------------------------------
FROM node:20-bookworm-slim AS ui-build

WORKDIR /ui
COPY ui/package.json ui/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
    else npm install --no-audit --no-fund; fi
COPY ui/ ./
RUN npm run build

# --- build stage: resolve and install Python deps -----------------------------
FROM python:3.11-slim-bookworm AS build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && install -m 0755 /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

# Install deps first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then install the project itself.
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

# --- runtime stage ------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    RAG_DB_PATH=/data/rag.db \
    RAG_HOST=0.0.0.0 \
    RAG_PORT=8080

RUN groupadd --system --gid 1000 app \
 && useradd  --system --uid 1000 --gid app --home-dir /app --shell /sbin/nologin app \
 && mkdir -p /data \
 && chown -R app:app /data

COPY --from=build --chown=app:app /opt/venv /opt/venv
COPY --from=build --chown=app:app /app /app
COPY --from=ui-build --chown=app:app /ui/dist /app/src/rag_coding_mcp/ui_dist

WORKDIR /app
USER app

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
r=urllib.request.urlopen('http://127.0.0.1:8080/graphql',data=b'{\"query\":\"{ __typename }\"}',timeout=3); \
sys.exit(0 if r.status==200 else 1)" || exit 1

CMD ["python", "-m", "rag_coding_mcp"]
