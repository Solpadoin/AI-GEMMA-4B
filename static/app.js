const state = {
  projects: [],
  currentProject: null,
  busy: false,
  config: null,
  settings: null,
  attachments: [],
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || response.statusText);
  }
  return response.json();
}

function roleLabel(role) {
  return { user: "Ты", assistant: "Модель", tool: "Инструмент" }[role] || role;
}

function parseGemmaContent(raw) {
  const normalized = String(raw || "").replaceAll("<channel|>", "<|end_channel|>");
  const match = normalized.match(/<\|channel\>thought\s*([\s\S]*?)<\|end_channel\|>/);
  let thinking = "";
  let answer = normalized;
  if (match) {
    thinking = match[1].trim();
    answer = normalized.slice(match.index + match[0].length);
  }
  answer = answer
    .replaceAll("<|channel>", "")
    .replaceAll("<|end_channel|>", "")
    .replaceAll("<|think|>", "")
    .trim();
  return { thinking, answer };
}

function setRunState(label) {
  $("runState").textContent = label;
}

function applySettings(settings) {
  state.settings = settings;
  $("thinkingToggle").checked = Boolean(settings.thinking);
  $("accessMode").value = settings.access_mode || "confirm";
  $("temperatureSlider").value = String(settings.temperature ?? 1);
  $("temperatureValue").textContent = Number(settings.temperature ?? 1).toFixed(2);
}

async function saveSettings(update) {
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify(update),
  });
  applySettings(settings);
  updateWorkspaceLabel();
}

function updateWorkspaceLabel() {
  if (!state.config || !state.settings) return;
  $("workspace").textContent = `${state.config.workspace_root} · ${state.settings.max_tokens} токенов · thinking ${
    state.settings.thinking ? "on" : "off"
  } · ${state.settings.access_mode}`;
}

function renderProjects() {
  $("projects").innerHTML = "";
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.className = `project ${state.currentProject?.id === project.id ? "active" : ""}`;
    button.textContent = `${project.name} (${project.messages})`;
    button.onclick = () => loadProject(project.id);
    $("projects").appendChild(button);
  }
}

function appendMessage(container, message) {
  const item = document.createElement("article");
  item.className = `message ${message.role}`;
  const role = document.createElement("span");
  role.className = "role";
  role.textContent = roleLabel(message.role);
  item.appendChild(role);

  if (message.role === "assistant") {
    const parsed = parseGemmaContent(message.content);
    if (parsed.thinking) {
      const details = document.createElement("details");
      details.className = "thinking";
      const summary = document.createElement("summary");
      summary.textContent = "Ход рассуждения";
      const body = document.createElement("pre");
      body.textContent = parsed.thinking;
      details.append(summary, body);
      item.appendChild(details);
    }
    const answer = document.createElement("div");
    answer.className = "answer";
    answer.textContent = parsed.answer || message.content || "";
    item.appendChild(answer);
  } else {
    item.append(document.createTextNode(message.content));
  }

  container.appendChild(item);
  return item;
}

function renderCurrentProject() {
  const project = state.currentProject;
  $("projectTitle").textContent = project ? project.name : "Выбери проект";
  $("messageInput").disabled = !project || state.busy;
  $("sendBtn").disabled = !project || state.busy;
  $("clearBtn").disabled = !project || state.busy;
  $("deleteBtn").disabled = !project || state.busy;

  $("messages").innerHTML = "";
  for (const message of project?.messages || []) {
    appendMessage($("messages"), message);
  }
  $("messages").scrollTop = $("messages").scrollHeight;
  renderPendingAction();
  renderAttachments();
}

function renderPendingAction() {
  const action = state.currentProject?.pending_action;
  const box = $("pendingAction");
  if (!action) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `
    <strong>Модель просит разрешение на действие: ${action.tool}</strong>
    <pre>${escapeHtml(JSON.stringify(action, null, 2))}</pre>
    <div class="pending-actions">
      <button id="approveAction">Разрешить</button>
      <button id="rejectAction">Отклонить</button>
    </div>
  `;
  $("approveAction").onclick = () => approveAction(action.id, true);
  $("rejectAction").onclick = () => approveAction(action.id, false);
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

async function refreshProjects() {
  state.projects = await api("/api/projects");
  renderProjects();
}

async function loadProject(id) {
  state.currentProject = await api(`/api/projects/${id}`);
  renderProjects();
  renderCurrentProject();
}

async function withBusy(work) {
  state.busy = true;
  setRunState("busy");
  renderCurrentProject();
  try {
    await work();
  } catch (error) {
    alert(error.message);
  } finally {
    state.busy = false;
    setRunState("idle");
    await refreshProjects();
    if (state.currentProject) {
      await loadProject(state.currentProject.id);
    } else {
      renderCurrentProject();
    }
  }
}

async function readSse(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const block of events) {
      const lines = block.split("\n");
      const event = lines.find((line) => line.startsWith("event: "))?.slice(7) || "message";
      const dataLine = lines.find((line) => line.startsWith("data: "));
      if (!dataLine) continue;
      onEvent(event, JSON.parse(dataLine.slice(6)));
    }
  }
}

