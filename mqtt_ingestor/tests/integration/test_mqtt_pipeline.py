import json
import os
import threading
import time
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest
from pymongo import MongoClient

from mqtt_ingestor.config import Settings
from mqtt_ingestor.service import MQTTIngestor


pytestmark = pytest.mark.integration
SATURATION_COLLECTIONS = (
    "elderly_profiles",
    "device_idempotency",
    "device_events",
    "device_status",
)


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, object],
    idempotency_key: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": "application/json"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode(),
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def wait_for(find, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = find()
        if found:
            return found
        time.sleep(0.2)
    raise AssertionError("timed out waiting for pipeline result")


def get_data(base_url: str, path: str) -> object:
    with urlopen(f"{base_url}{path}", timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read())["data"]


def wait_for_record(base_url: str, path: str, predicate):
    return wait_for(
        lambda: next(
            (record for record in get_data(base_url, path) if predicate(record)),
            None,
        )
    )


def publish(client: mqtt.Client, elderly_id: str, kind: str, payload: dict[str, object]) -> None:
    info = client.publish(
        f"kindcare/{elderly_id}/{kind}",
        json.dumps(payload, separators=(",", ":")),
        qos=1,
        retain=False,
    )
    assert info.rc == mqtt.MQTT_ERR_SUCCESS
    info.wait_for_publish(timeout=10)
    assert info.is_published()


def mqtt_connection_cycle(settings: Settings, *, clean_session: bool) -> bool:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.client_id,
        clean_session=clean_session,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(settings.username, settings.password)
    connected = threading.Event()
    disconnected = threading.Event()
    session_present = False

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        nonlocal session_present
        if reason_code == 0:
            session_present = bool(flags.session_present)
            connected.set()
            client.disconnect()

    def on_disconnect(
        client, userdata, disconnect_flags, reason_code, properties
    ) -> None:
        disconnected.set()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect(settings.broker, settings.port, 60)
    client.loop_start()
    try:
        assert connected.wait(10)
        assert disconnected.wait(10)
        return session_present
    finally:
        client.disconnect()
        client.loop_stop()


def clear_persistent_session(settings: Settings) -> None:
    assert mqtt_connection_cycle(settings, clean_session=True) is False
    try:
        assert mqtt_connection_cycle(settings, clean_session=False) is False
    finally:
        assert mqtt_connection_cycle(settings, clean_session=True) is False


def cleanup_saturation_resources(
    *,
    publisher,
    publisher_loop_started: bool,
    ingestor,
    ingestor_thread,
    ingestor_thread_started: bool,
    proxy,
    settings,
    database,
    elderly_id: str,
    mongo_client,
    session_cleanup=clear_persistent_session,
    join_timeout: float = 30,
) -> None:
    cleanup_errors: list[Exception] = []
    termination_error: AssertionError | None = None

    def attempt(operation) -> None:
        try:
            operation()
        except Exception as error:
            cleanup_errors.append(error)

    proxy.release_first.set()
    if publisher_loop_started:
        attempt(publisher.loop_stop)
    attempt(publisher.disconnect)
    attempt(ingestor.shutdown)
    if ingestor_thread_started:
        try:
            ingestor_thread.join(join_timeout)
        except Exception as error:
            cleanup_errors.append(error)
        else:
            if ingestor_thread.is_alive():
                termination_error = AssertionError(
                    "ingestor thread did not terminate after bounded join"
                )
    attempt(proxy.close)
    attempt(lambda: session_cleanup(settings))
    for collection_name in SATURATION_COLLECTIONS:
        attempt(
            lambda collection_name=collection_name: database[
                collection_name
            ].delete_many({"elderly_id": elderly_id})
        )
    attempt(mongo_client.close)

    if termination_error is not None:
        if cleanup_errors:
            raise termination_error from ExceptionGroup(
                "saturation cleanup also failed", cleanup_errors
            )
        raise termination_error
    if cleanup_errors:
        raise ExceptionGroup("saturation cleanup failed", cleanup_errors)


class TrackingIngestor(MQTTIngestor):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.connect_count = 0
        self.disconnect_count = 0

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        super().on_connect(client, userdata, flags, reason_code, properties)
        if reason_code == 0:
            self.connect_count += 1

    def on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ) -> None:
        self.disconnect_count += 1
        super().on_disconnect(
            client, userdata, disconnect_flags, reason_code, properties
        )


