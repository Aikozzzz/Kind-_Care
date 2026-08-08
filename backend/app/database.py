from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from fastapi import FastAPI
from pymongo import DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.config import get_settings
from app.services.dashboard import DashboardService
from app.services.dashboard_live import DashboardHub


ACTIVITY_STATE_MIGRATION_ID = "activity_state_v1"
ACTIVITY_STATE_MIGRATION_BATCH_SIZE = 500
ALERT_ID_MIGRATION_ID = "alerts_alert_id_uuid_v1"
ALERT_ID_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
KINDCARE_ALERT_NAMESPACE = UUID("10445fb8-395a-5a09-8b80-839022bcc3db")


def derive_alert_id(event_id: object, alert_type: object) -> str:
    return str(uuid5(KINDCARE_ALERT_NAMESPACE, f"{event_id}\0{alert_type}"))


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


async def _claim_alert_id(claims: object, document_id: object, candidate: str | None) -> str:
    existing = await claims.find_one({"_id": document_id}, {"alert_id": 1})
    if existing is not None:
        return existing["alert_id"]
    while True:
        alert_id = candidate or str(uuid4())
        try:
            await claims.update_one(
                {"_id": document_id},
                {"$setOnInsert": {"alert_id": alert_id}},
                upsert=True,
            )
        except DuplicateKeyError:
            candidate = None
            continue
        claimed = await claims.find_one({"_id": document_id}, {"alert_id": 1})
        if claimed is not None:
            return claimed["alert_id"]


async def enforce_alert_id_validator(database: AsyncDatabase) -> None:
    await database.command(
        {
            "collMod": "alerts",
            "validator": {
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": ["alert_id"],
                    "properties": {
                        "alert_id": {
                            "bsonType": "string",
                            "pattern": ALERT_ID_PATTERN,
                        }
                    },
                }
            },
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )


async def migrate_alert_ids(database: AsyncDatabase, batch_size: int = 500) -> bool:
    if await database.schema_migrations.find_one({"_id": ALERT_ID_MIGRATION_ID}):
        return False

    claims = database.alert_id_migration_claims
    await claims.create_index("alert_id", unique=True, name="unique_claimed_alert_id")
    last_id = None
    while True:
        query = {} if last_id is None else {"_id": {"$gt": last_id}}
        alerts = await (
            database.alerts.find(query, {"alert_id": 1})
            .sort("_id", 1)
            .limit(batch_size)
            .to_list(length=batch_size)
        )
        if not alerts:
            break
        for alert in alerts:
            alert_id = await _claim_alert_id(
                claims,
                alert["_id"],
                _canonical_uuid(alert.get("alert_id")),
            )
            await database.alerts.update_one(
                {"_id": alert["_id"]}, {"$set": {"alert_id": alert_id}}
            )
        last_id = alerts[-1]["_id"]

    indexes = await database.alerts.index_information()
    existing = indexes.get("unique_alert_id")
    if existing is not None and (
        existing.get("key") != [("alert_id", 1)]
        or existing.get("unique") is not True
        or "partialFilterExpression" in existing
    ):
        await database.alerts.drop_index("unique_alert_id")
    await database.alerts.create_index("alert_id", unique=True, name="unique_alert_id")
    await enforce_alert_id_validator(database)
    await database.schema_migrations.update_one(
        {"_id": ALERT_ID_MIGRATION_ID},
        {"$setOnInsert": {"completed_at": datetime.now(UTC)}},
        upsert=True,
    )
    return True


async def ensure_named_index(
    collection: object,
    keys: list[tuple[str, int]],
    name: str,
    **options: object,
) -> None:
    indexes = await collection.index_information()
    existing = indexes.get(name)
    definition_matches = existing is not None and existing.get("key") == keys
    if definition_matches:
        definition_matches = all(existing.get(key) == value for key, value in options.items())
    if definition_matches and "partialFilterExpression" not in options:
        definition_matches = "partialFilterExpression" not in existing
    if existing is not None and not definition_matches:
        try:
            await collection.drop_index(name)
        except OperationFailure as error:
            if error.code != 27:
                raise
    await collection.create_index(keys, name=name, **options)


