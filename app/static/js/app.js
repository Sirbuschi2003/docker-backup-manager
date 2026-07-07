// Docker Backup Manager - frontend (vanilla JS, no build step required)
const root = document.getElementById("app");
const state = { route: "dashboard", user: null, jobs: {} };

// ---------- API helper ----------
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (res.status === 401) {
    render(loginScreen());
    throw new Error("not authenticated");
  }
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    throw new Error((data && data.detail) || `Request failed (${res.status})`);
  }
  return data;
}

function fmtBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}
function fmtDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleString();
}
function fmtDuration(sec) {
  if (sec == null) return "-";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}

// ---------- toasts ----------
let toastStack;
function toast(message, type = "ok") {
  if (!toastStack) {
    toastStack = document.createElement("div");
    toastStack.className = "toast-stack";
    document.body.appendChild(toastStack);
  }
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  toastStack.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

// ---------- global job tray (always visible, on every page) ----------
let jobTrayEl;
let settingsClockTimer = null;
const toastedJobIds = new Set();
const finishedJobHideAt = new Map(); // jobId -> timestamp when it should be removed from the tray

function ensureJobTray() {
  if (!jobTrayEl) {
    jobTrayEl = document.createElement("div");
    jobTrayEl.className = "job-tray";
    document.body.appendChild(jobTrayEl);
  }
  return jobTrayEl;
}

function _jobStatusBadge(status) {
  if (status === "running") return { cls: "running", label: "läuft" };
  if (status === "cancelling") return { cls: "running", label: "wird abgebrochen…" };
  if (status === "cancelled") return { cls: "neutral", label: "abgebrochen" };
  if (status === "success") return { cls: "ok", label: "fertig" };
  return { cls: "failed", label: "fehlgeschlagen" };
}

function renderJobCard(job) {
  const { cls, label } = _jobStatusBadge(job.status);
  const card = h(`
    <div class="card job-card" data-job-id="${job.id}">
      <div class="job-title"><span>${job.kind === "backup" ? "💾" : "♻️"} ${job.label}</span>
        <span class="badge ${cls}">${label}</span></div>
      <div class="muted job-step" style="font-size:.8rem">${job.step_name}</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div style="width:${job.percent}%"></div></div>
        <div class="progress-meta">
          <span class="job-step-count">Schritt ${job.current_step}/${job.total_steps}</span>
          <span class="job-elapsed">${job.status === "running"
            ? "Verstrichen: " + fmtDuration(job.elapsed_seconds) + (job.eta_seconds != null ? " · ETA " + fmtDuration(job.eta_seconds) : "")
            : fmtDuration(job.elapsed_seconds)}</span>
        </div>
      </div>
      ${job.error ? `<div class="error-msg">${job.error}</div>` : ""}
      ${job.cancellable ? `<div class="row-actions" style="margin-top:8px;"><button type="button" class="btn danger job-cancel-btn" style="padding:4px 10px; font-size:.8rem;">Abbrechen</button></div>` : ""}
    </div>
  `);
  _wireJobCancelButton(card, job.id);
  return card;
}

function _wireJobCancelButton(card, jobId) {
  const btn = card.querySelector(".job-cancel-btn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Breche ab…";
    try {
      await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    } catch (e) {
      toast(e.message, "error");
      btn.disabled = false;
      btn.textContent = "Abbrechen";
    }
  });
}

function updateJobCard(card, job) {
  const { cls, label } = _jobStatusBadge(job.status);
  const badge = card.querySelector(".badge");
  badge.className = `badge ${cls}`;
  badge.textContent = label;
  card.querySelector(".job-step").textContent = job.step_name;
  card.querySelector(".progress-bar > div").style.width = `${job.percent}%`;
  card.querySelector(".job-step-count").textContent = `Schritt ${job.current_step}/${job.total_steps}`;
  card.querySelector(".job-elapsed").textContent = job.status === "running"
    ? "Verstrichen: " + fmtDuration(job.elapsed_seconds) + (job.eta_seconds != null ? " · ETA " + fmtDuration(job.eta_seconds) : "")
    : fmtDuration(job.elapsed_seconds);
  const existingError = card.querySelector(".error-msg");
  if (job.error && !existingError) {
    card.appendChild(h(`<div class="error-msg">${job.error}</div>`));
  } else if (job.error && existingError) {
    existingError.textContent = job.error;
  } else if (!job.error && existingError) {
    existingError.remove();
  }
  const existingCancelWrap = card.querySelector(".job-cancel-btn")?.closest(".row-actions");
  if (job.cancellable && !existingCancelWrap) {
    const wrap = h(`<div class="row-actions" style="margin-top:8px;"><button type="button" class="btn danger job-cancel-btn" style="padding:4px 10px; font-size:.8rem;">Abbrechen</button></div>`);
    card.appendChild(wrap);
    _wireJobCancelButton(card, job.id);
  } else if (!job.cancellable && existingCancelWrap) {
    existingCancelWrap.remove();
  }
}

function _syncJobCardsInContainer(container, jobs, emptyMessage) {
  if (!jobs.length) {
    if (emptyMessage && !container.querySelector(".empty-state")) {
      container.innerHTML = `<div class="empty-state">${emptyMessage}</div>`;
    }
    return;
  }
  const emptyState = container.querySelector(".empty-state");
  if (emptyState) emptyState.remove();
  const visibleIds = new Set(jobs.map((j) => String(j.id)));
  container.querySelectorAll(".job-card").forEach((card) => {
    if (!visibleIds.has(card.dataset.jobId)) card.remove();
  });
  jobs.forEach((job) => {
    const existing = container.querySelector(`.job-card[data-job-id="${job.id}"]`);
    if (existing) updateJobCard(existing, job);
    else container.appendChild(renderJobCard(job));
  });
}

// Single shared poll for both the floating tray (every page) and the
// Dashboard's "Letzte Jobs" list (only patched if that element is currently
// on screen) - one /api/jobs request per tick, not one per consumer.
async function pollGlobalJobs() {
  let jobs;
  try {
    jobs = (await api("/api/jobs")).jobs;
  } catch (e) { return; }

  const now = Date.now();
  for (const job of jobs) {
    if (job.status !== "running") {
      if (!toastedJobIds.has(job.id)) {
        toastedJobIds.add(job.id);
        toast(`${job.label}: ${job.status === "success" ? "erfolgreich abgeschlossen" : "fehlgeschlagen – " + job.error}`,
              job.status === "success" ? "ok" : "error");
        finishedJobHideAt.set(job.id, now + 4000);
      }
    }
  }

  const trayVisible = jobs.filter((j) => j.status === "running" ||
    (finishedJobHideAt.has(j.id) && finishedJobHideAt.get(j.id) > now)).slice(0, 5);
  _syncJobCardsInContainer(ensureJobTray(), trayVisible, null);

  const dashboardContainer = document.getElementById("jobs-container");
  if (dashboardContainer) {
    _syncJobCardsInContainer(dashboardContainer, jobs.slice(0, 6), "Keine Jobs bisher");
  }
}

function startGlobalJobPoller() {
  pollGlobalJobs();
  setInterval(pollGlobalJobs, 1500);
}

// ---------- render helpers ----------
function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
function render(el) {
  root.innerHTML = "";
  root.appendChild(el);
}

// ---------- Auth screens ----------
function loginScreen() {
  const wrap = h(`
    <div class="center-screen">
      <div class="auth-card">
        <h1>Docker Backup Manager</h1>
        <p class="sub">Melde dich an, um fortzufahren</p>
        <div class="field"><label>Benutzername</label><input type="text" id="login-user" /></div>
        <div class="field"><label>Passwort</label><input type="password" id="login-pass" /></div>
        <button class="btn primary block" id="login-btn">Anmelden</button>
        <div class="error-msg" id="login-error"></div>
      </div>
    </div>
  `);
  wrap.querySelector("#login-btn").addEventListener("click", async () => {
    const username = wrap.querySelector("#login-user").value.trim();
    const password = wrap.querySelector("#login-pass").value;
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      await boot();
    } catch (e) {
      wrap.querySelector("#login-error").textContent = e.message;
    }
  });
  return wrap;
}

