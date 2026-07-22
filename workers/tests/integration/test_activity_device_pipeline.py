import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import permutations
from threading import Barrier
from uuid import uuid4

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from workers.activity_worker import (
    acquire_scan_lease,
    persist_activity_event,
    renew_scan_lease,
    scan_inactive_profiles,
)
from workers.celery_app import celery_app
from workers.database import create_indexes
from workers.device_worker import persist_device_event, scan_offline_devices
from workers.health_worker import EventPayloadConflict, derive_public_alert_id
from workers.service_health import check_service_health


pytestmark = pytest.mark.integration
BASE = datetime(2026, 7, 17, 8, 0, tzinfo=UTC)


def activity(
    value: str,
    seconds: int,
    event_id: str | None = None,
    elderly_id: str = "E901",
) -> dict[str, object]:
    return {
        "event_id": event_id or str(uuid4()),
        "elderly_id": elderly_id,
        "value": value,
        "recorded_at": BASE + timedelta(seconds=seconds),
        "received_at": BASE + timedelta(seconds=seconds),
    }


def heartbeat(seconds: int, event_id: str | None = None) -> dict[str, object]:
    return {
        "event_id": event_id or str(uuid4()),
        "elderly_id": "E901",
        "recorded_at": BASE + timedelta(seconds=seconds),
        "received_at": BASE + timedelta(seconds=seconds),
    }


def run_concurrently(*operations):
    barrier = Barrier(len(operations))

    def invoke(operation):
        barrier.wait()
        return operation()

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(invoke, operation) for operation in operations]
        return [future.result() for future in futures]


@pytest.fixture
def mongo_database() -> Iterator[object]:
    uri = os.environ.get("MONGO_URI", "mongodb://mongodb:27017/?replicaSet=rs0")
    name = os.environ.get("DATABASE_NAME", "kindcare_integration_test")
    if name != "kindcare_integration_test" and not name.startswith("kindcare_test_"):
        raise ValueError(f"Refusing to drop non-test database: {name}")
    client = MongoClient(uri, tz_aware=True)
    client.drop_database(name)
    database = client[name]
    create_indexes(database)
    yield database
    client.drop_database(name)
    client.close()


def test_real_mongo_inactivity_scanner_alerts_without_later_sample_and_resolves(mongo_database: object) -> None:
    persist_activity_event(activity("active", 0), mongo_database, 60)
    persist_activity_event(activity("inactive", 10), mongo_database, 60)
    assert scan_inactive_profiles(mongo_database, BASE + timedelta(seconds=69), 60, 10, 30) == 0
    assert scan_inactive_profiles(mongo_database, BASE + timedelta(seconds=70), 60, 10, 30) == 1
    original_alert_id = mongo_database.alerts.find_one(
        {"alert_type": "long_inactivity"}
    )["_id"]
    assert mongo_database.alerts.find_one(
        {"alert_type": "long_inactivity"}
    )["severity"] == "warning"
    assert scan_inactive_profiles(mongo_database, BASE + timedelta(seconds=71), 60, 10, 30) == 0
    assert mongo_database.alerts.count_documents({"alert_type": "long_inactivity"}) == 1
    assert (
        mongo_database.alerts.find_one({"alert_type": "long_inactivity"})["_id"]
        == original_alert_id
    )
    persist_activity_event(activity("active", 100), mongo_database, 60)
    alert = mongo_database.alerts.find_one({"alert_type": "long_inactivity"})
    assert alert["status"] == "resolved"
    assert alert["resolved_at"] == BASE + timedelta(seconds=100)


def test_reconstructed_e947_state_is_scanner_visible_and_later_active_resolves(
    mongo_database: object,
) -> None:
    mongo_database.activity_state.insert_one(
        {
            "elderly_id": "E947",
            "event_id": "legacy-inactive",
            "value": "inactive",
            "received_at": BASE,
            "inactive_since": BASE,
            "episode_id": "activity:E947:legacy-inactive",
            "alerted_at": None,
            "updated_at": BASE,
        }
    )

    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=60), 60, 10, 30
    ) == 1
    alert = mongo_database.alerts.find_one({"elderly_id": "E947"})
    assert alert["status"] == "unresolved"

    persist_activity_event(
        activity("active", 61, elderly_id="E947"), mongo_database, 60
    )

    alert = mongo_database.alerts.find_one({"elderly_id": "E947"})
    assert alert["status"] == "resolved"
    assert alert["resolved_at"] == BASE + timedelta(seconds=61)