async def migrate_received_at(database: AsyncDatabase) -> None:
    update = [
        {
            "$set": {
                "received_at": {"$ifNull": ["$created_at", "$recorded_at"]}
            }
        }
    ]
    query = {"received_at": {"$exists": False}}
    await database.activity_logs.update_many(query, update)
    await database.device_events.update_many(query, update)


def derive_reconstructed_activity_state(
    history: list[dict[str, object]],
) -> dict[str, object]:
    state: dict[str, object] | None = None
    for event in sorted(
        history,
        key=lambda document: (document["received_at"], str(document["event_id"])),
    ):
        received_at = event["received_at"]
        event_id = str(event["event_id"])
        if event["value"] == "inactive":
            continuing = state is not None and state["value"] == "inactive"
            inactive_since = state["inactive_since"] if continuing else received_at
            episode_id = (
                state["episode_id"]
                if continuing
                else f"activity:{event['elderly_id']}:{event_id}"
            )
        else:
            inactive_since = None
            episode_id = None
        state = {
            "elderly_id": event["elderly_id"],
            "event_id": event_id,
            "value": event["value"],
            "received_at": received_at,
            "inactive_since": inactive_since,
            "episode_id": episode_id,
            "alerted_at": None,
            "updated_at": received_at,
        }
    if state is None:
        raise ValueError("activity history must not be empty")
    return state


async def create_telemetry_history_indexes(database: AsyncDatabase) -> None:
    for collection, prefix in (
        (database.activity_logs, "activity"),
        (database.device_events, "device"),
    ):
        await ensure_named_index(
            collection,
            [("elderly_id", 1), ("received_at", DESCENDING), ("event_id", DESCENDING)],
            f"{prefix}_history_latest",
        )
        await ensure_named_index(
            collection,
            [
                ("elderly_id", 1),
                ("received_at", 1),
                ("created_at", DESCENDING),
                ("event_id", DESCENDING),
            ],
            f"{prefix}_history_legacy",
        )
        await ensure_named_index(
            collection,
            [
                ("elderly_id", 1),
                ("received_at", 1),
                ("created_at", 1),
                ("recorded_at", DESCENDING),
                ("event_id", DESCENDING),
            ],
            f"{prefix}_history_legacy_recorded",
        )


def _latest_reconstructed_state(event: dict[str, object]) -> dict[str, object]:
    received_at = event["received_at"]
    event_id = str(event["event_id"])
    inactive = event["value"] == "inactive"
    return {
        "elderly_id": event["elderly_id"],
        "event_id": event_id,
        "value": event["value"],
        "received_at": received_at,
        "inactive_since": received_at if inactive else None,
        "episode_id": (
            f"activity:{event['elderly_id']}:{event_id}" if inactive else None
        ),
        "alerted_at": None,
        "updated_at": received_at,
    }


async def _write_reconstructed_activity_state(
    database: AsyncDatabase, reconstructed: dict[str, object]
) -> None:
    elderly_id = str(reconstructed["elderly_id"])
    earliest_alert = None
    if reconstructed["value"] == "inactive":
        earliest_alert = await database.alerts.find_one(
            {
                "elderly_id": elderly_id,
                "alert_type": "long_inactivity",
                "status": "unresolved",
            },
            {"episode_id": 1, "created_at": 1},
            sort=[("created_at", 1), ("episode_id", 1)],
        )
        if earliest_alert is not None:
            reconstructed = {
                **reconstructed,
                "episode_id": earliest_alert["episode_id"],
                "alerted_at": earliest_alert["created_at"],
            }

    existing = await database.activity_state.find_one({"elderly_id": elderly_id})
    reconstructed_order = (
        reconstructed["received_at"],
        reconstructed["event_id"],
    )
    if existing is not None:
        existing_order = (
            existing.get("received_at"),
            str(existing.get("event_id", "")),
        )
        if existing_order >= reconstructed_order:
            if existing.get("value") == "active":
                await resolve_stale_inactivity_alerts(
                    database, elderly_id, existing["received_at"]
                )
            elif earliest_alert is not None:
                replacement = {
                    **existing,
                    "episode_id": earliest_alert["episode_id"],
                    "alerted_at": earliest_alert["created_at"],
                }
                await database.activity_state.replace_one(
                    {
                        "_id": existing["_id"],
                        "received_at": existing.get("received_at"),
                        "event_id": existing.get("event_id"),
                    },
                    replacement,
                )
            return

    won = False
    if existing is None:
        try:
            await database.activity_state.insert_one(reconstructed)
            won = True
        except DuplicateKeyError:
            return
    else:
        replacement = {**reconstructed, "_id": existing["_id"]}
        result = await database.activity_state.replace_one(
            {
                "_id": existing["_id"],
                "received_at": existing.get("received_at"),
                "event_id": existing.get("event_id"),
            },
            replacement,
        )
        won = result.modified_count == 1

    if won and reconstructed["value"] == "active":
        await resolve_stale_inactivity_alerts(
            database, elderly_id, reconstructed["received_at"]
        )


