FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY workers/requirements*.txt workers/requirements*.lock ./workers/
RUN pip install --no-cache-dir -r workers/requirements.lock

RUN groupadd --gid 10002 kindcare-worker \
    && useradd --uid 10002 --gid kindcare-worker --no-create-home kindcare-worker

COPY --chown=kindcare-worker:kindcare-worker analysis ./analysis
COPY --chown=kindcare-worker:kindcare-worker workers ./workers

FROM base AS test
COPY backend/requirements*.txt backend/requirements*.lock ./backend/
RUN pip install --no-cache-dir -r workers/requirements-dev.lock \
    -r backend/requirements-dev.lock \
    && mkdir -p /app/workers/.pytest_cache \
    && chown kindcare-worker:kindcare-worker /app/workers/.pytest_cache
COPY --chown=kindcare-worker:kindcare-worker backend/app ./app
USER 10002:10002
CMD ["python", "-m", "pytest", "-m", "not integration", "-q"]

FROM base AS production
USER 10002:10002
CMD ["celery", "-A", "workers.celery_app:celery_app", "worker", "--loglevel=INFO", "--without-gossip", "--without-mingle"]
