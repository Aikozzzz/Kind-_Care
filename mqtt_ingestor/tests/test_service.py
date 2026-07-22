import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import paho.mqtt.client as mqtt

from mqtt_ingestor.config import Settings
from mqtt_ingestor.http_bridge import HTTPResult, Outcome
from mqtt_ingestor.service import InboundMessage, MQTTIngestor, create_client


class FakeClient:
    def __init__(self) -> None:
        self.username_calls = []
        self.reconnect_calls = []
        self.connect_calls = []
        self.subscribe_calls = []
        self.ack_calls = []
        self.disconnect_calls = 0
        self.loop_calls = []
        self.reconnect_calls_count = 0
        self.reconnect_results = [mqtt.MQTT_ERR_SUCCESS]
        self.ack_result = mqtt.MQTT_ERR_SUCCESS
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.username_calls.append((username, password))

    def reconnect_delay_set(self, *, min_delay: int, max_delay: int) -> None:
        self.reconnect_calls.append((min_delay, max_delay))

    def connect_async(self, broker: str, port: int, keepalive: int) -> None:
        self.connect_calls.append((broker, port, keepalive))

    def subscribe(self, topic: str, qos: int):
        self.subscribe_calls.append((topic, qos))
        return mqtt.MQTT_ERR_SUCCESS, 1

    def ack(self, mid: int, qos: int) -> int:
        self.ack_calls.append((mid, qos))
        return self.ack_result

    def disconnect(self) -> None:
        self.disconnect_calls += 1

    def loop_forever(self, *, retry_first_connection: bool) -> None:
        self.loop_calls.append(retry_first_connection)

    def reconnect(self) -> int:
        self.reconnect_calls_count += 1
        return self.reconnect_results.pop(0)


class FakeBridge:
    def __init__(self, results: list[HTTPResult], stop_event=None) -> None:
        self.results = results
        self.requests = []
        self.stop_event = stop_event

    def send(self, request):
        self.requests.append(request)
        result = self.results.pop(0)
        if self.stop_event is not None:
            self.stop_event.set()
        return result


def settings(tmp_path: Path, **updates) -> Settings:
    values = {
        "broker": "mosquitto",
        "port": 1883,
        "username": "kindcare",
        "password": "secret",
        "client_id": "kindcare-mqtt-ingestor",
        "api_base_url": "http://backend:8000",
        "max_payload_bytes": 4096,
        "queue_size": 2,
        "http_timeout": 1,
        "retry_initial_seconds": 0.001,
        "retry_max_seconds": 0.002,
        "reconnect_min_seconds": 1,
        "reconnect_max_seconds": 10,
        "health_file": str(tmp_path / "connected"),
        "health_max_age_seconds": 1,
    }
    values.update(updates)
    return Settings(**values)


def message(
    *,
    mid: int = 7,
    topic: str = "kindcare/E001/device",
    payload: bytes | None = None,
    qos: int = 1,
    retain: bool = False,
    dup: bool = False,
) -> InboundMessage:
    return InboundMessage(
        topic=topic,
        payload=payload
        or json.dumps(
            {
                "idempotency_key": "stable-device-key",
                "elderly_id": "E001",
                "recorded_at": "2026-07-18T10:00:00Z",
            }
        ).encode(),
        mid=mid,
        qos=qos,
        retain=retain,
        dup=dup,
    )


def device_payload(key: str, *, recorded_at: str = "2026-07-18T10:00:00Z") -> bytes:
    return json.dumps(
        {
            "idempotency_key": key,
            "elderly_id": "E001",
            "recorded_at": recorded_at,
        }
    ).encode()


