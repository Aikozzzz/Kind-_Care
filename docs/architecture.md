# KindCare Architecture

## System Purpose And Boundary

KindCare is a local academic distributed elderly-monitoring MVP. It demonstrates
validated ingestion, asynchronous processing, deterministic risk rules, durable
idempotency, monitoring-state scanners, account/relationship authorization, optional
Telegram notification delivery, and a live caregiver dashboard. It is not a clinical
system and has no TLS, per-device MQTT ACLs, secret manager, replicated persistence,
or production operations controls.

## Component Topology

The default Docker Compose project runs eight services:

| Service | Responsibility | Depends on |
| --- | --- | --- |
| `backend` | FastAPI HTTP/WS, validation, profile/reminder/alert lifecycle, reservations, Celery publication, migrations/indexes | Healthy MongoDB and RabbitMQ |
| `worker` | Celery telemetry processing, risk analysis, immutable logs, current state, scanner tasks | Healthy Backend, MongoDB, RabbitMQ |
| `beat` | Celery schedules for inactivity, offline, missed-reminder, and service heartbeat tasks | Healthy Backend, MongoDB, RabbitMQ |
| `dashboard` | Streamlit REST views/actions and browser WebSocket component | Healthy Backend |
| `mqtt-ingestor` | Persistent authenticated MQTT-to-HTTP transport bridge | Healthy Mosquitto and Backend |
| `mongodb` | Single-node `rs0` replica set and persistent application state | Named `mongodb_data` volume |
| `rabbitmq` | Celery broker and local management UI | Non-guest environment credentials |
| `mosquitto` | Authenticated persistent MQTT 3.1.1 broker | Named `mosquitto_data` volume |

The optional `telegram` Compose profile adds `telegram-bot`. It polls Telegram over
outbound HTTPS, calls authenticated internal backend routes, and has no public port.

```text
HTTP node --device token-----------> FastAPI backend
                                           |
                                           | confirmed Celery publish
                                           v
MQTT node -> Mosquitto -> MQTT ingestor --service token--> HTTP API -> RabbitMQ -> Worker
                                                              |         |
                                                              |         v
Caregiver browser <-> Streamlit ---- REST + ticketed WebSocket -> API <-> MongoDB
                         |                                      ^         ^
                         +-- browser owns WebSocket             |         |
                                                        Celery Beat ------+
```

MongoDB and RabbitMQ AMQP are internal-only. Compose publishes FastAPI `8000`,
Streamlit `8501`, RabbitMQ management `15672`, and MQTT `1883` on `127.0.0.1`.
Container URLs use service DNS names such as `backend`, `mongodb`, `rabbitmq`, and
`mosquitto`; browser URLs must use browser-reachable hostnames.

## Component Ownership

### FastAPI Backend

FastAPI is the schema and orchestration authority. It owns:

- Pydantic validation and UTC normalization.
- Active-profile checks for new work.
- Profile CRUD and soft deletion.
- Health/activity/device idempotency reservations before RabbitMQ publication.
- Synchronous reminder creation and owner-bound reminder taken actions.
- Alert acknowledgement/resolution with active-source guards.
- Bounded history and dashboard summary queries.
- One shared per-profile WebSocket polling channel per backend process.
- MongoDB migrations, validator enforcement, and authoritative indexes at startup.
- Account sessions, resident relationships, Telegram binding/status routes, and
  durable alert-notification intent storage.

Routes stay thin; services implement storage and transition logic. The API does not
perform worker risk analysis or scanner threshold transitions.

Authentication establishes an account or service principal; an active relationship and
permission authorize a particular resident. An `elderly_id` selects a resource but
never authorizes disclosure.

### RabbitMQ, Worker, And Beat

FastAPI Celery dispatchers publish JSON tasks with broker publisher confirms. Worker
uses late acknowledgements, rejects tasks when a worker is lost, prefetches one task,
and retries escaped transient MongoDB connectivity failures up to three times.
PyMongo transaction callbacks handle labeled transient transaction retries and
ambiguous commits.

Worker tasks own deterministic health risk evaluation and persistence of health,
activity, and device events. Beat only schedules tasks; it does not evaluate or
mutate state directly. Scanner tasks run in Worker and coordinate through MongoDB
leases.

### Dashboard

