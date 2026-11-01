const endpoint = document.getElementById("endpoint");
const status = document.getElementById("status");

chrome.storage.local.get(["processingEndpoint"]).then((settings) => {
  endpoint.value = settings.processingEndpoint || "";
});

document.getElementById("save").addEventListener("click", async () => {
  const value = endpoint.value.trim();
  if (value && !/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?(?:\/|$)/.test(value)) {
    status.textContent = "For safety, use an HTTP(S) localhost endpoint.";
    return;
  }
  await chrome.storage.local.set({ processingEndpoint: value });
  status.textContent = "Settings saved.";
});
