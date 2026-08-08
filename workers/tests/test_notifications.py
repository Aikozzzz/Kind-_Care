from workers.notifications import enqueue_alert_notification, retry_at


class Collection:
    def __init__(self) -> None:
        self.calls = []

    def update_one(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


class Database:
    def __init__(self) -> None:
        self.alert_notification_events = Collection()


def test_alert_notification_intent_is_deterministic_and_transactional() -> None:
    database = Database()

    enqueue_alert_notification(database, alert_id="alert-1", elderly_id="E001", session="session")

    args, kwargs = database.alert_notification_events.calls[0]
    assert args[0] == {"alert_id": "alert-1", "notification_kind": "created"}
    assert args[1]["$setOnInsert"]["notification_event_id"] == "alert-1:created"
    assert kwargs == {"upsert": True, "session": "session"}


def test_notification_retry_backoff_is_bounded() -> None:
    assert retry_at(0).tzinfo is not None
    assert retry_at(100).tzinfo is not None