function renderAttachments() {
  const box = $("attachmentList");
  box.innerHTML = "";
  for (const file of state.attachments) {
    const item = document.createElement("span");
    item.className = "attachment";
    item.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.onclick = () => {
      state.attachments = state.attachments.filter((candidate) => candidate.id !== file.id);
      renderAttachments();
    };
    item.appendChild(remove);
    box.appendChild(item);
  }
}

function publicUserMessage(message, attachments) {
  if (!attachments.length) return message;
  return `${message}\n\nПрикреплено: ${attachments.map((file) => file.name).join(", ")}`;
}

async function streamChat(message) {
  if (!state.currentProject) return;
  state.busy = true;
  setRunState("streaming");
  $("messageInput").disabled = true;
  $("sendBtn").disabled = true;

  const attachments = state.attachments;
  state.attachments = [];
  renderAttachments();

  state.currentProject.messages.push({ role: "user", content: publicUserMessage(message, attachments) });
  renderCurrentProject();
  const assistantNode = appendMessage($("messages"), { role: "assistant", content: "" });
  const answerNode = assistantNode.querySelector(".answer");

  try {
    const response = await fetch(`/api/projects/${state.currentProject.id}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, attachments }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || response.statusText);
    }

    await readSse(response, (event, data) => {
      if (event === "token") {
        answerNode.textContent = data.answer || "";
        $("messages").scrollTop = $("messages").scrollHeight;
      }
      if (event === "error") {
        throw new Error(data.detail || "Generation failed");
      }
      if (event === "done") {
        state.currentProject = data;
      }
      if (event === "tool") {
        setRunState("tool");
      }
    });
  } finally {
    state.busy = false;
    setRunState("idle");
    await refreshProjects();
    if (state.currentProject) await loadProject(state.currentProject.id);
  }
}

$("projectForm").onsubmit = (event) => {
  event.preventDefault();
  const name = $("projectName").value.trim();
  if (!name) return;
  withBusy(async () => {
    const project = await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    $("projectName").value = "";
    await loadProject(project.id);
  });
};

$("chatForm").onsubmit = (event) => {
  event.preventDefault();
  const message = $("messageInput").value.trim();
  if (!message || !state.currentProject) return;
  $("messageInput").value = "";
  streamChat(message).catch((error) => {
    state.busy = false;
    setRunState("idle");
    alert(error.message);
    renderCurrentProject();
  });
};

$("messageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("chatForm").requestSubmit();
  }
});

$("fileInput").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files || []);
  for (const file of files) {
    const content = await file.text();
    state.attachments.push({
      id: crypto.randomUUID(),
      name: file.name,
      type: file.type || "text/plain",
      content,
    });
  }
  $("fileInput").value = "";
  renderAttachments();
});

$("thinkingToggle").addEventListener("change", (event) => {
  saveSettings({ thinking: event.target.checked }).catch((error) => alert(error.message));
});

$("accessMode").addEventListener("change", (event) => {
  const value = event.target.value;
  if (value === "auto_all") {
    const ok = confirm("Полный авто-доступ разрешит модели писать файлы и запускать команды без подтверждения. Включить?");
    if (!ok) {
      $("accessMode").value = state.settings.access_mode || "confirm";
      return;
    }
  }
  saveSettings({ access_mode: value }).catch((error) => alert(error.message));
});

$("temperatureSlider").addEventListener("input", (event) => {
  $("temperatureValue").textContent = Number(event.target.value).toFixed(2);
});

$("temperatureSlider").addEventListener("change", (event) => {
  saveSettings({ temperature: Number(event.target.value) }).catch((error) => alert(error.message));
});

$("clearBtn").onclick = () => {
  if (!state.currentProject || !confirm("Очистить контекст проекта?")) return;
  withBusy(async () => {
    state.currentProject = await api(`/api/projects/${state.currentProject.id}/clear`, { method: "POST" });
  });
};

$("deleteBtn").onclick = () => {
  if (!state.currentProject || !confirm("Удалить проект и его контекст?")) return;
  withBusy(async () => {
    const id = state.currentProject.id;
    state.currentProject = null;
    await api(`/api/projects/${id}`, { method: "DELETE" });
  });
};

async function approveAction(actionId, approved) {
  if (!state.currentProject) return;
  await withBusy(async () => {
    state.currentProject = await api(`/api/projects/${state.currentProject.id}/actions/${actionId}`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    });
  });
}

async function init() {
  state.config = await api("/api/config");
  applySettings(state.config.settings);
  const modelLabel = state.config.model_name || state.config.model_path.split(/[\\/]/).pop();
  $("modelState").textContent = state.config.llama_server_url
    ? `Сервер: ${modelLabel}`
    : state.config.model_exists
      ? `Файл: ${modelLabel}`
      : "Модель не загружена";
  updateWorkspaceLabel();
  await refreshProjects();
  if (state.projects[0]) {
    await loadProject(state.projects[0].id);
  } else {
    renderCurrentProject();
  }
}

init().catch((error) => alert(error.message));
