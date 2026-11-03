(() => {
  const promptId = "meeting-minutes-capture-prompt";
  if (document.getElementById(promptId)) return;

  const prompt = document.createElement("div");
  prompt.id = promptId;
  prompt.style.cssText = [
    "position:fixed",
    "top:16px",
    "right:16px",
    "z-index:2147483647",
    "background:#ffffff",
    "border:1px solid #d5daf8",
    "border-radius:10px",
    "box-shadow:0 4px 18px #17203344",
    "color:#172033",
    "font:14px system-ui,sans-serif",
    "padding:14px",
    "width:290px",
  ].join(";");
  prompt.innerHTML = `
    <strong>Meeting Minutes Companion</strong>
    <p style="margin:8px 0">Google Meet detected. Open the recorder?</p>
    <button id="meeting-minutes-open" style="background:#5568d9;border:0;border-radius:6px;color:#fff;cursor:pointer;font-weight:700;padding:8px 10px">Open recorder</button>
    <button id="meeting-minutes-dismiss" style="background:transparent;border:0;color:#5568d9;cursor:pointer;margin-left:6px;padding:8px">Not now</button>
  `;
  document.documentElement.appendChild(prompt);
  prompt.querySelector("#meeting-minutes-open").addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "open-recorder" });
    prompt.remove();
  });
  prompt.querySelector("#meeting-minutes-dismiss").addEventListener("click", () => prompt.remove());

})();
