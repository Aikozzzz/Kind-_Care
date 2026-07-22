from datetime import UTC, datetime, timedelta

import pytest

from client_nodes.simulator import build_parser, build_scenario, main


@pytest.mark.parametrize("scenario", ["normal", "warning", "emergency", "inactivity", "offline", "mixed"])
def test_scenarios_generate_valid_readings_for_requested_elderly_id(
    scenario: str,
) -> None:
    readings = list(build_scenario(scenario, "E009", count=4))

    assert len(readings) == 4
    assert {reading["elderly_id"] for reading in readings} == {"E009"}
    assert all(20 <= reading["heart_rate"] <= 250 for reading in readings)
    assert all(25 <= reading["temperature"] <= 45 for reading in readings)
    assert all(50 <= reading["oxygen_level"] <= 100 for reading in readings)
    if scenario == "warning":
        assert all(reading["temperature"] > 38 for reading in readings)
    if scenario == "emergency":
        assert all(reading["emergency_pressed"] for reading in readings)
    if scenario == "inactivity":
        assert all(reading["movement_status"] == "inactive" for reading in readings)


def test_mixed_scenario_cycles_through_monitoring_states() -> None:
    readings = list(build_scenario("mixed", "E001", count=3))

    assert readings[0]["emergency_pressed"] is False
    assert readings[0]["temperature"] <= 38
    assert readings[1]["temperature"] > 38
    assert readings[2]["emergency_pressed"] is True


def test_cli_prints_queued_event_and_uses_configured_options(capsys, monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, url: str, **options: object) -> None:
            calls.append((url, options))

        def send_health(self, payload: dict[str, object]):
            calls.append(("health", payload))
            return type(
                "Result",
                (),
                {"event_id": "event-7", "idempotency_key": "key-7"},
            )()

        def send_activity(self, payload: dict[str, object]):
            calls.append(("activity", payload))
            return type("Result", (), {"event_id": "activity-7", "idempotency_key": "activity-key-7"})()

        def send_heartbeat(self, payload: dict[str, object]):
            calls.append(("heartbeat", payload))
            return type("Result", (), {"event_id": "device-7", "idempotency_key": "device-key-7"})()

    monkeypatch.setattr("client_nodes.simulator.ElderlyNodeClient", FakeClient)

    exit_code = main(
        [
            "--url",
            "http://api:8000",
            "--elderly-id",
            "E007",
            "--scenario",
            "normal",
            "--count",
            "1",
            "--interval",
            "0",
            "--timeout",
            "2",
            "--retries",
            "4",
            "--backoff",
            "0.1",
        ]
    )

    assert exit_code == 0
    assert calls[0] == (
        "http://api:8000",
        {"timeout": 2.0, "max_retries": 4, "backoff": 0.1},
    )
    assert [call[0] for call in calls[1:]] == ["health", "activity", "heartbeat"]
    assert calls[2][1]["value"] == "active"
    assert calls[2][1]["recorded_at"].endswith("Z")
    assert calls[3][1]["recorded_at"] == calls[2][1]["recorded_at"]
    output = capsys.readouterr().out
    assert "[+] health 1/1 elderly=E007 event=event-7 key=key-7" in output
    assert "[+] activity 1/1" in output
    assert "[+] heartbeat 1/1" in output