def test_create_client_uses_persistent_manual_ack_v311(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    fake = FakeClient()

    def factory(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr("mqtt_ingestor.service.mqtt.Client", factory)

    assert create_client(settings(tmp_path)) is fake
    assert captured == {
        "callback_api_version": mqtt.CallbackAPIVersion.VERSION2,
        "client_id": "kindcare-mqtt-ingestor",
        "clean_session": False,
        "protocol": mqtt.MQTTv311,
        "manual_ack": True,
    }
    assert fake.username_calls == [("kindcare", "secret")]
    assert fake.reconnect_calls == [(1, 10)]


def test_connect_subscribes_qos_one_and_marks_health(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))

    ingestor.start()
    ingestor.on_connect(client, None, None, 0, None)

    deadline = time.monotonic() + 1
    while not Path(ingestor.settings.health_file).exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert client.subscribe_calls == [("kindcare/+/+", 1)]
    assert Path(ingestor.settings.health_file).exists()

    ingestor.on_disconnect(client, None, None, 1, None)
    assert not Path(ingestor.settings.health_file).exists()
    ingestor.shutdown()


def test_failed_connect_does_not_subscribe_or_mark_health(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))

    ingestor.on_connect(client, None, None, 5, None)

    assert client.subscribe_calls == []
    assert not Path(ingestor.settings.health_file).exists()


def test_retained_message_is_acked_without_http(tmp_path: Path) -> None:
    client = FakeClient()
    bridge = FakeBridge([])
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(retain=True))

    assert bridge.requests == []
    assert client.ack_calls == [(7, 1)]


def test_non_qos_one_message_is_not_forwarded(tmp_path: Path) -> None:
    client = FakeClient()
    bridge = FakeBridge([])
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(qos=0))

    assert bridge.requests == []
    assert client.ack_calls == []


def test_invalid_message_is_permanent_and_payload_is_not_logged(
    tmp_path: Path, caplog
) -> None:
    client = FakeClient()
    bridge = FakeBridge([])
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)
    sensitive = b'{"medical":"do-not-log"}'

    ingestor.process_message(message(payload=sensitive))

    assert bridge.requests == []
    assert client.ack_calls == [(7, 1)]
    assert "do-not-log" not in caplog.text


def test_success_and_permanent_http_outcomes_are_acked(tmp_path: Path) -> None:
    client = FakeClient()
    bridge = FakeBridge(
        [HTTPResult(Outcome.SUCCESS, 202), HTTPResult(Outcome.PERMANENT, 422)]
    )
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(mid=7))
    ingestor.process_message(message(mid=8))

    assert client.ack_calls == [(7, 1), (8, 1)]
    assert [request.idempotency_key for request in bridge.requests] == [
        "stable-device-key",
        "stable-device-key",
    ]


def test_transient_result_interrupted_by_shutdown_is_not_acked(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))
    ingestor.bridge = FakeBridge(
        [HTTPResult(Outcome.TRANSIENT, 503)], stop_event=ingestor.stop_event
    )

    ingestor.process_message(message())

    assert client.ack_calls == []


def test_callback_uses_bounded_queue_without_mutating_message(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path, queue_size=1), client=client, bridge=FakeBridge([]))
    paho_message = SimpleNamespace(
        topic="kindcare/E001/device",
        payload=b"payload",
        mid=4,
        qos=1,
        retain=False,
        dup=True,
    )

    ingestor.on_message(client, None, paho_message)

    queued = ingestor.queue.get_nowait()
    assert queued == InboundMessage(
        topic="kindcare/E001/device",
        payload=b"payload",
        mid=4,
        qos=1,
        retain=False,
        dup=True,
    )


def test_process_start_removes_marker_left_by_ungraceful_exit(tmp_path: Path) -> None:
    state_file = tmp_path / "connected"
    state_file.touch()

    MQTTIngestor(settings(tmp_path), client=FakeClient(), bridge=FakeBridge([]))

    assert not state_file.exists()


def test_full_queue_callback_returns_immediately_disconnects_and_does_not_ack(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(
        settings(tmp_path, queue_size=1), client=client, bridge=FakeBridge([])
    )
    ingestor.queue.put(message(mid=1))
    Path(ingestor.settings.health_file).touch()
    incoming = SimpleNamespace(
        topic="kindcare/E001/device",
        payload=b"overflow",
        mid=2,
        qos=1,
        retain=False,
        dup=False,
    )
    callback = threading.Thread(
        target=ingestor.on_message, args=(client, None, incoming), daemon=True
    )

    callback.start()
    callback.join(0.05)
    was_blocked = callback.is_alive()
    ingestor.stop_event.set()
    callback.join(1)
    deadline = time.monotonic() + 1
    while client.disconnect_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.005)

    assert was_blocked is False
    assert client.disconnect_calls == 1
    assert client.ack_calls == []
    assert ingestor.backpressure_event.is_set()
    assert not Path(ingestor.settings.health_file).exists()


