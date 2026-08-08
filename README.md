# KindCare

KindCare is an academic distributed elderly-care monitoring MVP. HTTP or MQTT
client nodes submit health, activity, device, and reminder events; FastAPI validates
and reserves event identity; RabbitMQ and Celery process telemetry; MongoDB stores
profiles and monitoring state; and a Streamlit dashboard presents caregiver views.

This release is a local demonstration, not a clinical or production system. Protected
API and dashboard data require account authentication and resident relationships.
Telegram is an optional third-party channel. Do not expose the stack publicly or use
real personal or medical data.

## Clean-Clone Quick Start

### Prerequisites

- Docker Desktop with Docker Compose v2 and the Linux container engine running.
- Windows PowerShell 5.1 or PowerShell 7.
- Free loopback ports `8000`, `8501`, `1883`, and `15672`.
- At least 4 GB of memory available to Docker for the eight default services.

From the repository root, optionally create a local Compose environment file:

```powershell
Copy-Item .env.example .env
```

The checked-in defaults work without `.env`. If you create it, change the demo
RabbitMQ and MQTT passwords before sharing the machine. `.env` is ignored by both
Git and the root Docker build context.

For a clean database, set a local-only bootstrap secret in `.env` before starting,
for example `AUTH_BOOTSTRAP_SECRET=local_bootstrap_only`. Never commit this value.

Compose interpolates `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` directly
into AMQP URLs. Both values must contain only URI-unreserved
`[A-Za-z0-9._~-]+` characters. Do not use raw URI-reserved characters, percent
escapes, whitespace, or empty values; this Compose file does not URL-encode them.

Build, start, and wait for every default service to become healthy:

```powershell
docker compose up --build -d --wait
```

Even after Compose reports healthy, use this bounded API readiness check before
creating data. It prints the successful health envelope or throws after two minutes:

```powershell
$deadline = (Get-Date).AddMinutes(2)
$health = $null
do {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
        if ($health.success -and $health.data.status -eq "healthy") {
            break
        }
    }
    catch {
        $health = $null
    }

    if ((Get-Date) -ge $deadline) {
        docker compose ps
        throw "KindCare API did not become healthy within two minutes."
    }
    Start-Sleep -Seconds 2
} while ($true)
$health | ConvertTo-Json -Depth 4
```

Expected HTTP `200` data:

```json
{
  "success": true,
  "message": "KindCare API is healthy",
  "data": {
    "status": "healthy",
    "mongodb": "available",
    "rabbitmq": "available"
  }
}
```

### Create The First Administrator

The first account is created once using the bootstrap header. Store the response token
in the current PowerShell session only:

```powershell
$bootstrapSecret = "local_bootstrap_only"
$adminBody = @{
    login_name = "admin"
    display_name = "Demo Administrator"
    password = "ChangeThisDemoPassword123!"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/auth/bootstrap" `
    -Headers @{ "X-Bootstrap-Secret" = $bootstrapSecret } `
    -ContentType "application/json" -Body $adminBody

$loginBody = @{ login_name = "admin"; password = "ChangeThisDemoPassword123!" } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/auth/login" `
    -ContentType "application/json" -Body $loginBody
$authHeaders = @{ Authorization = "Bearer $($login.data.access_token)" }
```

If the administrator already exists, skip bootstrap and run only the login portion.

Bootstrap the active `E001` demo profile before opening the dashboard or running
either simulator:

```powershell
$profileBody = @{
    elderly_id = "E001"
    full_name = "Margaret Lee"
    date_of_birth = "1948-04-12"
    phone_number = "555-0101"
    address = "10 Garden Road"
    emergency_contact_name = "Daniel Lee"
    emergency_contact_phone = "555-0199"
    medical_notes = "Demo data only"
} | ConvertTo-Json

$profile = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/elderly" `
    -Headers $authHeaders -ContentType "application/json" -Body $profileBody -TimeoutSec 10
