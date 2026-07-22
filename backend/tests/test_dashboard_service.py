from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from app.services.dashboard import DashboardService, DashboardStorageUnavailable
from app.services.elderly import ElderlyProfileNotFound


NOW = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
EVENT_ID = UUID("008b2d23-93e6-5ef5-b676-f629c63c8bbb")
ALERT_ID = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"


def profile_document() -> dict[str, object]:
    return {
        "elderly_id": "E001",
        "full_name": "Margaret Lee",
        "date_of_birth": date(1948, 4, 12),
        "phone_number": "555-0101",
        "address": "10 Garden Road",
        "emergency_contact_name": "Daniel Lee",
        "emergency_contact_phone": "555-0199",
        "medical_notes": "Demo only",
        "active": True,
        "created_at": NOW,
        "updated_at": NOW,
    }


def health_document(risk_level: str = "normal") -> dict[str, object]:
    return {
        "event_id": EVENT_ID,
        "elderly_id": "E001",
        "heart_rate": 82,
        "temperature": 36.8,
        "oxygen_level": 97,
        "blood_pressure": "121/79",
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
        "recorded_at": NOW,
        "risk_level": risk_level,
        "created_at": NOW,
    }


def activity_document() -> dict[str, object]:
    return {
        "event_id": EVENT_ID,
        "elderly_id": "E001",
        "value": "inactive",
        "recorded_at": NOW,
        "received_at": NOW,
        "created_at": NOW,
    }


def device_document() -> dict[str, object]:
    return {
        "event_id": EVENT_ID,
        "elderly_id": "E001",
        "status": "online",
        "last_seen": NOW,
        "updated_at": NOW,
    }


def alert_document(severity: str, alert_type: str = "high_temperature") -> dict[str, object]:
    return {
        "alert_id": ALERT_ID,
        "event_id": EVENT_ID,
        "elderly_id": "E001",
        "alert_type": alert_type,
        "severity": severity,
        "status": "unresolved",
        "message": f"{severity} condition",
        "created_at": NOW,
    }


class ProfileCollection:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document
        self.query = None

    async def find_one(self, query: dict[str, object]):
        self.query = query
        return self.document


class HealthCollection:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document
        self.sort = None

    async def find_one(self, query: dict[str, object], *, sort: list[tuple[str, int]]):
        self.sort = sort
        return self.document


class ActivityCollection:
    def __init__(self, document: dict[str, object] | None) -> None:
        self.document = document
        self.pipeline = None
        self.find_queries = []
        self.normal = AlertCursor([document] if document is not None else [])
        self.legacy_created = AlertCursor([])
        self.legacy_recorded = AlertCursor([])

    async def aggregate(self, pipeline):
        self.pipeline = pipeline
        return AlertCursor([self.document] if self.document is not None else [])

    def find(self, query):
        self.find_queries.append(query)
        if query["received_at"] == {"$exists": True}:
            return self.normal
        if query["created_at"] == {"$exists": True}:
            return self.legacy_created
        return self.legacy_recorded


class AlertCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents
        self.sort_keys = None
        self.limit_value = None

    def sort(self, keys: list[tuple[str, int]]):
        self.sort_keys = keys
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def hint(self, name: str):
        self.hint_name = name
        return self

    async def to_list(self, length: int):
        return self.documents[:length]


class AlertCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.cursor = AlertCursor(documents)
        self.documents = documents
        self.find_calls = 0
        self.find_one_calls = 0
        self.find_one_queries = []
        self.find_one_sorts = []
        self.find_one_hints = []

    def find(self, query: dict[str, object]):
        self.find_calls += 1
        return self.cursor

    async def find_one(
        self,
        query: dict[str, object],
        projection: dict[str, int] | None = None,
        *,
        sort: list[tuple[str, int]] | None = None,
        hint: str | None = None,
    ):
        self.find_one_calls += 1
        self.find_one_queries.append(query)
        self.find_one_sorts.append(sort)
        self.find_one_hints.append(hint)
        statuses = (
            query["status"].get("$in", [])
            if isinstance(query["status"], dict)
            else [query["status"]]
        )
        matches = [
            document
            for document in self.documents
            if document["severity"] == query["severity"]
            and document["status"] in statuses
        ]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(
                    key=lambda document: document[field], reverse=direction < 0
                )
        return matches[0] if matches else None


