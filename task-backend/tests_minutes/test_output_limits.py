import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from minutes_module.config import Settings
from minutes_module.llm_service import LLMService, LLMError
from minutes_module.schemas import Minutes, Claim, GenerateRequest
from minutes_module.monitoring import Monitor
from minutes_module.summarizer import Summarizer
from minutes_module.transcript_processor import normalize

def response(finish, content=""):
    return NS(usage=NS(prompt_tokens=100,completion_tokens=128),choices=[NS(finish_reason=finish,message=NS(content=content,refusal=None))])

def test_length_retry_increases_budget_without_accepting_partial_json():
    valid=Minutes(executive_summary=Claim(text="Release discussed"))
    client=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[response("length",'{"partial":'),response("stop",valid.model_dump_json())]))))
    settings=Settings(provider="openai",max_output_tokens=512,output_token_ceiling=2048)
    llm=LLMService(settings,client)
    record=Monitor().begin("m","mock","model")
    result=asyncio.run(llm.generate("Summarize",{},record))
    calls=client.chat.completions.create.call_args_list
    assert result==valid
    assert [c.kwargs["max_tokens"] for c in calls]==[512,1024]
    assert record["retries"]==1 and record["calls"]==2
    assert record["output_tokens"]==256
    assert calls[0].kwargs["messages"]==calls[1].kwargs["messages"]

def test_length_retries_are_bounded():
    client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response("length")))))
    llm=LLMService(Settings(provider="openai",max_output_tokens=512,output_token_ceiling=1024),client)
    with pytest.raises(LLMError) as exc:
        asyncio.run(llm.generate("Summarize",{},Monitor().begin("m","mock","model")))
    assert exc.value.diagnostics["finish_reason"]=="length"
    assert client.chat.completions.create.await_count==2

def test_refusal_is_not_retried_as_length():
    refused=response("length");refused.choices[0].message.refusal="refused"
    client=NS(chat=NS(completions=NS(create=AsyncMock(return_value=refused))))
    llm=LLMService(Settings(provider="openai"),client)
    with pytest.raises(LLMError):
        asyncio.run(llm.generate("Summarize",{},Monitor().begin("m","mock","model")))
    assert client.chat.completions.create.await_count==1

def test_minutes_split_after_truncation_and_keep_all_source_ids():
    class Truncating:
        async def generate(self,instruction,payload,record):
            record["calls"]+=1
            if isinstance(payload,list):
                raise LLMError("incomplete_or_refused_output",diagnostics={"finish_reason":"length"})
            if len(payload["segments"])>1:
                raise LLMError("incomplete_or_refused_output",diagnostics={"finish_reason":"length"})
            s=payload["segments"][0]
            c=Claim(text=s["text"],evidence=[{"segment_id":s["segment_id"],"timestamp":s["timestamp"],"excerpt":s["text"]}])
            return Minutes(executive_summary=c,decisions=[c])
    source=normalize(GenerateRequest(transcript_text="Decision one.\nDecision two.\nDecision three."),10000)
    record=Monitor().begin("m","mock","model")
    result,_=asyncio.run(Summarizer(Settings(enable_verification=False),Truncating()).generate(GenerateRequest().meeting,source,record))
    refs={e.segment_id for c in result.decisions for e in c.evidence}
    assert refs=={s.segment_id for s in source}
    assert len(result.decisions)==3

def test_enrichment_batches_preserve_task_ids_and_cross_task_catalog():
    from models import ContextualInference,EnrichedTask
    from task_llm import enrich_tasks
    class TaskLLM:
        provider,model="mock","mock"
        settings=Settings()
        payloads=[]
        async def generate(self,instruction,payload,record,**kwargs):
            self.payloads.append(payload)
            if len(payload["factual_tasks"])>1:
                raise LLMError("incomplete_or_refused_output",diagnostics={"finish_reason":"length"})
            t=payload["factual_tasks"][0]
            return ContextualInference(enriched_tasks=[EnrichedTask(task_id=t["task_id"],description=t["description"],owner=None,missing_owner=True,deadline=None,missing_deadline=True,priority="Medium",explicit_prerequisites=[])])
    llm=TaskLLM()
    tasks=[{"task_id":"T1","description":"One"},{"task_id":"T2","description":"Two"}]
    result=asyncio.run(enrich_tasks(llm,"A transcript",tasks,"Original inference rules."))
    assert [t.task_id for t in result.enriched_tasks]==["T1","T2"]
    assert all(len(payload["all_task_ids_and_descriptions"])==2 for payload in llm.payloads)


