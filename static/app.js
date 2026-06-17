const state = {
  projects: [],
  currentProject: null,
  busy: false,
  config: null,
  settings: null,
  capabilities: null,
  attachments: [],
  stickToBottom: true,
};

const TEXT_EXTENSIONS = new Set([
  "txt",
  "md",
  "json",
  "js",
  "ts",
  "tsx",
  "jsx",
  "html",
  "css",
  "py",
  "ps1",
  "bat",
  "cmd",
  "yml",
  "yaml",
  "xml",
  "csv",
  "log",
  "sma",
  "ini",
  "cfg",
]);

const $ = (id) => document.getElementById(id);

function formatError(error) {
  if (!error) return "Unknown error";
  if (typeof error === "string") return error;
  if (Array.isArray(error)) return error.map(formatError).join("\n");
  if (error.detail) return formatError(error.detail);
  if (error.msg) {
    const path = Array.isArray(error.loc) ? error.loc.join(".") : "";
    return path ? `${path}: ${error.msg}` : error.msg;
  }
  if (error.message) return String(error.message);
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(formatError(body.detail || body || response.statusText));
  }
  return response.json();
}

function roleLabel(role) {
  return { user: "Ты", assistant: "Модель", tool: "Инструмент" }[role] || role;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

function stripAgentAction(text) {
  return String(text || "").replace(/```agent_action\s*\{[\s\S]*?\}\s*```/g, "").trim();
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
  answer = stripAgentAction(
    answer
      .replaceAll("<|channel>", "")
      .replaceAll("<|end_channel|>", "")
      .replaceAll("<|think|>", "")
      .trim(),
  );
  return { thinking, answer };
}

function renderInlineMarkdown(text) {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function markdownToHtml(markdown) {
  const blocks = [];
  const text = stripAgentAction(markdown || "");
  const parts = text.split(/(```[\s\S]*?```)/g);

  for (const part of parts) {
    if (!part) continue;
    if (part.startsWith("```")) {
      const code = part.replace(/^```[a-zA-Z0-9_-]*\n?/, "").replace(/```$/, "");
      blocks.push(`<pre><code>${escapeHtml(code.trimEnd())}</code></pre>`);
      continue;
    }

    const lines = part.split(/\r?\n/);
    let list = [];
    const flushList = () => {
      if (!list.length) return;
      blocks.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      list = [];
    };

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line.trim()) {
        flushList();
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushList();
        blocks.push(`<h${heading[1].length}>${renderInlineMarkdown(heading[2])}</h${heading[1].length}>`);
        continue;
      }
      const bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/);
      if (bullet) {
        list.push(bullet[1]);
        continue;
      }
      flushList();
      blocks.push(`<p>${renderInlineMarkdown(line)}</p>`);
    }
    flushList();
  }

  return blocks.join("");
}

function setMarkdown(node, markdown) {
  node.innerHTML = markdownToHtml(markdown);
}

function setRunState(label) {
  $("runState").textContent = label;
}

function applySettings(settings) {
  state.settings = settings;
  $("agentModeToggle").checked = Boolean(settings.agent_mode);
  $("thinkingToggle").checked = Boolean(settings.thinking);
  $("webSearchToggle").checked = Boolean(settings.web_search);
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
  $("workspace").textContent = `${state.config.workspace_root} · ${state.settings.max_tokens} токенов · agent ${state.settings.agent_mode ? "on" : "off"} · thinking ${state.settings.thinking ? "on" : "off"} · search ${state.settings.web_search ? "on" : "off"} · ${state.settings.access_mode}`;
}

function shouldStickToBottom() {
  const box = $("messages");
  return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
}

