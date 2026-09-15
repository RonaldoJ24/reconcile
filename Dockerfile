FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
RUN pnpm install --frozen-lockfile && pnpm run build

FROM ghcr.io/astral-sh/uv:0.11.7-python3.11-trixie-slim AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app/backend/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RECONCILE_MODE=preview \
    RECONCILE_RANKER_MODE=shadow \
    RECONCILE_LLM_ENABLED=0 \
    RECONCILE_LLM_MODEL=deepseek-flash \
    RECONCILE_LLM_EXECUTION_BUDGET_USD=0 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/src/reconcile/__init__.py ./backend/src/reconcile/__init__.py
COPY backend/src/reconcile/web.py ./backend/src/reconcile/web.py
COPY backend/src/reconcile/api ./backend/src/reconcile/api
COPY backend/src/reconcile/domain ./backend/src/reconcile/domain
COPY backend/src/reconcile/ingest ./backend/src/reconcile/ingest
COPY backend/src/reconcile/interpretation ./backend/src/reconcile/interpretation
COPY backend/src/reconcile/jobs ./backend/src/reconcile/jobs
COPY backend/src/reconcile/persistence ./backend/src/reconcile/persistence
COPY backend/src/reconcile/ml/__init__.py backend/src/reconcile/ml/artifact.py backend/src/reconcile/ml/features.py backend/src/reconcile/ml/runtime.py ./backend/src/reconcile/ml/
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/alembic ./backend/alembic
COPY artifacts/ranker-ml-v1/model.pkl artifacts/ranker-ml-v1/metadata.json ./artifacts/ranker-ml-v1/
COPY deploy/runtime-allowlist.txt ./deploy/runtime-allowlist.txt
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

CMD ["python", "-m", "reconcile.web"]
