# KindCare HTTP, WebSocket, And MQTT API

## Scope And Security

The local MVP exposes HTTP on `http://127.0.0.1:8000`, Swagger UI at
`http://127.0.0.1:8000/docs`, WebSocket on the same server, and authenticated MQTT
on `mqtt://127.0.0.1:1883`. HTTP and WebSocket have no user authentication or
authorization. Bindings are loopback-only in Compose; do not expose them or use real
care data.

JSON timestamps are ISO 8601. Client timestamps must include a timezone and are
normalized to UTC. `elderly_id` is 1-50 ASCII letters, digits, underscores, or
hyphens: `[A-Za-z0-9_-]{1,50}`.

## Response Conventions

Success:

```json
{
  "success": true,
  "message": "Health event queued successfully",
  "data": {
    "event_id": "a3ce37d4-6d4a-4f4e-a5af-744fb1f35cf0",
    "elderly_id": "E001",
    "status": "queued"
  }
}
```

KindCare application failure:

```json
{
  "success": false,
  "message": "Activity data storage is unavailable",
  "data": {
    "status": "unavailable"
  }
}
```

Activity, device, reminder, and alert validation errors use the same failure envelope
with `data.errors`. Their route-raised `HTTPException` responses currently use
`data.status: "not_found"` for both `404` and reminder/alert lifecycle `409` results.
Telemetry `IdempotencyConflict` instead uses `data.status: "conflict"`; storage and
broker `503` responses use `data.status: "unavailable"`. Profile, health, and
dashboard route `HTTPException`/validation responses retain FastAPI's standard format:

```json
{
  "detail": "Elderly profile E404 not found"
}
```

For example, an active-source alert resolution conflict is:

```json
{
  "success": false,
  "message": "Alert cannot be resolved while its source condition is still active",
  "data": {
    "status": "not_found"
  }
}
```

Status meanings used by this API:

| Status | Meaning |
| --- | --- |
| `200 OK` | Read, update, soft-delete, health check, or idempotent transition completed |
| `201 Created` | Profile or reminder created |
| `202 Accepted` | Telemetry reservation succeeded and RabbitMQ confirmed publication; worker persistence may still be pending |
| `404 Not Found` | Required active profile, alert, or owner-bound reminder was not found |
| `409 Conflict` | Duplicate profile, changed idempotent payload, invalid/concurrent lifecycle transition, or active alert source |
| `422 Unprocessable Entity` | Path, query, header, or JSON schema validation failed |
| `503 Service Unavailable` | Required MongoDB or RabbitMQ operation failed, or readiness is unhealthy |

Unknown JSON fields are rejected for health, activity, device, reminder, and alert
write models. Profile models use Pydantic's default extra-field handling.

## Route Matrix

| Method and path | Success | Inputs | Other documented statuses |
| --- | --- | --- | --- |
| `GET /health` | `200` | None | `503` |
| `POST /api/elderly` | `201` | Profile JSON | `409`, `422`, `503` |
| `GET /api/elderly` | `200` | `include_inactive`, `limit`, `offset` | `422`, `503` |
| `GET /api/elderly/{elderly_id}` | `200` | Valid path ID | `404`, `422`, `503` |
| `PATCH /api/elderly/{elderly_id}` | `200` | Non-empty profile update JSON | `404`, `422`, `503` |
| `DELETE /api/elderly/{elderly_id}` | `200` | Valid path ID | `404`, `422`, `503` |
| `POST /api/health` | `202` | `Idempotency-Key`, health JSON | `404`, `409`, `422`, `503` |
| `GET /api/health/{elderly_id}` | `200` | `limit`, `offset` | `422`, `503` |
| `POST /api/activity` | `202` | `Idempotency-Key`, activity JSON | `404`, `409`, `422`, `503` |
| `GET /api/activity/{elderly_id}` | `200` | `limit`, `offset` | `422`, `503` |
| `POST /api/device-status` | `202` | `Idempotency-Key`, heartbeat JSON | `404`, `409`, `422`, `503` |
| `GET /api/device-status/{elderly_id}` | `200` | `limit`, `offset` | `422`, `503` |
| `POST /api/reminders` | `201` | `Idempotency-Key`, reminder JSON | `404`, `409`, `422`, `503` |
| `GET /api/reminders/{elderly_id}` | `200` | `status`, `limit`, `offset` | `422`, `503` |
| `PATCH /api/reminders/{reminder_id}` | `200` | Owner-bound taken JSON | `404`, `409`, `422`, `503` |
| `GET /api/alerts/{elderly_id}` | `200` | `severity`, `status`, `limit`, `offset` | `422`, `503` |
| `PATCH /api/alerts/{alert_id}` | `200` | Lifecycle target JSON | `404`, `409`, `422`, `503` |
| `GET /api/dashboard/{elderly_id}` | `200` | Valid path ID | `404`, `422`, `503` |
| `WS /ws/dashboard/{elderly_id}` | Accepted socket | Allowed `Origin` and active profile | Close `4403`, `4404`, or `1011` |

