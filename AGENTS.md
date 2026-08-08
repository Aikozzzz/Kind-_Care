# KindCare Agent Guide

## Purpose

KindCare is a Python 3.12 academic distributed elderly-care monitoring MVP. Preserve
its core flow: independent HTTP/MQTT nodes submit validated events, FastAPI reserves
identity and publishes telemetry, RabbitMQ/Celery process events, MongoDB stores
history/state, and Streamlit helps a caregiver inspect and transition records.

This file describes the current repository. `AGENTS1stver.md` is archived historical
guidance and is not authoritative.

## Safety And Trust Boundary

- Protected HTTP and WebSocket routes require bearer account authentication and an
  explicit account-to-resident relationship. `/health` remains data-free and public.
- Compose publishes interfaces only on `127.0.0.1`. Never expose this release or use
  real personal/medical data.
- RabbitMQ uses a non-guest local account. Compose directly interpolates
  `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` into AMQP URLs, so both must
  match URI-unreserved `[A-Za-z0-9._~-]+`; never claim raw reserved characters work.
  Mosquitto requires separate shared demo credentials.
- Root `.env.example` is the canonical Compose override/default list; `.env` must not
  be committed or included in Docker context.
- `.superpowers/` is local agent state, excluded by Git and the release Docker context;
  do not treat it as release documentation or source.
- `backend/.env.example` is backend-only direct-run configuration. Do not put worker,
  dashboard, broker, or MQTT settings there.
- No TLS, production secret management, credential rotation, or per-device MQTT ACL
  is implemented. Optional Telegram polling and alert outbox delivery are local-MVP
  integrations; do not imply clinical or production-grade messaging guarantees.

## Current Eight-Service Stack

| Service | Current role |
| --- | --- |
| `backend` | FastAPI REST/WebSocket, validation, idempotency reservations, synchronous profile/reminder/alert operations, migrations/indexes |
| `worker` | Celery health/activity/device processing and scanner task execution |
| `beat` | Celery scanner and service-heartbeat scheduling |
| `dashboard` | Streamlit REST views/actions and browser-owned WebSocket live panel |
| `mqtt-ingestor` | Persistent QoS 1 MQTT-to-HTTP adapter with bounded backpressure |
| `mongodb` | Single-node `rs0` transaction-capable database |
| `rabbitmq` | Celery broker and local management interface |
| `mosquitto` | Authenticated persistent MQTT 3.1.1 broker |

The optional Compose `telegram` profile adds `telegram-bot`, an outbound polling
adapter. It has no inbound public port and is disabled unless explicitly configured.

FastAPI remains the validation/reservation/publication authority. MQTT ingestor must
not import backend business services or access MongoDB. Workers remain risk and
processed-state authorities. Backend startup owns migrations and dependent indexes;
Compose gates Worker and Beat on healthy Backend.

## Configuration Rules

- Root `.env.example` must cover every user-overridable Compose substitution/default.
- `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` are embedded directly in AMQP
  URLs. Require URI-unreserved `[A-Za-z0-9._~-]+` values; raw reserved characters,
  percent escapes, whitespace, and empty values are unsupported.
- Keep internal service URLs distinct from host/browser URLs. Never put Compose-only
  `backend`, `mongodb`, `rabbitmq`, or `mosquitto` names into browser instructions.
- Keep `.env`, `.superpowers/`, caches, VCS/tool state, logs, and local database dumps
  out of release build context.

## Public Routes

```text
GET    /health

POST   /api/auth/bootstrap
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me
POST   /api/auth/accounts
POST   /api/auth/websocket-ticket/{elderly_id}
POST   /api/relationships
GET    /api/relationships
PATCH  /api/relationships/{relationship_id}
DELETE /api/relationships/{relationship_id}
GET    /api/relationships/mine
POST   /api/access-requests
GET    /api/access-requests
POST   /api/access-requests/{request_id}/approve

POST   /api/elderly
GET    /api/elderly
GET    /api/elderly/{elderly_id}
PATCH  /api/elderly/{elderly_id}
DELETE /api/elderly/{elderly_id}
POST   /api/elderly/{elderly_id}/restore

POST   /api/health
GET    /api/health/{elderly_id}
POST   /api/activity
GET    /api/activity/{elderly_id}
POST   /api/device-status
GET    /api/device-status/{elderly_id}

POST   /api/reminders
GET    /api/reminders/{elderly_id}
PATCH  /api/reminders/{reminder_id}

GET    /api/alerts/{elderly_id}
PATCH  /api/alerts/{alert_id}

GET    /api/dashboard/{elderly_id}
WS     /ws/dashboard/{elderly_id}

POST   /api/telegram/link
POST   /api/telegram/admin/link/{account_id}
GET    /api/telegram/admin/bindings
DELETE /api/telegram/admin/bindings/{telegram_user_id}
POST   /api/telegram/unlink
POST   /api/telegram/bind
POST   /api/telegram/status
POST   /api/telegram/request
```

Alerts are created by health processing and activity/device/reminder scanners, not a
public write route. Reminder taken requires body owner plus path reminder ID. Alert
updates only acknowledge or resolve under the implemented lifecycle/source guards.

## Transport And Consistency Rules