function scrollToBottom(force = false) {
  if (!force && !state.stickToBottom) return;
  const box = $("messages");
  box.scrollTop = box.scrollHeight;
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

function renderCapabilities() {
  const box = $("capabilities");
  if (!box || !state.capabilities) return;
  const labels = {
    text_files: "Files",
    shell: "OS",
    browser: "Browser",
    web_search: "Search",
    image_metadata: "Images",
    vision: "Vision",
  };
  box.innerHTML = Object.entries(labels)
    .map(([key, label]) => {
      const enabled = Boolean(state.capabilities[key]?.enabled);
      return `<span class="cap ${enabled ? "on" : "off"}">${label}</span>`;
    })
    .join("");
}

function appendMessage(container, message) {
  if (message.hidden || message.role === "tool") return null;

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
    answer.className = "answer markdown";
    setMarkdown(answer, parsed.answer || "Выполняю действие...");
    item.appendChild(answer);
  } else {
    const body = document.createElement("div");
    body.className = "answer";
    body.textContent = message.content;
    item.appendChild(body);
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
  $("compactBtn").disabled = !project || state.busy;
  $("deleteBtn").disabled = !project || state.busy;

  $("messages").innerHTML = "";
  for (const message of project?.messages || []) {
    appendMessage($("messages"), message);
  }
  scrollToBottom(true);
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
    <strong>Модель просит разрешение на действие: ${escapeHtml(action.tool)}</strong>
    <pre>${escapeHtml(JSON.stringify(action, null, 2))}</pre>
    <div class="pending-actions">
      <button id="approveAction">Разрешить</button>
      <button id="rejectAction">Отклонить</button>
    </div>
  `;
  $("approveAction").onclick = () => approveAction(action.id, true);
  $("rejectAction").onclick = () => approveAction(action.id, false);
}

function showActivity(text) {
  const box = $("activity");
  box.classList.remove("hidden");
  box.textContent = text;
}

function hideActivity() {
  $("activity").classList.add("hidden");
  $("activity").textContent = "";
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
    alert(formatError(error));
  } finally {
    state.busy = false;
    setRunState("idle");
    hideActivity();
    await refreshProjects();
    if (state.currentProject) await loadProject(state.currentProject.id);
    else renderCurrentProject();
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
    item.textContent = file.binary ? `${file.name} (файл)` : file.name;
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
  let assistantNode = appendMessage($("messages"), { role: "assistant", content: "" });
  let answerNode = assistantNode.querySelector(".answer");
  let thinkingNode = null;
  scrollToBottom(true);

  try {
    const response = await fetch(`/api/projects/${state.currentProject.id}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, attachments }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(formatError(body.detail || body || response.statusText));
    }

    await readSse(response, (event, data) => {
      if (event === "token") {
        if (data.thinking) {
          if (!thinkingNode) {
            const details = document.createElement("details");
            details.className = "thinking";
            details.open = true;
            const summary = document.createElement("summary");
            summary.textContent = "Ход рассуждения";
            const body = document.createElement("pre");
            details.append(summary, body);
            assistantNode.insertBefore(details, answerNode);
            thinkingNode = body;
          }
          thinkingNode.textContent = data.thinking;
        }
        setMarkdown(answerNode, data.answer || "Выполняю действие...");
        scrollToBottom();
      }
      if (event === "tool") {
        const label = data.tool === "search_web" ? "Поиск" : "Инструмент";
        showActivity(`${label}: ${data.summary || data.tool}`);
        setRunState(`${data.tool}: ${data.status}`);
        assistantNode = appendMessage($("messages"), { role: "assistant", content: "" });
        answerNode = assistantNode.querySelector(".answer");
        thinkingNode = null;
        scrollToBottom();
      }
      if (event === "action_required") {
        setRunState("approval needed");
      }
      if (event === "error") {
        throw new Error(formatError(data.detail || "Generation failed"));
      }
      if (event === "done") {
        state.currentProject = data;
      }
    });
  } finally {
    state.busy = false;
    setRunState("idle");
    hideActivity();
    await refreshProjects();
    if (state.currentProject) await loadProject(state.currentProject.id);
  }
}

$("messages").addEventListener("scroll", () => {
  state.stickToBottom = shouldStickToBottom();
});

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
    hideActivity();
    alert(formatError(error));
    renderCurrentProject();
  });
};

$("messageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("chatForm").requestSubmit();
  }
});

function isTextFile(file) {
  if (file.type.startsWith("text/")) return true;
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  return TEXT_EXTENSIONS.has(extension);
}

$("fileInput").addEventListener("change", async (event) => {
  const files = Array.from(event.target.files || []);
  for (const file of files) {
    if (!isTextFile(file)) {
      state.attachments.push({
        id: crypto.randomUUID(),
        name: file.name,
        type: file.type || "application/octet-stream",
        size: file.size,
        binary: true,
        content: "",
      });
      continue;
    }
    const content = await file.text();
    state.attachments.push({
      id: crypto.randomUUID(),
      name: file.name,
      type: file.type || "text/plain",
      size: file.size,
      content,
    });
  }
  $("fileInput").value = "";
  renderAttachments();
});

$("agentModeToggle").addEventListener("change", (event) => {
  saveSettings({ agent_mode: event.target.checked }).catch((error) => alert(formatError(error)));
});

$("thinkingToggle").addEventListener("change", (event) => {
  saveSettings({ thinking: event.target.checked }).catch((error) => alert(formatError(error)));
});

$("webSearchToggle").addEventListener("change", (event) => {
  saveSettings({ web_search: event.target.checked }).catch((error) => alert(formatError(error)));
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
  saveSettings({ access_mode: value }).catch((error) => alert(formatError(error)));
});

$("temperatureSlider").addEventListener("input", (event) => {
  $("temperatureValue").textContent = Number(event.target.value).toFixed(2);
});

$("temperatureSlider").addEventListener("change", (event) => {
  saveSettings({ temperature: Number(event.target.value) }).catch((error) => alert(formatError(error)));
});

$("compactBtn").onclick = () => {
  if (!state.currentProject) return;
  withBusy(async () => {
    showActivity("Сжимаю старую переписку в краткий контекст...");
    state.currentProject = await api(`/api/projects/${state.currentProject.id}/compact`, { method: "POST" });
  });
};

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
  state.capabilities = await api("/api/capabilities");
  applySettings(state.config.settings);
  const modelLabel = state.config.model_name || state.config.model_path.split(/[\\/]/).pop();
  $("modelState").textContent = state.config.llama_server_url
    ? `Сервер: ${modelLabel}`
    : state.config.model_exists
      ? `Файл: ${modelLabel}`
      : "Модель не загружена";
  updateWorkspaceLabel();
  renderCapabilities();
  await refreshProjects();
  if (state.projects[0]) await loadProject(state.projects[0].id);
  else renderCurrentProject();
}

init().catch((error) => alert(formatError(error)));