There is no public endpoint for creating alerts. Health workers and the three scanner
families create them from monitored conditions.

## Common Headers And Query Bounds

`Content-Type: application/json` is required for write bodies. Health, activity,
device, and reminder creation additionally require:

```http
Idempotency-Key: sensor-E001-20260721T120000Z
```

The value must contain 1-128 visible ASCII characters (`!` through `~`) with no
spaces. Generate one key per logical request and reuse that exact key and body for
transport retries. Raw keys are SHA-256 hashed before storage.

| Query | Default | Bounds/values | Routes |
| --- | --- | --- | --- |
| `limit` | `50` | Integer `1..100` | All list/history routes |
| `offset` | `0` | Integer `0..10000` | All list/history routes |
| `include_inactive` | `false` | Boolean | Profile list |
| `severity` | unset | `warning`, `emergency` | Alert history |
| `status` | unset | `unresolved`, `acknowledged`, `resolved` for alerts; `pending`, `missed`, `taken` for reminders | Alert/reminder history |

History endpoints do not first require an active profile. A valid ID with no stored
history returns `200` with `data: []`, not `404`.

## System Health

### `GET /health`

MongoDB and RabbitMQ probes run concurrently with the positive
`READINESS_TIMEOUT_SECONDS` bound (`<=4.0`). Healthy response:

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

If either dependency fails or times out, the route returns `503`, `success: false`,
`message: "KindCare API is unhealthy"`, and the same data fields with the failed
component set to `unavailable`.

## Elderly Profiles

### Create

`POST /api/elderly`

```json
{
  "elderly_id": "E001",
  "full_name": "Margaret Lee",
  "date_of_birth": "1948-04-12",
  "phone_number": "555-0101",
  "address": "10 Garden Road",
  "emergency_contact_name": "Daniel Lee",
  "emergency_contact_phone": "555-0199",
  "medical_notes": "Demo data only"
}
```

`full_name` is trimmed and 1-120 characters. Birth date cannot be in the future.
Optional limits are phone 30, address 300, emergency contact name 120, emergency
phone 30, and medical notes 1000 characters. `201` returns the profile plus
`active: true`, `created_at`, and `updated_at`. Existing `elderly_id` returns `409`.

### List, Read, Update, And Delete

- `GET /api/elderly` sorts ascending by `elderly_id`; inactive profiles are omitted
  unless `include_inactive=true`.
- `GET /api/elderly/{elderly_id}` returns only an active profile.
- `PATCH /api/elderly/{elderly_id}` accepts at least one create field other than
  `elderly_id`; `full_name` and `date_of_birth` cannot be set to null. It updates
  server `updated_at`.
- `DELETE /api/elderly/{elderly_id}` is a soft delete. It sets `active=false` and
  updates `updated_at`; repeated deletion returns `404` because the profile is no
  longer active.

Profile reads, updates, and deletion return `200` with the complete profile in
`data`. Missing/inactive records return `404`.

Example update for `PATCH /api/elderly/{elderly_id}`:

```json
{
  "phone_number": "555-0102",
  "medical_notes": "Updated demo note"
}
```

## Telemetry

### Health Submission

`POST /api/health` requires `Idempotency-Key`.

```json
{
  "elderly_id": "E001",
  "heart_rate": 86,
  "temperature": 36.7,
  "oxygen_level": 97,
  "blood_pressure": "120/80",
  "movement_status": "active",
  "medicine_status": "taken",
  "emergency_pressed": false,
  "recorded_at": "2026-07-21T12:00:00Z"
}
```

`recorded_at` may be omitted; the first server-generated UTC value is reserved and
reused on retry. Heart rate is `20..250`, temperature `25..45`, and oxygen
`50..100`. Optional blood pressure is numeric `systolic/diastolic`, systolic
`60..250`, diastolic `30..150`, with systolic greater than diastolic. Movement is
`active|inactive`; medicine is `taken|missed|not_due`.

`202` response:

