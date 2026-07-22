from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_elderly_service
from app.main import app
from app.models.elderly import ElderlyProfile, ElderlyProfileCreate, ElderlyProfileUpdate
from app.services.elderly import ElderlyProfileAlreadyExists, ElderlyProfileNotFound


class FakeElderlyService:
    def __init__(self) -> None:
        now = datetime(2026, 7, 10, 8, 30, tzinfo=UTC)
        self.profiles = {
            "E001": ElderlyProfile(
                elderly_id="E001",
                full_name="Margaret Lee",
                date_of_birth=date(1948, 4, 12),
                phone_number="555-0101",
                address="10 Garden Road",
                emergency_contact_name="Daniel Lee",
                emergency_contact_phone="555-0199",
                medical_notes="Penicillin allergy",
                active=True,
                created_at=now,
                updated_at=now,
            ),
            "E002": ElderlyProfile(
                elderly_id="E002",
                full_name="Robert Tan",
                date_of_birth=date(1945, 9, 20),
                active=False,
                created_at=now,
                updated_at=now,
            ),
        }

    async def create_profile(self, profile: ElderlyProfileCreate) -> ElderlyProfile:
        if profile.elderly_id in self.profiles:
            raise ElderlyProfileAlreadyExists(profile.elderly_id)
        now = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)
        created = ElderlyProfile(
            **profile.model_dump(), active=True, created_at=now, updated_at=now
        )
        self.profiles[profile.elderly_id] = created
        return created

    async def list_profiles(
        self,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ElderlyProfile]:
        profiles = [
            profile
            for profile in self.profiles.values()
            if include_inactive or profile.active
        ]
        return profiles[offset : offset + limit]

    async def get_profile(self, elderly_id: str) -> ElderlyProfile:
        profile = self.profiles.get(elderly_id)
        if profile is None or not profile.active:
            raise ElderlyProfileNotFound(elderly_id)
        return profile

    async def update_profile(
        self, elderly_id: str, updates: ElderlyProfileUpdate
    ) -> ElderlyProfile:
        profile = await self.get_profile(elderly_id)
        updated = profile.model_copy(update=updates.model_dump(exclude_unset=True))
        self.profiles[elderly_id] = updated
        return updated

    async def delete_profile(self, elderly_id: str) -> ElderlyProfile:
        profile = await self.get_profile(elderly_id)
        deleted = profile.model_copy(update={"active": False})
        self.profiles[elderly_id] = deleted
        return deleted


@pytest.fixture
def fake_service() -> FakeElderlyService:
    return FakeElderlyService()


@pytest.fixture
def client(fake_service: FakeElderlyService) -> TestClient:
    app.dependency_overrides[get_elderly_service] = lambda: fake_service
    test_client = TestClient(app, raise_server_exceptions=True)
    yield test_client
    test_client.close()
    app.dependency_overrides.clear()
