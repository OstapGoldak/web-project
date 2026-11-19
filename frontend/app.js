const MAX_N = 300;
let currentId = null;
let eta = null;
let timer = null;
let historyTimer = null;
let adminTimer = null;
let token = localStorage.getItem("token");

const $ = (id) => document.getElementById(id);

async function api(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401 && token) logout();
  return res;
}

function showApp() {
  $("auth").hidden = true;
  $("app").hidden = false;
  $("user").textContent = localStorage.getItem("username") || "";
  buildMatrix();
  loadHistory();
  loadMe();
  clearInterval(historyTimer);
  historyTimer = setInterval(loadHistory, 1500);
}

function showAuth() {
  $("auth").hidden = false;
  $("app").hidden = true;
}

function logout() {
  token = null;
  currentId = null;
  clearInterval(timer);
  clearInterval(historyTimer);
  clearInterval(adminTimer);
  localStorage.removeItem("token");
  localStorage.removeItem("username");
  $("current").textContent = "Немає активної задачі";
  $("bar").value = 0;
  $("admin").hidden = true;
  showAuth();
}

function authError(json) {
  return typeof json.detail === "string"
    ? json.detail
    : "Логін від 3 символів, пароль від 4";
}

async function sendCredentials(path) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: $("username").value.trim(),
      password: $("password").value,
    }),
  });
}

async function login() {
  const res = await sendCredentials("/login");
  const json = await res.json();
  if (!res.ok) {
    $("auth-error").textContent = authError(json);
    return;
  }
  token = json.access_token;
  localStorage.setItem("token", token);
  localStorage.setItem("username", $("username").value.trim());
  $("auth-error").textContent = "";
  $("password").value = "";
  showApp();
}

async function register() {
  const res = await sendCredentials("/register");
  if (!res.ok) {
    $("auth-error").textContent = authError(await res.json());
    return;
  }
  await login();
}

function buildMatrix() {
  const n = parseInt($("n").value);
  if (!n || n < 1 || n > MAX_N) {
    $("error").textContent = `n має бути від 1 до ${MAX_N}`;
    return;
  }
  $("error").textContent = "";
  let html = "";
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) html += `<input type="number" class="a" value="0" step="any">`;
    html += ` | <input type="number" class="b" value="0" step="any"><br>`;
  }
  $("matrix").innerHTML = html;
}

function fillRandom() {
  document.querySelectorAll("#matrix input").forEach((el) => {
    el.value = (Math.random() * 10 - 5).toFixed(2);
  });
}

function readMatrix() {
  const n = parseInt($("n").value);
  const av = [...document.querySelectorAll("#matrix .a")].map((e) => parseFloat(e.value));
  const bv = [...document.querySelectorAll("#matrix .b")].map((e) => parseFloat(e.value));
  if (av.length !== n * n || bv.length !== n || [...av, ...bv].some(isNaN)) return null;
  const a = [];
  for (let i = 0; i < n; i++) a.push(av.slice(i * n, (i + 1) * n));
  return { a, b: bv };
}

