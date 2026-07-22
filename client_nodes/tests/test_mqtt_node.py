import json
import threading
import time
from collections.abc import Iterator

import paho.mqtt.client as mqtt
import pytest

from client_nodes.mqtt_node import (
    MQTTNodeClient,
    build_parser,
    build_telemetry,
    create_mqtt_client,
)


class PublishInfo:
    def __init__(self, *, rc: int = mqtt.MQTT_ERR_SUCCESS, published: bool = True) -> None:
        self.rc = rc
        self.published = published
        self.waits = []

    def wait_for_publish(self, timeout: float) -> None:
        self.waits.append(timeout)

    def is_published(self) -> bool:
        return self.published


class FakeClient:
    def __init__(
        self,
        infos: Iterator[PublishInfo] | None = None,
        *,
        connack_reason: int | None = 0,
        connack_delay: float = 0,
    ) -> None:
        self.infos = infos or iter([PublishInfo()])
        self.credentials = []
        self.connects = []
        self.publishes = []
        self.loop_starts = 0
        self.loop_stops = 0
        self.disconnects = 0
        self.connack_reason = connack_reason
        self.connack_delay = connack_delay
        self.on_connect = None
        self.on_disconnect = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials.append((username, password))

    def connect(self, broker: str, port: int, keepalive: int) -> int:
        self.connects.append((broker, port, keepalive))
        return mqtt.MQTT_ERR_SUCCESS

    def loop_start(self) -> None:
        self.loop_starts += 1
        if self.connack_reason is None:
            return

        def connack() -> None:
            if self.connack_delay:
                time.sleep(self.connack_delay)
            self.on_connect(self, None, None, self.connack_reason, None)

        threading.Thread(target=connack).start()

    def loop_stop(self) -> None:
        self.loop_stops += 1

    def disconnect(self) -> None:
        self.disconnects += 1

    def publish(self, topic: str, payload: str, *, qos: int, retain: bool) -> PublishInfo:
        self.publishes.append((topic, payload, qos, retain))
        return next(self.infos)


def test_parser_configures_broker_credentials_identity_and_scenario() -> None:
    args = build_parser().parse_args(
        [
            "--broker",
            "broker.local",
            "--port",
            "1884",
            "--username",
            "node",
            "--password",
            "secret",
            "--elderly-id",
            "E099",
            "--scenario",
            "emergency",
            "--count",
            "3",
            "--interval",
            "0.2",
            "--publish-timeout",
            "4",
            "--connect-timeout",
            "2",
            "--reminder-id",
            "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f",
        ]
    )

    assert vars(args) == {
        "broker": "broker.local",
        "port": 1884,
        "username": "node",
        "password": "secret",
        "elderly_id": "E099",
        "scenario": "emergency",
        "count": 3,
        "interval": 0.2,
        "publish_timeout": 4.0,
        "connect_timeout": 2.0,
        "reminder_id": "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f",
    }


