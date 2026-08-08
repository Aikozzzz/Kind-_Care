from datetime import date

import pytest
from pymongo.errors import ConnectionFailure

import app.services.elderly as elderly_service
from app.models.elderly import ElderlyProfileCreate, ElderlyProfileUpdate


class FailingCursor:
    def sort(self, *args):
        return self

    def skip(self, *args):
        return self

    def limit(self, *args):
        return self

    async def to_list(self, *args, **kwargs):
        raise ConnectionFailure("MongoDB unavailable")


class FailingCollection:
    async def insert_one(self, document):
        raise ConnectionFailure("MongoDB unavailable")

    def find(self, query):
        return FailingCursor()

    async def find_one(self, query):
        raise ConnectionFailure("MongoDB unavailable")

    async def find_one_and_update(self, *args, **kwargs):
        raise ConnectionFailure("MongoDB unavailable")


@pytest.mark.parametrize(
    "operation",
    ["create", "list", "get", "update", "delete", "restore"],
)
async def test_profile_service_translates_pymongo_failures(operation: str) -> None:
    service = elderly_service.ElderlyProfileService(FailingCollection())
    profile = ElderlyProfileCreate(
        elderly_id="E900",
        full_name="Unavailable Test",
        date_of_birth=date(1950, 1, 1),
    )

    with pytest.raises(elderly_service.ElderlyProfileStorageUnavailable):
        if operation == "create":
            await service.create_profile(profile)
        elif operation == "list":
            await service.list_profiles()
        elif operation == "get":
            await service.get_profile("E900")
        elif operation == "update":
            await service.update_profile(
                "E900",
                ElderlyProfileUpdate(phone_number="555-0100"),
            )
        else:
            if operation == "delete":
                await service.delete_profile("E900")
            else:
                await service.restore_profile("E900")
