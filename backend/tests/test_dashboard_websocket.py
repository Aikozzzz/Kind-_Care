import time
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings, get_settings
from app.dependencies import get_dashboard_hub
from app.main import app
from app.models.dashboard import DashboardSummary
from app.models.elderly import ElderlyProfile
from app.models.health import AlertRecord
from app.services.dashboard import DashboardStorageUnavailable
from app.services.dashboard_live import DashboardHub
from app.services.elderly import ElderlyProfileNotFound
from app.websocket import dashboard_websocket


def make_summary(risk: str = "normal", *, with_alert: bool = False) -> DashboardSummary:
    now = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)
    return DashboardSummary(
        profile=ElderlyProfile(
            elderly_id="E001",
            full_name="Margaret Lee",
            date_of_birth=date(1948, 4, 12),
            active=True,
            created_at=now,
            updated_at=now,
        ),
        latest_health=None,
        current_risk=risk,
        current_alert=(
            AlertRecord(
                alert_id="a74cfda8-d0ef-518e-a671-a2eabca7f6b0",
                event_id="008b2d23-93e6-5ef5-b676-f629c63c8bbb",
                elderly_id="E001",
                alert_type="device_offline",
                severity="warning",
                status="unresolved",
                message="Monitoring device is offline",
                created_at=now,
            )
            if with_alert
            else None
        ),
        recent_alerts=[],
    )


class SequenceDashboardService:
    def __init__(self, values: list[DashboardSummary | Exception]) -> None:
        self.values = values
        self.calls = 0

    async def get_summary(self, elderly_id: str) -> DashboardSummary:
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value


ALLOWED_HEADERS = {"origin": "http://localhost:8501"}


def websocket_client(
    service: SequenceDashboardService,
    heartbeat: float = 5.0,
    poll: float = 0.01,
):
    settings = Settings(
        websocket_poll_interval=poll,
        websocket_heartbeat_interval=heartbeat,
        websocket_allowed_origins="http://localhost:8501",
        websocket_auth_required=False,
    )
    hub = DashboardHub(service, poll_interval=settings.websocket_poll_interval)
    app.dependency_overrides[get_dashboard_hub] = lambda: hub
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), hub


def test_websocket_sends_immediate_typed_summary() -> None:
    client, _ = websocket_client(
        SequenceDashboardService([make_summary("warning", with_alert=True)])
    )
    try:
        with client.websocket_connect(
            "/ws/dashboard/E001", headers=ALLOWED_HEADERS
        ) as socket:
            message = socket.receive_json()
            assert message["type"] == "summary"
            assert message["data"]["profile"]["elderly_id"] == "E001"
            assert message["data"]["current_alert"]["message"] == "Monitoring device is offline"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_websocket_sends_only_changed_summary() -> None:
    service = SequenceDashboardService(
        [make_summary(), make_summary(), make_summary("warning")]
    )
    client, _ = websocket_client(service)
    try:
        with client.websocket_connect(
            "/ws/dashboard/E001", headers=ALLOWED_HEADERS
        ) as socket:
            first = socket.receive_json()
            immediate_heartbeat = socket.receive_json()
            changed = socket.receive_json()
            assert first["data"]["current_risk"] == "normal"
            assert immediate_heartbeat["type"] == "heartbeat"
            assert changed["type"] == "summary"
            assert changed["data"]["current_risk"] == "warning"
            assert service.calls >= 3
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_websocket_emits_typed_heartbeat_while_state_is_unchanged() -> None:
    service = SequenceDashboardService([make_summary()])
    client, _ = websocket_client(
        service, heartbeat=0.03, poll=1
    )
    try:
        with client.websocket_connect(
            "/ws/dashboard/E001", headers=ALLOWED_HEADERS
        ) as socket:
            assert socket.receive_json()["type"] == "summary"
            immediate_heartbeat = socket.receive_json()
            scheduled_heartbeat = socket.receive_json()
            assert immediate_heartbeat["type"] == "heartbeat"
            assert immediate_heartbeat["data"]["interval_seconds"] == 0.03
            assert immediate_heartbeat["data"]["poll_interval_seconds"] == 1
            assert immediate_heartbeat["data"]["last_summary_check_at"].endswith("Z")
            assert scheduled_heartbeat["type"] == "heartbeat"
            assert scheduled_heartbeat["data"]["sent_at"].endswith("Z")
            assert scheduled_heartbeat["data"]["interval_seconds"] == 0.03
            assert service.calls == 1
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_websocket_closes_unknown_profile_with_4404() -> None:
    client, _ = websocket_client(
        SequenceDashboardService([ElderlyProfileNotFound("E404")])
    )
    try:
        with client.websocket_connect(
            "/ws/dashboard/E404", headers=ALLOWED_HEADERS
        ) as socket:
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_json()
        assert error.value.code == 4404
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_websocket_keeps_transport_alive_when_summary_storage_poll_fails() -> None:
    client, _ = websocket_client(
        SequenceDashboardService([make_summary(), DashboardStorageUnavailable()]),
        heartbeat=0.03,
    )
    try:
        with client.websocket_connect(
            "/ws/dashboard/E001", headers=ALLOWED_HEADERS
        ) as socket:
            assert socket.receive_json()["type"] == "summary"
            assert socket.receive_json()["type"] == "heartbeat"
            error = socket.receive_json()
            assert error == {
                "type": "error",
                "data": {"message": "Dashboard data storage is unavailable"},
            }
            messages = [socket.receive_json() for _ in range(5)]
            assert any(message["type"] == "heartbeat" for message in messages)
    finally:
        client.close()
        app.dependency_overrides.clear()


