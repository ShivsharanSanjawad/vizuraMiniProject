# Meeting Minutes Companion

This folder contains the browser extension for the live-meeting portion of the project **Meeting Minutes and Action-Item Generator Using Large Language Models**.

## Current scope

- Chrome/Edge Manifest V3 side panel.
- Meeting title and participant metadata.
- Microphone recording to a local WebM file.
- Timestamped browser speech-recognition transcript.
- JSON export containing the payload expected by the future minutes/action-item backend.
- Configurable localhost processing endpoint for end-to-end testing.

The extension does not call an LLM directly. Keeping LLM credentials in a browser extension would expose them to every user. The extension sends the structured payload to a local/backend service, where transcription, LLM calls, MLflow/Langfuse monitoring, Sentry, and validation can be implemented safely.

## Install for development

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this folder.
4. Open the extension side panel from the browser toolbar.
5. Grant microphone permission, enter meeting metadata, and select **Start meeting**.

The Web Speech API is used for the initial transcript prototype. The recorded WebM audio can be exported for a server-side speech-to-text pipeline later. Select **Export JSON** to inspect the exact data contract.

## Payload shape

```json
{
  "meeting": {
    "title": "Sprint planning",
    "participants": ["Asha", "Ravi"],
    "startedAt": "2026-11-01T00:00:00.000Z",
    "endedAt": "2026-11-01T00:30:00.000Z"
  },
  "transcript": [
    {
      "timestamp": "04:12",
      "text": "The API integration is due Friday.",
      "source": "browser-speech-recognition"
    }
  ],
  "processing": {
    "requestedOutputs": ["summary", "topics", "decisions", "action_items", "unresolved_issues"],
    "source": "meeting-minutes-companion",
    "version": "0.1.0"
  }
}
```