function setupScreen() {
  const wrap = h(`
    <div class="center-screen">
      <div class="auth-card">
        <h1>Willkommen</h1>
        <p class="sub">Erstelle das erste Administrator-Konto</p>
        <div class="field"><label>Benutzername</label><input type="text" id="su-user" /></div>
        <div class="field"><label>Passwort (min. 8 Zeichen)</label><input type="password" id="su-pass" /></div>
        <button class="btn primary block" id="su-btn">Konto erstellen</button>
        <div class="error-msg" id="su-error"></div>
      </div>
    </div>
  `);
  wrap.querySelector("#su-btn").addEventListener("click", async () => {
    const username = wrap.querySelector("#su-user").value.trim();
    const password = wrap.querySelector("#su-pass").value;
    try {
      await api("/api/auth/setup", { method: "POST", body: JSON.stringify({ username, password }) });
      await boot();
    } catch (e) {
      wrap.querySelector("#su-error").textContent = e.message;
    }
  });
  return wrap;
}

// ---------- Shell / layout ----------
const NAV_ITEMS = [
  { key: "dashboard", label: "Dashboard", icon: "▦" },
  { key: "containers", label: "Container", icon: "⚙" },
  { key: "backups", label: "Backups", icon: "⭘" },
  { key: "schedules", label: "Zeitpläne", icon: "⏰" },
  { key: "logs", label: "Logs", icon: "📜" },
  { key: "settings", label: "Einstellungen", icon: "⚙️" },
];

function shell(activeKey, contentEl) {
  const wrap = h(`
    <div style="display:flex; width:100%;">
      <div class="sidebar">
        <div class="brand">🐳 Backup Manager</div>
        <div id="nav"></div>
        <div class="spacer"></div>
        <div class="user-row" id="user-row"></div>
      </div>
      <div class="main" id="main"></div>
    </div>
  `);
  const nav = wrap.querySelector("#nav");
  NAV_ITEMS.forEach((item) => {
    const navEl = h(`<div class="nav-item ${item.key === activeKey ? "active" : ""}">
      <span>${item.icon}</span><span>${item.label}</span></div>`);
    navEl.addEventListener("click", () => navigate(item.key));
    nav.appendChild(navEl);
  });
  const userRow = wrap.querySelector("#user-row");
  userRow.innerHTML = `${state.user ? state.user.username : ""} &middot; <a href="#" id="logout-link">Abmelden</a>`;
  userRow.querySelector("#logout-link").addEventListener("click", async (e) => {
    e.preventDefault();
    await api("/api/auth/logout", { method: "POST" });
    render(loginScreen());
  });
  wrap.querySelector("#main").appendChild(contentEl);
  return wrap;
}

async function navigate(key) {
  state.route = key;
  if (settingsClockTimer) {
    clearInterval(settingsClockTimer);
    settingsClockTimer = null;
  }
  try {
    let content;
    if (key === "dashboard") content = await dashboardPage();
    else if (key === "containers") content = await containersPage();
    else if (key === "backups") content = await backupsPage();
    else if (key === "schedules") content = await schedulesPage();
    else if (key === "logs") content = await logsPage();
    else if (key === "settings") content = await settingsPage();
    render(shell(key, content));
  } catch (e) {
    if (e.message !== "not authenticated") toast(e.message, "error");
  }
}

// ---------- Dashboard ----------
async function dashboardPage() {
  const [overview, backupsData, jobsData] = await Promise.all([
    api("/api/settings/overview"), api("/api/backups"), api("/api/jobs"),
  ]);
  const allRecords = Object.entries(backupsData.groups).flatMap(([name, versions]) =>
    versions.map((v) => ({ ...v, name })));
  const totalBackups = allRecords.length;
  const lastBackup = allRecords.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];

  const wrap = h(`<div>
    <div class="page-header"><h2>Dashboard</h2></div>
    <div class="grid cols-4">
      <div class="card stat-card">
        <div class="label">Docker-Status</div>
        <div class="value">${overview.docker_available ? "✅ Verbunden" : "⚠️ Nicht erreichbar"}</div>
        <div class="sub">${overview.docker_available ? "" : (overview.docker_error || "")}</div>
      </div>
      <div class="card stat-card">
        <div class="label">Backups gesamt</div>
        <div class="value">${totalBackups}</div>
      </div>
      <div class="card stat-card">
        <div class="label">Speicherverbrauch</div>
        <div class="value">${fmtBytes(overview.backups_total_bytes)}</div>
        <div class="sub mono">${overview.backups_dir}</div>
      </div>
      <div class="card stat-card">
        <div class="label">Letztes Backup</div>
        <div class="value" style="font-size:1.1rem">${lastBackup ? lastBackup.name : "-"}</div>
        <div class="sub">${lastBackup ? fmtDate(lastBackup.created_at) : ""}</div>
      </div>
    </div>
    ${overview.encryption_error
      ? `<div class="card" style="margin-top:16px; border-color: var(--warn);">
           ⚠️ <span class="mono">DBM_ENCRYPTION_KEY</span> ist ungültig: ${overview.encryption_error}
           Backups werden deshalb aktuell <strong>unverschlüsselt</strong> gespeichert (siehe Einstellungen).
         </div>`
      : overview.encryption_enabled
      ? ""
      : `<div class="card" style="margin-top:16px; border-color: var(--warn);">
           ⚠️ Backups werden aktuell <strong>unverschlüsselt</strong> gespeichert. Setze
           <span class="mono">DBM_ENCRYPTION_KEY</span>, um Verschlüsselung zu aktivieren (siehe Einstellungen).
         </div>`}
    ${overview.timezone_error
      ? `<div class="card" style="margin-top:16px; border-color: var(--warn);">⚠️ ${overview.timezone_error}</div>`
      : ""}
    <div class="section-title">Letzte Jobs</div>
    <div id="jobs-container" class="grid cols-3"></div>
  </div>`);

  // Initial render only - pollGlobalJobs() (already running every 1.5s from
  // boot()) patches #jobs-container on every tick from here on, the same
  // single /api/jobs request that also drives the floating tray.
  const jobsContainer = wrap.querySelector("#jobs-container");
  if (!jobsData.jobs.length) {
    jobsContainer.appendChild(h(`<div class="empty-state">Keine Jobs bisher</div>`));
  } else {
    jobsData.jobs.slice(0, 6).forEach((job) => jobsContainer.appendChild(renderJobCard(job)));
  }

  return wrap;
}