```json
{
  "success": true,
  "message": "Health event queued successfully",
  "data": {
    "event_id": "a3ce37d4-6d4a-4f4e-a5af-744fb1f35cf0",
    "elderly_id": "E001",
    "status": "queued"
  }
}
```

### Activity Submission

`POST /api/activity` requires `Idempotency-Key` and explicit timezone-aware
`recorded_at`:

```json
{
  "elderly_id": "E001",
  "value": "inactive",
  "recorded_at": "2026-07-21T12:01:00Z"
}
```

`value` is `active|inactive`. `202` uses the health queued-response shape with
message `Activity event queued successfully` and an activity-specific `event_id`.

### Device Heartbeat Submission

`POST /api/device-status` requires `Idempotency-Key`:

```json
{
  "elderly_id": "E001",
  "recorded_at": "2026-07-21T12:02:00Z"
}
```

`202` uses message `Device heartbeat queued successfully`, stable `event_id`, owner,
and `status: queued`.

For activity/device requests, clients cannot set `event_id`, `received_at`, or other
fields. FastAPI reserves server `received_at`; workers order monitoring state by
`(received_at,event_id)`, so delayed client clocks or queues cannot regress state.

### Telemetry Retry Semantics

For health, activity, and device, FastAPI derives a type-specific UUID from owner and
key. In one MongoDB transaction it verifies the active profile and reserves the key
hash, canonical payload hash, event ID, and generated timestamp before confirmed
Celery publication. Same key/equivalent content returns `202` with the same identity;
changed content returns `409` and is not published. Reservation and processed-event
identity do not expire.

### History

- `GET /api/health/{elderly_id}` orders by `(recorded_at desc,event_id desc)` and
  returns measurements, `event_id`, `risk_level`, `recorded_at`, and `created_at`.
- `GET /api/activity/{elderly_id}` returns `value`, client `recorded_at`, server
  `received_at`, `event_id`, and `created_at`, newest effective receipt first.
- `GET /api/device-status/{elderly_id}` returns heartbeat equivalents, newest
  effective receipt first.

Activity/device rolling reads merge indexed current, legacy-created, and
legacy-recorded paths before applying offset/limit.

## Reminders

### Create

`POST /api/reminders` requires `Idempotency-Key`:

```json
{
  "elderly_id": "E001",
  "medicine_name": "Vitamin D",
  "scheduled_for": "2026-07-21T15:00:00Z",
  "instructions": "Take with water"
}
```

`medicine_name` is nonblank and at most 200 characters; optional instructions are
at most 1000; schedule is timezone-aware. `201` returns stable `reminder_id`, owner,
fields, `status: pending`, `created_at`, and `updated_at`. Reminder creation always
returns `"taken_at": null`; after it is marked taken, `taken_at` is a server-generated
UTC timestamp. There is no recurrence model. Same key/body returns the current
reminder; changed content returns `409`.

### List

`GET /api/reminders/{elderly_id}` orders by
`(scheduled_for desc,reminder_id desc)` and optionally filters `status`.

### Mark Taken

`PATCH /api/reminders/{reminder_id}` requires this exact owner payload; an
`Idempotency-Key` header is not required by this HTTP route:

```json
{
  "elderly_id": "E001",
  "status": "taken"
}
```

The owner and reminder must match. Pending or missed becomes taken with server
`taken_at`/`updated_at`; already taken is an idempotent `200`. In the same transaction,
all unresolved or acknowledged `missed_reminder` alerts for
`reminder:{reminder_id}` become resolved. Missing/wrong-owner returns `404`; unknown
or concurrently changed state returns `409`.

## Alerts

### List

`GET /api/alerts/{elderly_id}` orders by
`(created_at desc,event_id desc,alert_type asc)`. Records contain stable UUID
`alert_id`, source `event_id`, owner, `alert_type`, `severity`, lifecycle `status`,
message, `created_at`, and optional `updated_at`, `acknowledged_at`, `resolved_at`.

Health risk rules can generate emergency `high_heart_rate`, `low_heart_rate`,
`low_oxygen`, or `emergency_button` alerts and warning `high_temperature` or
`missed_medicine` alerts. Scanners generate warning `long_inactivity`,
`device_offline`, and `missed_reminder` episode alerts.

### Transition

`PATCH /api/alerts/{alert_id}` accepts one of:

```json
{
  "status": "acknowledged"
}
```

```json
{
  "status": "resolved"
}
```

