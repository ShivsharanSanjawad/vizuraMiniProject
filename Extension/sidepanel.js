const state = {
  startedAt: null,
  entries: [],
  mediaRecorder: null,
  audioChunks: [],
  audioBlob: null,
  recognition: null,
  interimText: "",
};

const $ = (id) => document.getElementById(id);
const startButton = $("startButton");
const stopButton = $("stopButton");
const statusBadge = $("statusBadge");
const transcript = $("transcript");
const processStatus = $("processStatus");

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const remainder = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remainder}`;
}

function renderTranscript() {
  transcript.className = state.entries.length ? "transcript" : "transcript empty-state";
  transcript.innerHTML = state.entries.length
    ? state.entries.map((entry) => `<article class="entry"><time>${entry.timestamp}</time><p>${escapeHtml(entry.text)}</p></article>`).join("")
    : "<p>Start a meeting to capture transcript entries.</p>";
  $("transcriptCount").textContent = `${state.entries.length} ${state.entries.length === 1 ? "entry" : "entries"}`;
  if (state.interimText) {
    transcript.insertAdjacentHTML("beforeend", `<article class="entry interim"><time>Listening...</time><p>${escapeHtml(state.interimText)}</p></article>`);
  }
  transcript.scrollTop = transcript.scrollHeight;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

function setRecordingUi(recording) {
  startButton.disabled = recording;
  stopButton.disabled = !recording;
  statusBadge.textContent = recording ? "Recording" : "Ready";
  statusBadge.className = `status-badge ${recording ? "recording" : "idle"}`;
  $("recordingNotice").textContent = recording
    ? "Listening for speech. Speak clearly and keep this panel open."
    : "Microphone access is required. Keep this panel open while recording.";
}

function addTranscriptEntry(text, timestamp) {
  const cleaned = text.trim();
  if (!cleaned) return;
  state.entries.push({ timestamp, text: cleaned, source: "browser-speech-recognition" });
  state.interimText = "";
  renderTranscript();
}

async function startMeeting() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.startedAt = Date.now();
    state.audioChunks = [];
    state.audioBlob = null;
    state.mediaRecorder = new MediaRecorder(stream);
    state.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size) state.audioChunks.push(event.data);
    };
    state.mediaRecorder.onstop = () => {
      state.audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
      stream.getTracks().forEach((track) => track.stop());
      $("exportAudioButton").disabled = false;
    };
    state.mediaRecorder.start(1000);
    startSpeechRecognition();
    setRecordingUi(true);
  } catch (error) {
    $("recordingNotice").textContent = `Unable to access the microphone: ${error.message}`;
  }
}

function startSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("recordingNotice").textContent = "Audio recording is active, but this browser does not support speech recognition.";
    return;
  }
  state.recognition = new SpeechRecognition();
  state.recognition.continuous = true;
  state.recognition.interimResults = true;
  state.recognition.lang = "en-US";
  state.recognition.onresult = (event) => {
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = result[0].transcript;
      if (result.isFinal) {
        addTranscriptEntry(text, formatTime((Date.now() - state.startedAt) / 1000));
      } else {
        state.interimText = text;
        renderTranscript();
      }
    }
  };
  state.recognition.onerror = (event) => {
    if (event.error !== "no-speech") $("recordingNotice").textContent = `Speech recognition: ${event.error}. Audio recording continues.`;
  };
  state.recognition.onend = () => {
    if (!stopButton.disabled) state.recognition.start();
  };
  state.recognition.start();
}

function stopMeeting() {
  if (state.recognition) {
    state.recognition.onend = null;
    state.recognition.stop();
    state.recognition = null;
  }
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") state.mediaRecorder.stop();
  state.interimText = "";
  setRecordingUi(false);
  renderTranscript();
}

function meetingPayload() {
  return {
    meeting: {
      title: $("meetingTitle").value.trim() || "Untitled meeting",
      participants: $("participants").value.split(",").map((name) => name.trim()).filter(Boolean),
      startedAt: state.startedAt ? new Date(state.startedAt).toISOString() : null,
      endedAt: new Date().toISOString(),
    },
    transcript: state.entries,
    processing: {
      requestedOutputs: ["summary", "topics", "decisions", "action_items", "unresolved_issues"],
      source: "meeting-minutes-companion",
      version: "0.1.0",
    },
  };
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  chrome.downloads.download({ url, filename, saveAs: true }, () => URL.revokeObjectURL(url));
}

startButton.addEventListener("click", startMeeting);
stopButton.addEventListener("click", stopMeeting);
$("clearButton").addEventListener("click", () => {
  if (!stopButton.disabled) return;
  state.entries = [];
  renderTranscript();
});
$("exportJsonButton").addEventListener("click", () => {
  downloadBlob(new Blob([JSON.stringify(meetingPayload(), null, 2)], { type: "application/json" }), "meeting-data.json");
});
$("exportAudioButton").addEventListener("click", () => {
  if (state.audioBlob) downloadBlob(state.audioBlob, "meeting-recording.webm");
});
$("processButton").addEventListener("click", async () => {
  const settings = await chrome.storage.local.get(["processingEndpoint"]);
  if (!settings.processingEndpoint) {
    processStatus.textContent = "Add a local processing endpoint in Processing settings before sending.";
    return;
  }
  processStatus.textContent = "Sending transcript for processing...";
  try {
    const response = await fetch(settings.processingEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(meetingPayload()),
    });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    processStatus.textContent = "Meeting data sent successfully.";
  } catch (error) {
    processStatus.textContent = `Processing failed: ${error.message}`;
  }
});

renderTranscript();
