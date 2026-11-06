import asyncio
import json
import logging
from time import monotonic
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Query, Response
from .schemas import GenerateRequest, EditRequest, ApprovalRequest, Revision, MinutesList
from .config import Settings
from .llm_service import LLMService, LLMError, generation_key
from .monitoring import Monitor, utcnow
from .storage import Store, Conflict
from .summarizer import Summarizer
from .task_adapter import TaskAdapter
from .transcript_processor import normalize
from .verifier import verify
from .export_service import export

class Runtime:
    def __init__(self, settings=None, llm=None, store=None, adapter=None):
        self.settings = settings or Settings.load()
        self.llm = llm or LLMService(self.settings)
        self.store = store or Store(self.settings)
        self.adapter = adapter or TaskAdapter()
        self.monitor = Monitor()
        self._jobs = {}
        logger = logging.getLogger("minutes")
        logger.setLevel(self.settings.log_level)
        if not logger.hasHandlers():
            logger.addHandler(logging.StreamHandler())

    async def latest(self, meeting_id, revision=None):
        history = await self.store.history(meeting_id)
        if revision is not None:
            history = [r for r in history if r["revision"]==revision]
        if not history:
            raise HTTPException(404, detail={"code":"minutes_not_found"})
        return Revision.model_validate(history[-1])

    async def save(self, rev, expected=None):
        try:
            return await self.store.append(rev, expected)
        except Conflict:
            raise HTTPException(409, detail={"code":"revision_conflict"}) from None
        except Exception:
            raise HTTPException(503, detail={"code":"storage_unavailable"}) from None

    async def generate(self, request, tasks=None, expected=None, source_segments=None, force=False):
        # The same request returns the saved current revision or joins its running job.
        # Explicit regeneration bypasses reuse, preserving the existing review workflow.
        meeting_id = request.meeting.id or str(uuid4())
        request.meeting.id = meeting_id
        try:
            segments = source_segments if source_segments is not None else normalize(request, self.settings.max_input_chars)
        except ValueError:
            return await self._generate(request, tasks, expected, source_segments, force=force)
        from .prompt_templates import SYSTEM, MAP, REDUCE, PROMPT_VERSION
        from .verifier import REVIEW_SYSTEM
        signature = {key:getattr(self.settings,key) for key in ("temperature","max_output_tokens","context_tokens","output_token_ceiling","chunk_tokens","enable_verification","length_retries")}
        key = generation_key([meeting_id, [s.model_dump() for s in segments], request.meeting.model_dump(), tasks or {},
            self.llm.provider,self.llm.model,signature,PROMPT_VERSION,SYSTEM,MAP,REDUCE,REVIEW_SYSTEM])
        history = await self.store.history(meeting_id)
        if not force and history and history[-1].get("generation",{}).get("fingerprint")==key:
            logging.getLogger("minutes").info(json.dumps({"event":"generation_reused", "meeting_id":meeting_id, "revision":history[-1]["revision"]}))
            return Revision.model_validate(history[-1])
        job_key = (asyncio.get_running_loop(), meeting_id, key, force, expected)
        if job_key in self._jobs:
            logging.getLogger("minutes").info(json.dumps({"event":"generation_joined", "meeting_id":meeting_id}))
            return (await asyncio.shield(self._jobs[job_key])).model_copy(deep=True)
        job = asyncio.create_task(self._generate(request, tasks, expected, segments, force=force, fingerprint=key))
        self._jobs[job_key] = job
        try:
            return await job
        finally:
            if self._jobs.get(job_key) is job:
                self._jobs.pop(job_key, None)

    async def _generate(self, request, tasks=None, expected=None, source_segments=None, force=False, fingerprint=None):
        meeting_id = request.meeting.id or str(uuid4())
        request.meeting.id = meeting_id
        record = self.monitor.begin(meeting_id, self.llm.provider, self.llm.model)
        record["force_refresh"] = force
        record["fingerprint"] = fingerprint
        try:
            segments = source_segments if source_segments is not None else normalize(request, self.settings.max_input_chars)
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                minutes, verification = await Summarizer(self.settings, self.llm).generate(request.meeting, segments, record)
            record["verification"] = {"flag_count":len(verification["flags"])}
            record["estimated_cost_usd"] = None
            if self.settings.pricing_file and record["usage_complete"]:
                pricing = json.loads(self.settings.pricing_file.read_text())
                rate = pricing.get(f"{self.llm.provider}/{self.llm.model}")
                if rate:
                    record["estimated_cost_usd"] = (record["input_tokens"]*rate["input_per_million"]+record["output_tokens"]*rate["output_per_million"])/1000000
            generation = {k:v for k,v in record.items() if k!="_clock"}
            generation.update(status="succeeded", completed_at=utcnow(),
                duration_seconds=round(monotonic()-record["_clock"], 3), duration_scope="LLM pipeline before storage")
            rev = Revision(meeting_id=meeting_id, revision=1, status="draft", created_at=utcnow(),
                minutes=minutes, meeting=request.meeting, segments=segments, verification=verification,
                generation=generation, task_records=tasks or {})
            saved = await self.save(rev, expected)
            await self.monitor.finish(record, "succeeded")
            return saved
        except ValueError:
            await self.monitor.finish(record, "failed", "invalid_input")
            raise HTTPException(422, detail={"code":"invalid_transcript_or_configuration"}) from None
        except LLMError as exc:
            await self.monitor.finish(record, "failed", exc.code)
            raise HTTPException(exc.status, detail={"code":exc.code, "request_id":record["request_id"], "provider":self.llm.provider, "model":self.llm.model, **exc.diagnostics}) from None
        except TimeoutError:
            await self.monitor.finish(record, "failed", "generation_timeout")
            raise HTTPException(504, detail={"code":"generation_timeout"}) from None
        except HTTPException:
            await self.monitor.finish(record, "failed", "storage_or_revision_error")
            raise
        except Exception:
            await self.monitor.finish(record, "failed", "internal_error")
            raise HTTPException(500, detail={"code":"minutes_internal_error"}) from None

