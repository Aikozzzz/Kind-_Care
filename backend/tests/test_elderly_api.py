import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_elderly_service
from app.main import app
import app.services.elderly as elderly_service


class UnavailableElderlyService:
    @staticmethod
    def _raise_unavailable() -> None:
        raise elderly_service.ElderlyProfileStorageUnavailable()

    async def create_profile(self, profile):
        self._raise_unavailable()

    async def list_profiles(self, **kwargs):
        self._raise_unavailable()

    async def get_profile(self, elderly_id):
        self._raise_unavailable()

    async def update_profile(self, elderly_id, updates):
        self._raise_unavailable()

    async def delete_profile(self, elderly_id):
        self._raise_unavailable()


def test_create_elderly_profile_returns_success_envelope(client: TestClient) -> None:
    response = client.post(
        "/api/elderly",
        json={
            "elderly_id": "E003",
            "full_name": "Aisha Rahman",
            "date_of_birth": "1950-03-15",
            "phone_number": "555-0133",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Elderly profile created successfully"
    assert body["data"]["elderly_id"] == "E003"
    assert body["data"]["active"] is True
    assert body["data"]["created_at"].endswith("Z")


def test_create_duplicate_elderly_profile_returns_conflict(client: TestClient) -> None:
    response = client.post(
        "/api/elderly",
        json={
            "elderly_id": "E001",
            "full_name": "Duplicate Profile",
            "date_of_birth": "1940-01-01",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Elderly profile E001 already exists"}


def test_list_elderly_profiles_returns_only_active_profiles_by_default(
    client: TestClient,
) -> None:
    response = client.get("/api/elderly")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Elderly profiles retrieved successfully"
    assert [profile["elderly_id"] for profile in body["data"]] == ["E001"]


def test_list_elderly_profiles_can_include_inactive_profiles(client: TestClient) -> None:
    response = client.get("/api/elderly?include_inactive=true")

    assert response.status_code == 200
    assert [profile["elderly_id"] for profile in response.json()["data"]] == [
        "E001",
        "E002",
    ]


def test_list_elderly_profiles_applies_limit_and_offset(client: TestClient) -> None:
    response = client.get(
        "/api/elderly?include_inactive=true&limit=1&offset=1"
    )

    assert response.status_code == 200
    assert [profile["elderly_id"] for profile in response.json()["data"]] == [
        "E002"
    ]


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "offset=-1", "offset=10001"],
)
def test_list_elderly_profiles_rejects_invalid_pagination(
    client: TestClient,
    query: str,
) -> None:
    response = client.get(f"/api/elderly?{query}")

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "request_kwargs"),
    [
        (
            "POST",
            "/api/elderly",
            {
                "json": {
                    "elderly_id": "E900",
                    "full_name": "Unavailable Test",
                    "date_of_birth": "1950-01-01",
                }
            },
        ),
        ("GET", "/api/elderly", {}),
        ("GET", "/api/elderly/E001", {}),
        ("PATCH", "/api/elderly/E001", {"json": {"phone_number": "555-0100"}}),
        ("DELETE", "/api/elderly/E001", {}),
    ],
)
def test_profile_routes_return_consistent_service_unavailable_envelope(
    client: TestClient,
    method: str,
    path: str,
    request_kwargs: dict[str, object],
) -> None:
    app.dependency_overrides[get_elderly_service] = lambda: UnavailableElderlyService()

    response = client.request(method, path, **request_kwargs)

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "message": "Elderly profile database is unavailable",
        "data": {"status": "unavailable"},
    }


def test_get_elderly_profile_returns_profile(client: TestClient) -> None:
    response = client.get("/api/elderly/E001")

    assert response.status_code == 200
    assert response.json()["data"]["full_name"] == "Margaret Lee"


def test_get_missing_or_inactive_profile_returns_not_found(client: TestClient) -> None:
    response = client.get("/api/elderly/E002")

    assert response.status_code == 404
    assert response.json() == {"detail": "Elderly profile E002 not found"}


def test_update_elderly_profile_returns_updated_profile(client: TestClient) -> None:
    response = client.patch(
        "/api/elderly/E001",
        json={"phone_number": "555-0110", "medical_notes": "No known allergies"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Elderly profile updated successfully"
    assert body["data"]["phone_number"] == "555-0110"
    assert body["data"]["medical_notes"] == "No known allergies"


def test_update_rejects_an_empty_request(client: TestClient) -> None:
    response = client.patch("/api/elderly/E001", json={})

    assert response.status_code == 422


def test_delete_elderly_profile_soft_deletes_profile(client: TestClient) -> None:
    response = client.delete("/api/elderly/E001")

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Elderly profile deleted successfully"
    assert body["data"]["active"] is False

    assert client.get("/api/elderly/E001").status_code == 404


def test_restore_elderly_profile_reactivates_archived_profile(client: TestClient) -> None:
    response = client.post("/api/elderly/E002/restore")

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Elderly profile restored successfully"
    assert body["data"]["active"] is True


@pytest.mark.parametrize(
    ("method", "request_kwargs"),
    [
        ("GET", {}),
        ("PATCH", {"json": {"phone_number": "555-0110"}}),
        ("DELETE", {}),
    ],
)
def test_elderly_profile_routes_validate_path_elderly_id(
    client: TestClient,
    method: str,
    request_kwargs: dict[str, object],
) -> None:
    response = client.request(
        method,
        "/api/elderly/invalid$id",
        **request_kwargs,
    )

    assert response.status_code == 422
