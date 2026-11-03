/**
 * background.js
 *
 * Fixes:
 *  - Chrome MV3 service workers terminate after ~30 s of inactivity.
 *    We use chrome.alarms (fires every 25 s) to keep the SW alive indefinitely.
 *  - Side-panel port management: any message from the content script is
 *    forwarded to the connected side panel.
 */

// ── Keep-alive alarm (prevents SW from going idle) ────────────────────────────
chrome.alarms.create("sw-keepalive", { periodInMinutes: 0.4 }); // ~24 s
chrome.alarms.onAlarm.addListener(() => {
  /* Just receiving the alarm event keeps the SW alive. */
});

// ── Side Panel Behaviour ──────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  chrome.alarms.create("sw-keepalive", { periodInMinutes: 0.4 });
});

// ── Port from Side Panel ──────────────────────────────────────────────────────
let sidePanelPort = null;

chrome.runtime.onConnect.addListener((port) => {
  if (port.name === "sidepanel") {
    sidePanelPort = port;
    port.onDisconnect.addListener(() => { sidePanelPort = null; });
    // Immediately check if there's a live Meet tab
    checkActiveMeetTab();
  }
});

function pushToPanel(message) {
  if (sidePanelPort) {
    try { sidePanelPort.postMessage(message); } catch (_) {}
  }
}

// ── Google Meet tab detection ─────────────────────────────────────────────────
function isGoogleMeetCallUrl(url) {
  return /^https:\/\/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:[/?#]|$)/i.test(url || "");
}

function checkActiveMeetTab() {
  chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
    const tab = tabs[0];
    if (tab && isGoogleMeetCallUrl(tab.url)) {
      pushToPanel({ type: "meet-tab-detected", tabId: tab.id, title: tab.title });
    } else {
      pushToPanel({ type: "meet-tab-none" });
    }
  });
}

chrome.tabs.onActivated.addListener(() => checkActiveMeetTab());
chrome.tabs.onUpdated.addListener((_, changeInfo) => {
  if (changeInfo.status === "complete") checkActiveMeetTab();
});

// ── Relay: content script → side panel ───────────────────────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message?.type) return;
  switch (message.type) {
    case "meet-caption":
    case "meet-captions-ready":
    case "meet-captions-enable-prompt":
      pushToPanel(message);
      break;
    case "keepalive":
      // Side panel pings us; just respond so the SW stays registered.
      sendResponse({ alive: true });
      break;
  }
  // Return true only when we intend to use sendResponse asynchronously.
  return false;
});