def create_router(runtime):
    r = APIRouter(prefix="/api/v1/minutes", tags=["Meeting minutes"])

    @r.get("/health")
    async def health():
        from database import database
        connected = await database.healthy()
        from pathlib import Path
        env_file = Path(__file__).resolve().parents[1] / ".env"
        return {"env_file":str(env_file), "env_file_present":env_file.is_file(), "database_connected":connected, "status":"degraded" if not connected else "ok" if runtime.llm.configured else "unconfigured", "provider":runtime.llm.provider,
                "model":runtime.llm.model, "configured":runtime.llm.configured, "storage_mode":runtime.store.last_mode,
                "verification":"deterministic_always", "generation_timeout_seconds":runtime.settings.total_timeout_seconds}

    @r.get("/monitoring/stats")
    async def stats():
        return await runtime.monitor.stats()

    @r.post("/generate", response_model=Revision, status_code=201)
    async def generate(request: GenerateRequest):
        history = await runtime.store.history(request.meeting.id) if request.meeting.id else []
        return await runtime.generate(request, expected=history[-1]["revision"] if history else 0)

    @r.post("/generate/{meeting_id}", response_model=Revision, status_code=201)
    async def from_existing(meeting_id: str, request: GenerateRequest | None = None):
        doc = await runtime.adapter.get(meeting_id)
        if not doc:
            raise HTTPException(404, detail={"code":"source_meeting_not_found"})
        history = await runtime.store.history(meeting_id)
        return await runtime.generate(runtime.adapter.request(meeting_id, doc, request), runtime.adapter.tasks(doc), history[-1]["revision"] if history else 0)

    @r.get("", response_model=MinutesList)
    async def listing(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return await runtime.store.list(offset, limit)

    @r.get("/{meeting_id}/history", response_model=list[Revision])
    async def history(meeting_id: str):
        await runtime.latest(meeting_id)
        return await runtime.store.history(meeting_id)

    @r.get("/{meeting_id}", response_model=Revision)
    async def retrieve(meeting_id: str, revision: int | None = Query(None, ge=1)):
        return await runtime.latest(meeting_id, revision)

    @r.put("/{meeting_id}", response_model=Revision)
    async def edit(meeting_id: str, request: EditRequest):
        rev = await runtime.latest(meeting_id)
        if rev.status != "draft":
            raise HTTPException(409, detail={"code":"approved_revision_immutable"})
        rev.minutes, rev.reviewer = request.minutes, request.reviewer
        rev.created_at = utcnow()
        rev.verification = verify(rev.minutes, rev.segments)
        return await runtime.save(rev, request.expected_revision)

    @r.post("/{meeting_id}/approve", response_model=Revision)
    async def approve(meeting_id: str, request: ApprovalRequest):
        rev = await runtime.latest(meeting_id)
        if rev.status != "draft":
            raise HTTPException(409, detail={"code":"already_approved"})
        if rev.verification["requires_human_review"] and not request.acknowledge_review_flags:
            raise HTTPException(409, detail={"code":"review_flags_require_acknowledgement"})
        rev.status, rev.reviewer, rev.approved_at = "approved", request.reviewer, utcnow()
        rev.created_at = utcnow()
        return await runtime.save(rev, request.expected_revision)

    @r.post("/{meeting_id}/regenerate", response_model=Revision, status_code=201)
    async def regenerate(meeting_id: str):
        rev = await runtime.latest(meeting_id)
        request = GenerateRequest(meeting=rev.meeting, transcript=[{"text":s.text,"timestamp":s.timestamp,"speaker":s.speaker,"source":s.source} for s in rev.segments])
        return await runtime.generate(request, rev.task_records, rev.revision, source_segments=rev.segments, force=True)

    async def download(meeting_id, kind, draft):
        history = await runtime.store.history(meeting_id)
        selected = history if draft else [r for r in history if r["status"]=="approved"]
        if not selected:
            raise HTTPException(404, detail={"code":"no_exportable_revision", "hint":"Approve minutes or explicitly set draft=true"})
        rev = Revision.model_validate(selected[-1])
        content = await asyncio.to_thread(export, rev, kind)
        media = "application/pdf" if kind=="pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return Response(content, media_type=media, headers={"Content-Disposition":f'attachment; filename="minutes-r{rev.revision}-{rev.status}.{kind}"'})

    @r.get("/{meeting_id}/export/pdf")
    async def pdf(meeting_id: str, draft: bool = False):
        return await download(meeting_id, "pdf", draft)

    @r.get("/{meeting_id}/export/docx")
    async def docx(meeting_id: str, draft: bool = False):
        return await download(meeting_id, "docx", draft)

    return r