// ---------- Shared: pick storage targets before starting a manual backup ----------
async function pickStorageTargetsAndRun(title, runFn) {
  let storageTargets = [];
  try { storageTargets = (await api("/api/settings/storage-targets")).targets; } catch (e) {}
  const targetsHtml = storageTargets.map((t) => `
    <label style="display:flex; align-items:center; gap:8px; padding:6px 0;">
      <input type="checkbox" class="b-storage-target" value="${t.id}" style="width:auto;" ${t.enabled ? "checked" : "disabled"} />
      ${t.name} <span class="muted" style="font-size:.78rem">(${t.type})</span>
      ${t.enabled ? "" : '<span class="badge neutral">deaktiviert</span>'}
    </label>`).join("");
  const STREAMABLE_TYPES = ["local_path", "smb", "s3", "rclone"];
  const streamTargetOptionsHtml = storageTargets
    .filter((t) => STREAMABLE_TYPES.includes(t.type) && t.enabled)
    .map((t) => `<option value="${t.id}">${t.name} (${t.type})</option>`).join("");
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>${title}</h3>
        ${storageTargets.length ? `
        <div class="field">
          <label>Zusätzlich hochladen nach (neben dem lokalen Speicher)</label>
          <div>${targetsHtml}</div>
        </div>
        <div class="field">
          <label>Volumes direkt streamen, ohne lokal zu speichern (optional)</label>
          <select id="b-stream-target">
            <option value="">Nein - lokal speichern (Standard)</option>
            ${streamTargetOptionsHtml}
          </select>
          <div class="muted" style="font-size:.75rem; margin-top:4px;">
            Für große Volumes bei wenig lokalem Speicherplatz. Umgeht die AES-256-Verschlüsselung
            dieser App (die greift nur bei lokal geschriebenen Dateien) - nur bei vertrauenswürdigem
            Ziel nutzen. Nur lokaler Pfad, SMB, S3 und rclone unterstützt.
          </div>
        </div>` : ""}
        <div class="field">
          <label style="display:flex; align-items:center; gap:8px;">
            <input type="checkbox" id="b-stop-containers" style="width:auto;" />
            Container(n) vor dem Backup stoppen, danach wieder starten
          </label>
          <div class="muted" style="font-size:.75rem; margin-top:4px;">
            Für ein anwendungskonsistentes statt nur crash-konsistentes Backup (z. B. bei
            Datenbanken). Bedeutet eine kurze Downtime für die Dauer des Backups.
          </div>
        </div>
        <div class="row-actions">
          <button class="btn" id="cancel-btn">Abbrechen</button>
          <button class="btn primary" id="start-btn">Backup starten</button>
        </div>
      </div>
    </div>
  `);
  overlay.querySelector("#cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#start-btn").addEventListener("click", async () => {
    const ids = Array.from(overlay.querySelectorAll(".b-storage-target:checked")).map((el) => parseInt(el.value, 10));
    const streamTargetEl = overlay.querySelector("#b-stream-target");
    const streamTargetId = streamTargetEl && streamTargetEl.value ? parseInt(streamTargetEl.value, 10) : null;
    const stopContainers = overlay.querySelector("#b-stop-containers").checked;
    overlay.remove();
    await runFn(ids, streamTargetId, stopContainers);
  });
  document.body.appendChild(overlay);
}

// ---------- Containers ----------
async function containersPage() {
  const data = await api("/api/containers").catch((e) => { toast(e.message, "error"); return { containers: [], projects: {} }; });
  const wrap = h(`<div>
    <div class="page-header"><h2>Container</h2>
      <div class="actions"><button class="btn primary" id="backup-all-btn">Gesamte Landschaft sichern</button></div>
    </div>
    <div class="card" style="padding:0">
      <table>
        <thead><tr><th>Name</th><th>Image</th><th>Status</th><th>Projekt</th><th></th></tr></thead>
        <tbody id="containers-tbody"></tbody>
      </table>
    </div>
  </div>`);

  const tbody = wrap.querySelector("#containers-tbody");
  if (!data.containers.length) {
    tbody.appendChild(h(`<tr><td colspan="5"><div class="empty-state">Keine Container gefunden</div></td></tr>`));
  }
  data.containers.forEach((c) => {
    const row = h(`<tr>
      <td>${c.name}</td>
      <td class="mono">${c.image}</td>
      <td><span class="badge ${c.status === "running" ? "ok" : "neutral"}">${c.status}</span></td>
      <td>${c.project || "-"}</td>
      <td><button class="btn">Backup jetzt</button></td>
    </tr>`);
    row.querySelector("button").addEventListener("click", async (e) => {
      await pickStorageTargetsAndRun(`Backup für ${c.name}`, async (storageTargetIds, streamTargetId, stopContainers) => {
        e.target.disabled = true;
        try {
          await api(`/api/containers/${encodeURIComponent(c.name)}/backup`, {
            method: "POST",
            body: JSON.stringify({
              storage_target_ids: storageTargetIds, stream_volumes_target_id: streamTargetId,
              stop_container: stopContainers,
            }),
          });
          toast(`Backup für ${c.name} gestartet`);
          pollGlobalJobs();
        } catch (err) { toast(err.message, "error"); }
        e.target.disabled = false;
      });
    });
    tbody.appendChild(row);
  });

  wrap.querySelector("#backup-all-btn").addEventListener("click", async () => {
    await pickStorageTargetsAndRun("Gesamte Landschaft sichern", async (storageTargetIds, streamTargetId, stopContainers) => {
      try {
        await api("/api/backups/landscape", {
          method: "POST",
          body: JSON.stringify({
            storage_target_ids: storageTargetIds, stream_volumes_target_id: streamTargetId,
            stop_containers: stopContainers,
          }),
        });
        toast("Landschafts-Backup gestartet");
        pollGlobalJobs();
      } catch (err) { toast(err.message, "error"); }
    });
  });

  return wrap;
}

// ---------- Backups ----------
async function backupsPage() {
  const data = await api("/api/backups");
  const wrap = h(`<div>
    <div class="page-header"><h2>Backups</h2></div>
    <div id="groups"></div>
  </div>`);
  const groupsEl = wrap.querySelector("#groups");
  const names = Object.keys(data.groups);
  if (!names.length) {
    groupsEl.appendChild(h(`<div class="empty-state">Noch keine Backups vorhanden</div>`));
  }
  names.forEach((name) => {
    const versions = data.groups[name];
    const totalSize = versions.reduce((s, v) => s + (v.size_bytes || 0), 0);
    const acc = h(`
      <div class="accordion">
        <div class="accordion-head">
          <div><strong>${name}</strong> <span class="muted">(${versions.length} Version${versions.length === 1 ? "" : "en"}, ${fmtBytes(totalSize)})</span></div>
          <div class="muted">${fmtDate(versions[0].created_at)}</div>
        </div>
        <div class="accordion-body" style="display:none">
          <table>
            <thead><tr><th>Erstellt</th><th>Typ</th><th>Größe</th><th>Status</th><th></th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    `);
    const head = acc.querySelector(".accordion-head");
    const body = acc.querySelector(".accordion-body");
    head.addEventListener("click", () => { body.style.display = body.style.display === "none" ? "block" : "none"; });

    const tbody = acc.querySelector("tbody");
    versions.forEach((v) => {
      const row = h(`<tr>
        <td>${fmtDate(v.created_at)}</td>
        <td>${v.backup_type === "landscape" ? "Landschaft" : "Container"}</td>
        <td>${fmtBytes(v.size_bytes)}</td>
        <td><span class="badge ${v.status === "ok" ? "ok" : "failed"}">${v.status}</span></td>
        <td style="display:flex; gap:8px;">
          ${v.backup_type === "container" ? '<button class="btn restore-btn">Wiederherstellen</button>' : '<button class="btn members-btn">Mitglieder</button>'}
          <button class="btn danger delete-btn">Löschen</button>
        </td>
      </tr>`);
      const restoreBtn = row.querySelector(".restore-btn");
      if (restoreBtn) restoreBtn.addEventListener("click", () => openRestoreModal(v));
      const membersBtn = row.querySelector(".members-btn");
      if (membersBtn) membersBtn.addEventListener("click", () => openLandscapeMembersModal(v));
      row.querySelector(".delete-btn").addEventListener("click", async () => {
        if (!confirm(`Backup vom ${fmtDate(v.created_at)} für "${name}" wirklich löschen?`)) return;
        try {
          const res = await api(`/api/backups/${v.id}`, { method: "DELETE" });
          if (res.warning) toast(res.warning, "error");
          else toast("Backup gelöscht");
          navigate("backups");
        } catch (e) { toast(e.message, "error"); }
      });
      tbody.appendChild(row);
    });
    groupsEl.appendChild(acc);
  });
  return wrap;
}

function openRestoreModal(version) {
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>Backup wiederherstellen</h3>
        <p class="muted">Erstellt am ${fmtDate(version.created_at)}</p>
        <div class="field"><label>Neuer Container-Name (optional, leer = Originalname)</label>
          <input type="text" id="restore-name" /></div>
        <div class="field">
          <label><input type="checkbox" id="restore-start" checked style="width:auto; margin-right:6px;" />Container nach Wiederherstellung starten</label>
        </div>
        <div class="row-actions">
          <button class="btn" id="cancel-btn">Abbrechen</button>
          <button class="btn primary" id="confirm-btn">Wiederherstellen</button>
        </div>
      </div>
    </div>
  `);
  overlay.querySelector("#cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#confirm-btn").addEventListener("click", async () => {
    const newName = overlay.querySelector("#restore-name").value.trim();
    const start = overlay.querySelector("#restore-start").checked;
    try {
      await api(`/api/backups/${version.id}/restore`, {
        method: "POST", body: JSON.stringify({ new_name: newName || null, start }),
      });
      toast("Wiederherstellung gestartet");
      pollGlobalJobs();
      overlay.remove();
    } catch (e) { toast(e.message, "error"); }
  });
  document.body.appendChild(overlay);
}