Streamlit calls authenticated backend REST routes. It does not read MongoDB or
RabbitMQ. Login, authorized resident filtering, and short-lived single-use WebSocket
tickets preserve the account relationship at the browser boundary.
Two configurable fragments refresh current state and history/actions through REST.
The embedded browser component, not the Streamlit Python process, opens the public
WebSocket URL and manages reconnect, connection generations, stale-state timing, and
terminal unknown-profile handling.
Administrators additionally receive an Administration view for profile create/update,
soft-delete/restore, family relationship permissions, and per-binding Telegram revoke
actions. Family link codes target an existing family account; the family member must
complete the binding from a private Telegram chat.

### Accounts And Telegram

Accounts are separate from elderly profiles. Opaque bearer sessions identify human
accounts, while `account_elderly_relationships` grants explicit per-resident
permissions. Family access begins as a request and requires staff/admin approval;
administrators may also create a family account and relationship directly. Knowing an
elderly ID is never sufficient for disclosure. Profile removal is an archive operation;
history remains retained, archived profiles are not readable by normal caregivers, and
pending Telegram alert intents are closed without delivery.

The optional `telegram-bot` uses outbound long polling and authenticated backend
routes. `/link CODE` binds one private Telegram chat to an already authenticated
family account. `/request E001` creates an access request, while `/status E001` returns
a minimal role-authorized status projection only after approval. New alert messages
contain only resident ID, alert type, and severity. The bot has no direct MongoDB
access. Its last acknowledged Telegram update offset is stored in the persistent
Compose `telegram_state` volume so a bot restart does not replay the pending update
queue.

Alert creators write a unique `alert_notification_events` record inside the same
MongoDB transaction as the alert. The Telegram adapter claims these intents and calls
Telegram outside the transaction, then records per-recipient delivery outcomes.

### MQTT Broker And Ingestor

Mosquitto is the authenticated device-facing transport. The MQTT ingestor is an
independently deployable adapter with one stable client ID and persistent session.
It owns topic/envelope validation, bounded queueing, HTTP retry classification,
manual acknowledgement, broker reconnect, and its health marker.

The ingestor does not import backend services, access MongoDB, publish Celery tasks,
calculate risk, or transition reminders directly. Keeping Paho outside FastAPI
prevents API lifespan/restarts from owning broker reconnect and prevents scaled API
replicas from creating duplicate wildcard subscribers.

## Data Flows

### HTTP Telemetry

1. A client submits health, activity, or device JSON with `Idempotency-Key`.
2. FastAPI validates fields, ranges, unknown fields, timezone, key, and owner syntax.
3. In a MongoDB transaction, the service finds an existing reservation or checks the
   active profile and inserts a reservation binding key hash, payload hash,
   deterministic event UUID, and first server-generated timestamp.
4. A same-key/equivalent retry reuses the reservation. Changed content returns `409`
   before publication.
5. After reservation commit, FastAPI publishes the canonical event through Celery.
   RabbitMQ confirm success returns `202`; this means queued, not yet persisted.
6. Worker processes the event in a MongoDB transaction. Unique event identity and
   payload hash make redelivery idempotent and detect changed payload reuse.

A broker failure after reservation leaves the reservation durable. The client retries
the same request and key, receives the same event identity/timestamp, and republishes.

### Health Processing

The pure risk analyzer applies emergency precedence. Heart rate below 50 or above
120, oxygen below 92, and emergency button are emergencies; temperature above 38 and
missed medicine are warnings. Worker inserts one `health_logs` record and all finding
alerts in one transaction. Any alert failure rolls back the log.

### Activity Processing

FastAPI reserves server `received_at`; client `recorded_at` remains observation
metadata. Worker inserts immutable activity history and conditionally advances one
`activity_state` by `(received_at,event_id)`. First inactive establishes an episode
anchor; repeated inactive data does not spam alerts. Active movement resolves all
unresolved/acknowledged inactivity alerts in the same transaction.

An older inactive event may legitimately move the current episode anchor earlier.
Worker uses `activity_episode_history` for at most two `limit(1)` transaction reads:
latest active before current state, then earliest inactive after it. The anchor cannot
cross a later active boundary.

### Device Processing

Worker stores every heartbeat in `device_events`; only a newer
`(received_at,event_id)` updates the single `device_status`. A heartbeat after an
offline transition restores online state and resolves that exact episode alert in
the same transaction. Delayed client clocks cannot delay or advance offline detection.