Allowed transitions are unresolved to acknowledged/resolved, acknowledged to
resolved, and same-state retry. Resolved is otherwise terminal. Server time supplies
`acknowledged_at` or `resolved_at` and `updated_at`. Invalid/concurrent transitions
return `409`; unknown IDs return `404`.

Before manual resolution, episode alerts enforce source state in the same
transaction:

- `long_inactivity` cannot resolve while `activity_state` is inactive for the same
  episode.
- `device_offline` cannot resolve while `device_status` is offline for the same
  episode.
- `missed_reminder` cannot resolve while the owner-bound reminder remains missed.
- Acknowledgement remains allowed while a source is active.
- New active movement, a newer heartbeat, or marking the reminder taken resolves
  its active episode transactionally.
- Health-reading alerts are immutable snapshot findings, so they have no continuing
  source-state guard.

## Dashboard Summary

`GET /api/dashboard/{elderly_id}` requires an active profile. `200` data contains:

```json
{
  "profile": {
    "elderly_id": "E001",
    "full_name": "Margaret Lee",
    "date_of_birth": "1948-04-12",
    "phone_number": "555-0101",
    "address": "10 Garden Road",
    "emergency_contact_name": "Daniel Lee",
    "emergency_contact_phone": "555-0199",
    "medical_notes": "Demo data only",
    "active": true,
    "created_at": "2026-07-21T11:00:00Z",
    "updated_at": "2026-07-21T11:00:00Z"
  },
  "latest_health": null,
  "current_risk": "normal",
  "current_alert": null,
  "recent_alerts": [],
  "latest_activity": null,
  "device_status": null,
  "upcoming_reminders": [],
  "recent_reminders": []
}
```

Current risk is the maximum of latest health risk and all unresolved/acknowledged
alert severities, not just displayed recent alerts. Candidate `current_alert` selection
uses emergency before warning, unresolved before acknowledged, then
`created_at` descending, `event_id` descending, and `alert_type` ascending. The complete
candidate is returned only when its severity equals final `current_risk`; otherwise it
is `null`, including when latest health outranks the active alert. A returned alert can
be older than `recent_alerts`. Alert/reminder display lists are bounded by configured
limits.

## Dashboard WebSocket

Connect to `WS /ws/dashboard/{elderly_id}` at, for example,
`ws://127.0.0.1:8000/ws/dashboard/E001`. Browsers send `Origin`; it must exactly
match one comma-separated `WEBSOCKET_ALLOWED_ORIGINS` entry. This allowlist is
independent of HTTP CORS.

The server sends JSON only and does not require client messages. On acceptance it
sends an immediate summary and heartbeat. Thereafter it sends changed summaries,
recovery summaries after polling errors, and periodic heartbeat metadata.

Summary message:

```json
{
  "type": "summary",
  "data": {
    "profile": {
      "elderly_id": "E001",
      "full_name": "Margaret Lee",
      "date_of_birth": "1948-04-12",
      "phone_number": "555-0101",
      "address": "10 Garden Road",
      "emergency_contact_name": "Daniel Lee",
      "emergency_contact_phone": "555-0199",
      "medical_notes": "Demo data only",
      "active": true,
      "created_at": "2026-07-21T11:00:00Z",
      "updated_at": "2026-07-21T11:00:00Z"
    },
    "latest_health": null,
    "current_risk": "normal",
    "current_alert": null,
    "recent_alerts": [],
    "latest_activity": null,
    "device_status": null,
    "upcoming_reminders": [],
    "recent_reminders": []
  }
}
```

Heartbeat message:

```json
{
  "type": "heartbeat",
  "data": {
    "sent_at": "2026-07-21T12:00:15Z",
    "interval_seconds": 15.0,
    "last_summary_check_at": "2026-07-21T12:00:14Z",
    "poll_interval_seconds": 1.0
  }
}
```

Error message:

```json
{
  "type": "error",
  "data": {
    "message": "Dashboard data storage is unavailable"
  }
}
```

`last_summary_check_at` advances only after a successful MongoDB summary query,
including unchanged data. Heartbeats prove transport liveness but cannot make stale
summary polling fresh. A recoverable polling/storage failure sends `error` and keeps
the socket open; the next successful result sends `summary` even if data is unchanged.
Subscribers for the same owner in one backend process share one poll task and each
has a latest-only queue.

| Close code | Reason |
| --- | --- |
| `4403` | Missing or disallowed WebSocket Origin; server accepts then closes so the application code is visible |
| `4404` | Profile missing/inactive initially or becomes unavailable during polling; clients should not auto-retry that ID |
| `1011` | Initial dashboard storage query unavailable after the socket is accepted and an error message is sent |
| `1000` | Normal client/page shutdown |