class ReminderCollection:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents
        self.queries = []
        self.cursors = []

    def find(self, query):
        self.queries.append(query)
        cursor = AlertCursor(self.documents)
        self.cursors.append(cursor)
        return cursor


@pytest.mark.asyncio
async def test_summary_combines_latest_data_highest_unresolved_risk_and_bounded_alerts() -> None:
    profiles = ProfileCollection(profile_document())
    health = HealthCollection(health_document())
    alerts = AlertCollection(
        [alert_document("warning") for _ in range(15)],
    )
    service = DashboardService(profiles, health, alerts, recent_alert_limit=10)

    summary = await service.get_summary("E001")

    assert profiles.query == {"elderly_id": "E001", "active": True}
    assert health.sort == [("recorded_at", -1), ("event_id", -1)]
    assert summary.profile.full_name == "Margaret Lee"
    assert summary.latest_health.heart_rate == 82
    assert summary.current_risk == "warning"
    assert summary.current_alert is not None
    assert summary.current_alert.severity == "warning"
    assert len(summary.recent_alerts) == 10
    assert alerts.cursor.limit_value == 10
    assert alerts.cursor.sort_keys == [
        ("created_at", -1),
        ("event_id", -1),
        ("alert_type", 1),
    ]
    assert alerts.find_calls == 1
    assert alerts.find_one_calls == 3
    assert alerts.find_one_sorts == [
        [("created_at", -1), ("event_id", -1), ("alert_type", 1)]
    ] * 3
    assert alerts.find_one_hints == ["alert_current_risk"] * 3


@pytest.mark.asyncio
async def test_summary_adds_latest_activity_and_device_with_deterministic_queries() -> None:
    activity = ActivityCollection(activity_document())
    devices = HealthCollection(device_document())
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(None),
        AlertCollection([]),
        activity_logs=activity,
        device_status=devices,
    )

    summary = await service.get_summary("E001")

    assert summary.latest_activity.value == "inactive"
    assert summary.device_status.status == "online"
    assert activity.pipeline is None
    assert activity.find_queries == [
        {"elderly_id": "E001", "received_at": {"$exists": True}},
        {
            "elderly_id": "E001",
            "received_at": {"$exists": False},
            "created_at": {"$exists": True},
        },
        {
            "elderly_id": "E001",
            "received_at": {"$exists": False},
            "created_at": {"$exists": False},
        },
    ]
    assert activity.normal.limit_value == activity.legacy_created.limit_value == 1
    assert activity.legacy_recorded.limit_value == 1
    assert activity.normal.hint_name == "activity_history_latest"
    assert activity.legacy_created.hint_name == "activity_history_legacy"
    assert activity.legacy_recorded.hint_name == "activity_history_legacy_recorded"
    assert devices.sort == [("last_seen", -1), ("event_id", -1)]


@pytest.mark.asyncio
async def test_latest_health_can_outrank_unresolved_alerts() -> None:
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(health_document("emergency")),
        AlertCollection([alert_document("warning")]),
    )

    summary = await service.get_summary("E001")

    assert summary.current_risk == "emergency"
    assert summary.current_alert is None


@pytest.mark.asyncio
async def test_current_alert_prefers_latest_unresolved_over_newer_acknowledged() -> None:
    acknowledged = alert_document("warning", "missed_reminder")
    acknowledged["status"] = "acknowledged"
    acknowledged["created_at"] = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    unresolved_old = alert_document("warning", "device_offline")
    unresolved_old["created_at"] = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    unresolved_latest = alert_document("warning", "inactivity")
    unresolved_latest["created_at"] = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    alerts = AlertCollection([acknowledged, unresolved_old, unresolved_latest])
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(health_document("normal")),
        alerts,
    )

    summary = await service.get_summary("E001")

    assert summary.current_risk == "warning"
    assert summary.current_alert is not None
    assert summary.current_alert.alert_type == "inactivity"
    assert alerts.find_one_queries[-1]["status"] == "unresolved"
    assert alerts.find_one_sorts[-1] == [
        ("created_at", -1),
        ("event_id", -1),
        ("alert_type", 1),
    ]


