import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid5

import pytest
import app.database as database_module
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo import AsyncMongoClient, MongoClient
from pymongo.errors import WriteError

from app.config import get_settings
from app.database import (
    ACTIVITY_STATE_MIGRATION_ID,
    ALERT_ID_MIGRATION_ID,
    create_indexes,
    database_lifespan,
    migrate_alert_ids,
)
from app.main import app
from app.models.elderly import ElderlyProfileCreate, ElderlyProfileUpdate
from app.models.health import HealthEventCreate
from app.models.activity import ActivityEventCreate
from app.models.device import DeviceHeartbeatCreate
from app.services.elderly import (
    ElderlyProfileAlreadyExists,
    ElderlyProfileNotFound,
    ElderlyProfileService,
)
from app.services.health import HealthEventService, hash_idempotency_key
from app.services.activity import ActivityEventService, derive_activity_event_id
from app.services.device import DeviceEventService, derive_device_event_id
from app.services.health import derive_health_event_id
from app.services.idempotency import IdempotencyConflict
from app.models.reminder import ReminderCreate
from app.services.alerts import AlertConflict, AlertService
from app.services.reminder import (
    ReminderConflict,
    ReminderNotFound,
    ReminderService,
    ReminderStorageUnavailable,
)


pytestmark = pytest.mark.integration
MISSING_ALERT_ID = object()


class IntegrationDispatcher:
    def __init__(self) -> None:
        self.events: list[object] = []

    def dispatch(self, event: object) -> None:
        self.events.append(event)


