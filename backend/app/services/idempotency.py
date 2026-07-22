import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import TypeVar


class IdempotencyConflict(Exception):
    """Raised before publish when a key is reused for different input."""


T = TypeVar("T")


async def run_reservation_transaction(
    collection: object,
    callback: Callable[[object | None], Awaitable[T]],
) -> T:
    database = getattr(collection, "database", None)
    client = getattr(database, "client", None)
    if client is None:
        return await callback(None)
    async with client.start_session() as session:
        return await session.with_transaction(callback)


def session_options(session: object | None) -> dict[str, object]:
    return {"session": session} if session is not None else {}


async def backfill_legacy_reservation(
    reservation: dict[str, object],
    reservations: object,
    event_logs: object,
    payload_hash_from_event: Callable[[dict[str, object]], str],
    session: object | None,
) -> dict[str, object]:
    if reservation.get("payload_hash") is not None:
        return reservation
    if session is None:
        return reservation
    event = await event_logs.find_one(
        {"event_id": reservation["event_id"]},
        session=session,
    )
    if event is None:
        raise IdempotencyConflict(
            "Idempotency-Key belongs to a legacy request that cannot be safely replayed"
        )
    payload_hash = payload_hash_from_event(event)
    await reservations.update_one(
        {"_id": reservation["_id"], "payload_hash": {"$exists": False}},
        {"$set": {"payload_hash": payload_hash}},
        session=session,
    )
    return {**reservation, "payload_hash": payload_hash}


def canonical_payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_payload_hash(reservation: dict[str, object], payload_hash: str) -> None:
    if reservation.get("payload_hash") != payload_hash:
        raise IdempotencyConflict(
            "Idempotency-Key was already used with a different payload"
        )
