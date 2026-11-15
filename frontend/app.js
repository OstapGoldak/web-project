const MAX_N = 300;
let currentId = null;
let timer = null;

const $ = (id) => document.getElementById(id);

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
  const res = await fetch("/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const json = await res.json();
  if (!res.ok) {
    $("error").textContent = json.detail;
    return;
  }
  currentId = json.id;
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
  const res = await fetch(`/tasks/${currentId}`);
  if (!res.ok) return;
  const t = await res.json();
  $("bar").value = t.progress;
  let text = `Задача #${t.id}: ${t.status}, ${t.progress}%`;
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
  await fetch(`/tasks/${currentId}/cancel`, { method: "POST" });
  pollCurrent();
}

async function loadHistory() {
  const res = await fetch("/tasks");
  const tasks = await res.json();
  const tbody = document.querySelector("#history tbody");
  tbody.innerHTML = "";
  for (const t of tasks) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${t.id}</td><td>${t.size}</td><td>${t.status}</td><td>${t.progress}%</td>
      <td>${new Date(t.created_at).toLocaleString()}</td>
      <td><button data-view="${t.id}">Дивитись</button><button data-del="${t.id}">Видалити</button></td>`;
    tbody.appendChild(tr);
  }
}

document.querySelector("#history tbody").addEventListener("click", async (e) => {
  if (e.target.dataset.view) {
    currentId = parseInt(e.target.dataset.view);
    startPolling();
  }
  if (e.target.dataset.del) {
    await fetch(`/tasks/${e.target.dataset.del}`, { method: "DELETE" });
    loadHistory();
  }
});

$("gen").onclick = buildMatrix;
$("random").onclick = fillRandom;
$("submit").onclick = submitTask;
$("cancel").onclick = cancelTask;

buildMatrix();
loadHistory();