### Reminder And Alert Actions

Reminder creation is synchronous MongoDB work rather than a Celery task. One
transaction validates the active profile and upserts both the idempotency reservation
and pending reminder. There is no recurrence model.

Marking a reminder taken matches both `reminder_id` and body `elderly_id`. In one
transaction it transitions pending/missed to taken and resolves matching active
missed-reminder alerts. Already taken is idempotent.

Alert updates permit unresolved to acknowledged/resolved, acknowledged to resolved,
and same-state retry. Before resolution, transaction reads reject a still-active
inactivity, offline, or owner-bound missed-reminder source. Health findings are
snapshot alerts and have no continuing source-state guard.

### Reads And Dashboard Summary

Histories are bounded (`limit<=100`, `offset<=10000`) and deterministically sorted.
Activity/device reads issue three index-hinted bounded queries for current,
legacy-created, and legacy-recorded documents, synthesize effective receipt time,
merge bounded candidates, then paginate.

Dashboard summary reads active profile, latest health, bounded recent alerts, latest
activity, device status, and bounded upcoming/recent reminders. It performs up to four
bounded, index-aligned alert probes: emergency then warning, with unresolved before
acknowledged. Each probe chooses latest `created_at`, then descending `event_id`, then
ascending `alert_type`. This accounts for active alerts outside the displayed recent
list. The first matching probe returns the complete candidate without another read;
the summary exposes it only when its severity equals the final health/alert risk.

## Consistency And Idempotency

The system provides retry-safe at-least-once processing, not exactly-once transport.
Exactly-once effects are approached through deterministic identity, unique indexes,
payload hashes, conditional updates, and transactions:

- Event UUID namespaces are separate for health, activity, and device and derive from
  `(elderly_id,raw idempotency key)`; reminder UUID has its own namespace.
- Raw idempotency keys are never stored; SHA-256 key hashes are unique per owner.
- Canonical payload hashes reject same-key changed content.
- Reservations and processed identities have no TTL. Expiring only reservations
  would violate stable generated timestamp/identity semantics.
- Health alerts are unique by `(event_id,alert_type)`; scanner alerts are unique by
  `(elderly_id,alert_type,episode_id)`.
- Public `alert_id` is a unique canonical UUID and MongoDB enforces its validator.
- Current activity/device state uses conditional monotonic ordering so redelivery and
  queue reordering converge.

The reservation transaction ends before broker publication because MongoDB and
RabbitMQ do not share a distributed transaction. The durable reservation plus client
retry is the intentional recovery protocol.

## Transaction Boundaries

MongoDB transactions atomically cover:

- Active-profile check and each telemetry reservation.
- Health log plus every finding alert.
- Activity event, current state, and active recovery resolutions.
- Device event, latest state, and heartbeat recovery resolution.
- Reminder reservation plus reminder creation.
- Owner-bound taken transition plus missed-alert resolution.
- Alert source check plus lifecycle transition.
- Per-scanner-candidate lease renewal, conditional source transition, and alert upsert.

Candidate scans and RabbitMQ publication are deliberately outside these transactions.
See `database-design.md` for collection and index details.

## Scanners And Leases

Beat schedules four recurring tasks:

| Task | Default cadence | Work |
| --- | --- | --- |
| Inactivity scan | 30 seconds | Inactive state at/after 3600 seconds |
| Device-offline scan | 30 seconds | Online device at/after 120 seconds since receipt |
| Missed-reminder scan | 30 seconds | Pending reminder at/after schedule + 300-second grace |
| Service heartbeat | 15 seconds | Prove Beat-to-RabbitMQ-to-Worker-to-MongoDB flow |

Each scanner validates positive cadence/threshold/batch/lease settings, acquires one
named `scan_leases` document, queries a deterministic indexed batch (default maximum
100), and revalidates/renews ownership inside every candidate transaction. If the
lease is expired or ownership is lost, scanning stops without processing the rest of
the stale candidate list.

Inactivity creates one `long_inactivity` warning and marks state `alerted_at`.
Device scanning conditionally changes the exact latest online heartbeat to offline
and creates one `device_offline` warning. Reminder scanning conditionally changes
pending to missed and creates one `missed_reminder` warning. Concurrent recovery or
taken actions win directly or resolve a scanner-created alert; task retries converge.

