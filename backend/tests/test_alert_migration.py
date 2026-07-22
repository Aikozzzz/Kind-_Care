from uuid import UUID

from app.database import _canonical_uuid, derive_alert_id


def test_derive_alert_id_is_stable_public_uuid() -> None:
    first = derive_alert_id("event-1", "high_temperature")
    assert first == derive_alert_id("event-1", "high_temperature")
    assert str(UUID(first)) == first
    assert first != derive_alert_id("event-1", "low_oxygen")


def test_canonical_uuid_rejects_noncanonical_persisted_values() -> None:
    canonical = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    assert _canonical_uuid(canonical) == canonical
    assert _canonical_uuid(canonical.upper()) is None
    assert _canonical_uuid(None) is None
    assert _canonical_uuid(42) is None
    assert _canonical_uuid("") is None
    assert _canonical_uuid("not-a-uuid") is None