class CountingProxy:
    def __init__(self, upstream: str, blocked_key: str) -> None:
        self.upstream = upstream
        self.blocked_key = blocked_key
        self.first_forwarded = threading.Event()
        self.release_first = threading.Event()
        self.counts: dict[str, int] = {}
        self._lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(content_length)
                key = self.headers["Idempotency-Key"]
                with proxy._lock:
                    proxy.counts[key] = proxy.counts.get(key, 0) + 1
                    request_count = proxy.counts[key]
                upstream_request = Request(
                    f"{proxy.upstream}{self.path}",
                    data=body,
                    headers={
                        "Content-Type": self.headers["Content-Type"],
                        "Idempotency-Key": key,
                    },
                    method="POST",
                )
                with urlopen(upstream_request, timeout=10) as response:
                    status = response.status
                    response_body = response.read()
                    content_type = response.headers.get(
                        "Content-Type", "application/json"
                    )
                if key == proxy.blocked_key and request_count == 1:
                    proxy.first_forwarded.set()
                    if not proxy.release_first.wait(20):
                        raise TimeoutError("saturation proxy was not released")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format: str, *args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.started = False

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self.thread.start()
        self.started = True

    def close(self) -> None:
        self.release_first.set()
        if self.started:
            self.server.shutdown()
            self.thread.join(5)
        self.server.server_close()


def test_counting_proxy_close_before_start_does_not_wait_for_serve_forever() -> None:
    proxy = CountingProxy("http://backend:8000", "never-forwarded")
    closer = threading.Thread(target=proxy.close, daemon=True)

    closer.start()
    closer.join(0.2)

    assert not closer.is_alive()


class CleanupRecorder:
    def __init__(self) -> None:
        self.events: list[object] = []


class CleanupPublisher:
    def __init__(self, recorder: CleanupRecorder) -> None:
        self.recorder = recorder

    def loop_stop(self) -> None:
        self.recorder.events.append("publisher-loop-stop")

    def disconnect(self) -> None:
        self.recorder.events.append("publisher-disconnect")


class CleanupIngestor:
    def __init__(self, recorder: CleanupRecorder) -> None:
        self.recorder = recorder

    def shutdown(self) -> None:
        self.recorder.events.append("ingestor-shutdown")


class CleanupThread:
    def __init__(self, recorder: CleanupRecorder, *, alive: bool) -> None:
        self.recorder = recorder
        self.alive = alive

    def join(self, timeout: float) -> None:
        self.recorder.events.append(("thread-join", timeout))

    def is_alive(self) -> bool:
        return self.alive


class UnstartedCleanupThread(CleanupThread):
    def join(self, timeout: float) -> None:
        raise AssertionError("unstarted thread was joined")


class CleanupProxy:
    def __init__(self, recorder: CleanupRecorder) -> None:
        self.recorder = recorder
        self.release_first = threading.Event()

    def close(self) -> None:
        self.recorder.events.append("proxy-close")


class CleanupCollection:
    def __init__(self, recorder: CleanupRecorder, name: str) -> None:
        self.recorder = recorder
        self.name = name

    def delete_many(self, query: dict[str, object]) -> None:
        self.recorder.events.append(("delete", self.name, query))


class CleanupDatabase:
    def __init__(self, recorder: CleanupRecorder) -> None:
        self.recorder = recorder

    def __getitem__(self, name: str) -> CleanupCollection:
        return CleanupCollection(self.recorder, name)


class CleanupMongoClient:
    def __init__(self, recorder: CleanupRecorder) -> None:
        self.recorder = recorder

    def close(self) -> None:
        self.recorder.events.append("mongo-close")