def test_backpressure_overlapping_health_refresh_leaves_marker_cleared(
    tmp_path: Path, monkeypatch
) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))
    ingestor.connected_event.set()
    mark_entered = threading.Event()
    release_mark = threading.Event()

    def blocked_mark(path: str) -> None:
        mark_entered.set()
        release_mark.wait(1)
        Path(path).touch()

    monkeypatch.setattr("mqtt_ingestor.service.mark_connected", blocked_mark)
    refresh = threading.Thread(target=ingestor._refresh_health)
    overload = threading.Thread(target=ingestor._trigger_backpressure)

    refresh.start()
    assert mark_entered.wait(1)
    overload.start()
    time.sleep(0.02)
    release_mark.set()
    refresh.join(1)
    overload.join(1)

    assert ingestor.backpressure_event.is_set()
    assert not Path(ingestor.settings.health_file).exists()


def test_full_queue_callback_never_waits_for_blocking_disconnect(tmp_path: Path) -> None:
    disconnect_entered = threading.Event()
    release_disconnect = threading.Event()

    class BlockingDisconnectClient(FakeClient):
        def disconnect(self) -> None:
            disconnect_entered.set()
            release_disconnect.wait(1)
            super().disconnect()

    client = BlockingDisconnectClient()
    ingestor = MQTTIngestor(
        settings(tmp_path, queue_size=1), client=client, bridge=FakeBridge([])
    )
    ingestor.queue.put(message(mid=1))
    incoming = SimpleNamespace(
        topic="kindcare/E001/device",
        payload=b"overflow",
        mid=2,
        qos=1,
        retain=False,
        dup=False,
    )
    callback = threading.Thread(
        target=ingestor.on_message, args=(client, None, incoming), daemon=True
    )

    callback.start()
    assert disconnect_entered.wait(0.2)
    callback.join(0.05)
    blocked = callback.is_alive()
    release_disconnect.set()
    callback.join(1)

    assert blocked is False


def test_concurrent_full_queue_callbacks_all_return_and_disconnect_once(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(
        settings(tmp_path, queue_size=1), client=client, bridge=FakeBridge([])
    )
    ingestor.queue.put(message(mid=1))
    barrier = threading.Barrier(9)

    def deliver(mid: int) -> None:
        barrier.wait()
        ingestor.on_message(
            client,
            None,
            SimpleNamespace(
                topic="kindcare/E001/device",
                payload=b"overflow",
                mid=mid,
                qos=1,
                retain=False,
                dup=False,
            ),
        )

    callbacks = [threading.Thread(target=deliver, args=(mid,)) for mid in range(2, 10)]
    for callback in callbacks:
        callback.start()
    barrier.wait()
    for callback in callbacks:
        callback.join(0.2)

    blocked = [callback for callback in callbacks if callback.is_alive()]
    ingestor.stop_event.set()
    for callback in blocked:
        callback.join(1)
    deadline = time.monotonic() + 1
    while client.disconnect_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.005)

    assert blocked == []
    assert client.disconnect_calls == 1
    assert client.ack_calls == []


def test_callback_racing_shutdown_does_not_enqueue_disconnect_or_ack(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))
    ingestor.stop_event.set()

    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=b"ignored",
            mid=2,
            qos=1,
            retain=False,
            dup=False,
        ),
    )

    assert ingestor.queue.empty()
    assert client.disconnect_calls == 0
    assert client.ack_calls == []


def test_backpressure_run_reconnects_after_queue_drains(tmp_path: Path) -> None:
    client = FakeClient()

    def first_loop(*, retry_first_connection: bool) -> None:
        client.loop_calls.append(retry_first_connection)
        if len(client.loop_calls) == 1:
            ingestor.backpressure_event.set()

    client.loop_forever = first_loop
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))

    ingestor.run()

    assert client.loop_calls == [True, True]
    assert client.reconnect_calls_count == 1