async def reconstruct_activity_state(
    database: AsyncDatabase,
    batch_size: int = ACTIVITY_STATE_MIGRATION_BATCH_SIZE,
) -> bool:
    completed = await database.schema_migrations.find_one(
        {"_id": ACTIVITY_STATE_MIGRATION_ID}, {"_id": 1}
    )
    if completed is not None:
        return False

    cursor = (
        database.activity_logs.find(
            {},
            {
                "_id": 0,
                "elderly_id": 1,
                "event_id": 1,
                "value": 1,
                "received_at": 1,
            },
        )
        .sort([("elderly_id", 1), ("received_at", -1), ("event_id", -1)])
        .hint("activity_history_latest")
        .batch_size(batch_size)
    )
    current: dict[str, object] | None = None
    collecting_inactive = False
    async for event in cursor:
        if current is None or event["elderly_id"] != current["elderly_id"]:
            if current is not None:
                await _write_reconstructed_activity_state(database, current)
            current = _latest_reconstructed_state(event)
            collecting_inactive = event["value"] == "inactive"
            continue
        if collecting_inactive:
            if event["value"] == "inactive":
                current["inactive_since"] = event["received_at"]
                current["episode_id"] = (
                    f"activity:{event['elderly_id']}:{event['event_id']}"
                )
            else:
                collecting_inactive = False
    if current is not None:
        await _write_reconstructed_activity_state(database, current)

    await database.schema_migrations.update_one(
        {"_id": ACTIVITY_STATE_MIGRATION_ID},
        {"$setOnInsert": {"completed_at": datetime.now(UTC)}},
        upsert=True,
    )
    return True


async def resolve_stale_inactivity_alerts(
    database: AsyncDatabase, elderly_id: str, resolved_at: object
) -> None:
    await database.alerts.update_many(
        {
            "elderly_id": elderly_id,
            "alert_type": "long_inactivity",
            "status": {"$in": ["unresolved", "acknowledged"]},
        },
        {"$set": {"status": "resolved", "resolved_at": resolved_at, "updated_at": resolved_at}},
    )