def test_saturation_cleanup_before_thread_start_skips_join_and_cleans_everything() -> None:
    recorder = CleanupRecorder()

    cleanup_saturation_resources(
        publisher=CleanupPublisher(recorder),
        publisher_loop_started=False,
        ingestor=CleanupIngestor(recorder),
        ingestor_thread=UnstartedCleanupThread(recorder, alive=False),
        ingestor_thread_started=False,
        proxy=CleanupProxy(recorder),
        settings=object(),
        database=CleanupDatabase(recorder),
        elderly_id="MSearly",
        mongo_client=CleanupMongoClient(recorder),
        session_cleanup=lambda settings: recorder.events.append("session-cleanup"),
        join_timeout=0.01,
    )

    assert "publisher-loop-stop" not in recorder.events
    assert "publisher-disconnect" in recorder.events
    assert "ingestor-shutdown" in recorder.events
    assert not any(
        isinstance(event, tuple) and event[0] == "thread-join"
        for event in recorder.events
    )
    assert "proxy-close" in recorder.events
    assert "session-cleanup" in recorder.events
    assert sum(
        isinstance(event, tuple) and event[0] == "delete"
        for event in recorder.events
    ) == 4
    assert recorder.events[-1] == "mongo-close"


def test_saturation_cleanup_reports_live_thread_only_after_full_cleanup() -> None:
    recorder = CleanupRecorder()

    with pytest.raises(AssertionError, match="ingestor thread did not terminate"):
        cleanup_saturation_resources(
            publisher=CleanupPublisher(recorder),
            publisher_loop_started=True,
            ingestor=CleanupIngestor(recorder),
            ingestor_thread=CleanupThread(recorder, alive=True),
            ingestor_thread_started=True,
            proxy=CleanupProxy(recorder),
            settings=object(),
            database=CleanupDatabase(recorder),
            elderly_id="MSstuck",
            mongo_client=CleanupMongoClient(recorder),
            session_cleanup=lambda settings: recorder.events.append("session-cleanup"),
            join_timeout=0.01,
        )

    assert ("thread-join", 0.01) in recorder.events
    assert "proxy-close" in recorder.events
    assert "session-cleanup" in recorder.events
    assert sum(
        isinstance(event, tuple) and event[0] == "delete"
        for event in recorder.events
    ) == 4
    assert recorder.events[-1] == "mongo-close"


