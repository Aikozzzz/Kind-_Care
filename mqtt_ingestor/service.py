import logging
import queue
import threading
import hashlib
from collections import OrderedDict
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from mqtt_ingestor.config import Settings
from mqtt_ingestor.healthcheck import clear_connected, mark_connected
from mqtt_ingestor.http_bridge import HTTPBridge, forward_with_retry
from mqtt_ingestor.routing import PermanentMessageError, parse_topic, prepare_request


logger = logging.getLogger(__name__)
SUBSCRIPTION = "kindcare/+/+"
MQTT_V311_DEFAULT_INFLIGHT = 20


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_topic_metadata(topic: str) -> tuple[str, str]:
    try:
        route = parse_topic(topic)
    except PermanentMessageError:
        return "invalid", _short_hash(topic)
    return route.kind, _short_hash(route.elderly_id)


def _message_fingerprint(topic: str, payload: bytes, retain: bool) -> bytes:
    topic_bytes = topic.encode("utf-8")
    digest = hashlib.sha256()
    digest.update(len(topic_bytes).to_bytes(4, "big"))
    digest.update(topic_bytes)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
    digest.update(b"\x01" if retain else b"\x00")
    return digest.digest()


@dataclass(frozen=True)
class InboundMessage:
    topic: str
    payload: bytes
    mid: int
    qos: int
    retain: bool
    dup: bool


def create_client(settings: Settings) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=settings.client_id,
        clean_session=False,
        protocol=mqtt.MQTTv311,
        manual_ack=True,
    )
    client.username_pw_set(settings.username, settings.password)
    client.reconnect_delay_set(
        min_delay=settings.reconnect_min_seconds,
        max_delay=settings.reconnect_max_seconds,
    )
    return client