@pytest.mark.parametrize("source", [
    {"transcript_text":"Priya: Fix the release bug."},
    {"transcript":[{"text":"Priya: Fix the release bug.","timestamp":"00:10"}]},
])
def test_real_task_and_minutes_transport_contracts_with_length_recovery(monkeypatch,source):
    import main
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from minutes_module.router import Runtime,create_router
    from models import FactualExtraction,ExplicitTask,ContextualInference,EnrichedTask
    factual=FactualExtraction(meeting_title="Release",date="",tasks=[ExplicitTask(task_id="T1",description="Fix release bug")])
    inferred=ContextualInference(enriched_tasks=[EnrichedTask(task_id="T1",description="Fix release bug",owner=None,missing_owner=True,deadline=None,missing_deadline=True,priority="Medium",explicit_prerequisites=[])])
    minutes=Minutes(executive_summary=Claim(text="Release bug discussed"))
    sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[
        response("stop",factual.model_dump_json()),response("length"),response("stop",inferred.model_dump_json()),
        response("length"),response("stop",minutes.model_dump_json()),
    ]))))
    settings=Settings(provider="openai",max_output_tokens=512,enable_verification=False)
    llm=LLMService(settings,sdk)
    monkeypatch.setattr(main,"llm_client",sdk)
    monkeypatch.setattr(main,"llm_service",llm)
    processed=TestClient(main.app).post("/api/meetings/process",json={"meeting":{"title":"Release"},**source})
    assert processed.status_code==200,processed.text
    assert {"meeting_id","tasks","factual_tasks","graph_analysis"}<=processed.json().keys()
    meeting_id=processed.json()["meeting_id"]
    app=FastAPI();app.include_router(create_router(Runtime(settings,llm)))
    generated=TestClient(app).post("/api/v1/minutes/generate/"+meeting_id)
    assert generated.status_code==201,generated.text
    assert generated.json()["meeting_id"]==meeting_id
    assert generated.json()["status"]=="draft"
    assert sdk.chat.completions.create.await_count==5


def test_call_cache_and_concurrent_call_coalescing():
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event()
        valid=Minutes(executive_summary=Claim(text="Discussion"))
        async def sdk_call(**kwargs):
            entered.set();await release.wait()
            return response("stop",valid.model_dump_json())
        sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=sdk_call))))
        llm=LLMService(Settings(provider="openai"),sdk)
        first_record=Monitor().begin("m","mock","model")
        second_record=Monitor().begin("m","mock","model")
        first=asyncio.create_task(llm.generate("Summary",{},first_record))
        await entered.wait()
        second=asyncio.create_task(llm.generate("Summary",{},second_record))
        await asyncio.sleep(0);release.set()
        a,b=await asyncio.gather(first,second)
        third_record=Monitor().begin("m","mock","model")
        c=await llm.generate("Summary",{},third_record)
        assert a==b==c and sdk.chat.completions.create.await_count==1
        assert second_record["calls"]==third_record["calls"]==0
        assert third_record["cache_hits"]==1
        a.title="Changed by caller"
        assert c.title!="Changed by caller"
    asyncio.run(scenario())

@pytest.mark.parametrize("code,expected",[("insufficient_quota","provider_quota_exhausted"),("organization_spend_limit_exceeded","provider_quota_exhausted")])
def test_quota_is_not_retried(code,expected):
    import httpx
    from openai import RateLimitError
    rejected=httpx.Response(429,request=httpx.Request("POST","https://example.com"))
    sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=RateLimitError("private",response=rejected,body={"error":{"code":code}})))))
    llm=LLMService(Settings(provider="openai"),sdk)
    with pytest.raises(LLMError,match=expected):
        asyncio.run(llm.generate("Summary",{},Monitor().begin("m","mock","model")))
    assert sdk.chat.completions.create.await_count==1


def test_rate_error_categories():
    from minutes_module.llm_service import classify_provider_error
    assert classify_provider_error(429,{"message":"Tokens per minute exceeded"})=="provider_token_rate_limit"
    assert classify_provider_error(429,{"message":"Requests per minute exceeded"})=="provider_request_rate_limit"
    assert classify_provider_error(400,{"code":"context_length_exceeded"})=="provider_context_limit"
    assert classify_provider_error(404,{})=="provider_model_unavailable"
    assert classify_provider_error(401,{})=="provider_authentication"


