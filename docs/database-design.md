# KindCare Database Design

## Runtime Model

KindCare uses one MongoDB database (`kindcare_db` in the default stack) on a
single-node `rs0` replica set. Replica-set transactions are required for event
idempotency, log/alert atomicity, monitoring-state transitions, reminder lifecycle,
and scanner convergence. BSON clients are timezone-aware; server timestamps are UTC
datetimes.

FastAPI owns startup migrations, validators, and the complete index set. Compose
does not start Worker or Beat until Backend is healthy. `workers/database.py` defines
the subset of indexes needed by worker-only deployments/tests, but normal Worker
startup only pings MongoDB and does not run migrations or index creation.

MongoDB adds the default unique `_id` index to every collection. The tables below
list application-created indexes separately.

## Collection Inventory

There are 16 application collections.

| Collection | Primary owner | Purpose and retention |
| --- | --- | --- |
| `elderly_profiles` | FastAPI profile service | Current and soft-deleted profiles; retained indefinitely |
| `health_idempotency` | FastAPI health service | Stable health key reservations; deliberately no TTL |
| `health_logs` | Health Celery worker | Immutable processed health history; no TTL |
| `alerts` | Workers/scanners create; FastAPI transitions | Health findings and episode lifecycle; no TTL |
| `activity_idempotency` | FastAPI activity service | Stable activity key reservations; deliberately no TTL |
| `activity_logs` | Activity Celery worker | Immutable activity history; no TTL |
| `activity_state` | Activity worker/scanner and startup migration | One bounded current-state document per profile |
| `device_idempotency` | FastAPI device service | Stable heartbeat key reservations; deliberately no TTL |
| `device_events` | Device Celery worker | Immutable heartbeat history; no TTL |
| `device_status` | Device worker/scanner | One bounded current status per profile |
| `reminder_idempotency` | FastAPI reminder service | Stable reminder-create reservations; deliberately no TTL |
| `reminders` | FastAPI and reminder scanner | Non-recurring reminder records and lifecycle; no TTL |
| `scan_leases` | Activity/device/reminder scanners | One renewable coordination record per scanner name; reused, not TTL-deleted |
| `schema_migrations` | FastAPI startup | Durable completion markers; retained indefinitely |
| `alert_id_migration_claims` | Alert-ID startup migration | Stable per-document claims during migration; retained for safe retries |
| `service_health` | Beat task writes; Worker/Beat healthchecks read | One shared processed scheduler/worker heartbeat document |

## Domain Collections

### `elderly_profiles`

Representative document:

```json
{
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
}
```

`date_of_birth` is inserted from `model_dump(mode="json")` and is therefore stored
as an ISO date string. `created_at`/`updated_at` are server UTC BSON datetimes.
Deletion is non-destructive: `active=false` and `updated_at` changes. Profile data is
not cascaded to histories.

Application index:

- `unique_elderly_id`: `{elderly_id: 1}`, unique.

### `health_logs`

The health worker canonicalizes the queued event, calculates deterministic risk, and
stores:

```text
event_id, elderly_id, heart_rate, temperature, oxygen_level,
blood_pressure, movement_status, medicine_status, emergency_pressed,
recorded_at, payload_hash, risk_level, alert_count, created_at
```

`recorded_at` is the client timestamp or first server-generated reservation time.
`created_at` is worker processing time. `payload_hash` includes canonical event
identity and data; duplicate `event_id` with a different hash raises a permanent
payload conflict. `alert_count` allows an identical redelivery to return the original
result without inserting alerts again.

Indexes:

- `unique_health_event_id`: `{event_id: 1}`, unique.
- `health_history_latest`: `{elderly_id: 1, recorded_at: -1, event_id: -1}`.

### `activity_logs`

Immutable fields:

```text
event_id, elderly_id, value, recorded_at, received_at, payload_hash, created_at
```