def test_real_mqtt_pipeline_uses_existing_http_worker_and_idempotency_paths() -> None:
    api_url = os.environ.get("API_BASE_URL", "http://backend:8000").rstrip("/")
    mongo_uri = os.environ.get(
        "MONGO_URI", "mongodb://mongodb:27017/?replicaSet=rs0"
    )
    database_name = os.environ.get("DATABASE_NAME", "kindcare_db")
    assert database_name == "kindcare_db" or database_name.startswith("kindcare_test_")
    elderly_id = f"MQ{uuid4().hex[:12]}"
    other_elderly_id = f"MQ{uuid4().hex[:12]}"
    mongo_client = MongoClient(mongo_uri, tz_aware=True)
    database = mongo_client[database_name]
    mqtt_client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv311,
    )
    mqtt_client.username_pw_set(
        os.environ.get("MQTT_USERNAME", "kindcare_mqtt"),
        os.environ.get("MQTT_PASSWORD", "kindcare_mqtt_dev_only"),
    )
    connected = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            connected.set()

    mqtt_client.on_connect = on_connect
    mqtt_client.connect(os.environ.get("MQTT_BROKER", "mosquitto"), 1883, 60)
    mqtt_client.loop_start()
    assert connected.wait(10)

    cleanup_collections = [
        "elderly_profiles",
        "health_idempotency",
        "health_logs",
        "activity_idempotency",
        "activity_logs",
        "activity_state",
        "device_idempotency",
        "device_events",
        "device_status",
        "reminder_idempotency",
        "reminders",
        "alerts",
    ]
    try:
        status, _ = request_json(
            api_url,
            "POST",
            "/api/elderly",
            {
                "elderly_id": elderly_id,
                "full_name": "MQTT Integration Person",
                "date_of_birth": date(1940, 1, 1).isoformat(),
            },
        )
        assert status == 201
        status, _ = request_json(
            api_url,
            "POST",
            "/api/elderly",
            {
                "elderly_id": other_elderly_id,
                "full_name": "MQTT Other Integration Person",
                "date_of_birth": date(1941, 1, 1).isoformat(),
            },
        )
        assert status == 201
        now = datetime.now(UTC).replace(microsecond=0)

        normal_key = "mqtt-integration-normal"
        normal = {
            "idempotency_key": normal_key,
            "elderly_id": elderly_id,
            "heart_rate": 80,
            "temperature": 36.7,
            "oxygen_level": 97,
            "movement_status": "active",
            "medicine_status": "taken",
            "emergency_pressed": False,
            "recorded_at": now.isoformat().replace("+00:00", "Z"),
        }
        publish(mqtt_client, elderly_id, "health", normal)
        stored_normal = wait_for_record(
            api_url,
            f"/api/health/{elderly_id}?limit=100",
            lambda record: record["recorded_at"] == normal["recorded_at"],
        )
        assert stored_normal["risk_level"] == "normal"
        normal_id = stored_normal["event_id"]
        assert database.health_logs.count_documents({"event_id": normal_id}) == 1

        emergency_key = "mqtt-integration-emergency"
        emergency = {
            **normal,
            "idempotency_key": emergency_key,
            "heart_rate": 130,
            "oxygen_level": 90,
            "emergency_pressed": True,
            "recorded_at": (now + timedelta(seconds=1)).isoformat().replace(
                "+00:00", "Z"
            ),
        }
        publish(mqtt_client, elderly_id, "health", emergency)
        stored_emergency = wait_for_record(
            api_url,
            f"/api/health/{elderly_id}?limit=100",
            lambda record: record["recorded_at"] == emergency["recorded_at"],
        )
        assert stored_emergency["risk_level"] == "emergency"
        emergency_id = stored_emergency["event_id"]
        assert wait_for(
            lambda: database.alerts.count_documents({"event_id": emergency_id}) == 3
        )

        activity_key = "mqtt-integration-activity"
        activity = {
            "idempotency_key": activity_key,
            "elderly_id": elderly_id,
            "value": "active",
            "recorded_at": now.isoformat().replace("+00:00", "Z"),
        }
        publish(mqtt_client, elderly_id, "activity", activity)
        stored_activity = wait_for_record(
            api_url,
            f"/api/activity/{elderly_id}?limit=100",
            lambda record: record["recorded_at"] == activity["recorded_at"],
        )
        assert stored_activity["value"] == "active"
        activity_http = {
            key: value for key, value in activity.items() if key != "idempotency_key"
        }
        status, response = request_json(
            api_url, "POST", "/api/activity", activity_http, activity_key
        )
        assert status == 202
        assert response["data"]["event_id"] == stored_activity["event_id"]
        status, _ = request_json(
            api_url,
            "POST",
            "/api/activity",
            {**activity_http, "value": "inactive"},
            activity_key,
        )
        assert status == 409
        publish(
            mqtt_client,
            elderly_id,
            "activity",
            {**activity, "value": "inactive"},
        )

        device_key = "mqtt-integration-device"
        device = {
            "idempotency_key": device_key,
            "elderly_id": elderly_id,
            "recorded_at": now.isoformat().replace("+00:00", "Z"),
        }
        publish(mqtt_client, elderly_id, "device", device)
        stored_device = wait_for_record(
            api_url,
            f"/api/device-status/{elderly_id}?limit=100",
            lambda record: record["recorded_at"] == device["recorded_at"],
        )
        assert stored_device["elderly_id"] == elderly_id
        device_http = {
            key: value for key, value in device.items() if key != "idempotency_key"
        }
        status, response = request_json(
            api_url, "POST", "/api/device-status", device_http, device_key
        )
        assert status == 202
        assert response["data"]["event_id"] == stored_device["event_id"]
        changed_device_time = (now + timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        )
        status, _ = request_json(
            api_url,
            "POST",
            "/api/device-status",
            {**device_http, "recorded_at": changed_device_time},
            device_key,
        )
        assert status == 409
        publish(
            mqtt_client,
            elderly_id,
            "device",
            {**device, "recorded_at": changed_device_time},
        )

        status, reminder_response = request_json(
            api_url,
            "POST",
            "/api/reminders",
            {
                "elderly_id": elderly_id,
                "medicine_name": "MQTT integration medicine",
                "scheduled_for": (now + timedelta(minutes=5)).isoformat().replace(
                    "+00:00", "Z"
                ),
            },
            "mqtt-integration-reminder-create",
        )
        assert status == 201
        reminder_id = reminder_response["data"]["reminder_id"]
        publish(
            mqtt_client,
            other_elderly_id,
            "reminder",
            {
                "idempotency_key": "mqtt-integration-wrong-owner",
                "elderly_id": other_elderly_id,
                "reminder_id": reminder_id,
                "status": "taken",
            },
        )
        barrier_time = (now + timedelta(seconds=3)).isoformat().replace(
            "+00:00", "Z"
        )
        publish(
            mqtt_client,
            other_elderly_id,
            "device",
            {
                "idempotency_key": "mqtt-integration-owner-barrier",
                "elderly_id": other_elderly_id,
                "recorded_at": barrier_time,
            },
        )
        wait_for_record(
            api_url,
            f"/api/device-status/{other_elderly_id}?limit=100",
            lambda record: record["recorded_at"] == barrier_time,
        )
        owner_reminders = get_data(api_url, f"/api/reminders/{elderly_id}?limit=100")
        assert next(
            record for record in owner_reminders if record["reminder_id"] == reminder_id
        )["status"] == "pending"

        publish(
            mqtt_client,
            elderly_id,
            "reminder",
            {
                "idempotency_key": "mqtt-integration-reminder-taken",
                "elderly_id": elderly_id,
                "reminder_id": reminder_id,
                "status": "taken",
            },
        )
        wait_for_record(
            api_url,
            f"/api/reminders/{elderly_id}?limit=100",
            lambda record: record["reminder_id"] == reminder_id
            and record["status"] == "taken",
        )

        equivalent_key = "mqtt-http-equivalent"
        equivalent = {
            **normal,
            "idempotency_key": equivalent_key,
            "recorded_at": (now + timedelta(seconds=2)).isoformat().replace(
                "+00:00", "Z"
            ),
        }
        publish(mqtt_client, elderly_id, "health", equivalent)
        stored_equivalent = wait_for_record(
            api_url,
            f"/api/health/{elderly_id}?limit=100",
            lambda record: record["recorded_at"] == equivalent["recorded_at"],
        )
        equivalent_id = stored_equivalent["event_id"]
        http_payload = {
            key: value for key, value in equivalent.items() if key != "idempotency_key"
        }
        status, response = request_json(
            api_url,
            "POST",
            "/api/health",
            http_payload,
            equivalent_key,
        )
        assert status == 202
        assert response["data"]["event_id"] == equivalent_id
        status, _ = request_json(
            api_url,
            "POST",
            "/api/health",
            {**http_payload, "oxygen_level": 90},
            equivalent_key,
        )
        assert status == 409
        publish(
            mqtt_client,
            elderly_id,
            "health",
            {**equivalent, "oxygen_level": 90},
        )
        barrier_time = (now + timedelta(seconds=10)).isoformat().replace(
            "+00:00", "Z"
        )
        publish(
            mqtt_client,
            elderly_id,
            "device",
            {
                "idempotency_key": "mqtt-conflict-barrier",
                "elderly_id": elderly_id,
                "recorded_at": barrier_time,
            },
        )
        wait_for_record(
            api_url,
            f"/api/device-status/{elderly_id}?limit=100",
            lambda record: record["recorded_at"] == barrier_time,
        )
        time.sleep(1)
        assert database.health_logs.count_documents({"event_id": equivalent_id}) == 1
        assert database.alerts.count_documents({"event_id": equivalent_id}) == 0
        assert database.activity_logs.count_documents(
            {"event_id": stored_activity["event_id"], "value": "active"}
        ) == 1
        assert database.device_events.count_documents(
            {
                "event_id": stored_device["event_id"],
                "recorded_at": now,
            }
        ) == 1
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        for collection_name in cleanup_collections:
            database[collection_name].delete_many(
                {"elderly_id": {"$in": [elderly_id, other_elderly_id]}}
            )
        mongo_client.close()