def test_scheduler_rpm_tpm_and_concurrency_without_real_waits():
    from minutes_module.llm_service import RequestScheduler
    async def scenario():
        now=[0.0];waits=[]
        async def sleep(seconds): waits.append(seconds);now[0]+=seconds
        scheduler=RequestScheduler(Settings(requests_per_minute=1,tokens_per_minute=1000,max_concurrency=1),clock=lambda:now[0],sleep=sleep)
        record={}
        async with scheduler.reserve(600,record): pass
        async with scheduler.reserve(600,record): pass
        assert waits==[60.0]
        assert record["scheduler_wait_seconds"]==60.0
        with pytest.raises(LLMError,match="request_exceeds_token_rate_budget"):
            async with scheduler.reserve(1001,record): pass
    asyncio.run(scenario())


def test_runtime_replay_reuses_saved_revision_but_regeneration_is_explicit(fake_database):
    from minutes_module.router import Runtime
    from test_minutes import FakeLLM
    async def scenario():
        runtime=Runtime(Settings(enable_verification=False),FakeLLM())
        request=GenerateRequest(meeting={"id":"idempotent"},transcript_text="Release was discussed.")
        one=await runtime.generate(request,expected=0)
        two=await runtime.generate(request,expected=1)
        assert one.revision==two.revision==1
        assert len(await runtime.store.history("idempotent"))==1
        three=await runtime.generate(request,expected=1,force=True)
        assert three.revision==2
        assert len(await runtime.store.history("idempotent"))==2
    asyncio.run(scenario())


def test_runtime_coalesces_two_generation_requests(fake_database):
    from minutes_module.router import Runtime
    from test_minutes import FakeLLM
    class Slow(FakeLLM):
        invocations=0
        async def generate(self,*args,**kwargs):
            self.invocations+=1
            await asyncio.sleep(.02)
            return await super().generate(*args,**kwargs)
    async def scenario():
        llm=Slow();runtime=Runtime(Settings(enable_verification=False),llm)
        request=GenerateRequest(meeting={"id":"same-job"},transcript_text="Release discussed.")
        a,b=await asyncio.gather(runtime.generate(request,expected=0),runtime.generate(request.model_copy(deep=True),expected=0))
        assert a.revision==b.revision==1
        assert llm.invocations==1
        assert len(await runtime.store.history("same-job"))==1
    asyncio.run(scenario())


@pytest.mark.parametrize("rpm,tpm",[(1,2000),(10,1000)])
def test_scheduler_enforces_each_rate_dimension(rpm,tpm):
    from minutes_module.llm_service import RequestScheduler
    async def scenario():
        now=[0.0];waits=[]
        async def sleep(seconds): waits.append(seconds);now[0]+=seconds
        scheduler=RequestScheduler(Settings(requests_per_minute=rpm,tokens_per_minute=tpm),clock=lambda:now[0],sleep=sleep)
        async with scheduler.reserve(600,{}): pass
        async with scheduler.reserve(600,{}): pass
        assert waits==[60.0]
    asyncio.run(scenario())


def test_sdk_concurrency_is_bounded_across_distinct_requests():
    async def scenario():
        active=0;peak=0
        async def sdk_call(**kwargs):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01)
            active-=1
            return response("stop",Minutes(executive_summary=Claim(text="Summary")).model_dump_json())
        sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=sdk_call))))
        llm=LLMService(Settings(provider="openai",max_concurrency=1),sdk)
        await asyncio.gather(*(llm.generate("Summary",{"meeting":i},Monitor().begin(str(i),"mock","model")) for i in range(3)))
        assert peak==1 and sdk.chat.completions.create.await_count==3
    asyncio.run(scenario())


def test_cache_invalidates_when_generation_settings_change():
    async def scenario():
        sdk=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response("stop",Minutes(executive_summary=Claim(text="Summary")).model_dump_json())))))
        llm=LLMService(Settings(provider="openai"),sdk)
        await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        llm.settings.temperature=.2
        await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        assert sdk.chat.completions.create.await_count==2
    asyncio.run(scenario())



def test_cache_expiry_recomputes_without_real_provider_calls():
    async def scenario():
        sdk=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response("stop",Minutes(executive_summary=Claim(text="Summary")).model_dump_json())))))
        llm=LLMService(Settings(provider="openai",cache_ttl_seconds=.001),sdk)
        await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        await asyncio.sleep(.01)
        await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        assert sdk.chat.completions.create.await_count==2
    asyncio.run(scenario())


