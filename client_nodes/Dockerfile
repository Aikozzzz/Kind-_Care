FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY client_nodes/requirements*.txt client_nodes/requirements*.lock ./client_nodes/
RUN pip install --no-cache-dir -r client_nodes/requirements.lock

COPY client_nodes ./client_nodes

FROM base AS test
RUN pip install --no-cache-dir -r client_nodes/requirements-dev.lock
CMD ["python", "-m", "pytest", "-c", "client_nodes/pytest.ini", "-q"]

FROM base AS production
ENTRYPOINT ["python", "-m", "client_nodes.simulator"]