$profile | ConvertTo-Json -Depth 4
```

A clean database returns HTTP `201 Created` and this shape (timestamps vary):

```json
{
  "success": true,
  "message": "Elderly profile created successfully",
  "data": {
    "elderly_id": "E001",
    "full_name": "Margaret Lee",
    "date_of_birth": "1948-04-12",
    "phone_number": "555-0101",
    "address": "10 Garden Road",
    "emergency_contact_name": "Daniel Lee",
    "emergency_contact_phone": "555-0199",
    "medical_notes": "Demo data only",
    "active": true,
    "created_at": "2026-07-21T12:00:00Z",
    "updated_at": "2026-07-21T12:00:00Z"
  }
}
```

If `E001` already exists, profile creation returns `409`; verify the existing active
profile with:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/elderly/E001" -Headers $authHeaders
```

### Local URLs

| Interface | Host/browser URL | Credentials |
| --- | --- | --- |
| FastAPI | `http://127.0.0.1:8000` | Bearer account token for protected routes |
| OpenAPI/Swagger | `http://127.0.0.1:8000/docs` | Bearer account token for protected routes |
| Streamlit dashboard | `http://127.0.0.1:8501` | KindCare account |
| RabbitMQ management | `http://127.0.0.1:15672` | Root `.env` RabbitMQ values |
| Mosquitto | `mqtt://127.0.0.1:1883` | Root `.env` MQTT values |

MongoDB, RabbitMQ AMQP `5672`, and container-to-container HTTP are internal only.
The default stack has eight services: `backend`, `worker`, `beat`, `dashboard`,
`mqtt-ingestor`, `mongodb`, `rabbitmq`, and `mosquitto`. The optional `telegram`
Compose profile adds the outbound Telegram polling bot.

### Run Demonstration Clients

Build the shared client image after bootstrapping `E001`:

```powershell
docker build --target production -t kindcare-client -f client_nodes/Dockerfile .
```

Run HTTP telemetry:

```powershell
docker run --rm kindcare-client --url http://host.docker.internal:8000 --elderly-id E001 --scenario mixed --count 12 --interval 1
```

Run authenticated MQTT QoS 1 telemetry with the default demo credentials (use the
values from `.env` if overridden):

```powershell
docker run --rm --entrypoint python kindcare-client -m client_nodes.mqtt_node --broker host.docker.internal --username kindcare_mqtt --password kindcare_mqtt_dev_only --elderly-id E001 --scenario normal --count 2 --interval 1
```

MQTT publishes use QoS 1, `retain=false`, a stable `idempotency_key`, and one of:

```text
kindcare/{elderly_id}/health
kindcare/{elderly_id}/activity
kindcare/{elderly_id}/device
kindcare/{elderly_id}/reminder
```

The complete payload and retry contract is in `docs/api-documentation.md`. The
ingestor intentionally ignores and acknowledges retained messages to prevent stale
replay.

### Optional Telegram Bot

Telegram is disabled unless explicitly configured. Set these values in the root `.env`
with fictional demo data only:

```text
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=<token from BotFather>
TELEGRAM_SERVICE_TOKEN=<random internal service secret>
```

Start the optional polling adapter:

```powershell
docker compose --profile telegram up --build -d --wait
```

An authenticated KindCare account creates a link code through the backend API or a
administrator dashboard. The administrator chooses a family account under
Administration, generates a one-time code, and gives it to the family member. In the
private bot chat, the family member uses `/link CODE`; `/status E001` then returns a
minimal authorized status after the family relationship has the `query_telegram_status`
permission. New alerts are delivered only when the relationship also has
`receive_telegram_alerts`. Knowing `E001` alone never grants health access. Telegram is
a third-party service and must not receive real medical data in this MVP.

### Administrator Management

An administrator can open the `Administration` and `Family & Caregivers` dashboard
views to:

- Add or edit elderly profiles.
- Archive profiles without deleting their history.
- Restore archived profiles.
- Create multiple trusted family accounts for one resident.
- Choose family status and Telegram-alert permissions.
- Generate a family-specific Telegram link code.
- Review or revoke individual Telegram bindings and family relationships.

Archived profiles are excluded from normal caregiver views and do not receive pending
Telegram alert deliveries. Restoring a profile makes its existing relationships usable
again. Telegram alert messages contain only the resident ID, alert type, and severity;
they do not include medical notes or vital-sign payloads.

## Stop And Cleanup

Stop containers and the network while retaining MongoDB and Mosquitto volumes:

```powershell
docker compose down
```

