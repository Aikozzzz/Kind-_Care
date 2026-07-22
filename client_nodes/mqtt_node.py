import argparse
import json
import os
import re
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import paho.mqtt.client as mqtt

from client_nodes.simulator import SCENARIOS, build_scenario


MessageKind = Literal["health", "activity", "device", "reminder"]
ELDERLY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,50}")


def create_mqtt_client(username: str, password: str) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set(username, password)
    return client


class MQTTNodeClient:
    def __init__(
        self,
        broker: str,
        port: int,
        username: str,
        password: str,
        elderly_id: str,
        *,
        publish_timeout: float = 10,
        connect_timeout: float = 10,
        client: mqtt.Client | None = None,
        key_factory=lambda: uuid4().hex,
    ) -> None:
        if ELDERLY_ID_PATTERN.fullmatch(elderly_id) is None:
            raise ValueError("elderly_id must use 1-50 ASCII letters, numbers, underscores, or hyphens")
        if connect_timeout <= 0:
            raise ValueError("connect_timeout must be positive")
        self.broker = broker
        self.port = port
        self.elderly_id = elderly_id
        self.publish_timeout = publish_timeout
        self.connect_timeout = connect_timeout
        self.client = client or create_mqtt_client(username, password)
        self.key_factory = key_factory
        self.connected = False
        self.closed = False
        self.loop_started = False
        self.connack_event = threading.Event()
        self.connack_reason: object | None = None
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connack_reason = getattr(reason_code, "value", reason_code)
        self.connected = self.connack_reason == 0
        self.connack_event.set()

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ) -> None:
        self.connected = False

    def _close_transport(self) -> None:
        if self.loop_started:
            self.client.loop_stop()
            self.loop_started = False
        self.client.disconnect()
        self.connected = False

    def connect(self) -> None:
        if self.closed:
            raise ConnectionError("MQTT client is closed")
        self.connack_event.clear()
        self.connack_reason = None
        result = self.client.connect(self.broker, self.port, keepalive=60)
        if result != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT connect failed with result {result}")
        self.client.loop_start()
        self.loop_started = True
        if not self.connack_event.wait(self.connect_timeout):
            self._close_transport()
            self.closed = True
            raise ConnectionError("MQTT CONNACK timeout")
        if self.connack_reason != 0:
            reason = self.connack_reason
            self._close_transport()
            self.closed = True
            raise ConnectionError(f"MQTT connection rejected with reason {reason}")

    def publish(
        self,
        kind: MessageKind,
        fields: dict[str, object],
        idempotency_key: str | None = None,
    ) -> str:
        if not self.connected or self.closed:
            raise ConnectionError("MQTT client is not connected")
        if kind not in {"health", "activity", "device", "reminder"}:
            raise ValueError("unsupported MQTT message kind")
        key = idempotency_key or self.key_factory()
        payload = {
            **fields,
            "idempotency_key": key,
            "elderly_id": self.elderly_id,
        }
        info = self.client.publish(
            f"kindcare/{self.elderly_id}/{kind}",
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT publish failed with result {info.rc}")
        try:
            info.wait_for_publish(timeout=self.publish_timeout)
        except (RuntimeError, ValueError) as error:
            raise ConnectionError("MQTT publish confirmation failed") from error
        if not info.is_published():
            raise ConnectionError("MQTT publish was not confirmed before timeout")
        return key

    def publish_reminder_taken(
        self,
        reminder_id: str,
        idempotency_key: str | None = None,
    ) -> str:
        return self.publish(
            "reminder",
            {"reminder_id": reminder_id, "status": "taken"},
            idempotency_key,
        )

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._close_transport()


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_telemetry(
    scenario: str,
    elderly_id: str,
    count: int,
) -> Iterator[tuple[MessageKind, dict[str, object]]]:
    for number, health in enumerate(
        build_scenario(scenario, elderly_id, count), start=1
    ):
        recorded_at = _utc_timestamp()
        health_payload = {**health, "recorded_at": recorded_at}
        yield "health", health_payload
        yield "activity", {
            "elderly_id": elderly_id,
            "value": health["movement_status"],
            "recorded_at": recorded_at,
        }
        if scenario != "offline" or number == 1:
            yield "device", {
                "elderly_id": elderly_id,
                "recorded_at": recorded_at,
            }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish KindCare MQTT demo events")
    parser.add_argument("--broker", default=os.environ.get("MQTT_BROKER", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    parser.add_argument("--username", default=os.environ.get("MQTT_USERNAME", "kindcare_mqtt"))
    parser.add_argument(
        "--password",
        default=os.environ.get("MQTT_PASSWORD", "kindcare_mqtt_dev_only"),
    )
    parser.add_argument("--elderly-id", default="E001")
    parser.add_argument("--scenario", choices=[*SCENARIOS, "mixed"], default="normal")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--publish-timeout", type=float, default=10.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--reminder-id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.count < 0
        or args.interval < 0
        or args.publish_timeout <= 0
        or args.connect_timeout <= 0
    ):
        raise SystemExit("count/interval must be nonnegative and timeouts positive")
    node = MQTTNodeClient(
        args.broker,
        args.port,
        args.username,
        args.password,
        args.elderly_id,
        publish_timeout=args.publish_timeout,
        connect_timeout=args.connect_timeout,
    )
    try:
        node.connect()
        if args.reminder_id:
            key = node.publish_reminder_taken(args.reminder_id)
            print(
                f"[+] reminder elderly={args.elderly_id} key={key}",
                flush=True,
            )
        for number, (kind, payload) in enumerate(
            build_telemetry(args.scenario, args.elderly_id, args.count), start=1
        ):
            key = node.publish(kind, payload)
            print(
                f"[+] {kind} message={number} elderly={args.elderly_id} key={key}",
                flush=True,
            )
            if args.interval and kind == "device":
                time.sleep(args.interval)
        return 0
    except Exception as error:
        print(f"[x] MQTT demo failed elderly={args.elderly_id}: {error}", flush=True)
        return 1
    finally:
        node.close()


if __name__ == "__main__":
    raise SystemExit(main())
