import os
from datetime import datetime, timezone
from typing import List, Optional, Union, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from models import FactualExtraction, ContextualInference
from graph_engine import analyze_task_graph

# ---------------------------------------------------------------------------
# Configuration & Environment
# ---------------------------------------------------------------------------
# Shared provider configuration for task extraction, minutes and meeting Q&A.
from minutes_module.config import Settings
from minutes_module.llm_service import LLMService
settings = Settings.load()
llm_service = LLMService(settings)
llm_client = llm_service.client
llm_model_name = llm_service.model
llm_provider_name = llm_service.provider

# Shared MongoDB persistence; no implicit local fallback.
from database import database, save_meeting_doc, update_meeting_doc, get_meeting_doc, list_meetings_docs

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    try:
        yield
    finally:
        database.close()
        await llm_service.close()

# ---------------------------------------------------------------------------
# FastAPI Application & CORS
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Meeting Minutes & Task Intelligence API",
    description="Extracts factual action items, infers context and dependencies, and performs graph-based schedule analysis.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Chrome Extensions and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[v.strip() for v in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if v.strip()],
    allow_origin_regex=r"chrome-extension://[a-p]{32}",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ---------------------------------------------------------------------------
# Pydantic Schemas for Extension Contract & Endpoints
# ---------------------------------------------------------------------------
class TranscriptEntry(BaseModel):
    timestamp: Optional[str] = None
    text: str
    source: Optional[str] = None
    speaker: Optional[str] = None
    at: Optional[str] = None

class MeetingMetadata(BaseModel):
    id: Optional[str] = None
    title: Optional[str] = "Untitled meeting"
    participants: Optional[List[str]] = Field(default_factory=list)
    startedAt: Optional[str] = None
    endedAt: Optional[str] = None

class ProcessingOptions(BaseModel):
    requestedOutputs: Optional[List[str]] = Field(default_factory=list)
    source: Optional[str] = None
    version: Optional[str] = None
    final: Optional[bool] = None
    sequence: Optional[int] = None

class MeetingProcessRequest(BaseModel):
    meeting: Optional[MeetingMetadata] = None
    transcript: Optional[Union[List[TranscriptEntry], str]] = None
    transcript_text: Optional[str] = None
    processing: Optional[ProcessingOptions] = None

class TranscriptInput(BaseModel):
    transcript_text: str

# ---------------------------------------------------------------------------
# Helper: Format Transcript from Payload
# ---------------------------------------------------------------------------
def extract_transcript_text(payload: MeetingProcessRequest) -> str:
    # 1. Plain text directly provided
    if payload.transcript_text and payload.transcript_text.strip():
        return payload.transcript_text.strip()
    
    # 2. Transcript is a string
    if isinstance(payload.transcript, str) and payload.transcript.strip():
        return payload.transcript.strip()
    
    # 3. Transcript is a list of entries
    if isinstance(payload.transcript, list) and payload.transcript:
        lines = []
        for entry in payload.transcript:
            speaker = getattr(entry, "speaker", None)
            timestamp = getattr(entry, "timestamp", None)
            text = entry.text.strip() if hasattr(entry, "text") else str(entry).strip()
            if not text:
                continue
            if speaker:
                text = f"{speaker}: {text}"
            if timestamp:
                lines.append(f"[{timestamp}] {text}")
            else:
                lines.append(text)
        return "\n".join(lines)
    
    return ""

# ---------------------------------------------------------------------------
# Helper: Run LLM Passes
# ---------------------------------------------------------------------------
async def run_factual_extraction(transcript_text: str) -> FactualExtraction:
    if not llm_client:
        raise HTTPException(
            status_code=503,
            detail="No LLM provider configured. Please set GEMINI_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY in .env."
        )
    
    from task_llm import factual_extraction
    return await factual_extraction(llm_service, transcript_text)


async def run_contextual_inference(transcript_text: str, factual_tasks: List[Dict[str, Any]]) -> ContextualInference:
    if not llm_client:
        raise HTTPException(
            status_code=503,
            detail="No LLM provider configured. Please set GEMINI_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY in .env."
        )

    system_prompt = (
        "You are an AI project manager. You are given a meeting transcript and a strictly factual list of extracted tasks. "
        "Your job is to enrich these tasks by inferring missing context. Assign priorities, flag missing owners/deadlines, "
        "deduce current statuses, and suggest inferred dependencies (tasks that logically must happen first, even if not explicitly stated). "
        "CRITICAL PREREQUISITE RULE: If a task is blocked by an external factor, event, or third-party action that is not tracked as a separate "
        "task ID (e.g., waiting on AWS support, client approvals, or external deliveries), you MUST add a clear descriptive string of this blocker "
        "into the explicit_prerequisites or inferred_prerequisites list (e.g., 'Waiting on AWS support to approve quota'). "
        "Do not leave prerequisites empty if an external blocker exists. Make sure all tasks keep their exact task_id."
    )
    from task_llm import enrich_tasks
    return await enrich_tasks(llm_service, transcript_text, factual_tasks, system_prompt)

# ---------------------------------------------------------------------------
# Core API Routes
# ---------------------------------------------------------------------------
@app.get("/")
@app.get("/health")
async def health_check():
    connected = await database.healthy()
    return {
        "status": "ok" if connected else "degraded",
        "message": "Meeting Minutes & Task Intelligence API is running",
        "llm_provider": llm_provider_name or "Not configured",
        "database_mode": "mongodb",
        "database_connected": connected,
        "endpoints": {
            "process_meeting": "POST /api/meetings/process",
            "extract_factual": "POST /api/v1/extract-factual",
            "infer_context": "POST /api/v1/infer-context/{meeting_id}",
            "list_meetings": "GET /api/meetings",
            "get_meeting": "GET /api/meetings/{meeting_id}",
        }
    }

