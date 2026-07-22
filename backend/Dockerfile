FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

RUN groupadd --gid 10001 kindcare \
    && useradd --uid 10001 --gid kindcare --no-create-home kindcare

COPY --chown=kindcare:kindcare . .

FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.lock \
    && mkdir -p /app/.pytest_cache \
    && chown kindcare:kindcare /app/.pytest_cache
USER 10001:10001
CMD ["python", "-m", "pytest", "-m", "not integration", "-q"]

FROM base AS production
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