class MQTTIngestor:
    def __init__(
        self,
        settings: Settings,
        *,
        client: mqtt.Client | None = None,
        bridge: HTTPBridge | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or create_client(settings)
        self.bridge = bridge or HTTPBridge(
            settings.api_base_url, settings.http_timeout
        )
        clear_connected(settings.health_file)
        self.stop_event = threading.Event()
        self.connected_event = threading.Event()
        self.backpressure_event = threading.Event()
        self.queue: queue.Queue[InboundMessage] = queue.Queue(settings.queue_size)
        self.worker = threading.Thread(
            target=self._work,
            name="mqtt-http-forwarder",
        )
        self._started = False
        self._shutdown = False
        self._lifecycle_lock = threading.Lock()
        self._backpressure_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._ack_lock = threading.Lock()
        self._completed_capacity = max(
            settings.queue_size + 1, MQTT_V311_DEFAULT_INFLIGHT
        )
        self._completed_deliveries: OrderedDict[tuple[int, int], bytes] = OrderedDict()
        self._pending_acknowledgements: set[tuple[int, int]] = set()

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self.connected_event.clear()
            clear_connected(self.settings.health_file)
            logger.error("MQTT connection rejected reason=%s", reason_code)
            return
        self._flush_pending_acknowledgements()
        result, _ = client.subscribe(SUBSCRIPTION, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            clear_connected(self.settings.health_file)
            logger.error("MQTT subscription failed result=%s", result)
            return
        self.connected_event.set()
        logger.info("MQTT connected and subscribed qos=1")

    def on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        with self._health_lock:
            self.connected_event.clear()
            clear_connected(self.settings.health_file)
        if not self.stop_event.is_set():
            logger.warning("MQTT disconnected reason=%s; reconnecting", reason_code)

    def on_message(self, client, userdata, message) -> None:
        if self.stop_event.is_set():
            return
        payload = bytes(message.payload)
        if message.dup:
            if self._ack_completed_redelivery(
                message.mid, message.qos, message.topic, payload, message.retain
            ):
                return
        else:
            self._forget_completed(message.mid, message.qos)
        inbound = InboundMessage(
            topic=message.topic,
            payload=payload,
            mid=message.mid,
            qos=message.qos,
            retain=message.retain,
            dup=message.dup,
        )
        try:
            self.queue.put_nowait(inbound)
        except queue.Full:
            self._trigger_backpressure()

    def _trigger_backpressure(self) -> None:
        if self.backpressure_event.is_set():
            return
        if not self._backpressure_lock.acquire(blocking=False):
            return
        try:
            if self.backpressure_event.is_set() or self.stop_event.is_set():
                return
            with self._health_lock:
                self.backpressure_event.set()
                self.connected_event.clear()
                clear_connected(self.settings.health_file)
            logger.warning("MQTT queue saturated; disconnected for broker redelivery")
            threading.Thread(
                target=self.client.disconnect,
                name="mqtt-backpressure-disconnect",
                daemon=True,
            ).start()
        finally:
            self._backpressure_lock.release()

    def _acknowledge(
        self, message: InboundMessage, kind: str, identity_hash: str
    ) -> None:
        acknowledgement = (message.mid, message.qos)
        fingerprint = _message_fingerprint(
            message.topic, message.payload, message.retain
        )
        with self._ack_lock:
            self._remember_completed(acknowledgement, fingerprint)
            result = self.client.ack(*acknowledgement)
            if result == mqtt.MQTT_ERR_SUCCESS:
                self._pending_acknowledgements.discard(acknowledgement)
            else:
                self._pending_acknowledgements.add(acknowledgement)
        if result != mqtt.MQTT_ERR_SUCCESS:
            logger.warning(
                "MQTT acknowledgement deferred kind=%s identity=%s mid=%d result=%s",
                kind,
                identity_hash,
                message.mid,
                result,
            )

    def _remember_completed(
        self, acknowledgement: tuple[int, int], fingerprint: bytes
    ) -> None:
        self._completed_deliveries[acknowledgement] = fingerprint
        self._completed_deliveries.move_to_end(acknowledgement)
        while len(self._completed_deliveries) > self._completed_capacity:
            evicted, _ = self._completed_deliveries.popitem(last=False)
            self._pending_acknowledgements.discard(evicted)

    def _forget_completed(self, mid: int, qos: int) -> None:
        acknowledgement = (mid, qos)
        with self._ack_lock:
            self._completed_deliveries.pop(acknowledgement, None)
            self._pending_acknowledgements.discard(acknowledgement)

    def _ack_completed_redelivery(
        self, mid: int, qos: int, topic: str, payload: bytes, retain: bool
    ) -> bool:
        acknowledgement = (mid, qos)
        fingerprint = _message_fingerprint(topic, payload, retain)
        with self._ack_lock:
            completed_fingerprint = self._completed_deliveries.get(acknowledgement)
            if completed_fingerprint is None:
                return False
            if completed_fingerprint != fingerprint:
                del self._completed_deliveries[acknowledgement]
                self._pending_acknowledgements.discard(acknowledgement)
                return False
            self._completed_deliveries.move_to_end(acknowledgement)
            result = self.client.ack(*acknowledgement)
            if result == mqtt.MQTT_ERR_SUCCESS:
                self._pending_acknowledgements.discard(acknowledgement)
            else:
                self._pending_acknowledgements.add(acknowledgement)
        return True

    def _flush_pending_acknowledgements(self) -> None:
        with self._ack_lock:
            for acknowledgement in tuple(self._pending_acknowledgements):
                if acknowledgement not in self._completed_deliveries:
                    self._pending_acknowledgements.remove(acknowledgement)
                    continue
                if self.client.ack(*acknowledgement) == mqtt.MQTT_ERR_SUCCESS:
                    self._pending_acknowledgements.remove(acknowledgement)

    def process_message(self, message: InboundMessage) -> None:
        kind, identity_hash = _safe_topic_metadata(message.topic)
        if message.qos != 1:
            logger.warning(
                "ignored non-QoS-1 MQTT message kind=%s identity=%s mid=%d qos=%d",
                kind,
                identity_hash,
                message.mid,
                message.qos,
            )
            return
        if message.retain:
            logger.info(
                "ignored retained MQTT message kind=%s identity=%s mid=%d",
                kind,
                identity_hash,
                message.mid,
            )
            self._acknowledge(message, kind, identity_hash)
            return
        try:
            route = parse_topic(message.topic)
            prepared = prepare_request(
                route, message.payload, self.settings.max_payload_bytes
            )
        except PermanentMessageError as error:
            logger.warning(
                "permanent MQTT message rejection kind=%s identity=%s mid=%d reason=%s",
                kind,
                identity_hash,
                message.mid,
                error,
            )
            self._acknowledge(message, kind, identity_hash)
            return

        forwarded = forward_with_retry(
            prepared,
            self.bridge,
            self.stop_event,
            self.settings.retry_initial_seconds,
            self.settings.retry_max_seconds,
        )
        result = forwarded.http_result
        if result is None:
            logger.info(
                "MQTT message unacknowledged kind=%s identity=%s mid=%d attempts=%d",
                kind,
                identity_hash,
                message.mid,
                forwarded.attempts,
            )
            return
        logger.info(
            "MQTT message final kind=%s identity=%s mid=%d outcome=%s status=%s attempts=%d",
            kind,
            identity_hash,
            message.mid,
            result.outcome.value,
            result.status_code,
            forwarded.attempts,
        )
        self._acknowledge(message, kind, identity_hash)

    def _work(self) -> None:
        health_interval = min(0.25, self.settings.health_max_age_seconds / 3)
        while not self.stop_event.is_set():
            self._refresh_health()
            try:
                message = self.queue.get(timeout=health_interval)
            except queue.Empty:
                continue
            try:
                self.process_message(message)
            finally:
                self.queue.task_done()
                self._refresh_health()
        clear_connected(self.settings.health_file)

    def _refresh_health(self) -> None:
        with self._health_lock:
            if (
                self.connected_event.is_set()
                and not self.backpressure_event.is_set()
                and not self.stop_event.is_set()
            ):
                mark_connected(self.settings.health_file)
            else:
                clear_connected(self.settings.health_file)

    def _wait_for_drain(self) -> bool:
        while self.queue.unfinished_tasks and not self.stop_event.wait(0.01):
            pass
        return not self.stop_event.is_set()

    def _reconnect_after_backpressure(self) -> bool:
        delay = self.settings.reconnect_min_seconds
        attempts = 0
        while not self.stop_event.is_set():
            attempts += 1
            try:
                result = self.client.reconnect()
            except OSError:
                result = mqtt.MQTT_ERR_NO_CONN
            if result == mqtt.MQTT_ERR_SUCCESS:
                logger.info("MQTT backpressure reconnect initiated attempts=%d", attempts)
                return True
            if self.stop_event.wait(delay):
                return False
            delay = min(delay * 2, self.settings.reconnect_max_seconds)
        return False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            self._started = True
            self.client.on_connect = self.on_connect
            self.client.on_disconnect = self.on_disconnect
            self.client.on_message = self.on_message
            self.worker.start()
            self.client.connect_async(
                self.settings.broker,
                self.settings.port,
                keepalive=60,
            )

    def run(self) -> None:
        try:
            self.start()
            while not self.stop_event.is_set():
                self.client.loop_forever(retry_first_connection=True)
                if self.stop_event.is_set() or not self.backpressure_event.is_set():
                    break
                if not self._wait_for_drain():
                    break
                self.backpressure_event.clear()
                if not self._reconnect_after_backpressure():
                    break
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                return
            self._shutdown = True
            self.stop_event.set()
            self.connected_event.clear()
            clear_connected(self.settings.health_file)
            self.client.disconnect()
        if self.worker.is_alive():
            self.worker.join(timeout=self.settings.http_timeout + 1)