def test_real_mosquitto_saturation_forwards_each_logical_message_once(
    tmp_path: Path,
) -> None:
    api_url = os.environ.get("API_BASE_URL", "http://backend:8000").rstrip("/")
    mongo_uri = os.environ.get(
        "MONGO_URI", "mongodb://mongodb:27017/?replicaSet=rs0"
    )
    database_name = os.environ.get("DATABASE_NAME", "kindcare_db")
    assert database_name == "kindcare_db" or database_name.startswith("kindcare_test_")
    elderly_id = f"MS{uuid4().hex[:12]}"
    keys = [f"mqtt-saturation-{uuid4().hex}" for _ in range(3)]
    mongo_client = MongoClient(mongo_uri, tz_aware=True)
    database = mongo_client[database_name]
    proxy = CountingProxy(api_url, keys[0])
    publisher = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv311,
    )
    publisher.username_pw_set(
        os.environ.get("MQTT_USERNAME", "kindcare_mqtt"),
        os.environ.get("MQTT_PASSWORD", "kindcare_mqtt_dev_only"),
    )
    publisher_connected = threading.Event()

    def on_publisher_connect(client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            publisher_connected.set()

    publisher.on_connect = on_publisher_connect
    settings = Settings(
        broker=os.environ.get("MQTT_BROKER", "mosquitto"),
        port=int(os.environ.get("MQTT_PORT", "1883")),
        username=os.environ.get("MQTT_USERNAME", "kindcare_mqtt"),
        password=os.environ.get("MQTT_PASSWORD", "kindcare_mqtt_dev_only"),
        client_id=f"kindcare-saturation-{uuid4().hex}",
        api_base_url=proxy.url,
        max_payload_bytes=16_384,
        queue_size=1,
        http_timeout=25,
        retry_initial_seconds=0.05,
        retry_max_seconds=0.2,
        reconnect_min_seconds=1,
        reconnect_max_seconds=2,
        health_file=str(tmp_path / "mqtt-saturation-health"),
        health_max_age_seconds=5,
    )
    ingestor = TrackingIngestor(settings)
    ingestor_thread = threading.Thread(target=ingestor.run, daemon=True)
    ingestor_thread_started = False
    publisher_loop_started = False
    now = datetime.now(UTC).replace(microsecond=0)
    recorded_at = [
        (now + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        for index in range(3)
    ]

    try:
        status, _ = request_json(
            api_url,
            "POST",
            "/api/elderly",
            {
                "elderly_id": elderly_id,
                "full_name": "MQTT Saturation Integration Person",
                "date_of_birth": date(1940, 1, 1).isoformat(),
            },
        )
        assert status == 201
        proxy.start()
        ingestor_thread.start()
        ingestor_thread_started = True
        assert wait_for(lambda: ingestor.connected_event.is_set())
        publisher.connect(settings.broker, settings.port, 60)
        publisher.loop_start()
        publisher_loop_started = True
        assert publisher_connected.wait(10)

        publish(
            publisher,
            elderly_id,
            "device",
            {
                "idempotency_key": keys[0],
                "elderly_id": elderly_id,
                "recorded_at": recorded_at[0],
            },
        )
        assert proxy.first_forwarded.wait(10)
        for index in (1, 2):
            publish(
                publisher,
                elderly_id,
                "device",
                {
                    "idempotency_key": keys[index],
                    "elderly_id": elderly_id,
                    "recorded_at": recorded_at[index],
                },
            )
        assert wait_for(lambda: ingestor.backpressure_event.is_set())
        proxy.release_first.set()

        assert wait_for(lambda: proxy.counts == {key: 1 for key in keys})
        assert wait_for(
            lambda: database.device_events.count_documents(
                {"elderly_id": elderly_id}
            )
            == 3
        )
        assert wait_for(
            lambda: ingestor.connected_event.is_set()
            and not ingestor.backpressure_event.is_set()
            and Path(settings.health_file).exists()
        )
        stable_counts = dict(proxy.counts)
        stable_connects = ingestor.connect_count
        stable_disconnects = ingestor.disconnect_count
        time.sleep(2)

        assert proxy.counts == stable_counts == {key: 1 for key in keys}
        assert ingestor.connect_count == stable_connects == 2
        assert ingestor.disconnect_count == stable_disconnects == 1
    finally:
        cleanup_saturation_resources(
            publisher=publisher,
            publisher_loop_started=publisher_loop_started,
            ingestor=ingestor,
            ingestor_thread=ingestor_thread,
            ingestor_thread_started=ingestor_thread_started,
            proxy=proxy,
            settings=settings,
            database=database,
            elderly_id=elderly_id,
            mongo_client=mongo_client,
        )
