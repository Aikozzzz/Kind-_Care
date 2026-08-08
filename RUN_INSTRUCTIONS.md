# KindCare Run And Rerun Instructions

KindCare is a local demonstration system with account authentication and resident
relationships. It has no TLS. Keep it on this computer and use only fictional data.

## Requirements

- Docker Desktop with the Linux container engine running.
- Docker Compose v2, included with Docker Desktop.
- Windows PowerShell 5.1 or PowerShell 7.
- Free local ports `8000`, `8501`, `1883`, and `15672`.
- At least 4 GB of memory available to Docker.

Run every command below from the project root:

```powershell
Set-Location "C:\Users\USER\Desktop\Kind Care"
```

## First Run

The checked-in defaults work without an `.env` file. To customize credentials or
other settings, create `.env` once and edit it before starting:

```powershell
Copy-Item .env.example .env
```

Do not overwrite an existing `.env`. RabbitMQ usernames and passwords may contain
only letters, numbers, `.`, `_`, `~`, and `-`.

Set `AUTH_BOOTSTRAP_SECRET` in `.env` before a clean first run. The bootstrap secret
is used only to create the first administrator and must not be committed.

Build and start all eight services, then wait for their health checks:

```powershell
docker compose up --build -d --wait
```

Confirm that the API is ready:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" | ConvertTo-Json -Depth 4
```

The response should report `status` as `healthy`, with MongoDB and RabbitMQ both
`available`.

### Create The First Administrator

Run once on a clean database, then keep the token in the current PowerShell window:

```powershell
$bootstrapSecret = "local_bootstrap_only"
$adminBody = @{ login_name = "admin"; display_name = "Demo Administrator"; password = "ChangeThisDemoPassword123!" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/auth/bootstrap" `
    -Headers @{ "X-Bootstrap-Secret" = $bootstrapSecret } `
    -ContentType "application/json" -Body $adminBody
$loginBody = @{ login_name = "admin"; password = "ChangeThisDemoPassword123!" } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/auth/login" `
    -ContentType "application/json" -Body $loginBody
$authHeaders = @{ Authorization = "Bearer $($login.data.access_token)" }
```

If the administrator already exists, skip bootstrap and run only login.

### Create The Demo Resident

Create `E001` the first time a new database is used:

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

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/elderly" `
    -Headers $authHeaders -ContentType "application/json" -Body $profileBody
```

If this returns HTTP `409`, `E001` already exists and does not need to be created
again. Confirm it with:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/elderly/E001" -Headers $authHeaders
```

Open the caregiver dashboard:

```powershell
Start-Process "http://127.0.0.1:8501"
```

## Rerun The Project

### After A Normal Stop

If the containers still exist because `docker compose stop` was used:

```powershell
docker compose start
docker compose ps
```

### After Docker Desktop Or The Computer Restarts

This recreates missing containers but preserves stored data:

```powershell
docker compose up -d --wait
```

### After Changing Source Code Or Configuration

Rebuild and recreate services so the containers contain the latest files:

```powershell
docker compose up --build -d --wait
```

To rebuild only the dashboard after dashboard code changes:

```powershell
docker compose up --build -d --no-deps dashboard
```

Then check its health:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8501/_stcore/health"
```

## Stop The Project

Temporarily stop the existing containers:

```powershell
docker compose stop
```

Stop and remove containers and the Compose network while retaining resident and
broker data:

```powershell
docker compose down
```

## Clean Reset

The following command permanently deletes all local KindCare database data and MQTT
broker session data. Use it only when a completely clean demonstration is intended:

```powershell
docker compose down --volumes --remove-orphans
docker compose up --build -d --wait
```

After a clean reset, create the `E001` demo resident again using the command above.

## Status And Logs

Show service status:

```powershell
docker compose ps
```

Follow logs from all services. Press `Ctrl+C` to stop following logs without stopping
the project:

```powershell
docker compose logs --tail 100 --follow
```

Inspect only the dashboard and API:

```powershell
docker compose logs --tail 200 dashboard backend
```

## Local URLs

| Interface | URL |
| --- | --- |
| Caregiver dashboard | `http://127.0.0.1:8501` |
| FastAPI service | `http://127.0.0.1:8000` |
| Swagger API documentation | `http://127.0.0.1:8000/docs` |
| RabbitMQ management | `http://127.0.0.1:15672` |
| MQTT broker | `mqtt://127.0.0.1:1883` |

RabbitMQ management credentials and MQTT credentials come from the root `.env` file
or, when it does not exist, from the defaults in `.env.example`.

To enable the optional Telegram bot, set `TELEGRAM_BOT_TOKEN` and a random
`TELEGRAM_SERVICE_TOKEN` in `.env`, then run:

```powershell
docker compose --profile telegram up --build -d --wait
```

Open the dashboard's `Family & Caregivers` view, create or select a family account for
`E001`, and generate a one-time Telegram link code. Give the code to the family member;
they must send `/link CODE` from a private chat with the bot. Use `/status E001` only
with fictional demo data. The family relationship must have `query_telegram_status` for
status and `receive_telegram_alerts` for alert delivery; knowing an ID is not enough.
Administrators can archive/restore profiles from `Administration`, then update
trusted-family permissions and revoke individual Telegram bindings from `Family &
Caregivers`.

## Common Problems

- Run `docker compose ps` and `docker compose logs --tail 200` first.
- Make sure Docker Desktop is running with Linux containers.
- If a service is unhealthy, inspect its log with `docker compose logs SERVICE_NAME`.
- If a port is occupied, run
  `Get-NetTCPConnection -LocalPort 8000,8501,1883,15672 -ErrorAction SilentlyContinue`.
- If the dashboard says the profile is missing, create or verify `E001` as shown above.
- If `.env` credentials changed, run `docker compose up --build -d --force-recreate --wait`.
- See `README.md` for demonstration clients, tests, detailed configuration, and full
  troubleshooting information.