async def create_indexes(database: AsyncDatabase) -> None:
    accounts = getattr(database, "accounts", None)
    if accounts is not None:
        await ensure_named_index(
            accounts, [("account_id", 1)], "unique_account_id", unique=True
        )
        await ensure_named_index(
            accounts, [("login_name", 1)], "unique_account_login_name", unique=True
        )
    sessions = getattr(database, "auth_sessions", None)
    if sessions is not None:
        await ensure_named_index(
            sessions, [("token_hash", 1)], "unique_auth_session_token", unique=True
        )
        await ensure_named_index(
            sessions, [("expires_at", 1)], "auth_session_expiry", expireAfterSeconds=0
        )
        await ensure_named_index(
            sessions, [("account_id", 1), ("revoked_at", 1)], "auth_session_account"
        )
    websocket_tickets = getattr(database, "websocket_tickets", None)
    if websocket_tickets is not None:
        await ensure_named_index(
            websocket_tickets,
            [("token_hash", 1)],
            "unique_websocket_ticket_token",
            unique=True,
        )
        await ensure_named_index(
            websocket_tickets,
            [("expires_at", 1)],
            "websocket_ticket_expiry",
            expireAfterSeconds=0,
        )
    relationships = getattr(database, "account_elderly_relationships", None)
    if relationships is not None:
        await ensure_named_index(
            relationships,
            [("relationship_id", 1)],
            "unique_relationship_id",
            unique=True,
        )
        await ensure_named_index(
            relationships,
            [("account_id", 1), ("elderly_id", 1)],
            "unique_account_elderly_relationship",
            unique=True,
        )
        await ensure_named_index(
            relationships,
            [("elderly_id", 1), ("status", 1), ("permissions", 1)],
            "elderly_relationship_lookup",
        )
    access_requests = getattr(database, "access_requests", None)
    if access_requests is not None:
        await ensure_named_index(
            access_requests,
            [("request_id", 1)],
            "unique_access_request_id",
            unique=True,
        )
        await ensure_named_index(
            access_requests,
            [("account_id", 1), ("elderly_id", 1), ("status", 1)],
            "access_request_lookup",
        )
    telegram_links = getattr(database, "telegram_link_tokens", None)
    if telegram_links is not None:
        await ensure_named_index(
            telegram_links, [("token_hash", 1)], "unique_telegram_link_token", unique=True
        )
        await ensure_named_index(
            telegram_links, [("expires_at", 1)], "telegram_link_expiry", expireAfterSeconds=0
        )
    telegram_bindings = getattr(database, "telegram_bindings", None)
    if telegram_bindings is not None:
        await ensure_named_index(
            telegram_bindings,
            [("telegram_user_id", 1)],
            "unique_telegram_user",
            unique=True,
        )
        await ensure_named_index(
            telegram_bindings,
            [("account_id", 1), ("revoked_at", 1)],
            "telegram_account_binding",
        )
    notification_events = getattr(database, "alert_notification_events", None)
    if notification_events is not None:
        await ensure_named_index(
            notification_events,
            [("alert_id", 1), ("notification_kind", 1)],
            "unique_alert_notification_event",
            unique=True,
        )
        await ensure_named_index(
            notification_events,
            [("notification_event_id", 1)],
            "unique_notification_event_id",
            unique=True,
        )
        await ensure_named_index(
            notification_events,
            [("status", 1), ("next_attempt_at", 1)],
            "notification_delivery_queue",
        )
    telegram_deliveries = getattr(database, "telegram_deliveries", None)
    if telegram_deliveries is not None:
        await ensure_named_index(
            telegram_deliveries,
            [("notification_event_id", 1), ("telegram_user_id", 1)],
            "unique_telegram_delivery",
            unique=True,
        )
    await database.elderly_profiles.create_index(
        "elderly_id",
        unique=True,
        name="unique_elderly_id",
    )
    await database.health_idempotency.create_index(
        [("elderly_id", 1), ("key_hash", 1)],
        unique=True,
        name="unique_health_idempotency_key",
    )
    await database.health_logs.create_index(
        "event_id",
        unique=True,
        name="unique_health_event_id",
    )
    await ensure_named_index(
        database.health_logs,
        [("elderly_id", 1), ("recorded_at", DESCENDING), ("event_id", DESCENDING)],
        "health_history_latest",
    )
    await database.alerts.create_index(
        [("event_id", 1), ("alert_type", 1)],
        unique=True,
        name="unique_event_alert_type",
    )
    await ensure_named_index(
        database.alerts,
        [("alert_id", 1)],
        "unique_alert_id",
        unique=True,
    )
    await ensure_named_index(
        database.alerts,
        [
            ("elderly_id", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
            ("alert_type", 1),
        ],
        "alert_history_latest",
    )
    await ensure_named_index(
        database.activity_idempotency,
        [("elderly_id", 1), ("key_hash", 1)],
        "unique_activity_idempotency_key",
        unique=True,
    )
    await ensure_named_index(
        database.activity_logs,
        [("event_id", 1)],
        "unique_activity_event_id",
        unique=True,
    )
    await ensure_named_index(
        database.activity_logs,
        [("elderly_id", 1), ("received_at", DESCENDING), ("event_id", DESCENDING)],
        "activity_history_latest",
    )
    await ensure_named_index(
        database.activity_logs,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "activity_history_legacy",
    )
    await ensure_named_index(
        database.activity_logs,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", 1),
            ("recorded_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "activity_history_legacy_recorded",
    )
    await ensure_named_index(
        database.activity_logs,
        [("elderly_id", 1), ("value", 1), ("received_at", 1), ("event_id", 1)],
        "activity_episode_history",
    )
    await ensure_named_index(
        database.activity_state,
        [("elderly_id", 1)],
        "unique_activity_state_elderly_id",
        unique=True,
    )
    await ensure_named_index(
        database.activity_state,
        [("value", 1), ("alerted_at", 1), ("inactive_since", 1), ("elderly_id", 1)],
        "activity_inactivity_scan",
    )
    await ensure_named_index(
        database.device_idempotency,
        [("elderly_id", 1), ("key_hash", 1)],
        "unique_device_idempotency_key",
        unique=True,
    )
    await ensure_named_index(
        database.device_events,
        [("event_id", 1)],
        "unique_device_event_id",
        unique=True,
    )
    await ensure_named_index(
        database.device_events,
        [("elderly_id", 1), ("received_at", DESCENDING), ("event_id", DESCENDING)],
        "device_history_latest",
    )
    await ensure_named_index(
        database.device_events,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "device_history_legacy",
    )
    await ensure_named_index(
        database.device_events,
        [
            ("elderly_id", 1),
            ("received_at", 1),
            ("created_at", 1),
            ("recorded_at", DESCENDING),
            ("event_id", DESCENDING),
        ],
        "device_history_legacy_recorded",
    )
    await ensure_named_index(
        database.device_status,
        [("elderly_id", 1)],
        "unique_device_status_elderly_id",
        unique=True,
    )
    await ensure_named_index(
        database.device_status,
        [("status", 1), ("last_seen", 1)],
        "device_offline_scan",
    )
    await ensure_named_index(
        database.alerts,
        [
            ("elderly_id", 1),
            ("status", 1),
            ("severity", 1),
            ("created_at", -1),
            ("event_id", -1),
            ("alert_type", 1),
        ],
        "alert_current_risk",
    )
    await ensure_named_index(
        database.alerts,
        [("elderly_id", 1), ("alert_type", 1), ("episode_id", 1)],
        "unique_alert_episode",
        unique=True,
        partialFilterExpression={"episode_id": {"$exists": True}},
    )
    await ensure_named_index(
        database.reminder_idempotency,
        [("elderly_id", 1), ("key_hash", 1)],
        "unique_reminder_idempotency_key",
        unique=True,
    )
    await ensure_named_index(
        database.reminders,
        [("reminder_id", 1)],
        "unique_reminder_id",
        unique=True,
    )
    await ensure_named_index(
        database.reminders,
        [("elderly_id", 1), ("scheduled_for", DESCENDING), ("reminder_id", DESCENDING)],
        "reminder_history_latest",
    )
    await ensure_named_index(
        database.reminders,
        [
            ("elderly_id", 1),
            ("status", 1),
            ("scheduled_for", DESCENDING),
            ("reminder_id", DESCENDING),
        ],
        "reminder_status_history_latest",
    )
    await ensure_named_index(
        database.reminders,
        [("status", 1), ("scheduled_for", 1), ("reminder_id", 1)],
        "reminder_missed_scan",
    )


@asynccontextmanager
async def database_lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongo_uri, tz_aware=True)
    database = client[settings.database_name]

    await database.command("ping")
    await migrate_received_at(database)
    await create_telemetry_history_indexes(database)
    await reconstruct_activity_state(database)
    await migrate_alert_ids(database)
    await create_indexes(database)
    await enforce_alert_id_validator(database)
    app.state.database = database
    dashboard_service = DashboardService(
        database.elderly_profiles,
        database.health_logs,
        database.alerts,
        recent_alert_limit=settings.dashboard_recent_alert_limit,
        activity_logs=database.activity_logs,
        device_status=database.device_status,
        reminders=database.reminders,
        upcoming_reminder_limit=settings.dashboard_upcoming_reminder_limit,
        recent_reminder_limit=settings.dashboard_recent_reminder_limit,
    )
    app.state.dashboard_hub = DashboardHub(
        dashboard_service,
        settings.websocket_poll_interval,
    )

    try:
        yield
    finally:
        await app.state.dashboard_hub.close()
        del app.state.dashboard_hub
        await client.close()