`recorded_at` is client observation metadata. `received_at` is reserved server time
and is the safety/order clock. For new records `created_at=received_at`. Event order
is `(received_at,event_id)`.

Indexes:

- `unique_activity_event_id`: `{event_id: 1}`, unique.
- `activity_history_latest`:
  `{elderly_id: 1, received_at: -1, event_id: -1}`.
- `activity_history_legacy`:
  `{elderly_id: 1, received_at: 1, created_at: -1, event_id: -1}`.
- `activity_history_legacy_recorded`:
  `{elderly_id: 1, received_at: 1, created_at: 1, recorded_at: -1, event_id: -1}`.
- `activity_episode_history`:
  `{elderly_id: 1, value: 1, received_at: 1, event_id: 1}`.

The two legacy indexes put potentially missing fields before their ordering fields.
This creates indexable null intervals for rolling upgrades; reads issue three
index-hinted bounded queries and merge at most three times `limit+offset` candidates
in application memory. Episode history supports bounded latest-active and
earliest-inactive anchor reads.

### `activity_state`

One current document per profile:

```text
elderly_id, event_id, value, received_at, inactive_since,
episode_id, alerted_at, updated_at
```

For an inactive episode, `inactive_since` is its safe receipt-time anchor and
`episode_id` is `activity:{elderly_id}:{first_event_id}`. Repeated inactive events
retain the episode. `alerted_at` is null until the inactivity scanner creates or
finds the episode alert. Active state clears episode fields. Older queue deliveries
cannot regress current state; an older inactive event that can move an anchor triggers
two bounded history reads without crossing a later active event.

Indexes:

- `unique_activity_state_elderly_id`: `{elderly_id: 1}`, unique.
- `activity_inactivity_scan`:
  `{value: 1, alerted_at: 1, inactive_since: 1, elderly_id: 1}`.

The scan index matches equality filters before cutoff range and deterministic owner
sorting.

### `device_events`

Immutable fields:

```text
event_id, elderly_id, recorded_at, received_at, payload_hash, created_at
```

Timestamp and ordering semantics match activity logs.

Indexes:

- `unique_device_event_id`: `{event_id: 1}`, unique.
- `device_history_latest`:
  `{elderly_id: 1, received_at: -1, event_id: -1}`.
- `device_history_legacy`:
  `{elderly_id: 1, received_at: 1, created_at: -1, event_id: -1}`.
- `device_history_legacy_recorded`:
  `{elderly_id: 1, received_at: 1, created_at: 1, recorded_at: -1, event_id: -1}`.

### `device_status`

One current document per profile:

```text
elderly_id, event_id, status, last_seen, updated_at, offline_episode_id?
```

`status` is `online|offline`. `last_seen` is server `received_at`, not client
`recorded_at`. The offline scanner conditionally changes the exact online heartbeat
state and adds `offline_episode_id=device:{elderly_id}:{event_id}`. A newer heartbeat
restores online and removes the episode field.

Indexes:

- `unique_device_status_elderly_id`: `{elderly_id: 1}`, unique.
- `device_offline_scan`: `{status: 1, last_seen: 1}`.

### `reminders`

Representative fields:

```text
reminder_id, elderly_id, medicine_name, scheduled_for, instructions?,
status, created_at, updated_at, taken_at?
```

`scheduled_for` is normalized to UTC. Status is `pending|missed|taken`.
`created_at` comes from the stable reservation; `updated_at` starts at that value,
then uses scanner/action server time. `taken_at` is set only when taken. There is no
recurrence document or recurrence expansion.

Indexes:

- `unique_reminder_id`: `{reminder_id: 1}`, unique.
- `reminder_history_latest`:
  `{elderly_id: 1, scheduled_for: -1, reminder_id: -1}`.
- `reminder_status_history_latest`:
  `{elderly_id: 1, status: 1, scheduled_for: -1, reminder_id: -1}`.