async function openLandscapeMembersModal(version) {
  const data = await api(`/api/backups/${version.id}/members`);
  const restorableCount = data.members.filter((m) => m.backup_id).length;
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>Landschafts-Mitglieder</h3>
        <p class="muted">Jedes Mitglied wird als eigenes Container-Backup wiederhergestellt. Du kannst
          das ganze Projekt auf einmal wiederherstellen oder gezielt nur einen einzelnen Container.</p>
        <div class="row-actions" style="justify-content:flex-start; margin-bottom:12px;">
          <button class="btn primary" id="restore-all-btn" ${restorableCount ? "" : "disabled"}>Ganzes Projekt wiederherstellen (${restorableCount})</button>
        </div>
        <div id="members-list"></div>
        <div class="row-actions"><button class="btn" id="close-btn">Schließen</button></div>
      </div>
    </div>
  `);
  const list = overlay.querySelector("#members-list");
  data.members.forEach((m) => {
    const row = h(`<div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border)">
      <span>${m.container_name}</span>
      ${m.backup_id ? '<button class="btn">Wiederherstellen</button>' : '<span class="muted">kein Backup gefunden</span>'}
    </div>`);
    const btn = row.querySelector("button");
    if (btn) btn.addEventListener("click", () => {
      overlay.remove();
      openRestoreModal({ id: m.backup_id, created_at: version.created_at });
    });
    list.appendChild(row);
  });
  overlay.querySelector("#restore-all-btn").addEventListener("click", async () => {
    if (!confirm(`Alle ${restorableCount} Container dieses Projekts wiederherstellen? Bestehende Container mit demselben Namen werden dabei nicht automatisch ersetzt (Namenskonflikt möglich).`)) return;
    overlay.remove();
    for (const m of data.members) {
      if (!m.backup_id) continue;
      try {
        await api(`/api/backups/${m.backup_id}/restore`, { method: "POST", body: JSON.stringify({ start: true }) });
      } catch (e) { toast(`${m.container_name}: ${e.message}`, "error"); }
    }
    toast(`Wiederherstellung für ${restorableCount} Container gestartet`);
    pollGlobalJobs();
  });
  overlay.querySelector("#close-btn").addEventListener("click", () => overlay.remove());
  document.body.appendChild(overlay);
}

// ---------- Schedules ----------
const WEEKDAY_LABELS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

function describeCron(cron) {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dayOfMonth, , dayOfWeek] = parts;
  const hourlyMatch = /^\*\/(\d+)$/.exec(hour);
  if (hourlyMatch && dayOfMonth === "*" && dayOfWeek === "*") {
    return `Alle ${hourlyMatch[1]} Stunden`;
  }
  const time = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")} Uhr`;
  if (dayOfMonth !== "*") return `Monatlich am ${dayOfMonth}. um ${time}`;
  if (dayOfWeek !== "*") {
    const days = dayOfWeek.split(",").map((d) => WEEKDAY_LABELS[parseInt(d, 10)] || d).join(", ");
    return `Wöchentlich (${days}) um ${time}`;
  }
  return `Täglich um ${time}`;
}

async function schedulesPage() {
  const [data, targetsData] = await Promise.all([api("/api/schedules"), api("/api/settings/storage-targets")]);
  const targetById = Object.fromEntries(targetsData.targets.map((t) => [t.id, t.name]));
  const wrap = h(`<div>
    <div class="page-header"><h2>Zeitpläne</h2>
      <div class="actions"><button class="btn primary" id="new-schedule-btn">Neuer Zeitplan</button></div>
    </div>
    <div class="card" style="padding:0">
      <table>
        <thead><tr><th>Name</th><th>Quelle</th><th>Zeitplan</th><th>Aufbewahrung</th><th>Speicherziele</th><th>Letzter Lauf</th><th>Status</th><th></th></tr></thead>
        <tbody id="sched-tbody"></tbody>
      </table>
    </div>
  </div>`);
  const tbody = wrap.querySelector("#sched-tbody");
  if (!data.schedules.length) tbody.appendChild(h(`<tr><td colspan="8"><div class="empty-state">Keine Zeitpläne konfiguriert</div></td></tr>`));
  data.schedules.forEach((s) => {
    const targetNames = (s.storage_target_ids || []).map((id) => targetById[id] || `#${id}`);
    const row = h(`<tr>
      <td>${s.name}</td>
      <td>${s.target_type === "container" ? "Container: " + s.target_ref
        : s.name_contains ? `Name enthält: ${s.name_contains}`
        : s.project_filter ? `Projekt: ${s.project_filter}` : "Gesamte Landschaft"}</td>
      <td>${describeCron(s.cron_expression)}</td>
      <td>${s.retention_count > 0 ? s.retention_count + " Versionen" : ""}${s.retention_days > 0 ? " / " + s.retention_days + " Tage" : ""}</td>
      <td>${targetNames.length ? targetNames.join(", ") : '<span class="muted">nur lokal</span>'}</td>
      <td>${fmtDate(s.last_run_at)}</td>
      <td>${s.last_status ? `<span class="badge ${s.last_status === "ok" ? "ok" : "failed"}">${s.last_status}</span>` : '<span class="badge neutral">nie ausgeführt</span>'}
          ${s.enabled ? "" : '<span class="badge neutral">deaktiviert</span>'}</td>
      <td style="display:flex; gap:8px;">
        <button class="btn edit-btn">Bearbeiten</button>
        <button class="btn run-btn">Jetzt ausführen</button>
        <button class="btn danger del-btn">Löschen</button>
      </td>
    </tr>`);
    row.querySelector(".edit-btn").addEventListener("click", () => openScheduleModal(s));
    row.querySelector(".run-btn").addEventListener("click", async () => {
      try { await api(`/api/schedules/${s.id}/run-now`, { method: "POST" }); toast("Zeitplan gestartet"); }
      catch (e) { toast(e.message, "error"); }
    });
    row.querySelector(".del-btn").addEventListener("click", async () => {
      if (!confirm(`Zeitplan "${s.name}" löschen?`)) return;
      await api(`/api/schedules/${s.id}`, { method: "DELETE" });
      navigate("schedules");
    });
    tbody.appendChild(row);
  });
  wrap.querySelector("#new-schedule-btn").addEventListener("click", () => openScheduleModal(null));
  return wrap;
}

function parseCronToFrequencyFields(cron) {
  const parts = (cron || "").trim().split(/\s+/);
  const fallback = { freq: "daily", hour: 3, minute: 0, weekdays: ["1"], monthday: "1", hourInterval: 6 };
  if (parts.length !== 5) return fallback;
  const [minuteStr, hourStr, dayOfMonth, , dayOfWeek] = parts;
  const hourlyMatch = /^\*\/(\d+)$/.exec(hourStr);
  if (hourlyMatch && dayOfMonth === "*" && dayOfWeek === "*") {
    return { ...fallback, freq: "hourly", hourInterval: parseInt(hourlyMatch[1], 10) };
  }
  const hour = parseInt(hourStr, 10) || 0;
  const minute = parseInt(minuteStr, 10) || 0;
  if (dayOfMonth !== "*") return { ...fallback, freq: "monthly", hour, minute, monthday: dayOfMonth };
  if (dayOfWeek !== "*") return { ...fallback, freq: "weekly", hour, minute, weekdays: dayOfWeek.split(",") };
  return { ...fallback, freq: "daily", hour, minute };
}