## Startup And Migrations

MongoDB health initializes `rs0`; Backend starts only after the node is primary.
Backend then pings, backfills missing activity/device `received_at`, creates history
indexes needed by migrations, reconstructs activity state when
`activity_state_v1` is unmarked, migrates/claims stable alert IDs when
`alerts_alert_id_uuid_v1` is unmarked, reconciles all indexes, and enforces the alert
validator.

Migration markers are written only on complete success. A crash leaves the marker
absent and the next startup retries idempotently. Completed activity migration skips
history reconstruction entirely. Compose gates Worker and Beat on healthy Backend so
workers never race startup migration/index ownership.

## WebSocket Fanout And Freshness

`/ws/dashboard/{elderly_id}` checks exact browser Origin independently of CORS.
Missing/disallowed Origin is accepted then closed `4403`; missing/inactive profile is
closed `4404`; initial storage failure sends an error then closes `1011`.

Within one backend process, all subscribers for one profile share one MongoDB poll
task. A new connection receives immediate summary and heartbeat. Polling sends only
changed summaries, except the first success after a recoverable error is always sent.
Each subscriber has a queue of size one; a newer event replaces an unsent old event,
preventing slow sockets from creating unbounded memory.

Heartbeat contains transport interval, polling interval, send time, and
`last_summary_check_at`. The last-check time advances only after successful summary
queries, including unchanged results. Heartbeats therefore prove socket liveness but
cannot conceal stalled data. A recoverable poll failure sends `error` without closing;
profile disappearance sends error and closes `4404`. The browser replaces a socket
after 2.5 heartbeat intervals without messages, ignores callbacks from replaced
generations, and does not auto-retry terminal `4404`.

## Detailed MQTT Delivery

### Topic Adapter

The ingestor subscribes QoS 1 to `kindcare/+/+` but accepts only exact health,
activity, device, and reminder topic forms. Payloads must be bounded UTF-8 JSON
objects with stable `idempotency_key`; optional payload owner must match topic owner.
Health/activity/device bodies are forwarded unchanged after moving the key to the
HTTP header. Reminder taken has an exact four-field shape and becomes owner-bound
FastAPI PATCH.

### Manual Acknowledgement Policy

Paho callbacks copy topic, payload bytes, packet ID, QoS, retained and duplicate flags
into a bounded queue. They never wait for HTTP. One worker forwards messages in order.

| Outcome | MQTT action |
| --- | --- |
| Retained message | Ignore and acknowledge |
| Invalid topic/JSON/size/key/identity | Log safe metadata and acknowledge QoS 1 delivery |
| HTTP `2xx` | Acknowledge |
| Permanent HTTP `4xx` | Acknowledge |
| HTTP `408`, `425`, `429`, or `5xx` | Retry with capped exponential backoff |
| Network failure | Retry with the original key |
| Shutdown during retry | Leave unacknowledged |
| Broker disconnect | Reconnect with bounded delay; persistent session retains QoS 1 work |

If the callback queue is full, a health lock atomically marks overload and clears the
health marker, then a separate thread requests disconnect without acknowledging the
new message. The forwarding worker drains already queued work before supervisor
reconnect. Shutdown takes precedence over reconnect and interrupts HTTP backoff.

### Completed-Delivery Tombstones

Final deliveries are recorded before ACK in a bounded LRU keyed by `(mid,qos)`, with
a SHA-256 fingerprint of length-delimited exact topic bytes, payload bytes, and
retained disposition. Capacity is `max(queue size + 1, 20)`, covering the queued plus
active worker set and MQTT 3.1.1 default in-flight window.

Local ACK success clears only pending-flush state, not the tombstone. An identical
buffered `dup=true` delivery can be acknowledged without another HTTP request.
Fingerprint mismatch discards the tombstone and follows normal handling. Every
`dup=false` publish removes same-MID state before queueing and replaces it only at
completion, even when topic/payload are identical. Retained disposition prevents an
ignored retained message suppressing a later normal publish. Tombstones are
process-local; backend idempotency remains authoritative after restart.

### MQTT Health, Logs, And Broker Durability

