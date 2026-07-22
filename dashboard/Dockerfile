FROM node:24.4.1-alpine AS js-test

WORKDIR /app
COPY components/live_state.js ./components/live_state.js
COPY tests_js ./tests_js
CMD ["node", "--test", "tests_js/live_state.test.js"]

FROM python:3.12.13-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

RUN groupadd --gid 10003 kindcare-dashboard \
    && useradd --uid 10003 --gid kindcare-dashboard --no-create-home kindcare-dashboard

COPY --chown=kindcare-dashboard:kindcare-dashboard . ./dashboard

FROM base AS test
RUN pip install --no-cache-dir -r dashboard/requirements-dev.lock \
    && mkdir -p /app/dashboard/.pytest_cache \
    && chown kindcare-dashboard:kindcare-dashboard /app/dashboard/.pytest_cache
USER 10003:10003
CMD ["python", "-m", "pytest", "-c", "dashboard/pytest.ini", "-q"]

FROM base AS production
USER 10003:10003
EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
