from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import get_current_principal, hash_password, verify_password


def test_password_hash_is_salted_and_verifies() -> None:
    first = hash_password("CorrectHorseBatteryStaple!")
    second = hash_password("CorrectHorseBatteryStaple!")

    assert first != second
    assert verify_password("CorrectHorseBatteryStaple!", first)
    assert not verify_password("wrong password", first)


def test_protected_dashboard_route_requires_bearer_auth(client: TestClient) -> None:
    app.dependency_overrides.pop(get_current_principal, None)

    response = client.get("/api/elderly/E001")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
