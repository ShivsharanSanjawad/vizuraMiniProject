# Meeting Minutes & Task Intelligence System

An end-to-end meeting intelligence system that captures live transcripts through a Chrome Extension or accepts uploaded and pasted transcripts through a React website. It extracts factual action items, infers contextual task details, analyzes dependencies and blockers, generates evidence-linked meeting minutes, and supports human review, approval, revision history, PDF/DOCX export, meeting Q&A, and LLM monitoring.

---

## Architecture Overview

```mermaid
graph LR
    Extension[Chrome Side Panel Extension] -->|POST /api/meetings/process| Backend[FastAPI Combined Backend]
    Web[React + TypeScript + Vite Website] -->|Meeting, minutes and workspace APIs| Backend
    Backend -->|Factual extraction and contextual inference| Gateway[Shared LLM Service]
    Backend -->|Transcript normalization and chunking| Minutes[Meeting Minutes Pipeline]
    Minutes -->|Summary, consolidation and compact review| Gateway
    Gateway -->|Scheduled requests, caching and bounded retries| LLM[Gemini / Groq / OpenAI]
    Backend -->|Local dependency analysis| Graph[NetworkX Graph Engine]
    Minutes -->|Source-reference validation| Verification[Local Evidence Checks]
    Backend -->|Meetings, revisions and monitoring| DB[(MongoDB Atlas)]
    Minutes -->|Reviewed minutes| Export[PDF / DOCX Export]
    Backend -->|Tasks, dependency feasibility and bottlenecks| Extension
    Backend -->|Minutes, tasks, graphs, Q&A and monitoring| Web
```

1. **Frontend (Web Application)**:
   - React, TypeScript and Vite application in `frontend/`.
   - Five main pages: Dashboard, Meetings, Action Items, Meeting Assistant and Monitoring.
   - Accepts pasted transcripts and `.txt` or `.json` uploads. Backend audio-file transcription is not implemented.
   - Processes tasks first, then generates minutes using the returned stored meeting ID. Saved results remain available if a later stage fails.
   - Provides meeting-detail tabs for overview, minutes, tasks, dependencies and transcript evidence.
   - Supports draft editing, approval, regeneration, revision history, PDF/DOCX downloads, persistent task-status updates and confirmed permanent meeting deletion.
   - The assistant answers questions about one selected meeting using retrieved transcript excerpts; cross-meeting search is not implemented.

2. **Frontend (Browser Extension)**:
   - Manifest V3 Side Panel extension in `Extension/`.
   - Captures timestamped Google Meet captions and offers browser speech-recognition capture options, subject to browser support and permissions.
   - Sends structured meeting payloads to `http://127.0.0.1:8000/api/meetings/process`.
   - Displays extracted tasks, priorities, missing-owner/deadline flags, dependencies and bottlenecks.
   - Meetings saved by the extension are accessible through the website's shared Atlas database. Minutes can be generated later from the website.

3. **Backend (`task-backend/`)**:
   - **Entry point (`combined_app.py`)**: Exposes the original task routes, minutes routes and workspace additions in one FastAPI application, sharing the provider service and database connection.
   - **Pass 1 (`/api/v1/extract-factual`)**: Extracts only explicitly stated tasks, owners, deadlines and prerequisites.
   - **Pass 2 (`/api/v1/infer-context/{meeting_id}`)**: Enriches tasks with contextual owners, deadlines, priorities, statuses and dependencies while retaining separate factual records.
   - **Graph Engine (`graph_engine.py`)**: Builds a directed dependency graph, detects cycles and external blockers, and ranks bottlenecks by outgoing dependencies. Its feasibility flag checks dependency cycles; it is not a complete deadline or resource scheduler.
   - **Minutes Pipeline (`minutes_module/`)**: Normalizes transcripts, preserves available source IDs, speaker labels and timestamps, summarizes bounded chunks, consolidates long meetings, and validates source references locally. Optional compact LLM review flags uncertain or unsupported claims for human review.
   - **Review and Export**: Preserves draft/approved revisions. Explicit regeneration creates a new draft without replacing previous approvals. Exports default to the latest approved revision; draft export requires an explicit option.
   - **LLM Integration**: Uses the OpenAI-compatible SDK for Gemini, Groq or OpenAI, with configurable RPM/TPM scheduling, bounded concurrency, classified retries, validated-output caching and duplicate-generation suppression. Scheduling and in-flight coordination are process-local; use one backend worker for this setup.
   - **Monitoring**: Persists generation timings, calls, retries, verification results and available token usage. Estimates are distinguished from provider-reported values; API cost remains unknown unless pricing and sufficient usage information are available.
   - **Storage (`database.py`)**: Requires MongoDB Atlas. Meetings/tasks, minutes revisions and monitoring use separate collections linked by meeting ID. There is no automatic local JSON fallback. Confirmed meeting deletion removes associated saved minutes and monitoring records in a database transaction.