@pytest.mark.asyncio
async def test_unresolved_alert_can_outrank_latest_health() -> None:
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(health_document("warning")),
        AlertCollection([alert_document("emergency", "low_oxygen")]),
    )

    summary = await service.get_summary("E001")

    assert summary.current_risk == "emergency"


@pytest.mark.asyncio
async def test_unresolved_alert_outside_recent_window_still_sets_current_risk() -> None:
    recent_resolved = [alert_document("warning") for _ in range(10)]
    for alert in recent_resolved:
        alert["status"] = "resolved"
    old_emergency = alert_document("emergency", "device_offline")
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(health_document("normal")),
        AlertCollection([*recent_resolved, old_emergency]),
        recent_alert_limit=10,
    )

    summary = await service.get_summary("E001")

    assert summary.current_risk == "emergency"
    assert summary.current_alert is not None
    assert summary.current_alert.alert_type == "device_offline"
    assert summary.current_alert not in summary.recent_alerts


@pytest.mark.asyncio
async def test_resolved_alert_does_not_override_latest_health() -> None:
    resolved = alert_document("emergency", "low_oxygen")
    resolved["status"] = "resolved"
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(health_document("warning")),
        AlertCollection([resolved]),
    )

    summary = await service.get_summary("E001")

    assert summary.current_risk == "warning"


@pytest.mark.asyncio
async def test_acknowledged_alert_remains_part_of_current_risk() -> None:
    acknowledged = alert_document("warning", "missed_reminder")
    acknowledged["status"] = "acknowledged"
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(health_document("normal")),
        AlertCollection([acknowledged]),
    )

    summary = await service.get_summary("E001")

    assert summary.current_risk == "warning"


@pytest.mark.asyncio
async def test_summary_empty_monitoring_state_is_normal() -> None:
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(None),
        AlertCollection([]),
    )

    summary = await service.get_summary("E001")

    assert summary.latest_health is None
    assert summary.current_risk == "normal"
    assert summary.current_alert is None
    assert summary.recent_alerts == []


@pytest.mark.asyncio
async def test_summary_rejects_missing_or_inactive_profile() -> None:
    service = DashboardService(
        ProfileCollection(None), HealthCollection(None), AlertCollection([])
    )

    with pytest.raises(ElderlyProfileNotFound, match="E404"):
        await service.get_summary("E404")


@pytest.mark.asyncio
async def test_summary_maps_mongodb_failure_to_storage_unavailable() -> None:
    class BrokenProfiles:
        async def find_one(self, query: dict[str, object]):
            raise ServerSelectionTimeoutError("offline")

    service = DashboardService(BrokenProfiles(), HealthCollection(None), AlertCollection([]))

    with pytest.raises(DashboardStorageUnavailable):
        await service.get_summary("E001")


@pytest.mark.asyncio
async def test_summary_includes_bounded_upcoming_and_recent_reminder_statuses() -> None:
    reminder = {
        "reminder_id": "7acdc1d0-0e14-54ce-bc9f-25c10297e6b7",
        "elderly_id": "E001",
        "medicine_name": "Aspirin",
        "scheduled_for": NOW,
        "status": "pending",
        "created_at": NOW,
        "updated_at": NOW,
    }
    reminders = ReminderCollection([reminder] * 8)
    service = DashboardService(
        ProfileCollection(profile_document()),
        HealthCollection(None),
        AlertCollection([]),
        reminders=reminders,
        upcoming_reminder_limit=3,
        recent_reminder_limit=4,
        clock=lambda: NOW,
    )

    summary = await service.get_summary("E001")

    assert len(summary.upcoming_reminders) == 3
    assert len(summary.recent_reminders) == 4
    assert summary.upcoming_reminders[0].status == "pending"
    assert reminders.queries == [
        {"elderly_id": "E001", "status": "pending", "scheduled_for": {"$gte": NOW}},
        {
            "elderly_id": "E001",
            "$or": [
                {"scheduled_for": {"$lt": NOW}},
                {"status": {"$in": ["missed", "taken"]}},
            ],
        },
    ]
    assert [cursor.limit_value for cursor in reminders.cursors] == [3, 4]