- `reminder_missed_scan`:
  `{status: 1, scheduled_for: 1, reminder_id: 1}`.

### `alerts`

All alerts expose a stable canonical UUID string `alert_id`. Common fields:

```text
alert_id, event_id, elderly_id, alert_type, severity, status,
message, created_at, updated_at?, acknowledged_at?, resolved_at?
```

Health findings are unique per `(event_id,alert_type)` and usually have no `source`
or `episode_id`. Scanner alerts add:

```text
source: activity | device | reminder
episode_id: activity:{elderly_id}:{event_id}
          | device:{elderly_id}:{event_id}
          | reminder:{reminder_id}
```

Severity is `warning|emergency`; status is
`unresolved|acknowledged|resolved`. Health worker alerts initially have only
`created_at`; scanner reminder alerts also initialize `updated_at`. Lifecycle fields
are optional and server-generated.

Indexes:

- `unique_event_alert_type`: `{event_id: 1, alert_type: 1}`, unique.
- `unique_alert_id`: `{alert_id: 1}`, unique.
- `alert_history_latest`:
  `{elderly_id: 1, created_at: -1, event_id: -1, alert_type: 1}`.
- `alert_current_risk`: `{elderly_id: 1, status: 1, severity: 1, created_at: -1,
  event_id: -1, alert_type: 1}`.
- `unique_alert_episode`:
  `{elderly_id: 1, alert_type: 1, episode_id: 1}`, unique only where
  `episode_id` exists.

This is the only collection with an explicit MongoDB JSON Schema validator. Startup
uses `collMod` with strict/error validation requiring `alert_id` to be a string
matching a lowercase canonical UUID pattern. Other field validation is currently in
Pydantic/service/worker code, not MongoDB validators.

Dashboard emergency-then-warning and unresolved-before-acknowledged probes use
`alert_current_risk` to retrieve one complete alert in deterministic latest order. The
candidate becomes `current_alert` only if its severity equals final summary risk; this
is independent of the bounded `alert_history_latest` result.

## Reservation Collections

### Shared Semantics

`health_idempotency`, `activity_idempotency`, and `device_idempotency` contain:

```text
elderly_id, key_hash, payload_hash, event_id, created_at,
recorded_at (health) or received_at (activity/device)
```

Each has a unique `{elderly_id:1,key_hash:1}` index named respectively:

- `unique_health_idempotency_key`
- `unique_activity_idempotency_key`
- `unique_device_idempotency_key`

The raw `Idempotency-Key` never persists. The transaction checks an active profile
and atomically binds the hash to canonical content, deterministic event UUID, and
the stable observation/receipt timestamp. Health preserves supplied `recorded_at` or
the first generated value when omitted; activity/device reserve server `received_at`.
Legacy hashless reservations are backfilled only when
the matching indexed event proves original content; an unprovable replay is rejected.

### `reminder_idempotency`

Fields are `elderly_id`, `key_hash`, `payload_hash`, `reminder_id`, and `created_at`.
`unique_reminder_idempotency_key` is unique on `{elderly_id:1,key_hash:1}`. Reminder
creation transactionally inserts/reads both reservation and reminder.

### Retention Tradeoff

All four reservation collections intentionally have no TTL index. Processed event
and reminder identities are also retained without expiry. Expiring only a reservation
would allow a reused key to acquire a new generated timestamp while colliding with an
old deterministic ID, breaking retry semantics. The MVP accepts unbounded storage;
a future retention policy must expire reservation and corresponding identity/history
as one explicit contract.

## Operational Collections

### `scan_leases`

Documents use scanner name as `_id`:

```text
_id: inactivity | device-offline | missed-reminders
owner: random UUID per acquisition
expires_at: UTC lease deadline
```

Acquisition conditionally claims an expired lease. Before each candidate mutation,
the scanner transaction renews only its matching unexpired owner. Lost ownership
stops the remaining bounded batch. Normal completion sets `expires_at` to release
time. No extra application index is needed beyond `_id`.