- Health, activity, device, and reminder creation require a 1-128 visible-ASCII
  `Idempotency-Key`. Reuse it only for an equivalent logical request.
- Health/activity/device reserve key hash, payload hash, deterministic ID, and server
  timestamp in MongoDB before confirmed Celery publication. Same-key changed content
  must remain `409`.
- Raw keys are never stored. Reservations intentionally have no TTL while processed
  event identities are retained.
- Worker redelivery must remain safe through unique identity, canonical payload hash,
  conditional state ordering, and transactions.
- Client `recorded_at` is observation metadata. Activity/device monitoring safety uses
  reserved server `received_at` and `(received_at,event_id)` ordering.
- Scanner queries remain index-aligned and batch-bounded. Renew lease ownership in
  every candidate transaction and stop after lease loss.
- Recovery events/actions resolve source episode alerts transactionally. Manual alert
  resolution must reject a still-active inactivity, offline, or missed-reminder source.
- WebSocket heartbeat liveness must not advance successful summary freshness.
- MQTT is QoS 1 at-least-once transport. Preserve stable persistent session, manual
  acknowledgement, nonblocking callback queue, transient HTTP retry classification,
  and backend-authoritative idempotency.

## Source Layout

```text
backend/app/
  models/       Pydantic requests/responses
  routes/       Thin FastAPI route adapters
  services/     Storage/orchestration and transitions
  database.py   Startup migrations, validator, authoritative indexes
  websocket.py  Socket protocol adapter
workers/        Celery tasks, state/scanners, worker health, sync database access
analysis/       Pure deterministic health risk rules
client_nodes/   HTTP/MQTT clients and scenario simulator
mqtt_ingestor/  Topic routing, HTTP bridge, Paho lifecycle/backpressure/health
dashboard/      Streamlit app, summary rendering, live browser component
telegram_bot/   Optional outbound Telegram polling adapter
mosquitto/      Broker config and credential-generating entrypoint
```

## Code Rules

- Read current code and tests before changing behavior. Reliability changes may have
  updated contracts more recently than historical plans.
- Make the smallest correct change. Keep routes thin and business logic in services
  or workers.
- Use Pydantic for public validation and UTC-aware datetimes. Reject unknown telemetry
  write fields.
- Preserve deterministic ordering and exact unique/index names; query changes must be
  checked against `backend/app/database.py` and `workers/database.py`.
- Keep transaction callbacks retry-safe. Do not perform irreversible external side
  effects inside MongoDB transaction callbacks.
- Keep client retry bounded and reuse the same key/body for one logical operation.
- Do not add dependencies without a concrete need. Update direct requirements and
  regenerate the component lock file together.
- Never log medical payloads, raw MQTT topics, elderly/reminder IDs, idempotency keys,
  Telegram bot tokens, Telegram chat/user IDs, access tokens, or credentials from any
  bridge/bot.
- Preserve loopback host bindings and internal-only database/AMQP networking unless a
  separately reviewed security design changes the trust model.
- Treat `DESIGN.md` as user-supplied upstream visual reference. Do not rewrite it.
  `docs/dashboard-design.md` is the canonical KindCare adaptation.

## Test Rules

- Add or update a focused test before behavior changes and observe the expected
  failure before implementation.
- Run lightweight suites for every affected component. Test targets exist for
  backend, workers, client nodes, dashboard Python, dashboard JavaScript, and MQTT
  ingestor.
- Keep integration tests marker-separated. Backend/worker integration use guarded
  dedicated `kindcare_integration_test`; MQTT integration uses unique identities and
  guarded per-identity/session cleanup in `kindcare_db`.
- Never weaken database cleanup guards or point integration cleanup at arbitrary
  database names.
- Documentation contracts should parse structure or runnable examples where useful;
  avoid large collections of brittle unrelated substring assertions.
- Validate Compose with `docker compose config --quiet` after environment/Compose
  changes. Do not overwrite an existing user `.env` during tests.
- Production and test images install pinned `.lock` files. Direct dependencies remain
  in each component's `requirements.txt`/`requirements-dev.txt`.

Canonical commands are maintained in `README.md`. Keep those commands runnable in
Windows PowerShell.

## Documentation Rules

When behavior changes, update all affected contracts:

- `README.md`: clean-clone operations, configuration boundaries, tests, troubleshooting.
- `docs/api-documentation.md`: every HTTP/WS/MQTT request, response, status, and limit.
- `docs/database-design.md`: collections, ownership, indexes, validators, retention,
  migrations, and transactions.
- `docs/architecture.md`: ownership, flows, consistency, scanners, failures, trust,
  and test topology.
- `docs/dashboard-design.md`: visual behavior and intentional upstream adaptations.

Examples must be non-empty, valid JSON/PowerShell, owner-bound where required, and
consistent with current models. Do not document planned functionality as current.

## Definition Of Done

- Relevant lightweight tests pass.
- Required integration or documentation-contract tests pass, or an external blocker
  is reported with exact evidence.
- `docker compose config --quiet` passes with defaults and safe example values.
- New environment substitutions appear in root `.env.example` and README descriptions.
- API, database, architecture, dashboard design, and agent guidance remain mutually
  consistent with code.
