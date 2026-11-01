# main.py
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from pathlib import Path
from pydantic import BaseModel
from openai import AsyncOpenAI
from models import FactualExtraction
from bson import ObjectId
from models import ContextualInference
from graph_engine import analyze_task_graph
import os
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    raise RuntimeError(f"GROQ_API_KEY is not set. Check the .env file at {env_path}")

# Groq provides an OpenAI-compatible API.
client = AsyncOpenAI(
    api_key=groq_api_key,
    base_url="https://api.groq.com/openai/v1",
)

# Database configuration
mongo_client = AsyncIOMotorClient(os.getenv("MONGODB_URL"))
db = mongo_client.meeting_db
tasks_collection = db.factual_tasks

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await mongo_client.meeting_db.command('ping')
        print("Successfully connected to MongoDB Atlas!")
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
    yield
    mongo_client.close()

app = FastAPI(lifespan=lifespan)

class TranscriptInput(BaseModel):
    transcript_text: str

@app.post("/api/v1/extract-factual")
async def extract_factual_pass(payload: TranscriptInput):
    try:
        # Pass 1: Strict factual extraction using Groq
        completion = await client.beta.chat.completions.parse(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "You are a strict factual extractor. Analyze the meeting transcript. "
                        "Extract tasks ONLY if they are explicitly stated. Do not infer, guess, "
                        "or suggest owners, deadlines, or dependencies unless spoken aloud."
                    )
                },
                {"role": "user", "content": payload.transcript_text}
            ],
            response_format=FactualExtraction,
        )
        
        # The parsed Pydantic object
        extracted_data = completion.choices[0].message.parsed
        
        # Prepare the MongoDB document
        meeting_doc = extracted_data.model_dump()
        meeting_doc["raw_transcript"] = payload.transcript_text
        
        # Save to MongoDB Atlas
        result = await tasks_collection.insert_one(meeting_doc)
        
        return {
            "status": "success", 
            "meeting_id": str(result.inserted_id), 
            "data": extracted_data
        }
        
    except Exception as e:
        print(type(e).__name__, repr(e))
        response = getattr(e, "response", None)
        if response is not None:
            print(response.text)
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/v1/infer-context/{meeting_id}")
async def infer_context_pass(meeting_id: str):
    try:
        # 1. Retrieve Pass 1 data from MongoDB
        meeting_doc = await tasks_collection.find_one({"_id": ObjectId(meeting_id)})
        if not meeting_doc:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Extract the transcript and factual tasks to feed back to the LLM
        raw_transcript = meeting_doc.get("raw_transcript", "")
        factual_tasks = meeting_doc.get("tasks", [])

        # 2. Pass 2: Contextual Inference using Grok
        system_prompt = (
            "You are an AI project manager. You are given a meeting transcript and a strictly factual list of extracted tasks. Your job is to enrich these tasks by inferring missing context. Assign priorities, flag missing owners/deadlines, deduce current statuses, and suggest inferred dependencies (tasks that logically must happen first, even if not explicitly stated). CRITICAL PREREQUISITE RULE: If a task is blocked by an external factor, event, or third-party action that is not tracked as a separate task ID (e.g., waiting on AWS support, client approvals, or external deliveries), you MUST add a clear descriptive string of this blocker into the explicit_prerequisites or inferred_prerequisites list (e.g., 'Waiting on AWS support to approve quota'). Do not leave prerequisites empty if an external blocker exists."
        )
                
        user_prompt = f"Transcript:\n{raw_transcript}\n\nFactual Tasks:\n{factual_tasks}"

        completion = await client.beta.chat.completions.parse(
            model="openai/gpt-oss-20b", 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format=ContextualInference,
        )
        
        # The parsed enriched Pydantic object
        enriched_data = completion.choices[0].message.parsed
        
        # 3. Update the existing document in MongoDB Atlas
        enriched_tasks_dict = [task.model_dump() for task in enriched_data.enriched_tasks]

        # Run the deterministic graph validation
        graph_analysis = analyze_task_graph(enriched_tasks_dict)
        
        # Save both the tasks AND the graph math to MongoDB
        await tasks_collection.update_one(
            {"_id": ObjectId(meeting_id)},
            {"$set": {
                "enriched_tasks": enriched_tasks_dict,
                "graph_analysis": graph_analysis
            }}
        )
        
        return {
            "status": "success", 
            "message": "Contextual inference and graph validation complete",
            "data": {
                "tasks": enriched_tasks_dict,
                "graph_analysis": graph_analysis
            }
        }
    
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))