Ingestor health is a fresh file marker written only while connected, non-overloaded,
not shutting down, and backed by a live forwarding worker. Startup removes stale
markers; disconnect, overload, shutdown, or marker age expiry becomes unhealthy.
Health refresh and overload marker removal share a lock so an in-progress refresh
cannot recreate false health after saturation.

Logs include kind, a short SHA-256 owner identity, packet ID, final outcome, HTTP
status when present, and attempts. They exclude raw topics, elderly/reminder IDs,
REST paths, payloads, medical readings, and credentials.

Mosquitto disables anonymous access, generates a hashed password file from environment
credentials, persists sessions, and saves after each change. Credentials reject CR/LF
and usernames reject `:`. This narrows but does not eliminate data loss: an unclean
broker, container runtime, host, or storage failure may lose state not yet durably
flushed. In particular, save-on-change is not proof that every acknowledgement was
durably flushed before a host failure. QoS 1 can duplicate delivery and is not a
replicated durability guarantee.

## Readiness And Failure Behavior

Backend `/health` concurrently probes MongoDB and RabbitMQ under bounded timeouts;
either failure returns `503`. Compose waits on this readiness before dependent app
services.

Worker and Beat healthchecks each require MongoDB ping, RabbitMQ connection, and a
fresh `service_health/_id=scheduled-worker` processed timestamp. This detects stopped
Beat, blocked broker queue, failed Worker, or stale processing. Docker restart policy
restarts exited containers, but Docker does not restart a still-running process merely
because its health status is unhealthy; this MVP intentionally has no autoheal sidecar.

Failure/recovery summary:

| Failure | Visible effect | Recovery contract |
| --- | --- | --- |
| MongoDB unavailable | Backend `503`; Worker/Beat unhealthy; WS error/stale | Restore primary; operations retry according to route/task semantics |
| RabbitMQ unavailable | Backend health `503`; telemetry publication `503`; Worker/Beat unhealthy | Same-key telemetry retry after broker returns |
| Worker stopped | API may accept queued tasks; service heartbeat becomes stale | Restart Worker; late-acked broker tasks redeliver safely |
| Beat stopped | Scanners/health task stop; Worker and Beat health become stale | Restart Beat; bounded scanners catch eligible state |
| Backend migration fails | Backend never healthy; dependents remain gated | Inspect logs/fix data/index conflict; restart for idempotent migration retry |
| MQTT bad credentials | Broker/ingestor unhealthy; clients rejected | Align root environment values and recreate services |
| MQTT HTTP backpressure | Ingestor unhealthy/disconnects; broker retains unacked work | Restore API path, drain, reconnect, idempotently redeliver |
| WebSocket Origin/public URL wrong | Close `4403` or browser connection failure | Correct exact allowlist/browser-reachable URL; recreate app services |

## Trust Model

Loopback publishing and non-guest shared credentials are development safeguards, not
identity. Any local process able to call HTTP can read/change all profiles and
lifecycle state. Any holder of MQTT credentials can publish for any topic owner.
MongoDB/RabbitMQ internal-only ports reduce host exposure, but containers share the
Compose network. No endpoint should be internet-facing until authentication,
authorization, TLS, secret rotation, auditing, per-device ACLs, and production data
governance are designed and tested.

## Test Topology

Lightweight tests run in five Python test images (backend, workers, client nodes,
dashboard, MQTT ingestor) plus one dashboard Node image. They cover Pydantic/routes,
service orchestration, deterministic risk, state/scanner races, simulator retry,
dashboard rendering/WebSocket lifecycle, MQTT routing/HTTP classification,
health/backpressure/tombstones, Compose contracts, and documentation contracts.

The Compose `test` profile has four services. `backend-tests` runs real replica-set
integration against dedicated `kindcare_integration_test`. `worker-integration` is a
single-queue production Worker dependency for `worker-tests`, which covers transaction
rollback and real broker-to-worker flow in the same guarded test database.
`mqtt-integration-tests` exercises the running default application through real
Mosquitto, ingestor, HTTP API, RabbitMQ, Worker, and MongoDB. It uses unique identities
and guarded per-identity/session cleanup in `kindcare_db` rather than dropping the
application database.

MQTT saturation tests place a counting proxy before FastAPI, prove logical messages
cross HTTP once through overload recovery, and attempt all proxy/thread/broker-session/
database cleanup before reporting teardown failure.