async def test_concurrent_conflicting_telemetry_reservations_publish_only_winner(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    await mongo_database.elderly_profiles.insert_one(
        {
            "elderly_id": "E-IDEMPOTENCY",
            "full_name": "Concurrent Profile",
            "date_of_birth": datetime(1940, 1, 1),
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    cases = [
        (
            HealthEventService(
                mongo_database.elderly_profiles,
                mongo_database.health_idempotency,
                mongo_database.health_logs,
                mongo_database.alerts,
                IntegrationDispatcher(),
            ),
            HealthEventCreate(
                elderly_id="E-IDEMPOTENCY",
                heart_rate=80,
                temperature=36.7,
                oxygen_level=97,
                movement_status="active",
                medicine_status="taken",
                recorded_at=now,
            ),
            HealthEventCreate(
                elderly_id="E-IDEMPOTENCY",
                heart_rate=80,
                temperature=36.7,
                oxygen_level=91,
                movement_status="active",
                medicine_status="taken",
                recorded_at=now,
            ),
            derive_health_event_id("E-IDEMPOTENCY", "health-shared"),
            "health-shared",
        ),
        (
            ActivityEventService(
                mongo_database.elderly_profiles,
                mongo_database.activity_idempotency,
                mongo_database.activity_logs,
                IntegrationDispatcher(),
                clock=lambda: now,
            ),
            ActivityEventCreate(
                elderly_id="E-IDEMPOTENCY", value="active", recorded_at=now
            ),
            ActivityEventCreate(
                elderly_id="E-IDEMPOTENCY", value="inactive", recorded_at=now
            ),
            derive_activity_event_id("E-IDEMPOTENCY", "activity-shared"),
            "activity-shared",
        ),
        (
            DeviceEventService(
                mongo_database.elderly_profiles,
                mongo_database.device_idempotency,
                mongo_database.device_events,
                IntegrationDispatcher(),
                clock=lambda: now,
            ),
            DeviceHeartbeatCreate(elderly_id="E-IDEMPOTENCY", recorded_at=now),
            DeviceHeartbeatCreate(
                elderly_id="E-IDEMPOTENCY", recorded_at=now + timedelta(seconds=1)
            ),
            derive_device_event_id("E-IDEMPOTENCY", "device-shared"),
            "device-shared",
        ),
    ]

    for service, first, changed, event_id, key in cases:
        results = await asyncio.gather(
            service.queue_event(first, event_id, key),
            service.queue_event(changed, event_id, key),
            return_exceptions=True,
        )

        assert sum(isinstance(result, IdempotencyConflict) for result in results) == 1
        assert len(service.dispatcher.events) == 1


async def test_legacy_activity_reservation_backfills_only_from_persisted_event(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 20, 12, 30, tzinfo=UTC)
    elderly_id = "E-LEGACY-IDEMPOTENCY"
    key = "legacy-activity"
    event_id = derive_activity_event_id(elderly_id, key)
    await mongo_database.elderly_profiles.insert_one(
        {
            "elderly_id": elderly_id,
            "full_name": "Legacy Profile",
            "date_of_birth": datetime(1940, 1, 1),
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    await mongo_database.activity_idempotency.insert_one(
        {
            "elderly_id": elderly_id,
            "key_hash": hash_idempotency_key(key),
            "event_id": str(event_id),
            "received_at": now,
            "created_at": now,
        }
    )
    await mongo_database.activity_logs.insert_one(
        {
            "event_id": str(event_id),
            "elderly_id": elderly_id,
            "value": "inactive",
            "recorded_at": now - timedelta(minutes=1),
            "received_at": now,
            "created_at": now,
        }
    )
    dispatcher = IntegrationDispatcher()
    service = ActivityEventService(
        mongo_database.elderly_profiles,
        mongo_database.activity_idempotency,
        mongo_database.activity_logs,
        dispatcher,
        clock=lambda: now + timedelta(hours=1),
    )
    request = ActivityEventCreate(
        elderly_id=elderly_id,
        value="inactive",
        recorded_at=now - timedelta(minutes=1),
    )

    replayed = await service.queue_event(request, event_id, key)

    reservation = await mongo_database.activity_idempotency.find_one(
        {"elderly_id": elderly_id}
    )
    assert isinstance(reservation["payload_hash"], str)
    assert replayed.received_at == now
    with pytest.raises(IdempotencyConflict):
        await service.queue_event(
            request.model_copy(update={"value": "active"}), event_id, key
        )
    assert len(dispatcher.events) == 1


def ensure_safe_test_database_name(database_name: str) -> None:
    if database_name == "kindcare_integration_test":
        return
    if database_name.startswith("kindcare_test_"):
        return
    raise ValueError(f"Refusing to drop non-test database: {database_name}")


def test_database_name_guard_rejects_non_test_database_names() -> None:
    with pytest.raises(ValueError, match="Refusing to drop non-test database"):
        ensure_safe_test_database_name("kindcare_db")


@pytest.mark.parametrize(
    "database_name",
    ["kindcare_integration_test", "kindcare_test_8f3c1a"],
)
def test_database_name_guard_accepts_test_only_names(database_name: str) -> None:
    ensure_safe_test_database_name(database_name)


@pytest_asyncio.fixture
async def mongo_database():
    settings = get_settings()
    ensure_safe_test_database_name(settings.database_name)
    cleanup_client = AsyncMongoClient(settings.mongo_uri, tz_aware=True)
    await cleanup_client.drop_database(settings.database_name)
    application = FastAPI()

    async with database_lifespan(application):
        yield application.state.database

    ensure_safe_test_database_name(settings.database_name)
    await cleanup_client.drop_database(settings.database_name)
    await cleanup_client.close()


async def test_database_lifecycle_creates_unique_elderly_id_index(
    mongo_database,
) -> None:
    indexes = await mongo_database.elderly_profiles.index_information()

    assert indexes["unique_elderly_id"]["key"] == [("elderly_id", 1)]
    assert indexes["unique_elderly_id"]["unique"] is True
    activity_indexes = await mongo_database.activity_logs.index_information()
    assert activity_indexes["activity_episode_history"]["key"] == [
        ("elderly_id", 1),
        ("value", 1),
        ("received_at", 1),
        ("event_id", 1),
    ]


async def test_deployed_alert_id_index_is_full_and_unique(mongo_database) -> None:
    index = (await mongo_database.alerts.index_information())["unique_alert_id"]

    assert index["key"] == [("alert_id", 1)]
    assert index["unique"] is True
    assert "partialFilterExpression" not in index


async def test_create_indexes_promotes_partial_alert_index_after_migration_marker(
    mongo_database,
) -> None:
    assert await mongo_database.schema_migrations.find_one(
        {"_id": ALERT_ID_MIGRATION_ID}
    )
    await mongo_database.alerts.drop_index("unique_alert_id")
    await mongo_database.alerts.create_index(
        "alert_id",
        unique=True,
        name="unique_alert_id",
        partialFilterExpression={"alert_id": {"$type": "string"}},
    )

    await create_indexes(mongo_database)

    index = (await mongo_database.alerts.index_information())["unique_alert_id"]
    assert index["unique"] is True
    assert "partialFilterExpression" not in index


@pytest.mark.parametrize(
    "alert_id",
    [
        pytest.param(MISSING_ALERT_ID, id="missing"),
        pytest.param(None, id="null"),
        pytest.param(42, id="non-string"),
        pytest.param("", id="empty"),
        pytest.param("bad", id="malformed"),
        pytest.param(
            "A74CFDA8-D0EF-518E-A671-A2EABCA7F6B0", id="noncanonical"
        ),
    ],
)
async def test_deployed_alert_validator_rejects_noncanonical_ids(
    mongo_database, alert_id
) -> None:
    document = {
        "event_id": f"invalid-alert-{repr(alert_id)}",
        "elderly_id": "E-INVALID-ALERT",
        "alert_type": f"invalid-{repr(alert_id)}",
        "severity": "warning",
        "status": "unresolved",
        "message": "Invalid alert ID fixture",
        "created_at": datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
    }
    if alert_id is not MISSING_ALERT_ID:
        document["alert_id"] = alert_id

    with pytest.raises(WriteError):
        await mongo_database.alerts.insert_one(document)


async def test_profile_service_runs_crud_with_bson_timestamps_and_pagination(
    mongo_database,
) -> None:
    service = ElderlyProfileService(mongo_database.elderly_profiles)
    first = ElderlyProfileCreate(
        elderly_id="E101",
        full_name="First Integration Profile",
        date_of_birth=date(1940, 1, 1),
    )
    second = ElderlyProfileCreate(
        elderly_id="E102",
        full_name="Second Integration Profile",
        date_of_birth=date(1941, 2, 2),
    )

    created = await service.create_profile(first)
    await service.create_profile(second)
    stored = await mongo_database.elderly_profiles.find_one({"elderly_id": "E101"})

    assert created.created_at.tzinfo is UTC
    assert isinstance(stored["created_at"], datetime)
    assert stored["created_at"].tzinfo is not None

    page = await service.list_profiles(limit=1, offset=1)
    assert [profile.elderly_id for profile in page] == ["E102"]

    updated = await service.update_profile(
        "E101",
        ElderlyProfileUpdate(phone_number="555-0101"),
    )
    assert updated.phone_number == "555-0101"
    assert updated.updated_at > created.updated_at

    deleted = await service.delete_profile("E101")
    assert deleted.active is False
    with pytest.raises(ElderlyProfileNotFound):
        await service.get_profile("E101")


async def test_profile_service_translates_duplicate_elderly_id(
    mongo_database,
) -> None:
    service = ElderlyProfileService(mongo_database.elderly_profiles)
    profile = ElderlyProfileCreate(
        elderly_id="E103",
        full_name="Duplicate Integration Profile",
        date_of_birth=date(1942, 3, 3),
    )
    await service.create_profile(profile)

    with pytest.raises(ElderlyProfileAlreadyExists):
        await service.create_profile(profile)


async def test_reminder_idempotency_taken_transition_and_alert_resolution(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    await mongo_database.elderly_profiles.insert_one(
        {"elderly_id": "E105", "active": True}
    )
    service = ReminderService(
        mongo_database.elderly_profiles,
        mongo_database.reminder_idempotency,
        mongo_database.reminders,
        mongo_database.alerts,
        clock=lambda: now,
    )
    request = ReminderCreate(
        elderly_id="E105",
        medicine_name="Aspirin",
        scheduled_for=now + timedelta(minutes=5),
    )
    first = await service.create(request, "dose-1")
    second = await service.create(request, "dose-1")
    assert first.reminder_id == second.reminder_id
    assert await mongo_database.reminders.count_documents({}) == 1
    with pytest.raises(ReminderConflict):
        await service.create(request.model_copy(update={"medicine_name": "Other"}), "dose-1")

    reminder_id = str(first.reminder_id)
    await mongo_database.reminders.update_one(
        {"reminder_id": reminder_id}, {"$set": {"status": "missed"}}
    )
    await mongo_database.alerts.insert_one(
        {
            "alert_id": "a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
            "event_id": "event-acknowledged",
            "elderly_id": "E105",
            "alert_type": "missed_reminder",
            "episode_id": f"reminder:{reminder_id}",
            "severity": "warning",
            "status": "acknowledged",
            "message": "Missed reminder",
            "created_at": now,
        }
    )
    taken = await service.mark_taken(reminder_id, "E105")
    repeated = await service.mark_taken(reminder_id, "E105")
    with pytest.raises(ReminderNotFound):
        await service.mark_taken(reminder_id, "E999")
    assert taken.status == repeated.status == "taken"
    assert taken.taken_at == repeated.taken_at == now
    assert await mongo_database.alerts.count_documents(
        {"episode_id": f"reminder:{reminder_id}", "status": "resolved"}
    ) == 1


async def test_reminder_replay_precedes_active_profile_validation(mongo_database) -> None:
    now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    await mongo_database.elderly_profiles.insert_one(
        {"elderly_id": "E-REPLAY", "active": True}
    )
    service = ReminderService(
        mongo_database.elderly_profiles,
        mongo_database.reminder_idempotency,
        mongo_database.reminders,
        mongo_database.alerts,
        clock=lambda: now,
    )
    request = ReminderCreate(
        elderly_id="E-REPLAY",
        medicine_name="Aspirin",
        scheduled_for=now + timedelta(minutes=5),
    )
    created = await service.create(request, "replay-key")
    await mongo_database.elderly_profiles.update_one(
        {"elderly_id": "E-REPLAY"}, {"$set": {"active": False}}
    )

    replayed = await service.create(request, "replay-key")
    assert replayed.reminder_id == created.reminder_id
    await mongo_database.reminders.delete_one(
        {"reminder_id": str(created.reminder_id)}
    )
    repaired = await service.create(request, "replay-key")
    assert repaired.reminder_id == created.reminder_id
    assert await mongo_database.reminders.count_documents(
        {"reminder_id": str(created.reminder_id)}
    ) == 1
    with pytest.raises(ReminderConflict):
        await service.create(
            request.model_copy(update={"medicine_name": "Changed"}), "replay-key"
        )
    with pytest.raises(ElderlyProfileNotFound):
        await service.create(request, "new-key")


async def test_reminder_create_rolls_back_reservation_when_reminder_write_fails(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    await mongo_database.elderly_profiles.insert_one(
        {"elderly_id": "E-ROLLBACK", "active": True}
    )
    await mongo_database.command(
        {
            "collMod": "reminders",
            "validator": {"medicine_name": {"$eq": "Allowed"}},
            "validationLevel": "strict",
            "validationAction": "error",
        }
    )
    service = ReminderService(
        mongo_database.elderly_profiles,
        mongo_database.reminder_idempotency,
        mongo_database.reminders,
        mongo_database.alerts,
        clock=lambda: now,
    )
    request = ReminderCreate(
        elderly_id="E-ROLLBACK",
        medicine_name="Rejected",
        scheduled_for=now + timedelta(minutes=5),
    )
    try:
        with pytest.raises(ReminderStorageUnavailable):
            await service.create(request, "rollback-key")
        assert await mongo_database.reminder_idempotency.count_documents(
            {"elderly_id": "E-ROLLBACK"}
        ) == 0
        assert await mongo_database.reminders.count_documents(
            {"elderly_id": "E-ROLLBACK"}
        ) == 0
    finally:
        await mongo_database.command({"collMod": "reminders", "validator": {}})


async def test_alert_lifecycle_timestamps_idempotence_and_terminal_conflict(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    alert_id = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    await mongo_database.alerts.insert_one(
        {
            "alert_id": alert_id,
            "event_id": "008b2d23-93e6-5ef5-b676-f629c63c8bbb",
            "elderly_id": "E105",
            "alert_type": "missed_reminder",
            "severity": "warning",
            "status": "unresolved",
            "message": "Missed reminder",
            "created_at": now,
        }
    )
    service = AlertService(mongo_database.alerts, clock=lambda: now)
    acknowledged = await service.update_status(alert_id, "acknowledged")
    repeated = await service.update_status(alert_id, "acknowledged")
    resolved = await service.update_status(alert_id, "resolved")
    assert acknowledged.acknowledged_at == repeated.acknowledged_at == now
    assert resolved.resolved_at == now
    with pytest.raises(AlertConflict):
        await service.update_status(alert_id, "acknowledged")


async def test_alert_id_migration_canonicalizes_all_invalid_and_duplicate_values(
    mongo_database,
) -> None:
    await mongo_database.schema_migrations.delete_one({"_id": ALERT_ID_MIGRATION_ID})
    await mongo_database.command({"collMod": "alerts", "validator": {}})
    try:
        await mongo_database.alerts.drop_index("unique_alert_id")
    except Exception:
        pass
    valid_duplicate = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    malformed = [None, "", 42, "not-a-uuid", valid_duplicate, valid_duplicate]
    documents = []
    for index, alert_id in enumerate(malformed):
        document = {
            "event_id": f"migration-event-{index}",
            "elderly_id": "E-MIGRATION",
            "alert_type": f"migration-{index}",
            "severity": "warning",
            "status": "unresolved",
            "message": "Migration fixture",
            "created_at": datetime(2026, 7, 18, 8, index, tzinfo=UTC),
        }
        if index != 0:
            document["alert_id"] = alert_id
        documents.append(document)
    await mongo_database.alerts.insert_many(documents)

    assert await migrate_alert_ids(mongo_database, batch_size=2) is True
    migrated = await mongo_database.alerts.find(
        {"elderly_id": "E-MIGRATION"}, {"alert_id": 1}
    ).sort("_id", 1).to_list(length=10)
    ids = [document["alert_id"] for document in migrated]
    assert len(set(ids)) == len(ids)
    assert all(str(UUID(alert_id)) == alert_id for alert_id in ids)
    assert await mongo_database.schema_migrations.find_one({"_id": ALERT_ID_MIGRATION_ID})
    indexes = await mongo_database.alerts.index_information()
    assert indexes["unique_alert_id"]["unique"] is True
    assert await migrate_alert_ids(mongo_database, batch_size=2) is False


async def test_alert_id_migration_resumes_persisted_claim_after_crash(
    mongo_database, monkeypatch
) -> None:
    await mongo_database.schema_migrations.delete_one({"_id": ALERT_ID_MIGRATION_ID})
    await mongo_database.command({"collMod": "alerts", "validator": {}})
    try:
        await mongo_database.alerts.drop_index("unique_alert_id")
    except Exception:
        pass
    result = await mongo_database.alerts.insert_one(
        {
            "event_id": "migration-crash-event",
            "elderly_id": "E-MIGRATION-CRASH",
            "alert_type": "migration-crash",
            "severity": "warning",
            "status": "unresolved",
            "message": "Crash migration fixture",
            "created_at": datetime(2026, 7, 18, 9, 0, tzinfo=UTC),
        }
    )
    original_claim = database_module._claim_alert_id
    claimed_id = None

    async def crash_after_claim(claims, document_id, candidate):
        nonlocal claimed_id
        claimed_id = await original_claim(claims, document_id, candidate)
        raise RuntimeError("simulated migration crash")

    monkeypatch.setattr(database_module, "_claim_alert_id", crash_after_claim)
    with pytest.raises(RuntimeError, match="simulated migration crash"):
        await migrate_alert_ids(mongo_database, batch_size=1)
    assert await mongo_database.schema_migrations.find_one(
        {"_id": ALERT_ID_MIGRATION_ID}
    ) is None
    assert "unique_alert_id" not in await mongo_database.alerts.index_information()
    assert "alert_id" not in await mongo_database.alerts.find_one({"_id": result.inserted_id})

    monkeypatch.setattr(database_module, "_claim_alert_id", original_claim)
    assert await migrate_alert_ids(mongo_database, batch_size=1) is True
    migrated = await mongo_database.alerts.find_one({"_id": result.inserted_id})
    assert migrated["alert_id"] == claimed_id


@pytest.mark.parametrize(
    ("alert_type", "episode_id", "state_collection", "state"),
    [
        (
            "long_inactivity",
            "activity:E106:event-1",
            "activity_state",
            {
                "elderly_id": "E106",
                "value": "inactive",
                "episode_id": "activity:E106:event-1",
            },
        ),
        (
            "device_offline",
            "device:E106:event-1",
            "device_status",
            {
                "elderly_id": "E106",
                "status": "offline",
                "offline_episode_id": "device:E106:event-1",
            },
        ),
    ],
)
async def test_alert_resolution_rejects_matching_active_source_episode_but_allows_acknowledge(
    mongo_database, alert_type, episode_id, state_collection, state
) -> None:
    alert_id = str(
        uuid5(UUID("aaf4862e-5da2-5f7a-b02e-fc7804fd30b0"), f"source:{alert_type}")
    )
    now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    await mongo_database[state_collection].insert_one(state)
    await mongo_database.alerts.insert_one(
        {
            "alert_id": alert_id,
            "event_id": "008b2d23-93e6-5ef5-b676-f629c63c8bbb",
            "elderly_id": "E106",
            "alert_type": alert_type,
            "episode_id": episode_id,
            "severity": "warning",
            "status": "unresolved",
            "message": "Source condition active",
            "created_at": now,
        }
    )
    service = AlertService(
        mongo_database.alerts,
        mongo_database.activity_state,
        mongo_database.device_status,
        clock=lambda: now,
    )

    acknowledged = await service.update_status(alert_id, "acknowledged")
    assert acknowledged.status == "acknowledged"
    with pytest.raises(AlertConflict, match="condition is still active"):
        await service.update_status(alert_id, "resolved")

    if state_collection == "activity_state":
        await mongo_database.activity_state.update_one(
            {"elderly_id": "E106"}, {"$set": {"value": "active", "episode_id": None}}
        )
    else:
        await mongo_database.device_status.update_one(
            {"elderly_id": "E106"}, {"$set": {"status": "online"}, "$unset": {"offline_episode_id": ""}}
        )
    resolved = await service.update_status(alert_id, "resolved")
    assert resolved.status == "resolved"

    if state_collection == "activity_state":
        await mongo_database.activity_state.update_one(
            {"elderly_id": "E106"},
            {"$set": {"value": "inactive", "episode_id": episode_id}},
        )
    else:
        await mongo_database.device_status.update_one(
            {"elderly_id": "E106"},
            {"$set": {"status": "offline", "offline_episode_id": episode_id}},
        )
    replayed = await service.update_status(alert_id, "resolved")
    assert replayed.status == "resolved"
    assert replayed.resolved_at == resolved.resolved_at


async def test_missed_reminder_alert_cannot_resolve_until_reminder_is_taken(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 20, 13, 0, tzinfo=UTC)
    reminder_id = "00000000-0000-5000-8000-000000000777"
    alert_id = "00000000-0000-5000-8000-000000000778"
    await mongo_database.reminders.insert_one(
        {
            "reminder_id": reminder_id,
            "elderly_id": "E-REMINDER-SOURCE",
            "medicine_name": "Aspirin",
            "scheduled_for": now - timedelta(hours=1),
            "status": "missed",
            "created_at": now - timedelta(hours=2),
            "updated_at": now,
        }
    )
    await mongo_database.alerts.insert_one(
        {
            "alert_id": alert_id,
            "event_id": reminder_id,
            "elderly_id": "E-REMINDER-SOURCE",
            "alert_type": "missed_reminder",
            "episode_id": f"reminder:{reminder_id}",
            "severity": "warning",
            "status": "unresolved",
            "message": "Reminder remains missed",
            "created_at": now,
        }
    )
    service = AlertService(
        mongo_database.alerts,
        mongo_database.activity_state,
        mongo_database.device_status,
        mongo_database.reminders,
        clock=lambda: now,
    )

    acknowledged = await service.update_status(alert_id, "acknowledged")
    assert acknowledged.status == "acknowledged"
    with pytest.raises(AlertConflict, match="condition is still active"):
        await service.update_status(alert_id, "resolved")

    await mongo_database.reminders.update_one(
        {"reminder_id": reminder_id},
        {"$set": {"status": "taken", "taken_at": now}},
    )
    resolved = await service.update_status(alert_id, "resolved")
    replayed = await service.update_status(alert_id, "resolved")
    assert resolved.status == replayed.status == "resolved"
    assert resolved.resolved_at == replayed.resolved_at


async def test_missed_reminder_resolution_racing_taken_converges_safely(
    mongo_database,
) -> None:
    now = datetime(2026, 7, 20, 13, 30, tzinfo=UTC)
    elderly_id = "E-REMINDER-RACE"
    reminder_id = "00000000-0000-5000-8000-000000000779"
    alert_id = "00000000-0000-5000-8000-000000000780"
    await mongo_database.reminders.insert_one(
        {
            "reminder_id": reminder_id,
            "elderly_id": elderly_id,
            "medicine_name": "Aspirin",
            "scheduled_for": now - timedelta(hours=1),
            "status": "missed",
            "created_at": now - timedelta(hours=2),
            "updated_at": now,
        }
    )
    await mongo_database.alerts.insert_one(
        {
            "alert_id": alert_id,
            "event_id": reminder_id,
            "elderly_id": elderly_id,
            "alert_type": "missed_reminder",
            "episode_id": f"reminder:{reminder_id}",
            "severity": "warning",
            "status": "unresolved",
            "message": "Racing reminder source",
            "created_at": now,
        }
    )
    alert_service = AlertService(
        mongo_database.alerts,
        mongo_database.activity_state,
        mongo_database.device_status,
        mongo_database.reminders,
        clock=lambda: now,
    )
    reminder_service = ReminderService(
        mongo_database.elderly_profiles,
        mongo_database.reminder_idempotency,
        mongo_database.reminders,
        mongo_database.alerts,
        clock=lambda: now,
    )

    results = await asyncio.gather(
        alert_service.update_status(alert_id, "resolved"),
        reminder_service.mark_taken(reminder_id, elderly_id),
        return_exceptions=True,
    )

    assert all(
        not isinstance(result, Exception) or isinstance(result, AlertConflict)
        for result in results
    )
    reminder = await mongo_database.reminders.find_one({"reminder_id": reminder_id})
    alert = await mongo_database.alerts.find_one({"alert_id": alert_id})
    assert reminder["status"] == "taken"
    assert alert["status"] == "resolved"


@pytest.mark.parametrize(
    ("alert_type", "episode_id", "state_collection", "active_state", "recovery_update"),
    [
        (
            "long_inactivity",
            "activity:E-RACE:event-1",
            "activity_state",
            {"elderly_id": "E-RACE", "value": "inactive", "episode_id": "activity:E-RACE:event-1"},
            {"$set": {"value": "active", "episode_id": None}},
        ),
        (
            "device_offline",
            "device:E-RACE:event-1",
            "device_status",
            {
                "elderly_id": "E-RACE",
                "status": "offline",
                "offline_episode_id": "device:E-RACE:event-1",
            },
            {"$set": {"status": "online"}, "$unset": {"offline_episode_id": ""}},
        ),
    ],
)
async def test_source_recovery_racing_caregiver_resolution_converges_safely(
    mongo_database,
    alert_type,
    episode_id,
    state_collection,
    active_state,
    recovery_update,
) -> None:
    alert_id = str(
        uuid5(UUID("aaf4862e-5da2-5f7a-b02e-fc7804fd30b0"), f"race:{alert_type}")
    )
    now = datetime(2026, 7, 18, 9, 30, tzinfo=UTC)
    await mongo_database[state_collection].insert_one(active_state)
    await mongo_database.alerts.insert_one(
        {
            "alert_id": alert_id,
            "event_id": f"event-{alert_type}",
            "elderly_id": "E-RACE",
            "alert_type": alert_type,
            "episode_id": episode_id,
            "severity": "warning",
            "status": "unresolved",
            "message": "Racing source recovery",
            "created_at": now,
        }
    )
    service = AlertService(
        mongo_database.alerts,
        mongo_database.activity_state,
        mongo_database.device_status,
        clock=lambda: now,
    )

    async def recover_source() -> None:
        async def callback(session):
            await mongo_database[state_collection].update_one(
                {"elderly_id": "E-RACE"}, recovery_update, session=session
            )
            await mongo_database.alerts.update_one(
                {"alert_id": alert_id, "status": {"$in": ["unresolved", "acknowledged"]}},
                {"$set": {"status": "resolved", "resolved_at": now, "updated_at": now}},
                session=session,
            )

        async with mongo_database.client.start_session() as session:
            await session.with_transaction(callback)

    results = await asyncio.gather(
        service.update_status(alert_id, "resolved"),
        recover_source(),
        return_exceptions=True,
    )
    assert all(isinstance(result, AlertConflict) for result in results if isinstance(result, Exception))
    stored = await mongo_database.alerts.find_one({"alert_id": alert_id})
    assert stored["status"] == "resolved"


async def test_asgi_request_uses_lifespan_database_and_dependency_wiring(
    mongo_database,
) -> None:
    with TestClient(app) as client:
        assert client.app.state.database.name == get_settings().database_name

        create_response = client.post(
            "/api/elderly",
            json={
                "elderly_id": "E104",
                "full_name": "ASGI Integration Profile",
                "date_of_birth": "1943-04-04",
            },
        )
        list_response = client.get("/api/elderly?limit=10&offset=0")

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert [profile["elderly_id"] for profile in list_response.json()["data"]] == [
        "E104"
    ]


async def test_health_idempotency_reservation_reuses_first_timestamp(
    mongo_database,
) -> None:
    await mongo_database.elderly_profiles.insert_one(
        {"elderly_id": "E201", "active": True}
    )
    dispatcher = IntegrationDispatcher()
    service = HealthEventService(
        mongo_database.elderly_profiles,
        mongo_database.health_idempotency,
        mongo_database.health_logs,
        mongo_database.alerts,
        dispatcher,
    )
    event_id = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")

    def request() -> HealthEventCreate:
        return HealthEventCreate(
            elderly_id="E201",
            heart_rate=80,
            temperature=36.7,
            oxygen_level=97,
            movement_status="active",
            medicine_status="taken",
        )

    first = await service.queue_event(request(), event_id, "shared-key")
    second = await service.queue_event(request(), event_id, "shared-key")
    stored = await mongo_database.health_idempotency.find_one(
        {"elderly_id": "E201"}
    )
    indexes = await mongo_database.health_idempotency.index_information()

    assert first.recorded_at == second.recorded_at == stored["recorded_at"]
    assert await mongo_database.health_idempotency.count_documents({}) == 1
    assert stored["key_hash"] == hash_idempotency_key("shared-key")
    assert "shared-key" not in stored.values()
    assert indexes["unique_health_idempotency_key"]["unique"] is True


@pytest.mark.parametrize(
    ("collection_name", "normal_index", "legacy_index", "recorded_index"),
    [
        (
            "activity_logs",
            "activity_history_latest",
            "activity_history_legacy",
            "activity_history_legacy_recorded",
        ),
        (
            "device_events",
            "device_history_latest",
            "device_history_legacy",
            "device_history_legacy_recorded",
        ),
    ],
)
async def test_telemetry_history_paths_use_bounded_indexes_without_blocking_sort(
    mongo_database,
    collection_name: str,
    normal_index: str,
    legacy_index: str,
    recorded_index: str,
) -> None:
    collection = mongo_database[collection_name]
    now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    documents = []
    for index in range(40):
        document = {
            "event_id": f"normal-{index:03d}",
            "elderly_id": "E946",
            "recorded_at": now + timedelta(seconds=index),
            "received_at": now + timedelta(seconds=index),
            "created_at": now + timedelta(seconds=index),
        }
        if collection_name == "activity_logs":
            document["value"] = "inactive"
        documents.append(document)
    for index in range(40):
        document = {
            "event_id": f"legacy-created-{index:03d}",
            "elderly_id": "E946",
            "recorded_at": now + timedelta(days=30, seconds=index),
            "created_at": now - timedelta(seconds=index),
        }
        if collection_name == "activity_logs":
            document["value"] = "inactive"
        documents.append(document)
    for index in range(40):
        document = {
            "event_id": f"legacy-recorded-{index:03d}",
            "elderly_id": "E946",
            "recorded_at": now - timedelta(days=1, seconds=index),
        }
        if collection_name == "activity_logs":
            document["value"] = "inactive"
        documents.append(document)
    await collection.insert_many(documents)

    normal_explain = await (
        collection.find(
            {"elderly_id": "E946", "received_at": {"$exists": True}}
        )
        .sort([("received_at", -1), ("event_id", -1)])
        .hint(normal_index)
        .limit(10)
        .explain()
    )
    legacy_explain = await (
        collection.find(
            {
                "elderly_id": "E946",
                "received_at": {"$exists": False},
                "created_at": {"$exists": True},
            }
        )
        .sort([("created_at", -1), ("event_id", -1)])
        .hint(legacy_index)
        .limit(10)
        .explain()
    )
    recorded_explain = await (
        collection.find(
            {
                "elderly_id": "E946",
                "received_at": {"$exists": False},
                "created_at": {"$exists": False},
            }
        )
        .sort([("recorded_at", -1), ("event_id", -1)])
        .hint(recorded_index)
        .limit(10)
        .explain()
    )

    assert normal_index in str(normal_explain)
    assert legacy_index in str(legacy_explain)
    assert recorded_index in str(recorded_explain)
    assert "'stage': 'SORT'" not in str(normal_explain)
    assert "'stage': 'SORT'" not in str(legacy_explain)
    assert "'stage': 'SORT'" not in str(recorded_explain)


def test_startup_runs_activity_state_migration_once() -> None:
    settings = get_settings()
    ensure_safe_test_database_name(settings.database_name)
    client = MongoClient(settings.mongo_uri, tz_aware=True)
    client.drop_database(settings.database_name)
    database = client[settings.database_name]
    now = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)
    database.elderly_profiles.insert_many(
        [
            {
                "elderly_id": elderly_id,
                "full_name": f"Legacy Profile {elderly_id}",
                "date_of_birth": datetime(1940, 1, 1),
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
            for elderly_id in ("E947", "E948", "E949")
        ]
    )
    database.activity_logs.insert_many(
        [
            {
                "event_id": "00000000-0000-4000-8000-000000000947",
                "elderly_id": "E947",
                "value": "active",
                "recorded_at": now - timedelta(days=2),
                "created_at": now,
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000948",
                "elderly_id": "E947",
                "value": "inactive",
                "recorded_at": now - timedelta(days=1),
                "created_at": now + timedelta(minutes=1),
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000949",
                "elderly_id": "E947",
                "value": "inactive",
                "recorded_at": now - timedelta(days=3),
                "created_at": now + timedelta(minutes=2),
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000950",
                "elderly_id": "E948",
                "value": "inactive",
                "recorded_at": now,
                "created_at": now,
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000951",
                "elderly_id": "E948",
                "value": "active",
                "recorded_at": now,
                "created_at": now + timedelta(minutes=3),
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000952",
                "elderly_id": "E949",
                "value": "inactive",
                "recorded_at": now,
                "created_at": now,
            },
        ]
    )
    database.alerts.insert_many(
        [
            {
                "event_id": "00000000-0000-4000-8000-000000000953",
                "elderly_id": "E947",
                "alert_type": "long_inactivity",
                "episode_id": "activity:E947:earliest-alert",
                "severity": "warning",
                "status": "unresolved",
                "message": "Earliest legacy inactivity alert",
                "created_at": now + timedelta(minutes=3),
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000954",
                "elderly_id": "E947",
                "alert_type": "long_inactivity",
                "episode_id": "activity:E947:later-alert",
                "severity": "warning",
                "status": "unresolved",
                "message": "Later legacy inactivity alert",
                "created_at": now + timedelta(minutes=4),
            },
            {
                "event_id": "00000000-0000-4000-8000-000000000955",
                "elderly_id": "E948",
                "alert_type": "long_inactivity",
                "episode_id": "activity:E948:legacy-alert",
                "severity": "warning",
                "status": "unresolved",
                "message": "Resolved by reconstructed active state",
                "created_at": now + timedelta(minutes=1),
            },
        ]
    )
    database.activity_state.insert_one(
        {
            "elderly_id": "E949",
            "event_id": "newer-state",
            "value": "active",
            "received_at": now + timedelta(minutes=10),
            "inactive_since": None,
            "episode_id": None,
            "alerted_at": None,
            "updated_at": now + timedelta(minutes=10),
        }
    )

    try:
        with TestClient(app) as api:
            activity_response = api.get("/api/activity/E947")

        assert activity_response.status_code == 200
        inactive_state = database.activity_state.find_one({"elderly_id": "E947"})
        assert inactive_state["value"] == "inactive"
        assert inactive_state["inactive_since"] == now + timedelta(minutes=1)
        assert inactive_state["episode_id"] == "activity:E947:earliest-alert"
        assert inactive_state["alerted_at"] == now + timedelta(minutes=3)
        assert database.alerts.count_documents(
            {
                "elderly_id": "E947",
                "alert_type": "long_inactivity",
                "status": "unresolved",
            }
        ) == 2

        active_state = database.activity_state.find_one({"elderly_id": "E948"})
        assert active_state["value"] == "active"
        resolved_alert = database.alerts.find_one({"elderly_id": "E948"})
        assert resolved_alert["status"] == "resolved"
        assert resolved_alert["resolved_at"] == now + timedelta(minutes=3)

        preserved = database.activity_state.find_one({"elderly_id": "E949"})
        assert preserved["event_id"] == "newer-state"
        marker = database.schema_migrations.find_one(
            {"_id": ACTIVITY_STATE_MIGRATION_ID}
        )
        assert marker is not None

        database.elderly_profiles.insert_one(
            {
                "elderly_id": "E950",
                "full_name": "Post-migration Profile",
                "date_of_birth": datetime(1940, 1, 1),
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
        )
        database.activity_logs.insert_one(
            {
                "event_id": "00000000-0000-4000-8000-000000000956",
                "elderly_id": "E950",
                "value": "inactive",
                "recorded_at": now,
                "created_at": now + timedelta(minutes=20),
            }
        )

        with TestClient(app):
            pass

        assert database.activity_state.find_one({"elderly_id": "E950"}) is None
        assert database.schema_migrations.find_one(
            {"_id": ACTIVITY_STATE_MIGRATION_ID}
        )["completed_at"] == marker["completed_at"]
    finally:
        client.drop_database(settings.database_name)
        client.close()