def test_reconstructed_alerted_state_cannot_create_duplicate_alert(
    mongo_database: object,
) -> None:
    mongo_database.activity_state.insert_one(
        {
            "elderly_id": "E947",
            "event_id": "legacy-latest",
            "value": "inactive",
            "received_at": BASE,
            "inactive_since": BASE,
            "episode_id": "legacy-origin",
            "alerted_at": BASE + timedelta(seconds=30),
            "updated_at": BASE,
        }
    )
    mongo_database.alerts.insert_one(
        {
            "event_id": "legacy-alert-event",
            "elderly_id": "E947",
            "alert_type": "long_inactivity",
            "episode_id": "legacy-origin",
            "severity": "warning",
            "status": "unresolved",
            "message": "Legacy inactivity",
            "created_at": BASE + timedelta(seconds=30),
        }
    )

    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=120), 60, 10, 30
    ) == 0
    assert mongo_database.alerts.count_documents(
        {"elderly_id": "E947", "alert_type": "long_inactivity"}
    ) == 1


def test_active_event_resolves_all_unresolved_inactivity_alert_origins(
    mongo_database: object,
) -> None:
    mongo_database.activity_state.insert_one(
        {
            "elderly_id": "E947",
            "event_id": "legacy-latest",
            "value": "inactive",
            "received_at": BASE,
            "inactive_since": BASE,
            "episode_id": "current-origin",
            "alerted_at": BASE + timedelta(seconds=30),
            "updated_at": BASE,
        }
    )
    mongo_database.alerts.insert_many(
        [
                {
                    "alert_id": derive_public_alert_id(
                        f"legacy-alert-{index}", "long_inactivity"
                    ),
                    "event_id": f"legacy-alert-{index}",
                "elderly_id": "E947",
                "alert_type": "long_inactivity",
                "episode_id": episode_id,
                "severity": "warning",
                "status": "unresolved",
                "message": "Legacy inactivity",
                "created_at": BASE + timedelta(seconds=index),
            }
            for index, episode_id in enumerate(
                ("older-origin", "current-origin"), start=1
            )
        ]
    )

    persist_activity_event(
        activity("active", 61, elderly_id="E947"), mongo_database, 60
    )

    alerts = list(
        mongo_database.alerts.find(
            {"elderly_id": "E947", "alert_type": "long_inactivity"}
        )
    )
    assert len(alerts) == 2
    assert all(alert["status"] == "resolved" for alert in alerts)
    assert all(
        alert["resolved_at"] == BASE + timedelta(seconds=61) for alert in alerts
    )


def test_newer_active_event_resolves_alerts_when_state_is_already_active(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("active", 0, elderly_id="E947"), mongo_database, 60)
    mongo_database.alerts.insert_many(
        [
                {
                    "alert_id": derive_public_alert_id(
                        f"stranded-alert-{index}", "long_inactivity"
                    ),
                    "event_id": f"stranded-alert-{index}",
                "elderly_id": "E947",
                "alert_type": "long_inactivity",
                "episode_id": f"stranded-origin-{index}",
                "severity": "warning",
                "status": "unresolved",
                "message": "Stranded legacy inactivity",
                "created_at": BASE + timedelta(seconds=index),
            }
            for index in range(2)
        ]
    )

    persist_activity_event(
        activity("active", 61, elderly_id="E947"), mongo_database, 60
    )

    alerts = list(
        mongo_database.alerts.find(
            {"elderly_id": "E947", "alert_type": "long_inactivity"}
        )
    )
    assert all(alert["status"] == "resolved" for alert in alerts)
    assert all(
        alert["resolved_at"] == BASE + timedelta(seconds=61) for alert in alerts
    )


def test_real_mongo_activity_conflict_is_atomic(mongo_database: object) -> None:
    event_id = str(uuid4())
    persist_activity_event(activity("active", 0, event_id), mongo_database, 60)
    with pytest.raises(EventPayloadConflict):
        persist_activity_event(activity("inactive", 0, event_id), mongo_database, 60)
    assert mongo_database.activity_logs.find_one({"event_id": event_id})["value"] == "active"


def test_activity_scan_query_uses_equality_prefix_range_sort_index(mongo_database: object) -> None:
    persist_activity_event(activity("inactive", 0), mongo_database, 60)

    indexes = mongo_database.activity_state.index_information()
    explanation = (
        mongo_database.activity_state.find(
            {
                "value": "inactive",
                "alerted_at": None,
                "inactive_since": {"$lte": BASE + timedelta(seconds=60)},
            }
        )
        .sort([("inactive_since", 1), ("elderly_id", 1)])
        .limit(10)
        .explain()
    )

    assert indexes["activity_inactivity_scan"]["key"] == [
        ("value", 1),
        ("alerted_at", 1),
        ("inactive_since", 1),
        ("elderly_id", 1),
    ]
    assert "activity_inactivity_scan" in str(explanation)