async function openScheduleModal(existing) {
  let containers = [];
  let projects = {};
  let storageTargets = [];
  try {
    const data = await api("/api/containers");
    containers = data.containers;
    projects = data.projects;
  } catch (e) {}
  try { storageTargets = (await api("/api/settings/storage-targets")).targets; } catch (e) {}

  const targetsHtml = storageTargets.length
    ? storageTargets.map((t) => `
        <label style="display:flex; align-items:center; gap:8px; padding:6px 0;">
          <input type="checkbox" class="s-storage-target" value="${t.id}" style="width:auto;" ${t.enabled ? "" : "disabled"} />
          ${t.name} <span class="muted" style="font-size:.78rem">(${t.type})</span>
          ${t.enabled ? "" : '<span class="badge neutral">deaktiviert</span>'}
        </label>`).join("")
    : `<p class="muted" style="font-size:.85rem">Noch keine Speicherziele konfiguriert. Unter <strong>Einstellungen</strong> anlegen (SMB, S3, Google Drive/OneDrive via rclone, ...).</p>`;

  // Only these target types can receive a live byte-stream; Google Drive/OneDrive
  // need a known size/seekable content up front, so they're not offered here.
  const STREAMABLE_TYPES = ["local_path", "smb", "s3", "rclone"];
  const streamableTargets = storageTargets.filter((t) => STREAMABLE_TYPES.includes(t.type) && t.enabled);
  const streamTargetOptionsHtml = streamableTargets.map((t) =>
    `<option value="${t.id}">${t.name} (${t.type})</option>`).join("");

  const cronFields = parseCronToFrequencyFields(existing ? existing.cron_expression : null);
  const timeValue = `${String(cronFields.hour).padStart(2, "0")}:${String(cronFields.minute).padStart(2, "0")}`;

  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>${existing ? "Zeitplan bearbeiten" : "Neuer Zeitplan"}</h3>
        <div class="field"><label>Name</label><input type="text" id="s-name" value="${existing ? existing.name : ""}" /></div>
        <div class="field"><label>Sicherungsquelle (was wird gesichert)</label>
          <select id="s-target-type">
            <option value="landscape">Gesamte Docker-Landschaft</option>
            <option value="container">Einzelner Container</option>
          </select>
        </div>
        <div class="field" id="s-container-field" style="display:none">
          <label>Container</label>
          <select id="s-target-ref">${containers.map((c) => `<option value="${c.name}">${c.name}</option>`).join("")}</select>
        </div>
        <div class="field" id="s-project-field">
          <label>Was sichern?</label>
          <select id="s-project-filter">
            <option value="">Alle Container (gesamte Landschaft)</option>
            ${Object.keys(projects).sort().map((p) => `<option value="${p}">Nur Projekt „${p}" (${projects[p].length} Container, z. B. Immich/Nextcloud-Stack)</option>`).join("")}
          </select>
          <div class="muted" style="font-size:.75rem; margin-top:6px;">
            Setups ohne Docker-Compose-Projekt (z. B. Nextcloud AIO) tauchen hier nicht auf.
            Stattdessen unten einen Namensbestandteil eintragen, den alle zugehörigen Container gemeinsam haben.
          </div>
          <label style="margin-top:8px; display:block;">Oder: Name enthält (überschreibt die Auswahl oben)</label>
          <input type="text" id="s-name-contains" placeholder="z. B. nextcloud-aio" />
        </div>
        <div class="field"><label>Wie oft?</label>
          <select id="s-freq">
            <option value="hourly">Alle X Stunden</option>
            <option value="daily">Täglich</option>
            <option value="weekly">Wöchentlich</option>
            <option value="monthly">Monatlich</option>
          </select>
        </div>
        <div class="field" id="s-hourly-field" style="display:none">
          <label>Alle wie viele Stunden?</label>
          <input type="number" id="s-hour-interval" value="${cronFields.hourInterval}" min="1" max="23" />
        </div>
        <div class="field" id="s-time-field"><label>Uhrzeit</label><input type="time" id="s-time" value="${timeValue}" /></div>
        <div class="field" id="s-weekdays-field" style="display:none">
          <label>An welchen Tagen?</label>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            ${[["Mo", 1], ["Di", 2], ["Mi", 3], ["Do", 4], ["Fr", 5], ["Sa", 6], ["So", 0]].map(([label, cronDow]) => `
              <label style="display:flex; align-items:center; gap:4px;">
                <input type="checkbox" class="s-weekday" value="${cronDow}" style="width:auto;" ${cronFields.weekdays.includes(String(cronDow)) ? "checked" : ""} /> ${label}
              </label>`).join("")}
          </div>
        </div>
        <div class="field" id="s-monthday-field" style="display:none">
          <label>An welchem Tag im Monat?</label>
          <select id="s-monthday">
            ${Array.from({ length: 28 }, (_, i) => i + 1).map((d) => `<option value="${d}" ${String(d) === cronFields.monthday ? "selected" : ""}>${d}.</option>`).join("")}
          </select>
        </div>
        <div class="field"><label>Aufbewahrung: Anzahl Versionen (0 = unbegrenzt)</label><input type="text" id="s-ret-count" value="${existing ? existing.retention_count : 7}" /></div>
        <div class="field"><label>Aufbewahrung: Tage (0 = deaktiviert)</label><input type="text" id="s-ret-days" value="${existing ? existing.retention_days : 0}" /></div>
        <div class="field">
          <label>Speicherziele für diesen Zeitplan (wohin zusätzlich hochgeladen wird)</label>
          <div id="s-storage-targets">${targetsHtml}</div>
        </div>
        <div class="field">
          <label>Volumes direkt streamen, ohne lokal zu speichern (optional)</label>
          <select id="s-stream-target">
            <option value="">Nein - lokal speichern (Standard)</option>
            ${streamTargetOptionsHtml}
          </select>
          <div class="muted" style="font-size:.75rem; margin-top:4px;">
            Für große Volumes (z. B. Immich), wenn lokal nicht genug Speicherplatz frei ist: die
            Volume-Daten gehen direkt an das gewählte Ziel, ohne je auf der lokalen Platte zu landen.
            <strong>Wichtig:</strong> dabei wird die AES-256-Verschlüsselung dieser App umgangen (die
            greift nur bei lokal geschriebenen Dateien) - nur nutzen, wenn du dem Zielsystem selbst
            vertraust (z. B. eigenes NAS im LAN). Nur lokaler Pfad, SMB, S3 und rclone unterstützt.
          </div>
        </div>
        <div class="field">
          <label style="display:flex; align-items:center; gap:8px;">
            <input type="checkbox" id="s-stop-containers" style="width:auto;" />
            Container(n) vor dem Backup stoppen, danach wieder starten
          </label>
          <div class="muted" style="font-size:.75rem; margin-top:4px;">
            Für ein anwendungskonsistentes statt nur crash-konsistentes Backup (z. B. bei
            Datenbanken). Bedeutet eine kurze Downtime während jedes Laufs dieses Zeitplans.
            Standardmäßig aus, damit bestehende Zeitpläne sich nicht ändern.
          </div>
        </div>
        <div class="row-actions">
          <button class="btn" id="cancel-btn">Abbrechen</button>
          <button class="btn primary" id="save-btn">${existing ? "Speichern" : "Erstellen"}</button>
        </div>
      </div>
    </div>
  `);
  overlay.querySelector("#s-freq").value = cronFields.freq;
  if (existing) {
    overlay.querySelector("#s-target-type").value = existing.target_type;
    if (existing.target_type === "container") overlay.querySelector("#s-target-ref").value = existing.target_ref || "";
    overlay.querySelector("#s-project-filter").value = existing.project_filter || "";
    overlay.querySelector("#s-name-contains").value = existing.name_contains || "";
    (existing.storage_target_ids || []).forEach((id) => {
      const cb = overlay.querySelector(`.s-storage-target[value="${id}"]`);
      if (cb) cb.checked = true;
    });
    if (existing.stream_volumes_target_id) {
      overlay.querySelector("#s-stream-target").value = String(existing.stream_volumes_target_id);
    }
    overlay.querySelector("#s-stop-containers").checked = !!existing.stop_containers;
  }
  overlay.querySelector("#s-target-type").addEventListener("change", (e) => {
    overlay.querySelector("#s-container-field").style.display = e.target.value === "container" ? "block" : "none";
    overlay.querySelector("#s-project-field").style.display = e.target.value === "landscape" ? "block" : "none";
  });
  overlay.querySelector("#s-target-type").dispatchEvent(new Event("change"));
  function updateFrequencyFields() {
    const freq = overlay.querySelector("#s-freq").value;
    overlay.querySelector("#s-hourly-field").style.display = freq === "hourly" ? "block" : "none";
    overlay.querySelector("#s-time-field").style.display = freq === "hourly" ? "none" : "block";
    overlay.querySelector("#s-weekdays-field").style.display = freq === "weekly" ? "block" : "none";
    overlay.querySelector("#s-monthday-field").style.display = freq === "monthly" ? "block" : "none";
  }
  overlay.querySelector("#s-freq").addEventListener("change", updateFrequencyFields);
  updateFrequencyFields();

  function buildCronExpression() {
    const freq = overlay.querySelector("#s-freq").value;
    if (freq === "hourly") {
      const interval = Math.min(23, Math.max(1, parseInt(overlay.querySelector("#s-hour-interval").value || "6", 10)));
      return `0 */${interval} * * *`;
    }
    const [hour, minute] = overlay.querySelector("#s-time").value.split(":").map((n) => parseInt(n, 10));
    if (freq === "weekly") {
      const days = Array.from(overlay.querySelectorAll(".s-weekday:checked")).map((el) => el.value);
      return `${minute} ${hour} * * ${days.length ? days.join(",") : "0"}`;
    }
    if (freq === "monthly") {
      const day = overlay.querySelector("#s-monthday").value;
      return `${minute} ${hour} ${day} * *`;
    }
    return `${minute} ${hour} * * *`;
  }

  overlay.querySelector("#cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#save-btn").addEventListener("click", async () => {
    const storageTargetIds = Array.from(overlay.querySelectorAll(".s-storage-target:checked")).map((el) => parseInt(el.value, 10));
    const payload = {
      name: overlay.querySelector("#s-name").value.trim() || "Backup",
      target_type: overlay.querySelector("#s-target-type").value,
      target_ref: overlay.querySelector("#s-target-ref").value || null,
      project_filter: overlay.querySelector("#s-name-contains").value.trim()
        ? null : (overlay.querySelector("#s-project-filter").value || null),
      name_contains: overlay.querySelector("#s-name-contains").value.trim() || null,
      cron_expression: buildCronExpression(),
      retention_count: parseInt(overlay.querySelector("#s-ret-count").value || "0", 10),
      retention_days: parseInt(overlay.querySelector("#s-ret-days").value || "0", 10),
      storage_target_ids: storageTargetIds,
      stream_volumes_target_id: overlay.querySelector("#s-stream-target").value
        ? parseInt(overlay.querySelector("#s-stream-target").value, 10) : null,
      stop_containers: overlay.querySelector("#s-stop-containers").checked,
      enabled: existing ? existing.enabled : true,
    };
    try {
      if (existing) {
        await api(`/api/schedules/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        await api("/api/schedules", { method: "POST", body: JSON.stringify(payload) });
      }
      overlay.remove();
      navigate("schedules");
    } catch (e) { toast(e.message, "error"); }
  });
  document.body.appendChild(overlay);
}