def test_failed_output_is_not_cached():
    async def scenario():
        valid=Minutes(executive_summary=Claim(text="Summary")).model_dump_json()
        sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[response("stop","invalid JSON"),response("stop",valid)]))))
        llm=LLMService(Settings(provider="openai"),sdk)
        with pytest.raises(LLMError,match="invalid_structured_output"):
            await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        result=await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        assert result.executive_summary.text=="Summary"
        assert sdk.chat.completions.create.await_count==2
    asyncio.run(scenario())


def test_explicit_regeneration_bypasses_call_cache():
    async def scenario():
        sdk=NS(chat=NS(completions=NS(create=AsyncMock(return_value=response("stop",Minutes(executive_summary=Claim(text="Summary")).model_dump_json())))))
        llm=LLMService(Settings(provider="openai"),sdk)
        await llm.generate("Summary",{},Monitor().begin("m","mock","model"))
        record=Monitor().begin("m","mock","model");record["force_refresh"]=True
        await llm.generate("Summary",{},record)
        assert sdk.chat.completions.create.await_count==2
    asyncio.run(scenario())



def test_oversized_semantic_review_is_explicitly_flagged_not_falsely_passed():
    from test_minutes import FakeLLM
    class BudgetedReview(FakeLLM):
        async def generate(self,instruction,payload,record,**kwargs):
            if kwargs.get("schema") is not None:
                raise LLMError("request_exceeds_token_rate_budget",422)
            return await super().generate(instruction,payload,record,**kwargs)
    settings=Settings(enable_verification=True)
    source=normalize(GenerateRequest(transcript_text="Release was discussed."),10000)
    record=Monitor().begin("m","mock","model")
    result,report=asyncio.run(Summarizer(settings,BudgetedReview()).generate(GenerateRequest().meeting,source,record))
    assert result.executive_summary.text=="Release was discussed."
    assert report["requires_human_review"]
    assert any(flag["reason"]=="semantic_review_exceeds_budget_requires_human_review" for flag in report["flags"])



def test_groq_json_validation_failure_retries_once_without_json_mode():
    import httpx
    from openai import BadRequestError
    rejection=httpx.Response(400,request=httpx.Request("POST","https://example.com"))
    valid=Minutes(executive_summary=Claim(text="Discussion")).model_dump_json()
    error=BadRequestError("Failed to generate JSON",response=rejection,body={"error":{"code":"json_validate_failed","message":"Failed to generate JSON. Please adjust your prompt."}})
    sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[error,response("stop",valid)]))))
    settings=Settings(provider="groq",tokens_per_minute=60000)
    llm=LLMService(settings,sdk)
    result=asyncio.run(llm.generate("Summarize",{},Monitor().begin("m","groq","model")))
    calls=sdk.chat.completions.create.call_args_list
    assert result.executive_summary.text=="Discussion"
    assert len(calls)==2
    assert calls[0].kwargs["response_format"]=={"type":"json_object"}
    assert "response_format" not in calls[1].kwargs
    assert calls[0].kwargs["reasoning_effort"]=="low"
    assert calls[0].kwargs["extra_body"]=={"reasoning_format":"hidden"}
    assert llm._json_mode_supported is True


def test_groq_json_fallback_still_rejects_invalid_output():
    import httpx
    from openai import BadRequestError
    rejection=httpx.Response(400,request=httpx.Request("POST","https://example.com"))
    error=BadRequestError("failed",response=rejection,body={"error":{"code":"json_validate_failed"}})
    sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[error,response("stop","not JSON")]))))
    llm=LLMService(Settings(provider="groq",tokens_per_minute=60000),sdk)
    with pytest.raises(LLMError,match="invalid_structured_output"):
        asyncio.run(llm.generate("Summarize",{},Monitor().begin("m","groq","model")))
    assert sdk.chat.completions.create.await_count==2


def test_unrelated_bad_request_is_not_retried():
    import httpx
    from openai import BadRequestError
    rejection=httpx.Response(400,request=httpx.Request("POST","https://example.com"))
    error=BadRequestError("invalid request",response=rejection,body={"error":{"code":"invalid_request_error","param":"temperature"}})
    sdk=NS(chat=NS(completions=NS(create=AsyncMock(side_effect=error))))
    llm=LLMService(Settings(provider="groq"),sdk)
    with pytest.raises(LLMError,match="provider_invalid_request"):
        asyncio.run(llm.generate("Summarize",{},Monitor().begin("m","groq","model")))
    assert sdk.chat.completions.create.await_count==1