def test_scan_lease_excludes_owner_renews_and_expires(mongo_database: object) -> None:
    first_owner = acquire_scan_lease(mongo_database, "lease-test", BASE, 30)

    assert first_owner is not None
    assert acquire_scan_lease(mongo_database, "lease-test", BASE, 30) is None
    assert renew_scan_lease(
        mongo_database, "lease-test", first_owner, BASE + timedelta(seconds=20), 30
    ) is True
    assert acquire_scan_lease(
        mongo_database, "lease-test", BASE + timedelta(seconds=31), 30
    ) is None
    replacement = acquire_scan_lease(
        mongo_database, "lease-test", BASE + timedelta(seconds=51), 30
    )
    assert replacement is not None
    assert replacement != first_owner
    assert renew_scan_lease(
        mongo_database, "lease-test", first_owner, BASE + timedelta(seconds=52), 30
    ) is False


def test_scanner_aborts_remaining_batch_when_lease_ownership_is_lost(mongo_database: object) -> None:
    for elderly_id in ("E901", "E902"):
        mongo_database.activity_state.insert_one(
            {
                "elderly_id": elderly_id,
                "event_id": str(uuid4()),
                "value": "inactive",
                "received_at": BASE,
                "inactive_since": BASE,
                "episode_id": f"activity:{elderly_id}:episode",
                "alerted_at": None,
                "updated_at": BASE,
            }
        )
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        if calls == 2:
            mongo_database.scan_leases.update_one(
                {"_id": "inactivity"},
                {"$set": {"owner": "replacement-owner", "expires_at": BASE + timedelta(seconds=31)}},
            )
        return BASE + timedelta(seconds=1)

    created = scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=1), 0.001, 10, 30, clock=clock
    )

    assert created == 1
    assert mongo_database.alerts.count_documents({"alert_type": "long_inactivity"}) == 1
    assert mongo_database.scan_leases.find_one({"_id": "inactivity"})["owner"] == "replacement-owner"


def test_simultaneous_first_inactive_events_create_one_latest_state(mongo_database: object) -> None:
    first = activity("inactive", 0)
    second = activity("inactive", 1)

    run_concurrently(
        lambda: persist_activity_event(first, mongo_database, 60),
        lambda: persist_activity_event(second, mongo_database, 60),
    )

    state = mongo_database.activity_state.find_one({"elderly_id": "E901"})
    assert mongo_database.activity_logs.count_documents({"elderly_id": "E901"}) == 2
    assert mongo_database.activity_state.count_documents({"elderly_id": "E901"}) == 1
    assert state["event_id"] == second["event_id"]
    assert state["inactive_since"] == first["received_at"]


def test_older_inactive_cannot_move_anchor_across_later_active(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("inactive", 20), mongo_database, 60)
    persist_activity_event(activity("active", 10), mongo_database, 60)
    persist_activity_event(activity("inactive", 5), mongo_database, 60)

    state = mongo_database.activity_state.find_one({"elderly_id": "E901"})

    assert state["value"] == "inactive"
    assert state["received_at"] == BASE + timedelta(seconds=20)
    assert state["inactive_since"] == BASE + timedelta(seconds=20)


def test_late_active_recomputes_anchor_after_older_inactive_arrived(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("inactive", 20), mongo_database, 60)
    persist_activity_event(activity("inactive", 5), mongo_database, 60)
    persist_activity_event(activity("active", 10), mongo_database, 60)

    state = mongo_database.activity_state.find_one({"elderly_id": "E901"})

    assert state["value"] == "inactive"
    assert state["received_at"] == BASE + timedelta(seconds=20)
    assert state["inactive_since"] == BASE + timedelta(seconds=20)


@pytest.mark.parametrize(
    "arrival_order",
    list(permutations(("current", "older", "active"))),
    ids=lambda order: "-".join(order),
)
@pytest.mark.parametrize(
    ("active_id", "expected_value", "expected_event_id", "expected_anchor"),
    [
        ("id-b", "inactive", "id-c", BASE + timedelta(seconds=20)),
        ("id-d", "active", "id-d", None),
    ],
    ids=("active-before-current", "active-after-current"),
)
def test_equal_timestamp_event_ids_define_activity_order_for_every_arrival_permutation(
    mongo_database: object,
    arrival_order: tuple[str, ...],
    active_id: str,
    expected_value: str,
    expected_event_id: str,
    expected_anchor: datetime | None,
) -> None:
    events = {
        "current": activity("inactive", 20, "id-c"),
        "older": activity("inactive", 5, "id-a"),
        "active": activity("active", 20, active_id),
    }

    for name in arrival_order:
        persist_activity_event(events[name], mongo_database, 60)

    state = mongo_database.activity_state.find_one({"elderly_id": "E901"})
    assert state["value"] == expected_value
    assert state["event_id"] == expected_event_id
    assert state["received_at"] == BASE + timedelta(seconds=20)
    assert state["inactive_since"] == expected_anchor