// ---------- Logs ----------
const LOG_CATEGORY_LABEL = { backup: "Backup", restore: "Restore", schedule: "Zeitplan" };

async function logsPage() {
  const data = await api("/api/logs?limit=300").catch((e) => { toast(e.message, "error"); return { entries: [] }; });
  const wrap = h(`<div>
    <div class="page-header"><h2>Logs</h2></div>
    <div class="card" style="padding:0">
      <table>
        <thead><tr><th>Zeitpunkt</th><th>Kategorie</th><th>Meldung</th></tr></thead>
        <tbody id="logs-tbody"></tbody>
      </table>
    </div>
  </div>`);
  const tbody = wrap.querySelector("#logs-tbody");
  if (!data.entries.length) {
    tbody.appendChild(h(`<tr><td colspan="3"><div class="empty-state">Noch keine Log-Einträge vorhanden</div></td></tr>`));
  }
  data.entries.forEach((entry) => {
    const row = h(`<tr>
      <td class="mono">${fmtDate(entry.created_at)}</td>
      <td><span class="badge ${entry.level === "error" ? "failed" : "ok"}">${LOG_CATEGORY_LABEL[entry.category] || entry.category}</span></td>
      <td>${entry.message}</td>
    </tr>`);
    tbody.appendChild(row);
  });
  return wrap;
}

