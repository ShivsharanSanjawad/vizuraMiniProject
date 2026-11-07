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
    response=client.post(f"/api/meetings/{meeting_id}/query",json={"question":"How does release relate to launch quality?"})
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



def seed_minutes(meeting_id,summary="Release planning summary",decision="Use the approved API design",issue="The deployment date is unresolved"):
    from minutes_module.schemas import Minutes,Revision,Meeting
    from minutes_module.storage import Store
    minutes=Minutes(executive_summary=Claim(text=summary),decisions=[Claim(text=decision)],unresolved_issues=[Claim(text=issue)])
    revision=Revision(meeting_id=meeting_id,revision=1,status="approved",created_at="2026-11-06T10:00:00+05:30",approved_at="2026-11-06T10:00:00+05:30",reviewer="Reviewer",
        minutes=minutes,meeting=Meeting(id=meeting_id,title="Release"),segments=[],verification={"requires_human_review":False,"flags":[]},generation={})
    return asyncio.run(Store().append(revision,expected=0))


@pytest.mark.parametrize("question,expected",[
    ("What was this meeting about?","Release planning summary"),
    ("What were the key decisions?","Use the approved API design"),
    ("What action items were assigned?","Fix bugs"),
    ("Who is responsible for Fix bugs?","Priya"),
    ("What are the deadlines?","not recorded"),
    ("What issues remain unresolved?","deployment date is unresolved"),
])
def test_basic_assistant_uses_saved_data_without_llm(workspace,question,expected):
    client,meeting_id,runtime=workspace
    seed_minutes(meeting_id)
    runtime.llm.generate=AsyncMock(side_effect=AssertionError("Basic queries must not call LLM"))
    response=client.post(f"/api/meetings/{meeting_id}/query",json={"question":question})
    assert response.status_code==200,response.text
    assert expected in " ".join(claim["text"] for claim in response.json()["claims"])
    assert response.json()["meeting_id"]==meeting_id
    assert response.json()["cross_meeting"] is False
    runtime.llm.generate.assert_not_awaited()


def test_assistant_never_reads_another_meetings_records(workspace):
    client,meeting_id,runtime=workspace
    seed_minutes(meeting_id)
    other_id=asyncio.run(save_meeting_doc({"meeting_title":"Private other meeting","tasks":[{"task_id":"T1","description":"SECRET_ORBIT task","explicit_owner":"Hidden owner"}]}))
    seed_minutes(other_id,summary="PRIVATE_OTHER_SUMMARY",decision="PRIVATE_OTHER_DECISION")
    runtime.llm.generate=AsyncMock(side_effect=AssertionError("No LLM needed"))
    for question in ("What decisions were made?","What action items were assigned?","Who is responsible for SECRET_ORBIT task?"):
        response=client.post(f"/api/meetings/{meeting_id}/query",json={"question":question})
        assert response.status_code==200
        assert "PRIVATE_OTHER" not in response.text and "Hidden owner" not in response.text and "SECRET_ORBIT" not in response.text
        assert response.json()["meeting_id"]==meeting_id
    assert response.json()["insufficient_evidence"] is True
    runtime.llm.generate.assert_not_awaited()


def test_assistant_uses_minutes_without_transcript_and_labels_inferred_owners(workspace):
    client,_,runtime=workspace
    meeting_id=asyncio.run(save_meeting_doc({"meeting_title":"Saved record","enriched_tasks":[{"task_id":"T1","description":"Build endpoint","owner":"Priya"}]}))
    seed_minutes(meeting_id)
    runtime.llm.generate=AsyncMock(side_effect=AssertionError("No LLM needed"))
    summary=client.post(f"/api/meetings/{meeting_id}/query",json={"question":"What was this meeting about?"})
    owner=client.post(f"/api/meetings/{meeting_id}/query",json={"question":"Who owns Build endpoint?"})
    assert summary.status_code==200
    assert owner.status_code==200
    assert "(inferred)" in owner.text
    assert "not recorded" not in " ".join(c["text"] for c in owner.json()["claims"])
    runtime.llm.generate.assert_not_awaited()


def test_assistant_missing_selected_meeting_returns_404(workspace):
    client,_,_=workspace
    assert client.post("/api/meetings/not-a-meeting/query",json={"question":"What decisions?"}).status_code==404