@pytest.mark.parametrize("headers", [{}, {"origin": "https://attacker.example"}])
def test_websocket_rejects_missing_or_disallowed_origin_before_accept(
    headers: dict[str, str],
) -> None:
    service = SequenceDashboardService([make_summary()])
    client, _ = websocket_client(service)
    try:
        with client.websocket_connect("/ws/dashboard/E001", headers=headers) as socket:
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_json()
        assert error.value.code == 4403
        assert service.calls == 0
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_websocket_disconnect_removes_subscription_promptly() -> None:
    client, hub = websocket_client(
        SequenceDashboardService([make_summary()]), heartbeat=1
    )
    try:
        with client.websocket_connect(
            "/ws/dashboard/E001", headers=ALLOWED_HEADERS
        ) as socket:
            assert socket.receive_json()["type"] == "summary"
            assert hub.subscriber_count("E001") == 1
        deadline = time.monotonic() + 0.5
        while hub.channel_count and time.monotonic() < deadline:
            time.sleep(0.01)
        assert hub.channel_count == 0
    finally:
        client.close()
        app.dependency_overrides.clear()


class RecordingWebSocket:
    def __init__(self, origin: str | None) -> None:
        self.headers = {"origin": origin} if origin is not None else {}
        self.events = []
        self.messages = []

    async def accept(self) -> None:
        self.events.append(("accept", None))

    async def close(self, code: int, reason: str | None = None) -> None:
        self.events.append(("close", code))

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    async def receive(self) -> dict[str, object]:
        return {}


@pytest.mark.asyncio
async def test_allowed_websocket_sends_configured_heartbeat_immediately() -> None:
    websocket = RecordingWebSocket("http://localhost:8501")
    service = SequenceDashboardService([make_summary()])
    hub = DashboardHub(service, poll_interval=1)
    settings = Settings(
        websocket_allowed_origins="http://localhost:8501",
        websocket_heartbeat_interval=60,
        websocket_auth_required=False,
    )

    await dashboard_websocket(websocket, "E001", hub, settings)

    assert [message["type"] for message in websocket.messages] == [
        "summary",
        "heartbeat",
    ]
    assert websocket.messages[1]["data"]["interval_seconds"] == 60.0


@pytest.mark.asyncio
async def test_origin_is_decided_before_accept_but_transports_4403_close_code() -> None:
    websocket = RecordingWebSocket("https://attacker.example")
    service = SequenceDashboardService([make_summary()])
    hub = DashboardHub(service, poll_interval=1)
    settings = Settings(
        websocket_allowed_origins="http://localhost:8501",
        websocket_auth_required=False,
    )

    await dashboard_websocket(websocket, "E001", hub, settings)

    assert websocket.events == [("accept", None), ("close", 4403)]
    assert service.calls == 0


@pytest.mark.asyncio
async def test_unknown_profile_lookup_precedes_accept_and_transports_4404() -> None:
    websocket = RecordingWebSocket("http://localhost:8501")
    service = SequenceDashboardService([ElderlyProfileNotFound("E404")])
    hub = DashboardHub(service, poll_interval=1)
    settings = Settings(
        websocket_allowed_origins="http://localhost:8501",
        websocket_auth_required=False,
    )

    await dashboard_websocket(websocket, "E404", hub, settings)

    assert websocket.events == [("accept", None), ("close", 4404)]
    assert service.calls == 1
