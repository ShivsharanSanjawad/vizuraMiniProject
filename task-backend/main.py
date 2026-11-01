from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
import os

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

mongodb_url = os.getenv("MONGODB_URL")
print(f".env file found: {env_path.is_file()}")
print(f"MONGODB_URL is set: {bool(mongodb_url)}")

# Lifespan context manager to test DB connection on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        # Send a ping to confirm a successful connection
        await mongo_client.admin.command('ping')
        print("Successfully connected to MongoDB Atlas!")
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
    yield
    mongo_client.close()

app = FastAPI(lifespan=lifespan)

if not mongodb_url:
    raise RuntimeError(f"MONGODB_URL is not set. Check the .env file at {env_path}")

mongo_client = AsyncIOMotorClient(mongodb_url)
db = mongo_client.meeting_db
meetings_collection = db.meetings

@app.get("/")
async def root():
    return {"message": "API is running"}