Delete containers, orphaned services, and all persisted demo data and broker session
state. This is destructive and is the clean-database reset:

```powershell
docker compose down --volumes --remove-orphans
```

## Configuration

Root `.env.example` is the canonical Compose override list. It contains every
`${NAME:-default}` substitution from `docker-compose.yml` and separates:

- Demo secrets: non-guest RabbitMQ and authenticated MQTT credentials.
- Host/browser values: `PUBLIC_WS_BASE_URL` and `WEBSOCKET_ALLOWED_ORIGINS`.
- Internal service values: dashboard `API_BASE_URL` defaults to
  `http://backend:8000`; fixed MongoDB, AMQP, ingestor API, and broker hostnames stay
  inside Compose.
- Detection, scanner, lease, health, dashboard, and MQTT retry/bound settings.

Because Compose inserts `RABBITMQ_DEFAULT_USER` and `RABBITMQ_DEFAULT_PASS` directly
into each internal AMQP URL, both are restricted to URI-unreserved
`[A-Za-z0-9._~-]+`. Raw reserved characters are not supported and must not be
documented as working credentials. `.superpowers/` is local agent state: Git and the
root Docker build context exclude it, and it is not part of the release.

`backend/.env.example` is only for running FastAPI directly outside Compose. Its
`localhost` MongoDB/RabbitMQ URLs are host URLs and its variables match
`backend/app/config.py`; worker, dashboard, broker, and MQTT settings belong in the
root Compose example. Never use the demonstration passwords in a deployed system.

`PUBLIC_WS_BASE_URL` is embedded in browser JavaScript. It must be reachable from the
browser, must use `wss://` when the dashboard is served over HTTPS, and must not use
the Compose-only hostname `backend`. WebSocket Origin validation is separate from
CORS; list each exact dashboard scheme/host/port in `WEBSOCKET_ALLOWED_ORIGINS`.

## API And Design References

- [`docs/api-documentation.md`](docs/api-documentation.md): complete HTTP,
  WebSocket, and MQTT contract.
- [`docs/database-design.md`](docs/database-design.md): collections, indexes,
  retention, migrations, and transactions.
- [`docs/architecture.md`](docs/architecture.md): components, data flows,
  consistency, failures, trust boundaries, and tests.
- [`docs/dashboard-design.md`](docs/dashboard-design.md): canonical KindCare
  dashboard design, Figma-export mapping, responsive behavior, and accessibility.
- [`figma design/`](figma%20design/): exported sidebar and main-content references
  that govern the current caregiver UI.
- [`DESIGN.md`](DESIGN.md): preserved upstream historical visual reference. It is not
  the current KindCare dashboard specification.

All successful application responses use `{ "success": true, "message": "...",
"data": ... }`. Application failures that use the KindCare envelope use
`{ "success": false, "message": "...", "data": ... }`; profile routes retain
FastAPI's standard `{"detail":"..."}` handling for `404`, `409`, and `422`.

## Tests

Each Python component has direct requirements in `requirements.txt`, development
requirements in `requirements-dev.txt`, and complete pinned transitive dependencies
in `requirements.lock` and `requirements-dev.lock`. Production and test images
install lock files.
The dashboard also has a dependency-free Node test target.

Build and run every lightweight unit image from the repository root:

```powershell
docker build --target test -t kindcare-backend-test backend
docker run --rm kindcare-backend-test python -m pytest -m "not integration" -q

docker build --target test -t kindcare-worker-test -f workers/Dockerfile .
docker run --rm kindcare-worker-test python -m pytest -c workers/pytest.ini -m "not integration" -q

docker build --target test -t kindcare-client-test -f client_nodes/Dockerfile .
docker run --rm kindcare-client-test

docker build --target test -t kindcare-dashboard-test dashboard
docker run --rm kindcare-dashboard-test

docker build --target js-test -t kindcare-dashboard-js-test dashboard
docker run --rm kindcare-dashboard-js-test

docker build --target test -t kindcare-mqtt-ingestor-test -f mqtt_ingestor/Dockerfile .
docker run --rm kindcare-mqtt-ingestor-test python -m pytest -c mqtt_ingestor/pytest.ini -m "not integration" -q
```

