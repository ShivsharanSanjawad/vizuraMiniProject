/**
 * sidepanel.js
 *
 * Three capture modes:
 *  1. GOOGLE_MEET  – reads captions injected by meet-captions.js via background port
 *                   (all participants, with speaker names)
 *  2. TAB_AUDIO   – chrome.tabCapture + Web Speech Recognition
 *                   (all participants, no speaker names)
 *  3. MICROPHONE  – getUserMedia + Web Speech Recognition
 *                   (your voice only)
 */

// ── Constants ─────────────────────────────────────────────────────────────────
const MODE = { NONE: "none", GOOGLE_MEET: "meet", TAB: "tab", MIC: "mic" };
const PROCESSING_ENDPOINT_DEFAULT =
  (window.EXTENSION_CONFIG && window.EXTENSION_CONFIG.processingEndpoint) ||
  "http://127.0.0.1:8000/api/meetings/process";

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  mode: MODE.NONE,
  isRecording: false,
  isProcessing: false,
  startedAt: null,
  entries: [],
  // Audio / speech
  mediaRecorder: null,
  audioChunks: [],
  audioBlob: null,
  recognition: null,
  interimText: "",
  // Background port (for Google Meet captions relay)
  bgPort: null,
};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const startButton       = $("startButton");
const stopButton        = $("stopButton");
const statusBadge       = $("statusBadge");
const transcriptEl      = $("transcript");
const processStatus     = $("processStatus");
const processButton     = $("processButton");
const resultsSection    = $("resultsSection");
const resultsSummary    = $("resultsSummary");
const graphCard         = $("graphCard");
const tasksList         = $("tasksList");
const captureBanner     = $("captureBanner");
const captureBannerTitle  = $("captureBannerTitle");
const captureBannerDesc   = $("captureBannerDesc");
const captureBannerIcon   = $("captureBannerIcon");
const meetCaptionNotice = $("meetCaptionNotice");

// ── Helpers ───────────────────────────────────────────────────────────────────
function escapeHtml(v) {
  if (!v) return "";
  return String(v).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])
  );
}

function formatTime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60).toString().padStart(2, "0");
  const r = (s % 60).toString().padStart(2, "0");
  return `${m}:${r}`;
}

function elapsed() {
  return state.startedAt ? Date.now() - state.startedAt : 0;
}

// ── Transcript ────────────────────────────────────────────────────────────────
function addEntry(text, timestamp) {
  const cleaned = (text || "").trim();
  if (!cleaned) return;
  state.entries.push({
    timestamp: timestamp || formatTime(elapsed()),
    text: cleaned,
    source: state.mode,
  });
  state.interimText = "";
  renderTranscript();
}

function updateOrAddTurnEntry(turnId, speaker, fullText) {
  const cleaned = (fullText || "").trim();
  if (!cleaned) return;

  // 1. If this turnId already exists, update in-place as speech grows
  if (turnId) {
    const existingIndex = state.entries.findIndex((e) => e.turnId === turnId);
    if (existingIndex !== -1) {
      if (state.entries[existingIndex].text !== cleaned) {
        state.entries[existingIndex].text = cleaned;
        renderTranscript();
      }
      return;
    }
  }

  // 2. If no turnId was supplied, check if the last entry is from the same speaker and simply extended
  if (!turnId) {
    const last = state.entries[state.entries.length - 1];
    if (last && last.speaker === speaker && (cleaned.startsWith(last.text) || cleaned.includes(last.text.slice(-20)))) {
      last.text = cleaned;
      renderTranscript();
      return;
    }
  }

  // 3. Brand new speaker turn!
  state.entries.push({
    turnId: turnId || `turn-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`,
    speaker: speaker || "",
    timestamp: formatTime(elapsed()),
    text: cleaned,
    source: state.mode,
  });
  state.interimText = "";
  renderTranscript();
}

