FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY mqtt_ingestor/requirements.txt mqtt_ingestor/requirements.lock ./mqtt_ingestor/
RUN pip install --no-cache-dir -r mqtt_ingestor/requirements.lock

RUN groupadd --gid 10001 kindcare \
    && useradd --uid 10001 --gid kindcare --no-create-home kindcare

COPY --chown=kindcare:kindcare mqtt_ingestor ./mqtt_ingestor

FROM base AS test
COPY --chown=kindcare:kindcare docker-compose.yml .gitignore ./
COPY --chown=kindcare:kindcare .env.example .dockerignore AGENTS.md ./
COPY --chown=kindcare:kindcare README.md ./README.md
COPY --chown=kindcare:kindcare docs ./docs
COPY --chown=kindcare:kindcare backend/.env.example ./backend/.env.example
COPY --chown=kindcare:kindcare mosquitto ./mosquitto
RUN pip install --no-cache-dir -r mqtt_ingestor/requirements-dev.lock \
    && mkdir -p /app/mqtt_ingestor/.pytest_cache \
    && chown kindcare:kindcare /app/mqtt_ingestor/.pytest_cache
USER 10001:10001
CMD ["python", "-m", "pytest", "-c", "mqtt_ingestor/pytest.ini", "-m", "not integration", "-q"]

FROM base AS production
USER 10001:10001
CMD ["python", "-m", "mqtt_ingestor"]
