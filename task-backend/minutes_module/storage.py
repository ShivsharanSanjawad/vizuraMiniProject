"""Mongo-only immutable revision journal with compare-and-swap updates."""
import hashlib
import json
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
from database import database
from .schemas import Revision

class Conflict(Exception):
    pass

class Store:
    def __init__(self, settings=None, collection=None):
        self._collection = collection
        self.last_mode = "mongodb"

    @property
    def collection(self):
        return self._collection if self._collection is not None else database.collection("meeting_minutes")

    @collection.setter
    def collection(self, value):
        self._collection = value

    def expand(self, doc):
        if not doc:
            return []
        rows = []
        for saved in doc.get("revisions", []):
            row = dict(saved)
            key = row.pop("source_key", None)
            if key:
                row["segments"] = doc.get("sources", {}).get(key, [])
            row["storage_mode"] = "mongodb"
            rows.append(row)
        return rows

    async def history(self, meeting_id: str):
        try:
            return self.expand(await self.collection.find_one({"_id":meeting_id}))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, detail={"code":"minutes_storage_read_failed"}) from None

    async def append(self, revision: Revision, expected=None):
        history = await self.history(revision.meeting_id)
        current = history[-1]["revision"] if history else 0
        if expected is not None and expected != current:
            raise Conflict("Revision changed")
        revision.revision, revision.storage_mode = current+1, "mongodb"
        row = revision.model_dump(mode="json")
        segments = row.pop("segments")
        key = hashlib.sha256(json.dumps(segments, sort_keys=True).encode()).hexdigest()
        row["source_key"] = key
        try:
            if current == 0:
                await self.collection.insert_one({"_id":revision.meeting_id, "version":1, "sources":{key:segments}, "revisions":[row], "latest":row})
            else:
                result = await self.collection.update_one({"_id":revision.meeting_id, "version":current},
                    {"$push":{"revisions":row}, "$set":{"version":current+1, "latest":row, "sources."+key:segments}})
                if not result.matched_count:
                    raise Conflict("Revision changed")
        except (Conflict, DuplicateKeyError):
            raise Conflict("Revision changed") from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, detail={"code":"minutes_storage_write_failed"}) from None
        return revision

    async def list(self, offset, limit):
        try:
            total = await self.collection.count_documents({})
            cursor = self.collection.find({}, {"sources":1, "revisions":{"$slice":-1}}).sort("_id", -1).skip(offset).limit(limit)
            items = [self.expand(doc)[-1] async for doc in cursor if doc.get("revisions")]
            return dict(items=items, total=total, offset=offset, limit=limit)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, detail={"code":"minutes_storage_read_failed"}) from None