The Compose `test` profile defines exactly four test-profile services:
`backend-tests`, `worker-integration`, `worker-tests`, and
`mqtt-integration-tests`. Run the three test runners; `worker-integration` is the
dedicated live Celery dependency started by `worker-tests`:

```powershell
docker compose --profile test run --rm --build backend-tests
docker compose --profile test run --rm --build worker-tests
docker compose --profile test run --rm --build mqtt-integration-tests
```

Backend and worker integration tests use the dedicated
`kindcare_integration_test` database. Their guarded cleanup may drop only that exact
name or a name beginning `kindcare_test_`. MQTT integration deliberately uses the
running application database `kindcare_db`; it creates unique elderly/client/event
identities and performs guarded per-identity and persistent-session cleanup instead
of dropping the database. Do not run integration tests against care or production
data.

Run only documentation contracts from an already built MQTT test image:

```powershell
docker run --rm kindcare-mqtt-ingestor-test python -m pytest -c mqtt_ingestor/pytest.ini mqtt_ingestor/tests/test_documentation_contract.py -q
```

## Troubleshooting

First inspect state and bounded recent logs:

```powershell
docker compose ps
docker compose logs --tail 200 backend mongodb rabbitmq worker beat mosquitto mqtt-ingestor dashboard
```

- **MongoDB replica set never becomes healthy:** inspect `mongodb` logs for
  `rs.initiate`/primary errors. Transactions require the `rs0` replica set. For
  disposable demo data only, use the volume-deleting cleanup command and restart.
- **Backend exits during startup:** inspect `backend` logs for
  `activity_state_v1`, `alerts_alert_id_uuid_v1`, validator, or index failures.
  Backend owns migrations and indexes; Worker/Beat wait for healthy Backend. Do not
  manually add a migration marker before the migration completes.
- **RabbitMQ, Worker, or Beat is unhealthy:** confirm `RABBITMQ_DEFAULT_USER` and
  `RABBITMQ_DEFAULT_PASS` match and each uses only URI-unreserved
  `[A-Za-z0-9._~-]+`; raw reserved characters break the directly interpolated AMQP
  URL. Then inspect all three logs. Worker and Beat health requires MongoDB,
  RabbitMQ, and a recent Beat-scheduled heartbeat processed by Worker. A running but
  unhealthy process is not automatically restarted by Docker health status.
- **MQTT authentication fails:** ensure `.env` MQTT values are identical for
  Mosquitto, the ingestor, and demo client. Usernames cannot contain `:` and neither
  credential can contain CR/LF. Rebuild/recreate Mosquitto after changing them.
- **MQTT ingestor is unhealthy or saturated:** inspect ingestor and broker logs.
  A full bounded queue intentionally clears health and disconnects without ACK so
  the persistent QoS 1 session can redeliver. Fix backend/RabbitMQ latency before
  increasing `MQTT_QUEUE_SIZE` or retry bounds.
- **A host port is occupied:** find the owning Windows process with
  `Get-NetTCPConnection -LocalPort 8000,8501,1883,15672 -ErrorAction SilentlyContinue`.
  Stop the conflict; published ports are intentionally loopback-only.
- **WebSocket closes with `4403`:** add the exact browser Origin to
  `WEBSOCKET_ALLOWED_ORIGINS`. If the page is remote or HTTPS, correct
  `PUBLIC_WS_BASE_URL` to a browser-reachable `ws://` or `wss://` URL and recreate
  backend/dashboard.
- **Dashboard/WebSocket reports a missing profile or closes `4404`:** rerun the
  `E001` bootstrap or choose another active profile. Soft-deleted profiles are not
  dashboard-visible.
- **Tests leave services running:** inspect with `docker compose --profile test ps`,
  then use `docker compose --profile test down --remove-orphans`. Add `--volumes`
  only when intentionally deleting all local demo/test state.

## Security Boundary

The local MVP now requires bearer account authentication for protected HTTP routes,
explicit account-to-resident relationships, and a telemetry service token. The demo
still has no TLS, production secret management, credential rotation, per-device MQTT
ACLs, or clinical authorization model. Optional Telegram polling sends third-party
messages only when configured and must use fictional data. Loopback bindings reduce
accidental exposure but are not a production security model.

## License

Developed for educational purposes.
