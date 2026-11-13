"""One shared Atlas connection and sanitized persistence errors."""
import os
from datetime import datetime, timezone
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException

class Database:
    client = None
    db = None

    async def connect(self):
        url = (os.getenv("MONGODB_URL") or "").strip().strip("\"'")
        if not url:
            raise RuntimeError("MONGODB_URL is required. Configure Atlas in task-backend/.env.")
        self.client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=15000)
        try:
            await self.client.admin.command("ping")
            self.db = self.client[os.getenv("MONGODB_DATABASE", "meeting_db")]
        except Exception as exc:
            self.close()
            import logging; logging.getLogger("uvicorn.error").error("Atlas connect failed: %s: %.300s", type(exc).__name__, exc)
            raise RuntimeError("Cannot connect to MongoDB Atlas. Check URI, network access list and database user.") from None

    def close(self):
        if self.client is not None:
            self.client.close()
        self.client = self.db = None

    def collection(self, name):
        if self.db is None:
            raise HTTPException(503, detail={"code":"database_unavailable"})
        return self.db[name]

    async def healthy(self):
        if self.client is None:
            return False
        try:
            await self.client.admin.command("ping")
            return True
        except Exception:
            return False

database = Database()

def meeting_query(meeting_id: str) -> dict:
    # Legacy records may use either string IDs or BSON ObjectIds.
    ids = [meeting_id]
    if ObjectId.is_valid(meeting_id):
        ids.append(ObjectId(meeting_id))
    return {"_id":{"$in":ids}}

def public(doc):
    if doc:
        doc = dict(doc)
        doc["_id"] = str(doc["_id"])
    return doc

async def save_meeting_doc(doc: dict) -> str:
    try:
        doc.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        result = await database.collection("factual_tasks").insert_one(doc)
        return str(result.inserted_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail={"code":"database_write_failed"}) from None

async def update_meeting_doc(meeting_id: str, updates: dict) -> bool:
    try:
        result = await database.collection("factual_tasks").update_one(meeting_query(meeting_id), {"$set":updates})
        return bool(result.matched_count)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail={"code":"database_write_failed"}) from None

async def get_meeting_doc(meeting_id: str):
    try:
        return public(await database.collection("factual_tasks").find_one(meeting_query(meeting_id)))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail={"code":"database_read_failed"}) from None

async def list_meetings_docs(limit: int = 50, offset: int = 0):
    try:
        cursor = database.collection("factual_tasks").find().sort("created_at", -1).skip(offset).limit(limit)
        return [public(doc) async for doc in cursor]
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail={"code":"database_read_failed"}) from None


async def delete_meeting_doc(meeting_id: str) -> dict:
    """Delete associated persisted records atomically; never report a partial delete as success."""
    try:
        if database.client is None:
            raise HTTPException(503, detail={"code":"database_unavailable"})
        async with await database.client.start_session() as session:
            async with session.start_transaction():
                meetings = database.collection("factual_tasks")
                doc = await meetings.find_one(meeting_query(meeting_id), session=session)
                if not doc:
                    raise HTTPException(404, detail={"code":"meeting_not_found"})
                canonical_id = str(doc["_id"])
                minutes = await database.collection("meeting_minutes").delete_one({"_id":canonical_id}, session=session)
                monitoring = await database.collection("llm_monitoring").delete_many({"meeting_id":canonical_id}, session=session)
                result = await meetings.delete_one({"_id":doc["_id"]}, session=session)
                if result.deleted_count != 1:
                    raise HTTPException(409, detail={"code":"meeting_delete_conflict"})
        return {"status":"deleted", "meeting_id":canonical_id, "minutes_histories_deleted":minutes.deleted_count,
                "monitoring_records_deleted":monitoring.deleted_count}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, detail={"code":"meeting_delete_failed",
            "message":"Atlas did not confirm the deletion transaction. Refresh before retrying."}) from None