// ---------- Settings ----------
async function settingsPage() {
  const [overview, targetsData] = await Promise.all([
    api("/api/settings/overview"), api("/api/settings/storage-targets"),
  ]);
  const wrap = h(`<div>
    <div class="page-header"><h2>Einstellungen</h2></div>

    <div class="section-title">Serverzeit</div>
    <div class="card">
      <span class="mono" id="server-clock" style="font-size:1.1rem"></span>
      <span class="muted">(Zeitzone: <span class="mono">${overview.timezone}</span> — maßgeblich für Zeitpläne)</span>
      ${overview.timezone_error
        ? `<div style="font-size:.8rem; margin-top:6px; color: var(--warn);">⚠️ ${overview.timezone_error}</div>`
        : overview.timezone === "UTC" ? `<div class="muted" style="font-size:.8rem; margin-top:6px;">
        Läuft ein Zeitplan nicht zur erwarteten Uhrzeit: die Standard-Zeitzone ist UTC. Setze die
        Umgebungsvariable <span class="mono">DBM_TZ</span> auf deine Zeitzone (z. B. <span class="mono">Europe/Berlin</span>)
        und starte den Container neu.</div>` : ""}
    </div>

    <div class="section-title">Speicherort</div>
    <div class="card mono">${overview.backups_dir}</div>

    <div class="section-title">Verschlüsselung</div>
    <div class="card">
      ${overview.encryption_error
        ? `<span class="badge failed">⚠️ Ungültiger Schlüssel</span> <span class="muted"><span class="mono">DBM_ENCRYPTION_KEY</span> ${overview.encryption_error}
           Erzeuge einen echten Schlüssel mit <span class="mono">openssl rand -base64 32</span> (nicht abtippen, sondern das Kommando ausführen und die Ausgabe kopieren) und starte den Container neu.</span>`
        : overview.encryption_enabled
        ? `<span class="badge ok">🔒 Aktiv</span> <span class="muted">Backups werden mit AES-256 verschlüsselt abgelegt (Schlüssel aus <span class="mono">DBM_ENCRYPTION_KEY</span>).</span>`
        : `<span class="badge failed">⚠️ Inaktiv</span> <span class="muted">Backups werden unverschlüsselt gespeichert. Setze die Umgebungsvariable
           <span class="mono">DBM_ENCRYPTION_KEY</span> (z. B. <span class="mono">openssl rand -base64 32</span>) und starte den Container neu.
           Wichtig: Schlüssel sicher aufbewahren – ohne ihn sind bestehende Backups nicht wiederherstellbar.</span>`}
    </div>

    <div class="section-title">Passwort ändern</div>
    <div class="card">
      <div class="grid cols-3">
        <div class="field"><label>Aktuelles Passwort</label><input type="password" id="cur-pass" /></div>
        <div class="field"><label>Neues Passwort</label><input type="password" id="new-pass" /></div>
        <div class="field" style="display:flex; align-items:flex-end;"><button class="btn primary" id="change-pass-btn">Ändern</button></div>
      </div>
    </div>

    <div class="section-title">Externe Speicherziele (SMB / NFS / S3 / Google Drive / OneDrive / ...)</div>
    <p class="muted" style="margin-top:-4px">Nach jedem Backup wird zusätzlich auf alle aktivierten Ziele hochgeladen/repliziert.</p>
    <div class="toolbar"><button class="btn primary" id="new-target-btn">Neues Ziel</button></div>
    <div class="card" style="padding:0">
      <table>
        <thead><tr><th>Name</th><th>Typ</th><th>Status</th><th>Letzter Sync</th><th></th></tr></thead>
        <tbody id="targets-tbody"></tbody>
      </table>
    </div>
  </div>`);

  // Live-ticking server clock: compute the offset between server and browser time once,
  // then keep displaying server-time-equivalent using the browser's own clock (no repeated polling).
  const serverTimeOffsetMs = new Date(overview.server_time).getTime() - Date.now();
  const clockEl = wrap.querySelector("#server-clock");
  function tickClock() {
    const now = new Date(Date.now() + serverTimeOffsetMs);
    clockEl.textContent = now.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "medium" });
  }
  tickClock();
  settingsClockTimer = setInterval(tickClock, 1000);

  wrap.querySelector("#change-pass-btn").addEventListener("click", async () => {
    const current_password = wrap.querySelector("#cur-pass").value;
    const new_password = wrap.querySelector("#new-pass").value;
    try {
      await api("/api/auth/change-password", { method: "POST", body: JSON.stringify({ current_password, new_password }) });
      toast("Passwort geändert");
      wrap.querySelector("#cur-pass").value = ""; wrap.querySelector("#new-pass").value = "";
    } catch (e) { toast(e.message, "error"); }
  });

  const tbody = wrap.querySelector("#targets-tbody");
  const typeLabels = {
    local_path: "SMB/NFS-Pfad (lokal gemountet)",
    smb: "SMB/CIFS (Benutzername/Passwort)",
    s3: "S3-kompatibel",
    rclone: "rclone (SFTP, WebDAV, B2, ...)",
    google_drive: "Google Drive",
    onedrive: "OneDrive",
  };
  if (!targetsData.targets.length) tbody.appendChild(h(`<tr><td colspan="5"><div class="empty-state">Keine externen Ziele konfiguriert</div></td></tr>`));
  targetsData.targets.forEach((t) => {
    const row = h(`<tr>
      <td>${t.name}${t.enabled ? "" : ' <span class="badge neutral">deaktiviert</span>'}</td>
      <td>${typeLabels[t.type] || t.type}</td>
      <td>${t.last_sync_status ? `<span class="badge ${t.last_sync_status === "ok" ? "ok" : "failed"}">${t.last_sync_status}</span>` : '<span class="badge neutral">noch nicht synchronisiert</span>'}</td>
      <td>${fmtDate(t.last_sync_at)}</td>
      <td style="display:flex; gap:8px;">
        <button class="btn edit-btn">Bearbeiten</button>
        <button class="btn test-btn">Testen</button>
        ${["local_path", "smb", "s3", "rclone"].includes(t.type) ? '<button class="btn import-btn">Katalog importieren</button>' : ""}
        <button class="btn danger del-btn">Löschen</button>
      </td>
    </tr>`);
    row.querySelector(".edit-btn").addEventListener("click", () => openStorageTargetModal(t));
    row.querySelector(".test-btn").addEventListener("click", async () => {
      try { await api(`/api/settings/storage-targets/${t.id}/test`, { method: "POST" }); toast("Verbindung erfolgreich"); }
      catch (e) { toast(e.message, "error"); }
    });
    const importBtn = row.querySelector(".import-btn");
    if (importBtn) importBtn.addEventListener("click", async () => {
      if (!confirm(`Speicherziel "${t.name}" nach vorhandenen Backups durchsuchen und in den Katalog übernehmen?`)) return;
      importBtn.disabled = true;
      importBtn.textContent = "Durchsuche...";
      try {
        const res = await api(`/api/settings/storage-targets/${t.id}/import-catalog`, { method: "POST" });
        toast(`${res.found} Backup(s) gefunden, ${res.imported} neu übernommen, ${res.skipped} bereits bekannt`);
        if (res.imported > 0 && state.route === "backups") navigate("backups");
      } catch (e) { toast(e.message, "error"); }
      importBtn.disabled = false;
      importBtn.textContent = "Katalog importieren";
    });
    row.querySelector(".del-btn").addEventListener("click", async () => {
      if (!confirm(`Speicherziel "${t.name}" löschen?`)) return;
      await api(`/api/settings/storage-targets/${t.id}`, { method: "DELETE" });
      navigate("settings");
    });
    tbody.appendChild(row);
  });
  wrap.querySelector("#new-target-btn").addEventListener("click", () => openStorageTargetModal());
  return wrap;
}