def test_offline_scenario_intentionally_omits_heartbeat(monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def send_health(self, payload):
            calls.append("health")
            return type("Result", (), {"event_id": "h", "idempotency_key": "hk"})()

        def send_activity(self, payload):
            calls.append("activity")
            return type("Result", (), {"event_id": "a", "idempotency_key": "ak"})()

        def send_heartbeat(self, payload):
            calls.append("heartbeat")
            return type("Result", (), {"event_id": "d", "idempotency_key": "dk"})()

    monkeypatch.setattr("client_nodes.simulator.ElderlyNodeClient", FakeClient)

    assert main(["--scenario", "offline", "--count", "2", "--interval", "0"]) == 0
    assert calls == ["health", "activity", "heartbeat", "health", "activity"]


@pytest.mark.parametrize("demo", ["taken", "missed"])
def test_reminder_demo_creates_once_without_telemetry_or_recurrence(
    demo, capsys, monkeypatch
) -> None:
    calls = []
    list_statuses = iter(["pending", "missed"])

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def create_reminder(self, payload):
            calls.append(("create", payload))
            return type("Result", (), {"reminder_id": "r-1", "status": "pending"})()

        def mark_reminder_taken(self, reminder_id, elderly_id):
            calls.append(("taken", reminder_id, elderly_id))
            return type("Result", (), {"reminder_id": reminder_id, "status": "taken"})()

        def list_reminders(self, elderly_id, limit=50):
            calls.append(("list", elderly_id, limit))
            status = next(list_statuses)
            return [type("Result", (), {"reminder_id": "r-1", "status": status})()]

        def send_health(self, payload):
            raise AssertionError("reminder demos must not send telemetry")

        send_activity = send_health
        send_heartbeat = send_health

    monkeypatch.setattr("client_nodes.simulator.ElderlyNodeClient", FakeClient)
    assert main(
        [
            "--reminder-demo",
            demo,
            "--elderly-id",
            "E007",
            "--reminder-poll-interval",
            "0",
            "--reminder-timeout",
            "1",
        ]
    ) == 0

    assert [call[0] for call in calls].count("create") == 1
    if demo == "taken":
        assert [call[0] for call in calls] == ["create", "taken"]
    else:
        assert [call[0] for call in calls] == ["create", "list", "list"]
        scheduled_for = datetime.fromisoformat(
            calls[0][1]["scheduled_for"].replace("Z", "+00:00")
        )
        assert scheduled_for <= datetime.now(UTC) - timedelta(seconds=301)
    assert f"[+] reminder {demo}" in capsys.readouterr().out


def test_reminder_demo_defaults_cover_grace_and_beat_scan_interval() -> None:
    args = build_parser().parse_args(["--reminder-demo", "missed"])

    assert args.reminder_grace_seconds == 300
    assert args.reminder_timeout == 60


def test_missed_demo_schedules_beyond_custom_grace(monkeypatch) -> None:
    payloads = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def create_reminder(self, payload):
            payloads.append(payload)
            return type("Result", (), {"reminder_id": "r-custom", "status": "pending"})()

        def list_reminders(self, elderly_id, limit=50):
            return [
                type("Result", (), {"reminder_id": "r-custom", "status": "missed"})()
            ]

    monkeypatch.setattr("client_nodes.simulator.ElderlyNodeClient", FakeClient)
    before = datetime.now(UTC)
    assert main(
        [
            "--reminder-demo",
            "missed",
            "--reminder-grace-seconds",
            "600",
        ]
    ) == 0
    after = datetime.now(UTC)
    scheduled_for = datetime.fromisoformat(
        payloads[0]["scheduled_for"].replace("Z", "+00:00")
    )
    assert before - timedelta(seconds=602) <= scheduled_for
    assert scheduled_for <= after - timedelta(seconds=601)


def test_missed_reminder_demo_stops_at_timeout(capsys, monkeypatch) -> None:
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def create_reminder(self, payload):
            calls.append("create")
            return type("Result", (), {"reminder_id": "r-1", "status": "pending"})()

        def list_reminders(self, elderly_id, limit=50):
            calls.append("list")
            return [type("Result", (), {"reminder_id": "r-1", "status": "pending"})()]

    monkeypatch.setattr("client_nodes.simulator.ElderlyNodeClient", FakeClient)
    assert main(
        [
            "--reminder-demo",
            "missed",
            "--reminder-poll-interval",
            "0",
            "--reminder-timeout",
            "0",
        ]
    ) == 1

    assert calls == ["create", "list"]
    assert "[x] reminder missed timeout" in capsys.readouterr().out
