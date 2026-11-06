import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from database import database, save_meeting_doc
from minutes_module.router import Runtime, create_router
from minutes_module.config import Settings
from minutes_module.schemas import Claim, Evidence
from workspace_api import create_workspace_router, Answer, retrieve
from minutes_module.transcript_processor import normalize
from minutes_module.schemas import GenerateRequest

TEXT="We agreed to fix the 3 blocking bugs before release."

@pytest.fixture
def workspace():
    class FakeLLM:
        provider,model,configured="mock","mock",True
        async def generate(self,instruction,payload,record,**kwargs):
            segment=payload["segments"][0]
            return Answer(claims=[Claim(text=segment["text"],assessment="supported",evidence=[Evidence(segment_id=segment["segment_id"],timestamp=segment["timestamp"],excerpt=segment["text"])])])
    runtime=Runtime(Settings(),FakeLLM())
    app=FastAPI();app.include_router(create_workspace_router(runtime));app.include_router(create_router(runtime))
    meeting_id=asyncio.run(save_meeting_doc({"meeting_title":"Release","raw_transcript":TEXT,"tasks":[{"task_id":"T1","description":"Fix bugs","explicit_owner":"Priya"}],"enriched_tasks":[{"task_id":"T1","description":"Fix bugs","owner":"Priya","status":"pending","explicit_prerequisites":[],"inferred_prerequisites":[]}]}))
    return TestClient(app),meeting_id,runtime


def test_task_status_persists_and_factual_record_is_unchanged(workspace):
    client,meeting_id,_=workspace
    url=f"/api/meetings/{meeting_id}/tasks/T1/status"
    result=client.patch(url,json={"status":"completed","expected_revision":0})
    assert result.status_code==200,result.text
    from database import get_meeting_doc
    saved=asyncio.run(get_meeting_doc(meeting_id))
    assert saved["enriched_tasks"][0]["status"]=="completed"
    assert saved["tasks"][0]["explicit_owner"]=="Priya"
    assert saved["graph_analysis"]["visualization_graph"]["nodes"][0]["data"]["status"]=="completed"
    assert client.patch(url,json={"status":"blocked","expected_revision":0}).status_code==409
    assert client.patch(url,json={"status":"invalid","expected_revision":1}).status_code==422


def test_query_returns_verified_source(workspace):
    client,meeting_id,_=workspace
    response=client.post(f"/api/meetings/{meeting_id}/query",json={"question":"What bugs block release?"})
    assert response.status_code==200,response.text
    data=response.json()
    assert data["claims"][0]["evidence"][0]["excerpt"]==TEXT
    assert data["cross_meeting"] is False


def test_query_drops_fake_evidence(workspace):
    client,meeting_id,runtime=workspace
    async def hallucinate(*args,**kwargs):
        return Answer(claims=[Claim(text="Invented",evidence=[Evidence(segment_id="fake",excerpt="Fake")])])
    runtime.llm.generate=hallucinate
    response=client.post(f"/api/meetings/{meeting_id}/query",json={"question":"What decisions?"})
    assert response.json()["claims"]==[]
    assert response.json()["insufficient_evidence"] is True


def test_query_missing_transcript(workspace):
    client,_,_=workspace
    meeting_id=asyncio.run(save_meeting_doc({"meeting_title":"No source"}))
    assert client.post(f"/api/meetings/{meeting_id}/query",json={"question":"What happened?"}).status_code==422


def test_lexical_retrieval_is_bounded():
    source=normalize(GenerateRequest(transcript_text="\n".join(f"topic {i}" for i in range(100))),10000)
    selected=retrieve("topic 99",source,limit=5)
    assert len(selected)==5
    assert any(s.text=="topic 99" for s in selected)


def test_atlas_startup_failure_never_falls_back(monkeypatch):
    from database import Database
    instance=Database()
    monkeypatch.delenv("MONGODB_URL",raising=False)
    with pytest.raises(RuntimeError,match="MONGODB_URL is required"):
        asyncio.run(instance.connect())


def test_partial_pipeline_exposes_saved_id(monkeypatch):
    import main
    from models import FactualExtraction
    monkeypatch.setattr(main,"run_factual_extraction",AsyncMock(return_value=FactualExtraction(meeting_title="Partial",date="",tasks=[])))
    monkeypatch.setattr(main,"run_contextual_inference",AsyncMock(side_effect=RuntimeError("private-key")))
    client=TestClient(main.app)
    response=client.post("/api/meetings/process",json={"transcript_text":TEXT})
    assert response.status_code==502
    detail=response.json()["detail"]
    assert detail["meeting_id"]
    assert "private-key" not in response.text
    assert client.get("/api/meetings/"+detail["meeting_id"]).status_code==200


def test_monitoring_survives_runtime_restart(workspace):
    _,meeting_id,runtime=workspace
    from minutes_module.monitoring import Monitor
    record=runtime.monitor.begin(meeting_id,"mock","model")
    asyncio.run(runtime.monitor.finish(record,"succeeded"))
    stats=asyncio.run(Monitor().stats())
    assert stats["requests"]==1
    assert stats["succeeded"]==1