def test_backpressure_reconnect_retries_broker_failure_with_bounded_delay(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.reconnect_results = [mqtt.MQTT_ERR_NO_CONN, mqtt.MQTT_ERR_SUCCESS]
    ingestor = MQTTIngestor(
        settings(
            tmp_path,
            reconnect_min_seconds=0.001,
            reconnect_max_seconds=0.002,
        ),
        client=client,
        bridge=FakeBridge([]),
    )

    assert ingestor._reconnect_after_backpressure() is True
    assert client.reconnect_calls_count == 2


def test_backpressure_reconnect_stops_without_another_attempt_on_shutdown(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))

    def reconnect() -> int:
        client.reconnect_calls_count += 1
        ingestor.stop_event.set()
        return mqtt.MQTT_ERR_NO_CONN

    client.reconnect = reconnect

    assert ingestor._reconnect_after_backpressure() is False
    assert client.reconnect_calls_count == 1


def test_failed_manual_ack_leaves_message_for_idempotent_redelivery(tmp_path: Path) -> None:
    client = FakeClient()
    client.ack_result = mqtt.MQTT_ERR_NO_CONN

    class IdempotentBridge:
        def __init__(self) -> None:
            self.requests = []
            self.effects = set()

        def send(self, request):
            self.requests.append(request)
            self.effects.add(request.idempotency_key)
            return HTTPResult(Outcome.SUCCESS, 202)

    bridge = IdempotentBridge()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(mid=10))
    client.ack_result = mqtt.MQTT_ERR_SUCCESS
    ingestor.process_message(message(mid=11))

    assert client.ack_calls == [(10, 1), (11, 1)]
    assert len(bridge.requests) == 2
    assert bridge.effects == {"stable-device-key"}


def test_connack_ack_success_then_buffered_duplicate_does_not_repeat_http(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.ack_result = mqtt.MQTT_ERR_NO_CONN
    payload = device_payload("buffered-duplicate-key")
    bridge = FakeBridge(
        [HTTPResult(Outcome.SUCCESS, 202), HTTPResult(Outcome.SUCCESS, 202)]
    )
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(mid=10, payload=payload))
    client.ack_result = mqtt.MQTT_ERR_SUCCESS
    ingestor.on_connect(client, None, None, 0, None)
    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=payload,
            mid=10,
            qos=1,
            retain=False,
            dup=True,
        ),
    )
    if not ingestor.queue.empty():
        ingestor.process_message(ingestor.queue.get_nowait())

    assert client.ack_calls == [(10, 1), (10, 1), (10, 1)]
    assert client.subscribe_calls == [("kindcare/+/+", 1)]
    assert len(bridge.requests) == 1


def test_fresh_same_mid_topic_and_payload_processes_and_replaces_tombstone(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    payload = device_payload("fresh-mid-reuse")
    bridge = FakeBridge(
        [HTTPResult(Outcome.SUCCESS, 202), HTTPResult(Outcome.SUCCESS, 202)]
    )
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(mid=10, payload=payload))
    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=payload,
            mid=10,
            qos=1,
            retain=False,
            dup=False,
        ),
    )
    ingestor.process_message(ingestor.queue.get_nowait())

    assert [request.idempotency_key for request in bridge.requests] == [
        "fresh-mid-reuse",
        "fresh-mid-reuse",
    ]


def test_pending_ack_redelivery_does_not_repeat_http_or_fill_queue(tmp_path: Path) -> None:
    client = FakeClient()
    client.ack_result = mqtt.MQTT_ERR_NO_CONN
    bridge = FakeBridge([HTTPResult(Outcome.SUCCESS, 202)])
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)
    original = message(mid=10)

    ingestor.process_message(original)
    client.ack_result = mqtt.MQTT_ERR_SUCCESS
    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=original.payload,
            mid=10,
            qos=1,
            retain=False,
            dup=True,
        ),
    )

    assert client.ack_calls == [(10, 1), (10, 1)]
    assert len(bridge.requests) == 1
    assert ingestor.queue.empty()


def test_ignored_retained_tombstone_does_not_suppress_normal_publish(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    payload = device_payload("retained-disposition")
    bridge = FakeBridge([HTTPResult(Outcome.SUCCESS, 202)])
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(mid=10, payload=payload, retain=True))
    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=payload,
            mid=10,
            qos=1,
            retain=False,
            dup=True,
        ),
    )

    assert not ingestor.queue.empty()
    ingestor.process_message(ingestor.queue.get_nowait())
    assert [request.idempotency_key for request in bridge.requests] == [
        "retained-disposition"
    ]