def test_create_client_uses_v311_and_configured_credentials(monkeypatch) -> None:
    captured = {}
    fake = FakeClient()

    def factory(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr("client_nodes.mqtt_node.mqtt.Client", factory)

    assert create_mqtt_client("node", "secret") is fake
    assert captured == {
        "callback_api_version": mqtt.CallbackAPIVersion.VERSION2,
        "protocol": mqtt.MQTTv311,
    }
    assert fake.credentials == [("node", "secret")]


def test_publish_constructs_matching_topic_payload_and_confirms_qos_one() -> None:
    info = PublishInfo()
    fake = FakeClient(iter([info]))
    node = MQTTNodeClient(
        "broker",
        1883,
        "node",
        "secret",
        "E001",
        publish_timeout=3,
        connect_timeout=1,
        client=fake,
        key_factory=lambda: "stable-key-1",
    )
    node.connect()

    key = node.publish(
        "activity",
        {
            "elderly_id": "WRONG",
            "value": "active",
            "recorded_at": "2026-07-18T10:00:00Z",
        },
    )

    assert key == "stable-key-1"
    assert fake.connects == [("broker", 1883, 60)]
    assert fake.loop_starts == 1
    topic, raw_payload, qos, retain = fake.publishes[0]
    assert topic == "kindcare/E001/activity"
    assert json.loads(raw_payload) == {
        "idempotency_key": "stable-key-1",
        "elderly_id": "E001",
        "value": "active",
        "recorded_at": "2026-07-18T10:00:00Z",
    }
    assert qos == 1
    assert retain is False
    assert info.waits == [3]


def test_explicit_retry_key_is_preserved() -> None:
    fake = FakeClient(iter([PublishInfo()]))
    node = MQTTNodeClient(
        "broker", 1883, "node", "secret", "E001", client=fake
    )
    node.connect()

    assert node.publish("device", {"recorded_at": "2026-07-18T10:00:00Z"}, "same-key") == "same-key"
    assert json.loads(fake.publishes[0][1])["idempotency_key"] == "same-key"


@pytest.mark.parametrize(
    "info",
    [
        PublishInfo(rc=mqtt.MQTT_ERR_NO_CONN),
        PublishInfo(published=False),
    ],
)
def test_publish_fails_without_broker_confirmation(info: PublishInfo) -> None:
    node = MQTTNodeClient(
        "broker", 1883, "node", "secret", "E001", client=FakeClient(iter([info]))
    )
    node.connect()

    with pytest.raises(ConnectionError, match="publish"):
        node.publish("device", {"recorded_at": "2026-07-18T10:00:00Z"})


def test_reminder_taken_payload_is_exact_and_identity_safe() -> None:
    fake = FakeClient(iter([PublishInfo()]))
    node = MQTTNodeClient(
        "broker", 1883, "node", "secret", "E001", client=fake, key_factory=lambda: "reminder-key"
    )
    node.connect()

    node.publish_reminder_taken("d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f")

    topic, raw_payload, qos, retain = fake.publishes[0]
    assert topic == "kindcare/E001/reminder"
    assert json.loads(raw_payload) == {
        "idempotency_key": "reminder-key",
        "elderly_id": "E001",
        "reminder_id": "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f",
        "status": "taken",
    }


def test_build_telemetry_generates_health_activity_device_with_same_utc_time() -> None:
    messages = list(build_telemetry("normal", "E001", 1))

    assert [kind for kind, _ in messages] == ["health", "activity", "device"]
    timestamps = {payload["recorded_at"] for _, payload in messages}
    assert len(timestamps) == 1
    assert timestamps.pop().endswith("Z")
    assert all(payload["elderly_id"] == "E001" for _, payload in messages)


def test_offline_scenario_publishes_only_first_heartbeat() -> None:
    messages = list(build_telemetry("offline", "E001", 3))

    assert [kind for kind, _ in messages].count("health") == 3
    assert [kind for kind, _ in messages].count("activity") == 3
    assert [kind for kind, _ in messages].count("device") == 1


def test_close_is_idempotent() -> None:
    fake = FakeClient()
    node = MQTTNodeClient("broker", 1883, "node", "secret", "E001", client=fake)
    node.connect()

    node.close()
    node.close()

    assert fake.loop_stops == 1
    assert fake.disconnects == 1


def test_connect_waits_for_delayed_successful_connack() -> None:
    fake = FakeClient(connack_delay=0.03)
    node = MQTTNodeClient(
        "broker",
        1883,
        "node",
        "secret",
        "E001",
        connect_timeout=0.2,
        client=fake,
    )

    started = time.monotonic()
    node.connect()

    assert time.monotonic() - started >= 0.02
    assert node.connected is True


def test_connect_fails_clearly_when_credentials_are_rejected() -> None:
    fake = FakeClient(connack_reason=5)
    node = MQTTNodeClient(
        "broker", 1883, "node", "bad", "E001", connect_timeout=0.2, client=fake
    )

    with pytest.raises(ConnectionError, match="rejected.*5"):
        node.connect()

    assert node.connected is False
    assert fake.loop_stops == 1
    assert fake.disconnects == 1
    assert fake.publishes == []


def test_connect_times_out_without_connack() -> None:
    fake = FakeClient(connack_reason=None)
    node = MQTTNodeClient(
        "broker", 1883, "node", "secret", "E001", connect_timeout=0.01, client=fake
    )

    with pytest.raises(ConnectionError, match="CONNACK.*timeout"):
        node.connect()

    assert node.connected is False
    assert fake.loop_stops == 1
    assert fake.disconnects == 1


def test_publish_before_successful_connack_is_rejected() -> None:
    fake = FakeClient()
    node = MQTTNodeClient("broker", 1883, "node", "secret", "E001", client=fake)

    with pytest.raises(ConnectionError, match="not connected"):
        node.publish("device", {"recorded_at": "2026-07-18T10:00:00Z"})

    assert fake.publishes == []


@pytest.mark.parametrize(
    "elderly_id",
    ["", "E 001", "E.001", "é", "E" * 51, "E/001"],
)
def test_node_rejects_elderly_id_outside_backend_contract(elderly_id: str) -> None:
    with pytest.raises(ValueError, match="elderly_id"):
        MQTTNodeClient("broker", 1883, "node", "secret", elderly_id, client=FakeClient())