def test_equal_timestamp_late_active_recomputes_scanner_boundary(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("inactive", 20, "id-c"), mongo_database, 60)
    persist_activity_event(activity("inactive", 5, "id-a"), mongo_database, 60)
    persist_activity_event(activity("active", 20, "id-b"), mongo_database, 60)

    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=79), 60, 10, 30
    ) == 0
    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=80), 60, 10, 30
    ) == 1


def test_late_active_does_not_resolve_alert_for_current_inactive_state(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("inactive", 20), mongo_database, 60)
    persist_activity_event(activity("inactive", 5), mongo_database, 60)
    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=65), 60, 10, 30
    ) == 1

    persist_activity_event(activity("active", 10), mongo_database, 60)

    state = mongo_database.activity_state.find_one({"elderly_id": "E901"})
    alert = mongo_database.alerts.find_one(
        {"elderly_id": "E901", "alert_type": "long_inactivity"}
    )
    assert state["value"] == "inactive"
    assert state["inactive_since"] == BASE + timedelta(seconds=20)
    assert alert["status"] == "unresolved"


def test_recomputed_inactive_anchor_controls_scanner_boundary(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("inactive", 20), mongo_database, 60)
    persist_activity_event(activity("inactive", 5), mongo_database, 60)
    persist_activity_event(activity("active", 10), mongo_database, 60)

    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=79), 60, 10, 30
    ) == 0
    assert scan_inactive_profiles(
        mongo_database, BASE + timedelta(seconds=80), 60, 10, 30
    ) == 1


def test_older_inactive_moves_anchor_when_no_active_boundary_exists(
    mongo_database: object,
) -> None:
    persist_activity_event(activity("inactive", 20), mongo_database, 60)
    persist_activity_event(activity("inactive", 5), mongo_database, 60)

    state = mongo_database.activity_state.find_one({"elderly_id": "E901"})

    assert state["received_at"] == BASE + timedelta(seconds=20)
    assert state["inactive_since"] == BASE + timedelta(seconds=5)


def test_active_event_racing_inactivity_scan_leaves_no_unresolved_alert(mongo_database: object) -> None:
    persist_activity_event(activity("inactive", 0), mongo_database, 60)

    run_concurrently(
        lambda: scan_inactive_profiles(
            mongo_database, BASE + timedelta(seconds=60), 60, 10, 30
        ),
        lambda: persist_activity_event(activity("active", 61), mongo_database, 60),
    )

    assert mongo_database.activity_state.find_one({"elderly_id": "E901"})["value"] == "active"
    assert mongo_database.alerts.count_documents(
        {"elderly_id": "E901", "alert_type": "long_inactivity", "status": "unresolved"}
    ) == 0


def test_heartbeat_racing_offline_scan_leaves_device_online(mongo_database: object) -> None:
    persist_device_event(heartbeat(0), mongo_database)

    run_concurrently(
        lambda: scan_offline_devices(
            mongo_database, BASE + timedelta(seconds=120), 120, 10, 30
        ),
        lambda: persist_device_event(heartbeat(121), mongo_database),
    )

    assert mongo_database.device_status.find_one({"elderly_id": "E901"})["status"] == "online"
    assert mongo_database.alerts.count_documents(
        {"elderly_id": "E901", "alert_type": "device_offline", "status": "unresolved"}
    ) == 0


def test_overlapping_scanners_have_one_lease_owner(mongo_database: object) -> None:
    owners = run_concurrently(
        lambda: acquire_scan_lease(mongo_database, "overlap", BASE, 30),
        lambda: acquire_scan_lease(mongo_database, "overlap", BASE, 30),
    )

    assert sum(owner is not None for owner in owners) == 1