function renderTranscript() {
  transcriptEl.className = state.entries.length
    ? "transcript"
    : "transcript empty-state";

  transcriptEl.innerHTML = state.entries.length
    ? state.entries
        .map(
          (e) =>
            `<article class="entry"><time>${escapeHtml(e.timestamp)}</time>` +
            `<p>${escapeHtml(e.text)}</p></article>`
        )
        .join("")
    : "<p>Start a capture mode or add text to see the transcript here.</p>";

  $("transcriptCount").textContent = `${state.entries.length} ${
    state.entries.length === 1 ? "entry" : "entries"
  }`;

  if (state.interimText) {
    transcriptEl.insertAdjacentHTML(
      "beforeend",
      `<article class="entry interim"><time>Listening…</time><p>${escapeHtml(
        state.interimText
      )}</p></article>`
    );
  }
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

// ── Banner ────────────────────────────────────────────────────────────────────
function setBanner(type, title, desc) {
  captureBanner.className = `capture-banner banner-${type}`;
  captureBannerIcon.textContent =
    type === "recording" ? "🔴" :
    type === "success"   ? "✓"  :
    type === "error"     ? "⚠"  : "🎙";
  captureBannerTitle.textContent = title;
  captureBannerDesc.textContent  = desc;
}

// ── Mode selection UI ─────────────────────────────────────────────────────────
function setModeButton(mode) {
  ["modeGoogleMeet", "modeTabAudio", "modeMic"].forEach((id) =>
    $(id).classList.remove("active")
  );
  if (mode === MODE.GOOGLE_MEET) $("modeGoogleMeet").classList.add("active");
  if (mode === MODE.TAB)         $("modeTabAudio").classList.add("active");
  if (mode === MODE.MIC)         $("modeMic").classList.add("active");

  meetCaptionNotice.style.display = mode === MODE.GOOGLE_MEET ? "block" : "none";
  startButton.disabled = false;
}

$("modeGoogleMeet").addEventListener("click", () => {
  if (state.isRecording) return;
  state.mode = MODE.GOOGLE_MEET;
  setModeButton(MODE.GOOGLE_MEET);
  setBanner("idle", "Google Meet mode selected",
    "Enable captions in Google Meet (⋮ → Turn on captions), then click Start recording.");
  $("recordingNotice").textContent =
    "Open Google Meet, enable captions (Ctrl+Shift+C), then click Start recording.";
});

$("modeTabAudio").addEventListener("click", () => {
  if (state.isRecording) return;
  state.mode = MODE.TAB;
  setModeButton(MODE.TAB);
  setBanner("idle", "Tab Audio mode selected",
    "Captures all voices from the active browser tab. Speaker names are NOT available.");
  $("recordingNotice").textContent =
    "Captures all audio from the active tab (all voices, no speaker names).";
});

$("modeMic").addEventListener("click", () => {
  if (state.isRecording) return;
  state.mode = MODE.MIC;
  setModeButton(MODE.MIC);
  setBanner("idle", "Microphone mode selected",
    "Captures only YOUR voice. Use Google Meet Captions for all participants.");
  $("recordingNotice").textContent =
    "Records YOUR microphone only. Use Google Meet Captions to capture all participants.";
});

// ── Recording UI ──────────────────────────────────────────────────────────────
function setRecordingUi(recording) {
  state.isRecording = recording;
  startButton.disabled = recording;
  stopButton.disabled  = !recording;
  statusBadge.textContent = recording ? "Recording" : "Ready";
  statusBadge.className   = `status-badge ${recording ? "recording" : "idle"}`;
  // Disable mode buttons while recording
  ["modeGoogleMeet", "modeTabAudio", "modeMic"].forEach(
    (id) => ($(id).disabled = recording)
  );
}

// ── Background Port (Meet captions relay) ─────────────────────────────────────
function connectToBackground() {
  try {
    state.bgPort = chrome.runtime.connect({ name: "sidepanel" });
    state.bgPort.onMessage.addListener(handleBackgroundMessage);
    state.bgPort.onDisconnect.addListener(() => {
      state.bgPort = null;
      // Show reconnecting indicator only while recording
      if (state.isRecording && state.mode === MODE.GOOGLE_MEET) {
        setBanner("error", "Connection to background lost – reconnecting…",
          "Captions will resume automatically. If they don't, reload the extension.");
      }
      // Reconnect immediately, then with backoff
      setTimeout(connectToBackground, 500);
    });
  } catch (e) {
    setTimeout(connectToBackground, 2000);
  }
}

function handleBackgroundMessage(msg) {
  if (!msg || !msg.type) return;

  if (msg.type === "meet-caption" && state.isRecording && state.mode === MODE.GOOGLE_MEET) {
    updateOrAddTurnEntry(msg.turnId, msg.speaker, msg.text);
    // Restore recording banner if we had a disconnect warning
    if (captureBanner.classList.contains("banner-error")) {
      setBanner("recording", "Google Meet captions active",
        "All participants' speech is being captured.");
    }
    return;
  }

  if (msg.type === "meet-captions-ready") {
    if (state.mode === MODE.GOOGLE_MEET) {
      setBanner("recording", "Google Meet captions are live",
        "Captions from all participants will appear in the transcript.");
    }
    return;
  }

  if (msg.type === "meet-captions-enable-prompt" && state.mode === MODE.GOOGLE_MEET) {
    setBanner("error", "Enable captions in Google Meet",
      "Click ⋮ → 'Turn on captions'  or press Ctrl+Shift+C in the Meet tab.");
    return;
  }

  if (msg.type === "meet-tab-detected") {
    if (state.mode === MODE.NONE || state.mode === MODE.GOOGLE_MEET) {
      state.mode = MODE.GOOGLE_MEET;
      setModeButton(MODE.GOOGLE_MEET);
      setBanner("idle", "Google Meet detected",
        "Enable captions in Meet, then click Start recording.");
    }
    return;
  }
}

// ── Start recording ───────────────────────────────────────────────────────────
startButton.addEventListener("click", async () => {
  if (state.mode === MODE.NONE) {
    $("recordingNotice").textContent = "Please select a capture mode first.";
    return;
  }
  state.startedAt = Date.now();
  state.audioChunks = [];
  state.audioBlob = null;

  if (state.mode === MODE.GOOGLE_MEET) await startGoogleMeetMode();
  else if (state.mode === MODE.TAB)    await startTabAudioMode();
  else                                 await startMicMode();
});

// ── Stop recording ────────────────────────────────────────────────────────────
stopButton.addEventListener("click", stopRecording);

function stopRecording() {
  if (state.recognition) {
    state.recognition.onend = null;
    state.recognition.stop();
    state.recognition = null;
  }
  if (state.mediaRecorder && state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
  state.interimText = "";
  setRecordingUi(false);
  setBanner("idle", "Recording stopped",
    `${state.entries.length} entries captured. Click 'Process with Backend' to analyse.`);
  $("recordingNotice").textContent = "Recording stopped.";
  renderTranscript();
}

// ── Google Meet mode ──────────────────────────────────────────────────────────
async function startGoogleMeetMode() {
  setRecordingUi(true);
  setBanner("recording", "Waiting for Google Meet captions…",
    "Speak in your Meet call. Captions from all participants will appear here automatically.");
  $("recordingNotice").textContent =
    "Listening via Google Meet captions. All participants' speech will be captured with names.";
}

// ── Tab Audio mode ────────────────────────────────────────────────────────────
async function startTabAudioMode() {
  try {
    // chrome.tabCapture must be called from an extension page context
    const stream = await new Promise((resolve, reject) => {
      chrome.tabCapture.capture({ audio: true, video: false }, (s) => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else if (!s) reject(new Error("No stream returned by tabCapture"));
        else resolve(s);
      });
    });

    state.mediaRecorder = new MediaRecorder(stream);
    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = () => {
      state.audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
      stream.getTracks().forEach((t) => t.stop());
      $("exportAudioButton").disabled = false;
    };
    state.mediaRecorder.start(1000);

    // Run Web Speech Recognition on the captured tab audio
    startSpeechRecognition(stream);
    setRecordingUi(true);
    setBanner("recording", "Tab audio capture active",
      "All voices in the active tab are being transcribed. Speaker names not available in this mode.");
    $("recordingNotice").textContent =
      "Capturing all audio from the active tab (all voices, no speaker names).";
  } catch (err) {
    setBanner("error", "Tab capture failed", err.message);
    $("recordingNotice").textContent = `Tab capture failed: ${err.message}`;
  }
}

// ── Microphone mode ───────────────────────────────────────────────────────────
async function startMicMode() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.mediaRecorder = new MediaRecorder(stream);
    state.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size) state.audioChunks.push(e.data);
    };
    state.mediaRecorder.onstop = () => {
      state.audioBlob = new Blob(state.audioChunks, { type: "audio/webm" });
      stream.getTracks().forEach((t) => t.stop());
      $("exportAudioButton").disabled = false;
    };
    state.mediaRecorder.start(1000);
    startSpeechRecognition(stream);
    setRecordingUi(true);
    setBanner("recording", "Microphone active",
      "Recording YOUR voice only. Use Google Meet Captions mode to capture all participants.");
    $("recordingNotice").textContent = "Recording your microphone.";
  } catch (err) {
    setBanner("error", "Microphone access denied", err.message);
    $("recordingNotice").textContent =
      `Microphone error: ${err.message}. Use the Options page to grant permission.`;
  }
}