async function submitTask() {
  $("error").textContent = "";
  const data = readMatrix();
  if (!data) {
    $("error").textContent = "Заповніть усі поля матриці числами";
    return;
  }
  if (data.a.length > MAX_N) {
    $("error").textContent = `Максимум ${MAX_N} невідомих`;
    return;
  }
  const res = await api("/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const json = await res.json();
  if (!res.ok) {
    $("error").textContent = typeof json.detail === "string" ? json.detail : "Некоректні дані";
    return;
  }
  currentId = json.id;
    eta = json.queued ? json.eta_seconds : null;
  startPolling();
  loadHistory();
}

function startPolling() {
  clearInterval(timer);
  timer = setInterval(pollCurrent, 700);
  pollCurrent();
}

async function pollCurrent() {
  if (!currentId) return;
  const res = await api(`/tasks/${currentId}`);
  if (!res.ok) return;
  const t = await res.json();
  $("bar").value = t.progress;
  let text = `Задача #${t.id} (${t.worker}): ${t.status}, ${t.progress}%`;
    if (t.status === "queued") {
    text += eta !== null
      ? `. Усі сервери зайняті, задачу поставлено в чергу. Орієнтовне очікування: ~${eta} с`
      : ". У черзі";
  }
  if (t.status === "done") text += `. Розв'язок: [${t.result.x.map((v) => v.toFixed(4)).join(", ")}]`;
  if (t.status === "failed") text += `. Помилка: ${t.error}`;
  $("current").textContent = text;
  $("cancel").disabled = !["queued", "running"].includes(t.status);
  if (!["queued", "running"].includes(t.status)) {
    clearInterval(timer);
    loadHistory();
  }
}

async function cancelTask() {
  if (!currentId) return;
  await api(`/tasks/${currentId}/cancel`, { method: "POST" });
  pollCurrent();
}

async function loadHistory() {
  const res = await api("/tasks");
  if (!res.ok) return;
  const tasks = await res.json();
    if (currentId === null) {
    const active = tasks.find((t) => ["queued", "running"].includes(t.status));
    if (active) {
      currentId = active.id;
      startPolling();
    }
  }
  const tbody = document.querySelector("#history tbody");
  tbody.innerHTML = "";
  for (const t of tasks) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${t.id}</td><td>${t.size}</td><td>${t.status}</td><td><progress value="${t.progress}" max="100"></progress> ${t.progress}%</td>
      <td>${new Date(t.created_at).toLocaleString()}</td>
      <td><button data-view="${t.id}">Дивитись</button><button data-del="${t.id}">Видалити</button></td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#history tbody").addEventListener("click", async (e) => {
  if (e.target.dataset.view) {
    currentId = parseInt(e.target.dataset.view);
    eta = null;
    startPolling();
  }
  if (e.target.dataset.del) {
    await api(`/tasks/${e.target.dataset.del}`, { method: "DELETE" });
    loadHistory();
  }
});

async function loadMe() {
  const res = await api("/me");
  if (!res.ok) return;
  const me = await res.json();
  $("user").textContent = me.username;
  if (me.is_admin) {
    $("admin").hidden = false;
    clearInterval(adminTimer);
    adminTimer = setInterval(loadAdmin, 2000);
    loadAdmin();
  }
}

async function loadAdmin() {
  const res = await api("/admin/stats");
  if (!res.ok) return;
  const s = await res.json();
  const byStatus = Object.entries(s.tasks_by_status).map(([k, v]) => `${k}: ${v}`).join(", ");
  $("admin-summary").textContent =
    `Користувачів: ${s.users}. Задач у БД: ${s.tasks_total} (${byStatus || "—"}). Максимум серверів: ${s.max_workers}`;

  document.querySelector("#workers tbody").innerHTML = s.workers.map((w) => `
    <tr>
      <td>${w.url}</td>
      <td>${w.dynamic ? "автозапуск" : "постійний"}</td>
      <td>${w.alive ? "працює" : "недоступний"}</td>
      <td>${w.active}</td><td>${w.queued}</td><td>${w.max_active}</td>
    </tr>`).join("");

  document.querySelector("#active-tasks tbody").innerHTML = s.active_tasks.length
    ? s.active_tasks.map((t) => `
      <tr><td>${t.id}</td><td>${t.user}</td><td>${t.size}</td><td>${t.status}</td>
      <td><progress value="${t.progress}" max="100"></progress> ${t.progress}%</td><td>${t.worker || "—"}</td></tr>`).join("")
    : `<tr><td colspan="6">Немає активних задач</td></tr>`;
}

$("login").onclick = login;
$("register").onclick = register;
$("logout").onclick = logout;
$("gen").onclick = buildMatrix;
$("random").onclick = fillRandom;
$("submit").onclick = submitTask;
$("cancel").onclick = cancelTask;

if (token) showApp();
else showAuth();