def test_real_transaction_retries_transient_callback_error(mongo_database: object) -> None:
    class TransientActivityState:
        def __init__(self, collection):
            self.collection = collection
            self.attempts = 0

        def find_one(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise OperationFailure(
                    "retry transaction",
                    code=112,
                    details={"errorLabels": ["TransientTransactionError"]},
                )
            return self.collection.find_one(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.collection, name)

    class DatabaseProxy:
        def __init__(self, database):
            self.database = database
            self.client = database.client
            self.activity_state = TransientActivityState(database.activity_state)

        def __getattr__(self, name):
            return getattr(self.database, name)

    proxy = DatabaseProxy(mongo_database)

    persist_activity_event(activity("inactive", 0), proxy, 60)

    assert proxy.activity_state.attempts == 2
    assert mongo_database.activity_logs.count_documents({"elderly_id": "E901"}) == 1
    assert mongo_database.activity_state.count_documents({"elderly_id": "E901"}) == 1


def test_real_mongo_offline_boundary_dedupes_and_heartbeat_recovers(mongo_database: object) -> None:
    first = heartbeat(0)
    persist_device_event(first, mongo_database)
    assert scan_offline_devices(mongo_database, BASE + timedelta(seconds=119), 120) == 0
    assert scan_offline_devices(mongo_database, BASE + timedelta(seconds=120), 120) == 1
    assert scan_offline_devices(mongo_database, BASE + timedelta(seconds=121), 120) == 0
    assert mongo_database.alerts.count_documents({"alert_type": "device_offline"}) == 1
    persist_device_event(heartbeat(121), mongo_database)
    status = mongo_database.device_status.find_one({"elderly_id": "E901"})
    alert = mongo_database.alerts.find_one({"alert_type": "device_offline"})
    assert status["status"] == "online"
    assert alert["status"] == "resolved"


def test_active_event_resolves_acknowledged_inactivity_alert_profile_wide(mongo_database: object) -> None:
    persist_activity_event(activity("inactive", 0), mongo_database, 60)
    scan_inactive_profiles(mongo_database, BASE + timedelta(seconds=60), 60, 10, 30)
    mongo_database.alerts.update_many(
        {"elderly_id": "E901", "alert_type": "long_inactivity"},
        {"$set": {"status": "acknowledged"}},
    )

    persist_activity_event(activity("active", 61), mongo_database, 60)

    assert mongo_database.alerts.count_documents(
        {"elderly_id": "E901", "alert_type": "long_inactivity", "status": "resolved"}
    ) == 1


def test_heartbeat_resolves_acknowledged_offline_alert_for_same_episode(mongo_database: object) -> None:
    persist_device_event(heartbeat(0), mongo_database)
    scan_offline_devices(mongo_database, BASE + timedelta(seconds=120), 120)
    mongo_database.alerts.update_one(
        {"elderly_id": "E901", "alert_type": "device_offline"},
        {"$set": {"status": "acknowledged"}},
    )

    persist_device_event(heartbeat(121), mongo_database)

    assert mongo_database.alerts.find_one(
        {"elderly_id": "E901", "alert_type": "device_offline"}
    )["status"] == "resolved"


def test_real_mongo_out_of_order_heartbeat_cannot_regress_latest(mongo_database: object) -> None:
    newer = heartbeat(60)
    persist_device_event(newer, mongo_database)
    persist_device_event(heartbeat(30), mongo_database)
    status = mongo_database.device_status.find_one({"elderly_id": "E901"})
    assert status["event_id"] == newer["event_id"]
    assert mongo_database.device_events.count_documents({}) == 2


def test_live_worker_registers_and_processes_activity_and_device_tasks(mongo_database: object) -> None:
    activity_event = activity("active", 0)
    device_event = heartbeat(0)
    celery_app.send_task(
        "workers.activity_worker.process_activity_data",
        args=[activity_event],
        queue="kindcare-integration",
        serializer="json",
    )
    celery_app.send_task(
        "workers.device_worker.process_device_heartbeat",
        args=[device_event],
        queue="kindcare-integration",
        serializer="json",
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if (
            mongo_database.activity_logs.find_one({"event_id": activity_event["event_id"]})
            and mongo_database.device_events.find_one({"event_id": device_event["event_id"]})
        ):
            break
        time.sleep(0.2)
    assert mongo_database.activity_logs.count_documents({"event_id": activity_event["event_id"]}) == 1
    assert mongo_database.device_events.count_documents({"event_id": device_event["event_id"]}) == 1


def test_live_scheduled_heartbeat_proves_broker_worker_and_mongodb(mongo_database: object) -> None:
    celery_app.send_task(
        "workers.service_health.record_service_heartbeat",
        queue="kindcare-integration",
        serializer="json",
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        heartbeat_document = mongo_database.service_health.find_one(
            {"_id": "scheduled-worker"}
        )
        if heartbeat_document is not None:
            break
        time.sleep(0.2)

    assert heartbeat_document is not None
    healthy, reason = check_service_health(
        mongo_database, datetime.now(UTC), 60
    )
    assert (healthy, reason) == (True, "healthy")