// ── Web Speech Recognition (shared for Mic and Tab Audio modes) ───────────────
function startSpeechRecognition(stream) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    $("recordingNotice").textContent += " (Speech recognition not available in this browser.)";
    return;
  }
  const recog = new SpeechRecognition();
  recog.continuous      = true;
  recog.interimResults  = true;
  recog.lang            = "en-US";

  recog.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      const text   = result[0].transcript;
      if (result.isFinal) {
        addEntry(text);
      } else {
        state.interimText = text;
        renderTranscript();
      }
    }
  };
  recog.onerror = (e) => {
    if (e.error !== "no-speech")
      $("recordingNotice").textContent = `Speech recognition error: ${e.error}`;
  };
  recog.onend = () => {
    if (state.isRecording && state.recognition === recog) {
      try { recog.start(); } catch (_) {}
    }
  };
  recog.start();
  state.recognition = recog;
}

// ── Payload ───────────────────────────────────────────────────────────────────
function meetingPayload() {
  return {
    meeting: {
      title:       $("meetingTitle").value.trim() || "Untitled meeting",
      participants: $("participants").value.split(",").map((n) => n.trim()).filter(Boolean),
      startedAt:   state.startedAt ? new Date(state.startedAt).toISOString() : null,
      endedAt:     new Date().toISOString(),
    },
    transcript: state.entries,
    processing: {
      requestedOutputs: ["summary", "topics", "decisions", "action_items", "unresolved_issues"],
      source:   "meeting-minutes-companion",
      version:  "1.0.0",
    },
  };
}