## MQTT Transport

### Connection And Topics

Mosquitto requires `MQTT_USERNAME`/`MQTT_PASSWORD`, uses MQTT 3.1.1, and publishes
only on loopback in Compose. Demo clients publish QoS 1 with `retain=false`:

```text
kindcare/{elderly_id}/health
kindcare/{elderly_id}/activity
kindcare/{elderly_id}/device
kindcare/{elderly_id}/reminder
```

Extra levels and unsupported kinds are invalid. Payloads are UTF-8 JSON objects no
larger than `MQTT_MAX_PAYLOAD_BYTES` (default 16,384 bytes). Every payload requires
`idempotency_key`, using the HTTP key's 1-128 visible ASCII contract. If
`elderly_id` is present it must equal the topic owner.

### Flat Payloads

Health topic example:

```json
{
  "idempotency_key": "health-E001-20260721T120000Z",
  "elderly_id": "E001",
  "heart_rate": 86,
  "temperature": 36.7,
  "oxygen_level": 97,
  "blood_pressure": "120/80",
  "movement_status": "active",
  "medicine_status": "taken",
  "emergency_pressed": false,
  "recorded_at": "2026-07-21T12:00:00Z"
}
```

Activity topic example:

```json
{
  "idempotency_key": "activity-E001-20260721T120100Z",
  "elderly_id": "E001",
  "value": "active",
  "recorded_at": "2026-07-21T12:01:00Z"
}
```

Device topic example:

```json
{
  "idempotency_key": "device-E001-20260721T120200Z",
  "elderly_id": "E001",
  "recorded_at": "2026-07-21T12:02:00Z"
}
```

The ingestor removes only `idempotency_key`, places it in `Idempotency-Key`, and
forwards the rest unchanged to the corresponding telemetry POST route.

Reminder topic payload must contain exactly these four fields:

```json
{
  "idempotency_key": "reminder-E001-d90f15bc-taken",
  "elderly_id": "E001",
  "reminder_id": "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f",
  "status": "taken"
}
```

The bridge calls `PATCH /api/reminders/{reminder_id}` with
`{"elderly_id":"E001","status":"taken"}`. Reminder ID must be a canonical UUID;
wrong-owner is a permanent `404` and changes nothing.

### Delivery And Failure Policy

The ingestor owns a stable client ID, persistent `clean_session=false` subscription,
manual acknowledgements, one bounded FIFO HTTP worker, and capped reconnect/backoff.
It does not access MongoDB, calculate risk, publish Celery tasks, or directly mutate
reminders.

| Outcome | MQTT action |
| --- | --- |
| Retained message | Ignore and acknowledge |
| Invalid QoS, topic, JSON, UTF-8, size, key, identity, or reminder shape | Treat as permanent; QoS 1 valid deliveries are acknowledged |
| HTTP `2xx` | Acknowledge |
| HTTP `4xx` except `408`, `425`, `429` | Permanent; acknowledge |
| HTTP `408`, `425`, `429`, any `5xx`, timeout/network interruption | Retry original request/key with capped exponential backoff |
| Shutdown during retry or broker disconnect | Leave unfinished delivery unacknowledged for broker redelivery |

Paho callbacks never wait for HTTP or queue capacity. If the queue is full, health is
cleared and the ingestor asynchronously disconnects without acknowledging the new
message. It drains queued work, reconnects, and relies on the persistent QoS 1 broker
session for redelivery. Backend reservations make duplicate HTTP delivery safe.

Completed deliveries are held in a bounded process-local LRU keyed by MQTT packet ID
and QoS, with a SHA-256 fingerprint of length-delimited topic/payload and retained
disposition. Matching `dup=true` redelivery can be ACKed without a second HTTP call;
`dup=false` always follows the normal path. Capacity is
`max(MQTT_QUEUE_SIZE + 1, 20)`. Process restarts rely on backend idempotency.

Health requires a fresh marker from a connected, non-saturated ingestor with a live
forwarding thread. Logs include only kind, short hashed owner identity, packet ID,
outcome, attempts, and optional HTTP status; they exclude raw topics, IDs, paths,
payloads, medical data, and credentials.

Mosquitto persists sessions in `mosquitto_data` and saves on change, but an unclean
broker/runtime/host/storage failure can still lose state not durably flushed. This
local demo has no TLS, per-device ACL, or production durability guarantee.