# Main endpoint consumed by the Chrome Extension
@app.post("/api/meetings/process")
async def process_meeting_from_extension(payload: MeetingProcessRequest):
    """
    Receives meeting payload directly from the Chrome Extension.
    Executes Pass 1 (Factual Extraction), Pass 2 (Contextual Inference),
    and Pass 3 (Deterministic Graph Analysis), then stores and returns the complete result.
    """
    raw_transcript = extract_transcript_text(payload)
    if not raw_transcript:
        raise HTTPException(
            status_code=400,
            detail="Transcript is empty. Speak or provide meeting transcript to process."
        )

    meeting_id = None
    try:
        meeting_meta = payload.meeting.model_dump() if payload.meeting else {
            "title": "Untitled meeting",
            "participants": [],
            "startedAt": None,
            "endedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Pass 1: Strict Factual Extraction
        extracted_factual = await run_factual_extraction(raw_transcript)
        factual_tasks_dict = [t.model_dump() for t in extracted_factual.tasks]

        # Initial Document Save
        meeting_doc = {
            "meeting_title": meeting_meta.get("title") or extracted_factual.meeting_title,
            "date": meeting_meta.get("startedAt") or extracted_factual.date or datetime.now(timezone.utc).isoformat(),
            "participants": meeting_meta.get("participants", []),
            "raw_transcript": raw_transcript,
            "transcript": [e.model_dump() for e in payload.transcript] if isinstance(payload.transcript, list) else None,
            "tasks": factual_tasks_dict,
            "processing_metadata": payload.processing.model_dump() if payload.processing else None,
            "status": "factual_extracted",
        }
        meeting_id = await save_meeting_doc(meeting_doc)

        # Pass 2: Contextual Inference
        enriched_data = await run_contextual_inference(raw_transcript, factual_tasks_dict)
        enriched_tasks_dict = [task.model_dump() for task in enriched_data.enriched_tasks]

        # Pass 3: Deterministic Graph Validation
        graph_analysis = analyze_task_graph(enriched_tasks_dict)

        # Update Document in Database
        await update_meeting_doc(meeting_id, {
            "enriched_tasks": enriched_tasks_dict,
            "graph_analysis": graph_analysis,
            "status": "completed",
        })

        return {
            "status": "success",
            "meeting_id": meeting_id,
            "meeting_title": meeting_doc["meeting_title"],
            "tasks": enriched_tasks_dict,
            "factual_tasks": factual_tasks_dict,
            "graph_analysis": graph_analysis,
            "total_tasks": len(enriched_tasks_dict),
            "bottlenecks_count": len(graph_analysis.get("bottlenecks", [])),
            "is_feasible": graph_analysis.get("is_feasible", True),
        }

    except Exception as exc:
        if meeting_id:
            raise HTTPException(status_code=502, detail={"code":"task_processing_partial_failure", "meeting_id":meeting_id,
                "message":"Factual tasks were saved. Retry contextual inference from the meeting detail page."}) from None
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=502, detail={"code":"task_processing_failed"}) from None

# Modular Pass 1 Endpoint
@app.post("/api/v1/extract-factual")
async def extract_factual_pass(payload: TranscriptInput):
    try:
        extracted_data = await run_factual_extraction(payload.transcript_text)
        meeting_doc = extracted_data.model_dump()
        meeting_doc["raw_transcript"] = payload.transcript_text
        meeting_doc["status"] = "factual_extracted"

        meeting_id = await save_meeting_doc(meeting_doc)
        return {
            "status": "success",
            "meeting_id": meeting_id,
            "data": extracted_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code":"task_provider_error"}) from None

# Modular Pass 2 Endpoint
@app.post("/api/v1/infer-context/{meeting_id}")
async def infer_context_pass(meeting_id: str):
    try:
        meeting_doc = await get_meeting_doc(meeting_id)
        if not meeting_doc:
            raise HTTPException(status_code=404, detail="Meeting not found")

        raw_transcript = meeting_doc.get("raw_transcript", "")
        factual_tasks = meeting_doc.get("tasks", [])

        enriched_data = await run_contextual_inference(raw_transcript, factual_tasks)
        enriched_tasks_dict = [task.model_dump() for task in enriched_data.enriched_tasks]

        graph_analysis = analyze_task_graph(enriched_tasks_dict)

        await update_meeting_doc(meeting_id, {
            "enriched_tasks": enriched_tasks_dict,
            "graph_analysis": graph_analysis,
            "status": "completed",
        })

        return {
            "status": "success",
            "message": "Contextual inference and graph validation complete",
            "data": {
                "tasks": enriched_tasks_dict,
                "graph_analysis": graph_analysis,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail={"code":"task_provider_error"}) from None

# Meetings list and retrieval
@app.get("/api/meetings")
async def list_meetings(offset: int = 0, limit: int = 50):
    from fastapi import HTTPException
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, detail={"code":"invalid_pagination"})
    meetings = await list_meetings_docs(limit=limit, offset=offset)
    return {"meetings": meetings}

@app.get("/api/meetings/{meeting_id}")
async def get_meeting(meeting_id: str):
    meeting = await get_meeting_doc(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return meeting

if __name__ != "__main__":
    # Mount the minutes + workspace routers whenever the app is imported (uvicorn main:app, Vercel)
    import combined_app  # noqa: E402,F401

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting uvicorn server on http://{host}:{port}...")
    uvicorn.run("combined_app:app", host=host, port=port, reload=True)
