from datetime import UTC, datetime

from pymongo import ReturnDocument
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.models.elderly import ElderlyProfile, ElderlyProfileCreate, ElderlyProfileUpdate


class ElderlyProfileNotFound(Exception):
    def __init__(self, elderly_id: str) -> None:
        super().__init__(f"Elderly profile {elderly_id} not found")


class ElderlyProfileAlreadyExists(Exception):
    def __init__(self, elderly_id: str) -> None:
        super().__init__(f"Elderly profile {elderly_id} already exists")


class ElderlyProfileStorageUnavailable(Exception):
    """Raised when MongoDB cannot complete a profile operation."""


class ElderlyProfileService:
    def __init__(self, collection: AsyncCollection) -> None:
        self.collection = collection

    async def create_profile(self, profile: ElderlyProfileCreate) -> ElderlyProfile:
        timestamp = datetime.now(UTC)
        document = {
            **profile.model_dump(mode="json"),
            "active": True,
            "created_at": timestamp,
            "updated_at": timestamp,
        }

        try:
            await self.collection.insert_one(document)
        except DuplicateKeyError as error:
            raise ElderlyProfileAlreadyExists(profile.elderly_id) from error
        except PyMongoError as error:
            raise ElderlyProfileStorageUnavailable() from error

        return ElderlyProfile.model_validate(document)

    async def list_profiles(
        self,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ElderlyProfile]:
        query = {} if include_inactive else {"active": True}
        try:
            cursor = (
                self.collection.find(query)
                .sort("elderly_id", 1)
                .skip(offset)
                .limit(limit)
            )
            documents = await cursor.to_list(length=None)
        except PyMongoError as error:
            raise ElderlyProfileStorageUnavailable() from error
        return [ElderlyProfile.model_validate(document) for document in documents]

    async def list_authorized_profiles(
        self,
        relationship_collection: AsyncCollection,
        account_id: str,
        limit: int,
        offset: int,
    ) -> list[ElderlyProfile]:
        relationships = await relationship_collection.find(
            {"account_id": account_id, "status": "active", "permissions": "read_profile"},
            {"elderly_id": 1},
        ).to_list(length=1000)
        elderly_ids = [document["elderly_id"] for document in relationships]
        if not elderly_ids:
            return []
        documents = await (
            self.collection.find({"elderly_id": {"$in": elderly_ids}, "active": True})
            .sort("elderly_id", 1)
            .skip(offset)
            .limit(limit)
            .to_list(length=None)
        )
        return [ElderlyProfile.model_validate(document) for document in documents]

    async def get_profile(self, elderly_id: str) -> ElderlyProfile:
        try:
            document = await self.collection.find_one(
                {"elderly_id": elderly_id, "active": True}
            )
        except PyMongoError as error:
            raise ElderlyProfileStorageUnavailable() from error
        if document is None:
            raise ElderlyProfileNotFound(elderly_id)
        return ElderlyProfile.model_validate(document)

    async def update_profile(
        self,
        elderly_id: str,
        updates: ElderlyProfileUpdate,
    ) -> ElderlyProfile:
        changes = updates.model_dump(mode="json", exclude_unset=True)
        changes["updated_at"] = datetime.now(UTC)
        try:
            document = await self.collection.find_one_and_update(
                {"elderly_id": elderly_id, "active": True},
                {"$set": changes},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise ElderlyProfileStorageUnavailable() from error
        if document is None:
            raise ElderlyProfileNotFound(elderly_id)
        return ElderlyProfile.model_validate(document)

    async def delete_profile(self, elderly_id: str) -> ElderlyProfile:
        try:
            document = await self.collection.find_one_and_update(
                {"elderly_id": elderly_id, "active": True},
                {"$set": {"active": False, "updated_at": datetime.now(UTC)}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise ElderlyProfileStorageUnavailable() from error
        if document is None:
            raise ElderlyProfileNotFound(elderly_id)
        return ElderlyProfile.model_validate(document)

    async def restore_profile(self, elderly_id: str) -> ElderlyProfile:
        try:
            document = await self.collection.find_one_and_update(
                {"elderly_id": elderly_id, "active": False},
                {"$set": {"active": True, "updated_at": datetime.now(UTC)}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError as error:
            raise ElderlyProfileStorageUnavailable() from error
        if document is None:
            raise ElderlyProfileNotFound(elderly_id)
        return ElderlyProfile.model_validate(document)
