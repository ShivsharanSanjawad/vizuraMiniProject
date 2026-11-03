# Meeting Minutes Companion (Chrome Extension)

This folder contains the browser extension for **Meeting Minutes & Task Intelligence**.

## Capabilities

- Chrome / Edge Manifest V3 side panel.
- Real-time speech recognition with timestamped transcript logging.
- Microphone audio recording exportable to WebM.
- One-click processing with the local FastAPI backend.
- Direct visualization of:
  - Extracted tasks with Priority, Assignee, and Deadline.
  - Schedule feasibility checks (detects circular dependencies).
  - Schedule bottlenecks and external blockers flagged by the graph engine.
- Configurable backend endpoint with built-in connection testing.

## Install for Development

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this `Extension` folder.
4. Click the extension action in the toolbar to open the side panel.
5. Click **Start meeting** to capture live speech.
6. When finished, click **Process with Backend** to analyze tasks.

## Settings & Backend Contract

The extension defaults to `http://127.0.0.1:8000/api/meetings/process`. You can change this in **Processing settings** (`options.html`) and test the connection at any time.

The payload sent to the backend follows this structure:

```json
{
  "meeting": {
    "title": "Sprint planning",
    "participants": ["Alice", "Bob"],
    "startedAt": "2026-11-02T16:00:00.000Z",
    "endedAt": "2026-11-02T16:30:00.000Z"
  },
  "transcript": [
    {
      "timestamp": "00:05",
      "text": "Bob, please fix the database migration by Friday.",
      "source": "browser-speech-recognition"
    }
  ],
  "processing": {
    "requestedOutputs": ["summary", "topics", "decisions", "action_items", "unresolved_issues"],
    "source": "meeting-minutes-companion",
    "version": "1.0.0"
  }
}
```
