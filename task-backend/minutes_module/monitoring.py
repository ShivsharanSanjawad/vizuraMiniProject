import json
import logging
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
from fastapi import HTTPException
from database import database
logger = logging.getLogger("minutes")

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class Monitor:
    def __init__(self, collection=None):
        self._collection = collection

    @property
    def collection(self):
        return self._collection if self._collection is not None else database.collection("llm_monitoring")

    def begin(self, meeting_id, provider, model):
        return dict(request_id=str(uuid4()), meeting_id=meeting_id, provider=provider, model=model,
                    started_at=utcnow(), calls=0, retries=0, input_tokens=0, output_tokens=0,
                    estimated_input_tokens=0, estimated_output_tokens=0, usage_complete=True,
                    chunk_count=0, status="processing", _clock=monotonic())

    async def finish(self, record, status, error=None):
        if "_clock" not in record:
            return
        record.update(status=status, error_type=error, completed_at=utcnow(), duration_seconds=round(monotonic()-record.pop("_clock"), 3))
        logger.info(json.dumps(record))
        try:
            await self.collection.replace_one({"_id":record["request_id"]}, dict(record), upsert=True)
        except Exception:
            raise HTTPException(503, detail={"code":"monitoring_persistence_failed", "meeting_id":record["meeting_id"]}) from None

    async def stats(self):
        try:
            cursor = self.collection.find({}, {"_id":0}).sort("started_at", -1).limit(1000)
            rows = [row async for row in cursor]
            return dict(scope="latest 1000 persisted generation requests", requests=len(rows),
                succeeded=sum(r["status"]=="succeeded" for r in rows), failed=sum(r["status"]=="failed" for r in rows),
                llm_calls=sum(r["calls"] for r in rows), retries=sum(r["retries"] for r in rows), recent=rows[:50],
                average_duration_seconds=sum(r["duration_seconds"] for r in rows)/len(rows) if rows else None,
                input_tokens=sum(r["input_tokens"] for r in rows), output_tokens=sum(r["output_tokens"] for r in rows),
                usage_complete=all(r["usage_complete"] for r in rows),
                estimated_cost_usd=sum(r["estimated_cost_usd"] for r in rows) if rows and all(r.get("estimated_cost_usd") is not None for r in rows) else None)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, detail={"code":"monitoring_storage_unavailable"}) from None