### `service_health`

Beat dispatches a Celery task that Worker processes as:

```text
_id: scheduled-worker
processed_at: worker UTC time
```

Worker and Beat container healthchecks require MongoDB and RabbitMQ connectivity,
then require this document to be no older than
`SERVICE_HEARTBEAT_MAX_AGE_SECONDS`. It detects missing Beat dispatch, blocked queue,
or stopped Worker. The single document is overwritten; no TTL/index is needed.

### `schema_migrations`

Completed migration markers use migration ID as `_id` and contain `completed_at`:

- `activity_state_v1`
- `alerts_alert_id_uuid_v1`

Markers are written only after complete success and retained indefinitely. Their
absence means startup retries idempotently.

### `alert_id_migration_claims`

During alert-ID migration, `_id` is the original alert document `_id` and `alert_id`
is its claimed canonical UUID. `unique_claimed_alert_id` uniquely indexes
`{alert_id:1}`. Claims make interrupted/retried migration stable. The collection is
not deleted after completion because retained claims preserve retry/audit safety.

## Startup Migration Order

Backend startup performs these steps before serving traffic:

1. Ping MongoDB.
2. `migrate_received_at`: idempotently set missing activity/device `received_at` to
   `created_at`, falling back to `recorded_at`. This compatibility update has no
   marker.
3. Create/reconcile the six current and legacy activity/device history indexes needed
   by migration queries.
4. `activity_state_v1`: if unmarked, stream `activity_logs` ordered by owner and
   latest receipt/event in 500-document batches, retaining one profile state in
   memory. Reconstructed active state resolves stale inactivity alerts; inactive
   state adopts the earliest unresolved episode alert when present. Equal/newer live
   state wins. Write marker only after every profile succeeds.
5. `alerts_alert_id_uuid_v1`: if unmarked, scan alerts by `_id` in 500-document
   batches, preserve canonical IDs where possible, otherwise claim a random UUID
   without collision. The claim document makes that choice stable across retries.
   Create the unique alert ID index, enforce the validator, then write the marker.
6. Reconcile the complete index set. Named indexes whose keys/options changed are
   dropped and recreated; the helper tolerates already-absent index error code 27.
7. Enforce the alert validator again.

Initial `activity_state_v1` startup time scales with activity history size. A crash
before its marker leaves partial reconstructed state but the next startup safely
retries without replacing equal/newer runtime state.

## Transaction Boundaries

- **Health/activity/device reservation:** active-profile check and idempotency
  reservation commit together. Confirmed Celery publication occurs after commit; if
  broker publication fails, the reservation remains and a same-body client retry
  republishes the same event identity.
- **Health worker:** immutable health log and all findings commit in one transaction.
  Any alert insert failure rolls back the log. PyMongo retries labeled transient
  transactions/ambiguous commits; Celery retries escaped transient connectivity
  errors up to three times with late acknowledgement.
- **Activity worker:** event log, current state, and active-event alert resolution
  commit together.
- **Device worker:** event log, latest device status, and offline-alert resolution
  commit together.
- **Reminder create:** owner check, reservation, and pending reminder commit together.
- **Reminder taken:** owner-bound status change and missed-alert resolution commit
  together.
- **Alert lifecycle:** current status, active source-state check, and conditional
  transition commit together.
- **Scanners:** candidate selection is bounded and outside transactions. Each exact
  conditional state/reminder change, lease renewal, and episode-alert upsert commits
  in one candidate transaction, making races and task redelivery convergent.

## Consistency And Backup Notes

MongoDB is the system of record, but this Compose deployment is a single-node demo,
not a highly available cluster. Histories, reservations, alerts, and profiles are
unbounded; capacity planning and backup/export policy are intentionally outside the
MVP. The destructive command `docker compose down --volumes --remove-orphans`
deletes `mongodb_data` and all database state.
