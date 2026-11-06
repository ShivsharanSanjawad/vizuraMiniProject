import asyncio
import json
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import APITimeoutError, RateLimitError, AuthenticationError, BadRequestError
from minutes_module.config import Settings
from minutes_module.schemas import GenerateRequest, Minutes, Claim, Evidence, Topic
from minutes_module.router import Runtime, create_router
from minutes_module.storage import Store
from minutes_module.llm_service import LLMService, LLMError
from minutes_module.transcript_processor import normalize, chunks, estimate
from minutes_module.verifier import verify
from minutes_module.monitoring import Monitor
from minutes_module.summarizer import Summarizer

TEXT = "We agreed to release the dashboard on Friday."

def output(segment=None):
    s = segment or dict(segment_id="seg-000001", text=TEXT, timestamp=None)
    claim = Claim(text=s["text"], evidence=[Evidence(segment_id=s["segment_id"], timestamp=s.get("timestamp"), excerpt=s["text"])], assessment="supported")
    return Minutes(title="Sprint planning", executive_summary=claim, topics=[Topic(topic="Release", summary=claim)], decisions=[claim], highlights=[claim])

class FakeLLM:
    provider, model, configured = "mock", "mock-model", True
    async def generate(self, instruction, payload, record, **kwargs):
        record["calls"] += 1
        if kwargs.get("schema") is not None:
            return kwargs["schema"](findings=[])
        if isinstance(payload, list):
            return Minutes.model_validate(payload[0])
        return output(payload["segments"][0])

class Adapter:
    async def get(self, meeting_id):
        if meeting_id == "missing":
            return None
        return {"raw_transcript":TEXT, "meeting_title":"Existing meeting", "tasks":[{"task_id":"T1", "explicit_owner":"Priya"}], "enriched_tasks":[{"task_id":"T1", "owner":"Inferred"}]}
    from minutes_module.task_adapter import TaskAdapter
    request = TaskAdapter.request
    tasks = TaskAdapter.tasks

@pytest.fixture
def api(tmp_path):
    settings = Settings(storage_mode="mongodb")
    runtime = Runtime(settings, FakeLLM(), adapter=Adapter())
    app = FastAPI()
    app.include_router(create_router(runtime))
    with TestClient(app) as client:
        yield client, runtime


