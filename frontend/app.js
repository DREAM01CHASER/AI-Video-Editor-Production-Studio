const healthStatus = document.querySelector("#health-status");
const healthMessage = document.querySelector("#health-message");

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`Backend returned HTTP ${response.status}`);
    }

    const data = await response.json();
    healthStatus.textContent = "Ready";
    healthStatus.className = "status status-ready";
    healthMessage.textContent = `${data.service} is running locally. Workspace: ${data.workspace}.`;
  } catch (error) {
    healthStatus.textContent = "Unavailable";
    healthStatus.className = "status status-error";
    healthMessage.textContent = "The local backend could not be reached. Start it with the command in README.md.";
    console.error(error);
  }
}

checkHealth();