function openStorageTargetModal(existing) {
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>${existing ? "Speicherziel bearbeiten" : "Neues Speicherziel"}</h3>
        <div class="field"><label>Name</label><input type="text" id="t-name" /></div>
        <div class="field"><label>Typ</label>
          <select id="t-type">
            <option value="smb">SMB/CIFS (Server + Benutzername/Passwort)</option>
            <option value="local_path">Bereits gemounteter Pfad (SMB/NFS am Host)</option>
            <option value="s3">S3-kompatibel (AWS S3, MinIO, Wasabi, ...)</option>
            <option value="google_drive">Google Drive (Anmelden per Browser)</option>
            <option value="onedrive">OneDrive (Anmelden per Browser)</option>
            <option value="rclone">rclone-Remote (SFTP, WebDAV, B2, ...)</option>
          </select>
        </div>
        <div id="t-config-fields"></div>
        <div class="row-actions">
          <button class="btn" id="cancel-btn">Abbrechen</button>
          <button class="btn" id="test-btn">Verbindung testen</button>
          <button class="btn primary" id="save-btn">Speichern</button>
        </div>
      </div>
    </div>
  `);
  let oauthPending = null; // { provider, state } once a Google/OneDrive login popup succeeds
  const fieldsEl = overlay.querySelector("#t-config-fields");
  function renderFields(type, cfg) {
    cfg = cfg || {};
    if (type === "smb") {
      fieldsEl.innerHTML = `
        <div class="field"><label>Server (IP oder Hostname)</label><input type="text" id="cfg-server" placeholder="192.168.1.50" value="${cfg.server || ""}" /></div>
        <div class="field"><label>Benutzername</label><input type="text" id="cfg-username" value="${cfg.username || ""}" /></div>
        <div class="field"><label>Passwort</label><input type="password" id="cfg-password" value="${cfg.password || ""}" /></div>
        <div class="field">
          <label>Freigabename (Share)</label>
          <div style="display:flex; gap:8px;">
            <input type="text" id="cfg-share" placeholder="backups" value="${cfg.share || ""}" style="flex:1" />
            <button type="button" class="btn" id="load-shares-btn">Freigaben anzeigen</button>
          </div>
          <div id="cfg-share-results" style="display:flex; flex-wrap:wrap; gap:6px; margin-top:6px;"></div>
          <div class="muted" style="font-size:.75rem; margin-top:4px;">Server, Benutzername und Passwort oben ausfüllen, dann auf "Freigaben anzeigen" klicken.</div>
        </div>
        <div class="field"><label>Unterordner (optional)</label><input type="text" id="cfg-base-path" placeholder="docker-backup-manager" value="${cfg.base_path || ""}" /></div>
        <div class="field"><label>Domain (optional)</label><input type="text" id="cfg-domain" value="${cfg.domain || ""}" /></div>
        <div class="field"><label>Port</label><input type="text" id="cfg-port" value="${cfg.port || "445"}" /></div>`;
      fieldsEl.querySelector("#load-shares-btn").addEventListener("click", async () => {
        const btn = fieldsEl.querySelector("#load-shares-btn");
        btn.disabled = true;
        btn.textContent = "Lade...";
        try {
          const res = await api("/api/settings/smb/shares", {
            method: "POST",
            body: JSON.stringify({
              server: fieldsEl.querySelector("#cfg-server").value.trim(),
              username: fieldsEl.querySelector("#cfg-username").value.trim(),
              password: fieldsEl.querySelector("#cfg-password").value,
              domain: fieldsEl.querySelector("#cfg-domain").value.trim(),
              port: fieldsEl.querySelector("#cfg-port").value.trim() || "445",
            }),
          });
          const results = fieldsEl.querySelector("#cfg-share-results");
          results.innerHTML = "";
          res.shares.forEach((s) => {
            const chip = h(`<button type="button" class="btn" style="padding:4px 10px; font-size:.85rem;">${s}</button>`);
            chip.addEventListener("click", () => { fieldsEl.querySelector("#cfg-share").value = s; });
            results.appendChild(chip);
          });
          if (res.shares.length) toast(`${res.shares.length} Freigabe(n) gefunden - anklicken zum Übernehmen`);
          else toast("Keine Freigaben gefunden", "error");
        } catch (e) {
          toast(e.message, "error");
        } finally {
          btn.disabled = false;
          btn.textContent = "Freigaben anzeigen";
        }
      });
    } else if (type === "local_path") {
      fieldsEl.innerHTML = `
        <div class="field"><label>Pfad im Container (z.B. gemountete SMB/NFS-Freigabe)</label>
          <input type="text" id="cfg-path" placeholder="/mnt/remote-backup" value="${cfg.path || ""}" /></div>`;
    } else if (type === "s3") {
      fieldsEl.innerHTML = `
        <div class="field"><label>Bucket</label><input type="text" id="cfg-bucket" value="${cfg.bucket || ""}" /></div>
        <div class="field"><label>Endpoint-URL (leer = AWS S3)</label><input type="text" id="cfg-endpoint" placeholder="https://s3.eu-central-1.amazonaws.com" value="${cfg.endpoint_url || ""}" /></div>
        <div class="field"><label>Region</label><input type="text" id="cfg-region" placeholder="eu-central-1" value="${cfg.region || ""}" /></div>
        <div class="field"><label>Access Key</label><input type="text" id="cfg-access" value="${cfg.access_key || ""}" /></div>
        <div class="field"><label>Secret Key</label><input type="password" id="cfg-secret" value="${cfg.secret_key || ""}" /></div>
        <div class="field"><label>Präfix (optional)</label><input type="text" id="cfg-prefix" value="${cfg.prefix || ""}" /></div>`;
    } else if (type === "google_drive" || type === "onedrive") {
      const provider = type === "google_drive" ? "google" : "onedrive";
      const providerLabel = type === "google_drive" ? "Google" : "Microsoft";
      const connected = cfg.connected || (oauthPending && oauthPending.state);
      fieldsEl.innerHTML = `
        <div class="field">
          <div id="oauth-status" class="muted" style="margin-bottom:8px;">
            ${connected
              ? `✅ Verbunden${cfg.account ? " als <strong>" + cfg.account + "</strong>" : ""}`
              : "Noch nicht verbunden."}
          </div>
          <button type="button" class="btn" id="oauth-connect-btn">${connected ? "Neu verbinden" : "Mit " + providerLabel + " anmelden"}</button>
        </div>
        <div class="field"><label>Zielordner (optional, wird angelegt falls nötig)</label>
          <input type="text" id="cfg-folder-path" placeholder="docker-backups" value="${cfg.folder_path || ""}" /></div>`;
      fieldsEl.querySelector("#oauth-connect-btn").addEventListener("click", () => {
        const popup = window.open(`/api/settings/oauth/${provider}/start`, "dbm-oauth", "width=520,height=650");
        const onMessage = (event) => {
          if (event.origin !== window.location.origin || !event.data || !event.data.dbmOAuth) return;
          window.removeEventListener("message", onMessage);
          if (!event.data.ok) { toast(`Anmeldung fehlgeschlagen: ${event.data.error}`, "error"); return; }
          oauthPending = { provider, state: event.data.state };
          toast("Erfolgreich verbunden - Speichern nicht vergessen");
          renderFields(type, cfg);
        };
        window.addEventListener("message", onMessage);
      });
    } else {
      fieldsEl.innerHTML = `
        <div class="field"><label>rclone Remote-Name (aus rclone.conf)</label><input type="text" id="cfg-remote" placeholder="gdrive" value="${cfg.remote || ""}" /></div>
        <div class="field"><label>Remote-Pfad</label><input type="text" id="cfg-remote-path" placeholder="docker-backups" value="${cfg.remote_path || ""}" /></div>
        <p class="muted" style="font-size:.8rem">Der Remote muss vorher per <span class="mono">rclone config</span> in der gemounteten rclone.conf eingerichtet sein (unterstützt SFTP, WebDAV, B2, u.v.m. - für Google Drive/OneDrive die eigenen Optionen oben verwenden).</p>`;
    }
  }
  if (existing) {
    overlay.querySelector("#t-name").value = existing.name;
    overlay.querySelector("#t-type").value = existing.type;
    renderFields(existing.type, existing.config);
  } else {
    renderFields("smb");
  }
  overlay.querySelector("#t-type").addEventListener("change", (e) => renderFields(e.target.value));

  function readConfig() {
    const type = overlay.querySelector("#t-type").value;
    let config = {};
    if (type === "smb") config = {
      server: overlay.querySelector("#cfg-server").value.trim(),
      share: overlay.querySelector("#cfg-share").value.trim(),
      base_path: overlay.querySelector("#cfg-base-path").value.trim(),
      username: overlay.querySelector("#cfg-username").value.trim(),
      password: overlay.querySelector("#cfg-password").value,
      domain: overlay.querySelector("#cfg-domain").value.trim(),
      port: overlay.querySelector("#cfg-port").value.trim() || "445",
    };
    else if (type === "local_path") config = { path: overlay.querySelector("#cfg-path").value.trim() };
    else if (type === "s3") config = {
      bucket: overlay.querySelector("#cfg-bucket").value.trim(),
      endpoint_url: overlay.querySelector("#cfg-endpoint").value.trim(),
      region: overlay.querySelector("#cfg-region").value.trim(),
      access_key: overlay.querySelector("#cfg-access").value.trim(),
      secret_key: overlay.querySelector("#cfg-secret").value,
      prefix: overlay.querySelector("#cfg-prefix").value.trim(),
    };
    else if (type === "google_drive" || type === "onedrive") config = {
      folder_path: overlay.querySelector("#cfg-folder-path").value.trim(),
    };
    else config = {
      remote: overlay.querySelector("#cfg-remote").value.trim(),
      remote_path: overlay.querySelector("#cfg-remote-path").value.trim(),
    };
    return { type, config };
  }

  function updateTestButtonVisibility() {
    const isOAuth = ["google_drive", "onedrive"].includes(overlay.querySelector("#t-type").value);
    overlay.querySelector("#test-btn").style.display = isOAuth ? "none" : "";
  }
  updateTestButtonVisibility();
  overlay.querySelector("#t-type").addEventListener("change", updateTestButtonVisibility);

  overlay.querySelector("#cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#test-btn").addEventListener("click", async () => {
    const { type, config } = readConfig();
    const btn = overlay.querySelector("#test-btn");
    btn.disabled = true;
    btn.textContent = "Teste...";
    try {
      await api("/api/settings/storage-targets/test", { method: "POST", body: JSON.stringify({ type, config }) });
      toast("Verbindung erfolgreich");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Verbindung testen";
    }
  });
  overlay.querySelector("#save-btn").addEventListener("click", async () => {
    const { type, config } = readConfig();
    const name = overlay.querySelector("#t-name").value.trim() || type;

    if (type === "google_drive" || type === "onedrive") {
      if (oauthPending) {
        try {
          await api("/api/settings/storage-targets/oauth-complete", {
            method: "POST",
            body: JSON.stringify({
              state: oauthPending.state, name, folder_path: config.folder_path,
              target_id: existing ? existing.id : null,
            }),
          });
          overlay.remove();
          navigate("settings");
        } catch (e) { toast(e.message, "error"); }
        return;
      }
      if (!existing || !existing.config.connected) {
        toast("Bitte zuerst über den Button oben anmelden", "error");
        return;
      }
    }

    const payload = { name, type, config, enabled: existing ? existing.enabled : true };
    try {
      if (existing) {
        await api(`/api/settings/storage-targets/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        await api("/api/settings/storage-targets", { method: "POST", body: JSON.stringify(payload) });
      }
      overlay.remove();
      navigate("settings");
    } catch (e) { toast(e.message, "error"); }
  });
  document.body.appendChild(overlay);
}

// ---------- Boot ----------
async function boot() {
  try {
    const authStatus = await fetch("/api/auth/status").then((r) => r.json());
    if (authStatus.setup_required) { render(setupScreen()); return; }
    const me = await api("/api/auth/me").catch(() => null);
    if (!me) { render(loginScreen()); return; }
    state.user = me;
    await navigate("dashboard");
    startGlobalJobPoller();
  } catch (e) {
    render(loginScreen());
  }
}
boot();