def generate(client, **extra):
    payload = {"meeting":{"id":"meeting-001", "title":"Sprint planning"}, "transcript_text":TEXT}
    payload.update(extra)
    response = client.post("/api/v1/minutes/generate", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_full_review_lifecycle_and_exports(api):
    c, runtime = api
    first = generate(c)
    assert first["meeting_id"] == "meeting-001"
    assert first["minutes"]["topics"][0]["topic"] == "Release"
    assert first["meeting"]["participants"] == []
    assert c.get("/api/v1/minutes/meeting-001/export/pdf").status_code == 404
    edited = c.put("/api/v1/minutes/meeting-001", json={"expected_revision":1,"minutes":first["minutes"],"reviewer":"Priya"})
    assert edited.status_code == 200
    assert edited.json()["revision"] == 2
    assert c.put("/api/v1/minutes/meeting-001", json={"expected_revision":1,"minutes":first["minutes"],"reviewer":"Priya"}).status_code == 409
    approved = c.post("/api/v1/minutes/meeting-001/approve", json={"expected_revision":2,"reviewer":"Priya","acknowledge_review_flags":True})
    assert approved.status_code == 200
    assert approved.json()["revision"] == 3
    assert approved.json()["approved_at"]
    assert c.put("/api/v1/minutes/meeting-001", json={"expected_revision":3,"minutes":first["minutes"],"reviewer":"Priya"}).status_code == 409
    for kind in ("pdf", "docx"):
        response = c.get(f"/api/v1/minutes/meeting-001/export/{kind}")
        assert response.status_code == 200
        if kind == "pdf":
            assert response.content.startswith(b"%PDF")
        else:
            from docx import Document
            document = Document(BytesIO(response.content))
            assert TEXT in " ".join(p.text for p in document.paragraphs)
    regenerated = c.post("/api/v1/minutes/meeting-001/regenerate")
    assert regenerated.status_code == 201
    assert regenerated.json()["status"] == "draft"
    assert c.get("/api/v1/minutes/meeting-001/export/pdf").headers["content-disposition"].endswith('r3-approved.pdf"')
    assert len(c.get("/api/v1/minutes/meeting-001/history").json()) == 4
    assert c.get("/api/v1/minutes/meeting-001?revision=1").json()["status"] == "draft"
    assert c.get("/api/v1/minutes?limit=1").json()["total"] == 1
    assert c.get("/api/v1/minutes/monitoring/stats").json()["succeeded"] == 2
    assert c.get("/api/v1/minutes/health").json()["configured"] is True

@pytest.mark.parametrize("timestamp", [None,"00:01:20","00:10","garbled","99:99"])
def test_timestamp_formats(api, timestamp):
    c, _ = api
    rev = generate(c, transcript_text=None, transcript=[{"text":TEXT,"timestamp":timestamp,"source":"browser-speech-recognition","speaker":"Priya"}])
    segment = rev["segments"][0]
    assert segment["timestamp"] == (timestamp if timestamp in ("00:01:20","00:10") else None)
    assert segment["speaker"] == "Priya"
    assert rev["minutes"]["decisions"][0]["evidence"][0]["timestamp"] == segment["timestamp"]

@pytest.mark.parametrize("payload", [{},{"transcript_text":"  "},{"transcript":[]},{"transcript":[{"text":""}]}])
def test_empty_input(api, payload):
    c, _ = api
    assert c.post("/api/v1/minutes/generate", json=payload).status_code == 422


def test_plain_string_and_stable_segments(api):
    c, _ = api
    rev = generate(c, transcript_text=None, transcript=TEXT)
    assert rev["segments"][0]["segment_id"] == "seg-000001"
    assert rev["segments"][0]["timestamp"] is None


def test_hallucinations_and_approval_gate(api):
    c, _ = api
    rev = generate(c)
    rev["minutes"]["decisions"][0] = {"text":"Spend $100000", "assessment":"supported", "evidence":[{"segment_id":"fake","timestamp":"00:55","excerpt":"Approved"}]}
    edited = c.put("/api/v1/minutes/meeting-001", json={"expected_revision":1,"minutes":rev["minutes"],"reviewer":"Priya"}).json()
    assert edited["minutes"]["decisions"][0]["assessment"] == "potentially_unsupported"
    assert c.post("/api/v1/minutes/meeting-001/approve", json={"expected_revision":2,"reviewer":"Priya"}).status_code == 409
    assert c.post("/api/v1/minutes/meeting-001/approve", json={"expected_revision":2,"reviewer":"Priya","acknowledge_review_flags":True}).status_code == 200


def test_valid_excerpt_does_not_prove_claim():
    segments = normalize(GenerateRequest(transcript_text=TEXT), 10000)
    m = output()
    m.decisions = [Claim(text="Spend $100000", evidence=m.executive_summary.evidence, assessment="supported")]
    report = verify(m, segments)
    assert m.decisions[0].assessment == "ambiguous"
    assert report["requires_human_review"]


def test_existing_meeting_adapter(api):
    c, _ = api
    response = c.post("/api/v1/minutes/generate/507f1f77bcf86cd799439011")
    assert response.status_code == 201
    rev = response.json()
    assert rev["meeting_id"] == "507f1f77bcf86cd799439011"
    assert rev["task_records"]["factual"][0]["explicit_owner"] == "Priya"
    assert rev["task_records"]["inferred"][0]["owner"] == "Inferred"
    assert rev["task_records"]["approved"] == []
    assert c.post("/api/v1/minutes/generate/missing").status_code == 404


def test_chunking_and_hierarchical_summary(tmp_path):
    settings = Settings(context_tokens=9000, max_output_tokens=1024)
    segments = normalize(GenerateRequest(transcript_text=(TEXT+"\n")*1500), 1000000)
    batches = chunks(segments, 2000)
    assert len(batches) > 1
    assert {s.segment_id for b in batches for s in b} == {s.segment_id for s in segments}
    assert all(sum(estimate(s.model_dump_json()) for s in b)<=2000 for b in batches)
    llm = FakeLLM()
    record = Monitor().begin("long", "mock", "mock")
    m, report = asyncio.run(Summarizer(settings,llm).generate(GenerateRequest().meeting,segments,record))
    assert record["chunk_count"] > 1
    assert record["calls"] > record["chunk_count"]
    assert m.executive_summary.evidence


def sdk_response(text=None, usage=True):
    return NS(usage=NS(prompt_tokens=100,completion_tokens=40) if usage else None,
              choices=[NS(finish_reason="stop",message=NS(content=text or output().model_dump_json(),refusal=None))])


def service(settings=None, effects=None):
    client = NS(chat=NS(completions=NS(create=AsyncMock(side_effect=effects))))
    llm = LLMService(settings or Settings(provider="openai",max_retries=1,tokens_per_minute=60000), client=client)
    return llm, client, Monitor().begin("meeting", llm.provider,llm.model)


def test_retry_timeout(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("minutes_module.llm_service.asyncio.sleep",sleep)
    llm,client,record=service(effects=[APITimeoutError(request=httpx.Request("POST","https://example.com")),sdk_response()])
    assert asyncio.run(llm.generate("summarize",{},record)).decisions
    assert record["retries"] == 1 and record["calls"] == 2
    assert record["usage_complete"] is False
    sleep.assert_awaited_once()
    assert 1 <= sleep.call_args.args[0] <= 1.25


def test_retry_after(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("minutes_module.llm_service.asyncio.sleep",sleep)
    response = httpx.Response(429,headers={"retry-after":"7"},request=httpx.Request("POST","https://example.com"))
    llm,_,record=service(effects=[RateLimitError("secret",response=response,body={}),sdk_response()])
    asyncio.run(llm.generate("summarize",{},record))
    sleep.assert_awaited_once_with(7)


def test_auth_not_retried_or_leaked():
    response = httpx.Response(401,request=httpx.Request("POST","https://example.com"))
    llm,client,record=service(effects=[AuthenticationError("secret-key",response=response,body={})])
    with pytest.raises(LLMError,match="provider_authentication") as exc:
        asyncio.run(llm.generate("summarize",{},record))
    assert "secret" not in str(exc.value)
    assert client.chat.completions.create.await_count == 1


def test_json_mode_fallback():
    response=httpx.Response(400,request=httpx.Request("POST","https://example.com"))
    error=BadRequestError("unsupported",response=response,body={"error":{"param":"response_format"}})
    llm,client,record=service(effects=[error,sdk_response()])
    asyncio.run(llm.generate("summarize",{},record))
    assert "response_format" not in client.chat.completions.create.call_args.kwargs

@pytest.mark.parametrize("text", ["not JSON",'{"executive_summary":17}','{}'])
def test_invalid_structured_output(text):
    llm,_,record=service(effects=[sdk_response(text)])
    with pytest.raises(LLMError,match="invalid_structured_output"):
        asyncio.run(llm.generate("summarize",{},record))


def test_missing_key(monkeypatch):
    for key in ("GEMINI_API_KEY","GROQ_API_KEY","OPENAI_API_KEY"):
        monkeypatch.delenv(key,raising=False)
    llm=LLMService(Settings(provider="openai"))
    with pytest.raises(LLMError,match="provider_not_configured"):
        asyncio.run(llm.generate("summarize",{},Monitor().begin("x","openai","x")))


def test_usage_absent():
    llm,_,record=service(effects=[sdk_response(usage=False)])
    asyncio.run(llm.generate("summarize",{},record))
    assert record["usage_complete"] is False
    assert record["input_tokens"] == 0 and record["estimated_input_tokens"]>0


def test_mongo_write_failure_is_not_success(api):
    c,runtime=api
    runtime.store.collection=NS(find_one=AsyncMock(return_value=None),insert_one=AsyncMock(side_effect=RuntimeError("private-uri")))
    response=c.post("/api/v1/minutes/generate",json={"meeting":{"id":"x"},"transcript_text":TEXT})
    assert response.status_code==503
    assert "private-uri" not in response.text


def test_storage_survives_runtime_restart(api):
    c,runtime=api
    generate(c)
    another=Store(runtime.settings)
    assert asyncio.run(another.history("meeting-001"))[0]["revision"]==1


def test_safe_filename_and_pagination(api):
    c,runtime=api
    assert asyncio.run(runtime.store.history("../../escape")) == []
    generate(c)
    assert c.get("/api/v1/minutes?limit=101").status_code==422
    assert c.get("/api/v1/minutes/absent").status_code==404
    assert c.get("/api/v1/minutes/meeting-001/export/pdf?draft=true").content.startswith(b"%PDF")


def test_combined_preserves_original_endpoints(monkeypatch,tmp_path):
    import main
    old_routes={r.path:r.endpoint for r in main.app.routes if hasattr(r,"endpoint")}
    monkeypatch.setenv("MINUTES_STORAGE_DIR",str(tmp_path))
    import combined_app
    assert combined_app.app is main.app
    new_routes={r.path:r.endpoint for r in combined_app.app.routes if hasattr(r,"endpoint")}
    for path,endpoint in old_routes.items():
        assert new_routes[path] is endpoint
    client=TestClient(combined_app.app)
    assert client.get("/health").status_code==200
    spec=client.get("/openapi.json").json()
    assert "/api/meetings/process" in spec["paths"]
    assert "/api/v1/minutes/generate" in spec["paths"]


def test_total_generation_deadline(api):
    c,runtime=api
    runtime.settings.total_timeout_seconds=.001
    async def slow(*args):
        await asyncio.sleep(.1)
    runtime.llm.generate=slow
    response=c.post("/api/v1/minutes/generate",json={"transcript_text":TEXT})
    assert response.status_code==504
    assert asyncio.run(runtime.monitor.stats())["failed"]==1


def test_monitoring_never_logs_transcript(api,caplog):
    c,_=api
    with caplog.at_level("INFO",logger="minutes"):
        rev=generate(c)
    assert TEXT not in caplog.text
    assert "request_id" in caplog.text
    assert rev["generation"]["status"]=="succeeded"
    assert rev["generation"]["completed_at"]


def test_mongo_reads_fail_explicitly(api):
    from fastapi import HTTPException
    _,runtime=api
    runtime.store.collection=NS(find_one=AsyncMock(side_effect=RuntimeError("private-uri")))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(runtime.store.history("x"))
    assert exc.value.status_code==503
    assert "private-uri" not in str(exc.value.detail)


def test_source_segments_are_stored_once_per_history(api):
    c,runtime=api
    generate(c)
    c.post("/api/v1/minutes/meeting-001/regenerate")
    doc=asyncio.run(runtime.store.collection.find_one({"_id":"meeting-001"}))
    assert len(doc["sources"])==1
    assert len(doc["revisions"])==2
    assert "segments" not in doc["revisions"][0]


def test_factual_review_can_be_disabled(api):
    c,runtime=api
    runtime.settings.enable_verification=False
    generate(c)
    assert asyncio.run(runtime.monitor.stats())["llm_calls"]==1


def test_reduction_retains_all_chunk_decisions(tmp_path):
    class DroppingReducer(FakeLLM):
        async def generate(self,instruction,payload,record):
            result=await super().generate(instruction,payload,record)
            if isinstance(payload,list):
                result.decisions=[]
                result.unresolved_issues=[]
            return result
    settings=Settings(context_tokens=9000,max_output_tokens=1024,enable_verification=False)
    request=GenerateRequest(transcript_text="\n".join(f"Decision {i}: use build {i}." for i in range(500)))
    source=normalize(request,100000)
    record=Monitor().begin("x","mock","mock")
    result,_=asyncio.run(Summarizer(settings,DroppingReducer()).generate(request.meeting,source,record))
    assert len(result.decisions)==record["chunk_count"]
    assert len(result.decisions)>1


def test_original_pipeline_and_lifespan_operational(monkeypatch,tmp_path):
    import main
    import combined_app
    from models import FactualExtraction, ContextualInference
    factual=FactualExtraction(meeting_title="Original meeting",date="2026-11-04",tasks=[])
    enriched=ContextualInference(enriched_tasks=[])
    extract=AsyncMock(return_value=factual)
    infer=AsyncMock(return_value=enriched)
    monkeypatch.setattr(main,"run_factual_extraction",extract)
    monkeypatch.setattr(main,"run_contextual_inference",infer)
    with TestClient(combined_app.app) as client:
        assert awaitable_health(main)
        response=client.post("/api/meetings/process",json={"transcript_text":TEXT})
        assert response.status_code==200,response.text
        meeting_id=response.json()["meeting_id"]
        assert client.get(f"/api/meetings/{meeting_id}").json()["raw_transcript"]==TEXT
        assert client.get("/api/meetings").status_code==200
        assert client.post("/api/v1/extract-factual",json={"transcript_text":TEXT}).status_code==200
        assert client.post(f"/api/v1/infer-context/{meeting_id}").status_code==200
        assert client.get("/api/v1/minutes/health").status_code==200
    assert extract.await_count==2
    assert infer.await_count==2


def test_fabricated_timestamps_are_removed():
    source=normalize(GenerateRequest(transcript_text=TEXT),10000)
    m=output()
    m.executive_summary.evidence[0].timestamp="12:34"
    verify(m,source)
    assert m.executive_summary.evidence[0].timestamp is None
    assert m.executive_summary.assessment=="potentially_unsupported"


def test_unknown_segment_evidence_is_removed():
    source=normalize(GenerateRequest(transcript_text=TEXT),10000)
    m=output()
    m.executive_summary.evidence[0].segment_id="fake"
    verify(m,source)
    assert not m.executive_summary.evidence


def test_llm_context_preflight():
    llm,client,record=service(Settings(provider="openai",context_tokens=4000,max_output_tokens=1024),[sdk_response()])
    with pytest.raises(LLMError,match="request_exceeds_context_budget"):
        asyncio.run(llm.generate("summarize",{"text":"x"*20000},record))
    assert client.chat.completions.create.await_count==0


def awaitable_health(main):
    return asyncio.run(main.database.healthy())
