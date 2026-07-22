async def bounded_received_at_documents(
    collection: object,
    elderly_id: str,
    limit: int,
    offset: int = 0,
    normal_index: str = "activity_history_latest",
    legacy_created_index: str = "activity_history_legacy",
    legacy_recorded_index: str = "activity_history_legacy_recorded",
) -> list[dict[str, object]]:
    candidate_limit = limit + offset
    received_cursor = (
        collection.find(
            {"elderly_id": elderly_id, "received_at": {"$exists": True}}
        )
        .sort([("received_at", -1), ("event_id", -1)])
        .hint(normal_index)
        .limit(candidate_limit)
    )
    legacy_created_cursor = (
        collection.find(
            {
                "elderly_id": elderly_id,
                "received_at": {"$exists": False},
                "created_at": {"$exists": True},
            }
        )
        .sort([("created_at", -1), ("event_id", -1)])
        .hint(legacy_created_index)
        .limit(candidate_limit)
    )
    legacy_recorded_cursor = (
        collection.find(
            {
                "elderly_id": elderly_id,
                "received_at": {"$exists": False},
                "created_at": {"$exists": False},
            }
        )
        .sort([("recorded_at", -1), ("event_id", -1)])
        .hint(legacy_recorded_index)
        .limit(candidate_limit)
    )
    received = await received_cursor.to_list(length=candidate_limit)
    legacy_created = await legacy_created_cursor.to_list(length=candidate_limit)
    legacy_recorded = await legacy_recorded_cursor.to_list(length=candidate_limit)
    candidates = [
        *received,
        *(
            {**document, "received_at": document["created_at"]}
            for document in legacy_created
        ),
        *(
            {**document, "received_at": document["recorded_at"]}
            for document in legacy_recorded
        ),
    ]
    candidates.sort(
        key=lambda document: (document["received_at"], str(document["event_id"])),
        reverse=True,
    )
    return candidates[offset : offset + limit]