---

## Quickstart

### 1. Environment Setup

Use **Python 3.11+**, **Node.js 22.12+**, a MongoDB Atlas database user, and an API key for one supported provider. Allow your current IP in Atlas Network Access and give the database user appropriate read/write permissions.

From the project root, create one Python environment and install the authoritative backend dependency file:

```powershell
# Skip environment creation if the root .venv already exists.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r task-backend\requirements.txt

# Create the configuration file only if it does not already exist.
if (!(Test-Path task-backend\.env)) {
    Copy-Item task-backend\.env.example task-backend\.env
}
```

Configure **only `task-backend/.env`**. Example using Groq:

```env
# MongoDB Atlas is required; replace the placeholders.
MONGODB_URL=mongodb+srv://YOUR_USER:YOUR_ENCODED_PASSWORD@YOUR_CLUSTER.mongodb.net/?retryWrites=true&w=majority
MONGODB_DATABASE=meeting_db

# Fill one provider key. Keep unused provider keys blank.
GEMINI_API_KEY=
GROQ_API_KEY=your_groq_key_here
OPENAI_API_KEY=

MINUTES_LLM_PROVIDER=groq
MINUTES_LLM_MODEL=openai/gpt-oss-20b
LLM_MODEL=

MINUTES_ENABLE_VERIFICATION=true
MINUTES_STORAGE_MODE=mongodb
MINUTES_REQUESTS_PER_MINUTE=20
MINUTES_TOKENS_PER_MINUTE=8000
MINUTES_MAX_CONCURRENCY=1

FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
HOST=127.0.0.1
PORT=8000
```

Use a model available to your provider account and set RPM/TPM at or below its actual account/model limits. The scheduler defaults are not a guarantee of your account's quota. URL-encode special characters in the Atlas password.

For Gemini, fill `GEMINI_API_KEY`, set provider to `gemini`, and choose a compatible Gemini model. For OpenAI, fill `OPENAI_API_KEY`, set provider to `openai`, and choose a compatible chat-completions model. Explicit provider selection takes precedence; otherwise the first configured key is selected in Gemini, Groq, OpenAI order. Model selection is `MINUTES_LLM_MODEL`, then `LLM_MODEL`, then the provider default.

The backend reads only `task-backend/.env`; root `.env` and example files are not loaded. Process environment variables take precedence. Restart the backend after configuration changes. Keep `.env`, API keys, Atlas credentials and `.venv/` out of Git.

### 2. Start the Backend

In terminal one, from the project root:

```powershell
.\.venv\Scripts\Activate.ps1
cd task-backend
python -m uvicorn combined_app:app --reload --host 127.0.0.1 --port 8000
```

Or invoke the shared environment's Python directly without activation:

```powershell
.\.venv\Scripts\python.exe -m uvicorn combined_app:app --app-dir task-backend --reload --host 127.0.0.1 --port 8000
```

Start only one backend on port 8000. Use `combined_app:app` for the complete website and extension API, rather than starting separate `main.py` and `main2.py` servers.

