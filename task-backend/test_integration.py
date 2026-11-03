import sys
import os
from pathlib import Path

# Force UTF-8 stdout
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from fastapi.testclient import TestClient

backend_dir = Path(__file__).resolve().parent / "task-backend"
sys.path.insert(0, str(backend_dir))

from main import app

client = TestClient(app)

def test_health():
    print("\n--- Test 1: GET /health ---")
    response = client.get("/health")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    print("Health response:", data)
    assert data["status"] == "ok"
    print("[PASS] Health check passed!")

def test_extension_meeting_process():
    print("\n--- Test 2: POST /api/meetings/process (Extension Payload) ---")
    payload = {
        "meeting": {
            "title": "Backend & Frontend Sync",
            "participants": ["Alice", "Bob", "Charlie"],
            "startedAt": "2026-11-02T16:00:00.000Z",
            "endedAt": "2026-11-02T16:30:00.000Z"
        },
        "transcript": [
            {
                "timestamp": "00:05",
                "text": "Alice: Welcome everyone. Bob, please build the API database migration by Wednesday.",
                "source": "browser-speech-recognition"
            },
            {
                "timestamp": "00:20",
                "text": "Bob: Sure, but Charlie has to finish the schema definition first.",
                "source": "browser-speech-recognition"
            },
            {
                "timestamp": "00:35",
                "text": "Charlie: I can finish the schema definition by tomorrow evening.",
                "source": "browser-speech-recognition"
            },
            {
                "timestamp": "00:50",
                "text": "Alice: Great. And we are waiting on AWS support to approve our new quota before deployment.",
                "source": "browser-speech-recognition"
            }
        ],
        "processing": {
            "requestedOutputs": ["summary", "topics", "decisions", "action_items", "unresolved_issues"],
            "source": "meeting-minutes-companion",
            "version": "1.0.0"
        }
    }

    # Simulate request with CORS Origin header like a Chrome extension
    response = client.post(
        "/api/meetings/process",
        json=payload,
        headers={"Origin": "chrome-extension://abcdefghijklmnop"}
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    print("Processing result status:", data["status"])
    print("Meeting ID:", data["meeting_id"])
    print("Total tasks:", data["total_tasks"])
    print("Schedule Feasible:", data["is_feasible"])
    print("Bottlenecks count:", data["bottlenecks_count"])
    for t in data["tasks"]:
        print(f"  [{t['task_id']}] {t['description']} (Owner: {t.get('owner')}, Due: {t.get('deadline')}, Priority: {t.get('priority')})")
    
    assert data["status"] == "success"
    assert data["total_tasks"] > 0
    assert "graph_analysis" in data
    print("[PASS] Extension end-to-end processing passed!")
    return data["meeting_id"]

def test_get_meeting(meeting_id):
    print("\n--- Test 3: GET /api/meetings/{id} ---")
    response = client.get(f"/api/meetings/{meeting_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["_id"] == meeting_id
    print("Retrieved meeting title:", data.get("meeting_title"))
    print("[PASS] Retrieve meeting passed!")

if __name__ == "__main__":
    test_health()
    meeting_id = test_extension_meeting_process()
    test_get_meeting(meeting_id)
    print("\n==========================================")
    print("ALL INTEGRATION TESTS PASSED SUCCESSFULLY!")
    print("==========================================")