def test_same_mid_with_different_payload_replaces_completed_delivery(tmp_path: Path) -> None:
    client = FakeClient()
    first_payload = device_payload("first-mid-use")
    second_payload = device_payload(
        "second-mid-use", recorded_at="2026-07-18T10:00:01Z"
    )
    bridge = FakeBridge(
        [HTTPResult(Outcome.SUCCESS, 202), HTTPResult(Outcome.SUCCESS, 202)]
    )
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=bridge)

    ingestor.process_message(message(mid=10, payload=first_payload))
    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=second_payload,
            mid=10,
            qos=1,
            retain=False,
            dup=False,
        ),
    )
    ingestor.process_message(ingestor.queue.get_nowait())

    assert [request.idempotency_key for request in bridge.requests] == [
        "first-mid-use",
        "second-mid-use",
    ]


def test_completed_delivery_tombstones_use_deterministic_lru_capacity(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    payloads = [device_payload(f"lru-{mid}") for mid in range(1, 22)]
    bridge = FakeBridge([HTTPResult(Outcome.SUCCESS, 202)] * 23)
    ingestor = MQTTIngestor(
        settings(tmp_path, queue_size=1), client=client, bridge=bridge
    )

    for mid, payload in enumerate(payloads, start=1):
        ingestor.process_message(message(mid=mid, payload=payload))

    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=payloads[0],
            mid=1,
            qos=1,
            retain=False,
            dup=True,
        ),
    )
    ingestor.process_message(ingestor.queue.get_nowait())
    ingestor.on_message(
        client,
        None,
        SimpleNamespace(
            topic="kindcare/E001/device",
            payload=payloads[-1],
            mid=21,
            qos=1,
            retain=False,
            dup=True,
        ),
    )

    assert len(bridge.requests) == 22
    assert ingestor.queue.empty()


def test_reminder_logs_only_kind_hashed_identity_and_final_attempt_count(
    tmp_path: Path, caplog
) -> None:
    caplog.set_level(logging.INFO)
    reminder_id = "d90f15bc-cb99-49fa-8dcd-4cf1f664bb7f"
    payload = json.dumps(
        {
            "idempotency_key": "reminder-key",
            "elderly_id": "E001",
            "reminder_id": reminder_id,
            "status": "taken",
        }
    ).encode()
    client = FakeClient()
    ingestor = MQTTIngestor(
        settings(tmp_path),
        client=client,
        bridge=FakeBridge(
            [HTTPResult(Outcome.TRANSIENT, 503), HTTPResult(Outcome.SUCCESS, 200)]
        ),
    )

    ingestor.process_message(
        message(topic="kindcare/E001/reminder", payload=payload)
    )

    assert "kind=reminder" in caplog.text
    assert "attempts=2" in caplog.text
    assert reminder_id not in caplog.text
    assert "E001" not in caplog.text
    assert "/api/reminders" not in caplog.text


def test_start_and_run_configure_callbacks_reconnect_and_worker(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))

    ingestor.run()

    assert client.connect_calls == [("mosquitto", 1883, 60)]
    assert client.loop_calls == [True]
    assert client.on_connect == ingestor.on_connect
    assert client.on_disconnect == ingestor.on_disconnect
    assert client.on_message == ingestor.on_message
    assert client.disconnect_calls == 1


def test_shutdown_is_idempotent_and_leaves_queued_messages_unacked(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(settings(tmp_path), client=client, bridge=FakeBridge([]))
    ingestor.queue.put(message())

    ingestor.shutdown()
    ingestor.shutdown()

    assert client.disconnect_calls == 1
    assert client.ack_calls == []


def test_worker_processes_enqueued_message(tmp_path: Path) -> None:
    client = FakeClient()
    ingestor = MQTTIngestor(
        settings(tmp_path),
        client=client,
        bridge=FakeBridge([HTTPResult(Outcome.SUCCESS, 202)]),
    )
    ingestor.start()
    ingestor.queue.put(message())

    deadline = threading.Event()
    for _ in range(100):
        if client.ack_calls:
            break
        deadline.wait(0.005)
    ingestor.shutdown()

    assert client.ack_calls == [(7, 1)]