// ── Download helper ───────────────────────────────────────────────────────────
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  if (typeof chrome !== "undefined" && chrome.downloads) {
    chrome.downloads.download({ url, filename, saveAs: true }, () =>
      URL.revokeObjectURL(url)
    );
  } else {
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

// ── Results rendering ─────────────────────────────────────────────────────────
function renderResults(data) {
  resultsSection.style.display = "block";
  resultsSummary.textContent = `Meeting ID: ${data.meeting_id} • ${data.total_tasks || 0} tasks`;

  const graph      = data.graph_analysis || {};
  const isFeasible = graph.is_feasible !== false;
  const bottlenecks = graph.bottlenecks || [];
  const circular   = graph.circular_dependencies || [];

  let gHtml = `<div class="${isFeasible ? "graph-feasible" : "graph-infeasible"}">
    ${isFeasible ? "✓ Schedule Feasible – No Circular Deadlocks" : "⚠ Infeasible – Circular Dependency Detected!"}
  </div>`;

  if (circular.length) {
    gHtml += `<p style="font-size:12px;color:#b91c1c;margin:4px 0"><strong>Cycle:</strong>
      ${circular.map((c) => `${c.from} ➔ ${c.to}`).join(", ")}</p>`;
  }

  if (bottlenecks.length) {
    gHtml += `<div style="margin-top:6px;"><strong style="font-size:12px;">Bottlenecks:</strong>
      <ul class="bottlenecks-list">
        ${bottlenecks
          .map(
            (b) =>
              `<li><strong>${escapeHtml(b.entity_id)}</strong>
              ${b.is_external ? '<span class="badge badge-blocked">External</span>' : ""}
              → blocks <strong>${b.blocks_count}</strong> task(s)</li>`
          )
          .join("")}
      </ul></div>`;
  } else {
    gHtml += `<p style="font-size:12px;color:#64748b;margin:4px 0 0">No critical bottlenecks.</p>`;
  }
  graphCard.innerHTML = gHtml;

  const tasks = data.tasks || [];
  tasksList.innerHTML = tasks.length
    ? tasks
        .map((task) => {
          const priority = task.priority || "Medium";
          const status   = task.status   || "pending";
          const owner    = task.owner    || (task.missing_owner ? "<em>Unassigned</em>" : "—");
          const deadline = task.deadline || (task.missing_deadline ? "<em>No deadline</em>" : "—");
          const prereqs  = [
            ...(task.explicit_prerequisites  || []),
            ...(task.inferred_prerequisites  || []),
          ];
          return `<article class="task-item priority-${priority}">
            <div class="task-top">
              <span class="task-id">${escapeHtml(task.task_id)}</span>
              <div class="task-badges">
                <span class="badge badge-${priority}">${priority}</span>
                <span class="badge badge-status">${escapeHtml(status)}</span>
              </div>
            </div>
            <p class="task-desc">${escapeHtml(task.description)}</p>
            <div class="task-meta">
              <span class="task-meta-item"><strong>Owner:</strong> ${owner}</span>
              <span class="task-meta-item"><strong>Due:</strong> ${deadline}</span>
            </div>
            ${
              prereqs.length
                ? `<div class="task-prereqs"><strong>Prerequisites:</strong>
                   ${prereqs.map((p) => `<span>${escapeHtml(p)}</span>`).join("")}</div>`
                : ""
            }
          </article>`;
        })
        .join("")
    : `<p class="hint">No explicit tasks found in this transcript.</p>`;
}

// ── Process button ────────────────────────────────────────────────────────────
processButton.addEventListener("click", async () => {
  if (state.isProcessing) return;
  if (!state.entries.length) {
    processStatus.textContent = "Please capture transcript entries before processing.";
    processStatus.className   = "hint error";
    return;
  }

  const settings = await chrome.storage.local.get(["processingEndpoint"]);
  const endpoint = settings.processingEndpoint || PROCESSING_ENDPOINT_DEFAULT;

  processStatus.textContent = `Sending to ${endpoint} …`;
  processStatus.className   = "hint";
  processButton.disabled    = true;
  state.isProcessing        = true;

  try {
    const response = await fetch(endpoint, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(meetingPayload()),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try { const j = await response.json(); if (j.detail) detail = j.detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await response.json();
    processStatus.textContent = `✓ Done! ${data.total_tasks || 0} tasks extracted.`;
    processStatus.className   = "hint success";
    renderResults(data);
  } catch (err) {
    processStatus.textContent = `Failed: ${err.message}`;
    processStatus.className   = "hint error";
  } finally {
    processButton.disabled = false;
    state.isProcessing     = false;
  }
});

// ── Misc event listeners ──────────────────────────────────────────────────────
$("clearButton").addEventListener("click", () => {
  if (state.isRecording) return;
  state.entries = [];
  renderTranscript();
});

$("clearResultsButton").addEventListener("click", () => {
  resultsSection.style.display = "none";
});

$("exportJsonButton").addEventListener("click", () => {
  downloadBlob(
    new Blob([JSON.stringify(meetingPayload(), null, 2)], { type: "application/json" }),
    "meeting-data.json"
  );
});

$("exportAudioButton").addEventListener("click", () => {
  if (state.audioBlob) downloadBlob(state.audioBlob, "meeting-recording.webm");
});

// Manual entry
$("manualEntryToggle").addEventListener("click", () => {
  const box = $("manualInputBox");
  box.style.display = box.style.display === "none" ? "block" : "none";
  if (box.style.display === "block") $("manualText").focus();
});

$("addManualBtn").addEventListener("click", () => {
  const text = $("manualText").value.trim();
  if (text) {
    addEntry(text, formatTime(elapsed()));
    $("manualText").value = "";
    $("manualInputBox").style.display = "none";
  }
});

// Demo transcript (uses realistic multi-speaker format)
$("sampleButton").addEventListener("click", () => {
  $("meetingTitle").value  = "Sprint Planning Sync";
  $("participants").value  = "Alice, Bob, Charlie";
  state.entries = [
    { timestamp: "00:05", text: "Alice: Welcome everyone. Bob, please build the API database migration by Wednesday.", source: "demo" },
    { timestamp: "00:22", text: "Bob: Sure, but Charlie has to finish the schema definition first.", source: "demo" },
    { timestamp: "00:38", text: "Charlie: I can finish the schema definition by tomorrow evening.", source: "demo" },
    { timestamp: "00:51", text: "Alice: Great. We are also waiting on AWS support to approve our quota before deployment.", source: "demo" },
    { timestamp: "01:10", text: "Bob: Once Alice approves the architecture doc, I can begin the performance testing.", source: "demo" },
  ];
  renderTranscript();
});

// ── Background keepalive heartbeat ───────────────────────────────────────────
// Chrome MV3 service workers terminate after ~30 s of inactivity.
// We ping the background every 25 s to prevent that.
function keepBackgroundAlive() {
  chrome.runtime.sendMessage({ type: "keepalive" }).catch(() => {
    // Background SW was already terminated — reconnect port
    connectToBackground();
  });
}
setInterval(keepBackgroundAlive, 25000);

// ── Init ──────────────────────────────────────────────────────────────────────
connectToBackground();
renderTranscript();