Verify backend health at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) and minutes configuration at [http://127.0.0.1:8000/api/v1/minutes/health](http://127.0.0.1:8000/api/v1/minutes/health). A configured key does not prove provider authentication or quota; generation exercises those. Startup fails if Atlas cannot connect.

Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 3. Start the Web Frontend

In terminal two, from the project root:

```powershell
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173). No frontend env file is required for this local setup: the API client defaults to `http://127.0.0.1:8000`. If your backend is elsewhere, optionally set `VITE_API_BASE_URL` in `frontend/.env.local` and restart Vite. Never put provider keys or database credentials in frontend variables.

1. Choose **New meeting**, enter metadata and paste or upload a transcript. `task-backend/minutes_sample_request.json` provides a sample payload.
2. Submit once: the frontend calls meeting processing, obtains the stored meeting ID, then calls minutes generation for that ID.
3. Inspect the meeting tabs, edit draft minutes, review evidence/flags, enter a reviewer name and approve. Reviewer names are audit labels, not authenticated identities.
4. Download PDF/DOCX, update task statuses, inspect dependencies and ask the assistant about the selected meeting.
5. If enrichment or minutes generation fails, open the saved meeting and retry that stage instead of submitting another copy.
6. Use **Delete** in the Meetings list only when you want to permanently remove the meeting and its associated saved records, including approved minutes. Download needed reports first and avoid deletion while generation is running.

### 4. Load the Chrome Extension

1. Open Chrome at `chrome://extensions` or Edge at `edge://extensions`.
2. Enable **Developer mode**, click **Load unpacked**, and select the `Extension/` directory.
3. Open the extension side panel and confirm the processing endpoint in its settings is `http://127.0.0.1:8000/api/meetings/process`.
4. Enter the meeting title and participants, then select a capture mode. For Google Meet, enable meeting captions and select **Google Meet Captions** in the extension.
5. Click **Start recording**, capture the conversation, and stop when finished. Microphone mode captures your voice; available capture behavior depends on browser permissions and support.
6. Click **Process with Backend** to extract tasks and analyze dependencies.
7. Open the website to view the saved meeting and generate its minutes if needed. The website also works without the extension running.

### 5. Verify and Test

From the project root, run the offline backend suite and frontend checks:

```powershell
.\.venv\Scripts\python.exe -B -m pytest task-backend\tests_minutes -q -p no:cacheprovider
npm --prefix frontend run test
npm --prefix frontend run build
```

Backend tests mock provider and MongoDB calls; they do not verify your real quota, credentials or Atlas transaction permissions. Test the live workflow through the website or Swagger: process a transcript, generate minutes, edit/approve, export, change a task status, ask a question, inspect monitoring and refresh saved data.

Use the `meeting_id` returned by processing; it can differ from the transcript payload's original metadata ID. Before minutes exist, a minutes lookup can legitimately return 404. Exports require an approved revision unless `draft=true` is explicitly requested. Generation errors include a safe `detail.code` and available diagnostics; do not repeatedly resubmit a whole meeting to retry a failed later stage.

Detailed troubleshooting: [RUN_AND_TEST.md](RUN_AND_TEST.md). Backend optimization and monitoring notes: [MINUTES_MODULE_README.md](task-backend/MINUTES_MODULE_README.md).

---

## API Endpoints

All paths below are exposed by `combined_app:app`. Meeting and minutes list endpoints accept `offset` and `limit` pagination parameters. The minutes list path has no trailing slash.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Alias for the backend health response |
| `GET` | `/health` | Backend health, database connectivity and selected LLM provider |
| `POST` | `/api/meetings/process` | Factual extraction, contextual enrichment, graph analysis and meeting persistence; used by the extension and web submission flow |
| `POST` | `/api/v1/extract-factual` | Extract and save factual tasks without running contextual inference |
| `POST` | `/api/v1/infer-context/{meeting_id}` | Enrich a saved meeting's tasks and update dependency analysis; used to retry incomplete inference |
| `GET` | `/api/meetings` | List saved meetings, including extension-created records |
| `GET` | `/api/meetings/{meeting_id}` | Retrieve meeting metadata, saved transcript, tasks and graph analysis |
| `DELETE` | `/api/meetings/{meeting_id}` | Permanently delete the meeting, its minutes revisions and associated generation monitoring records |
| `PATCH` | `/api/meetings/{meeting_id}/tasks/{task_id}/status` | Persist a task status using the expected task revision and refresh graph analysis |
| `POST` | `/api/meetings/{meeting_id}/query` | Answer a question using retrieved excerpts from one meeting's transcript |
| `GET` | `/api/v1/minutes/health` | Minutes provider/model configuration, env-file presence and database connectivity; does not reveal keys |
| `GET` | `/api/v1/minutes/monitoring/stats` | Statistics over the latest 1,000 persisted generation jobs and up to 50 recent records |
| `POST` | `/api/v1/minutes/generate` | Generate minutes from a supplied transcript; does not run the task pipeline or create a task meeting |
| `POST` | `/api/v1/minutes/generate/{meeting_id}` | Generate minutes for an existing meeting; reuses a matching current revision or joins identical work in flight |
| `GET` | `/api/v1/minutes` | List the latest minutes revision for each stored minutes history |
| `GET` | `/api/v1/minutes/{meeting_id}` | Retrieve the latest minutes revision; optional `revision` query parameter selects a specific revision |
| `GET` | `/api/v1/minutes/{meeting_id}/history` | Retrieve preserved minutes revisions and review/approval history |
| `PUT` | `/api/v1/minutes/{meeting_id}` | Save draft edits as a new revision using `expected_revision` and `reviewer` |
| `POST` | `/api/v1/minutes/{meeting_id}/approve` | Approve the current draft with reviewer information and acknowledgment of review flags when required |
| `POST` | `/api/v1/minutes/{meeting_id}/regenerate` | Explicitly generate a new draft while preserving previous edits and approvals |
| `GET` | `/api/v1/minutes/{meeting_id}/export/pdf` | Download the latest approved PDF; `draft=true` explicitly selects the latest version, including a draft |
| `GET` | `/api/v1/minutes/{meeting_id}/export/docx` | Download the latest approved DOCX; `draft=true` explicitly selects the latest version, including a draft |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/redoc` | ReDoc API reference |
| `GET` | `/openapi.json` | Machine-readable OpenAPI schema |
