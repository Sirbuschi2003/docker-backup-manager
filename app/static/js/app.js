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
  try {
    let content;
    if (key === "dashboard") content = await dashboardPage();
    else if (key === "containers") content = await containersPage();
    else if (key === "backups") content = await backupsPage();
    else if (key === "schedules") content = await schedulesPage();
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
  const allRecords = Object.values(backupsData.groups).flat();
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
    <div class="section-title">Laufende & letzte Jobs</div>
    <div id="jobs-container" class="grid cols-3"></div>
  </div>`);

  const jobsContainer = wrap.querySelector("#jobs-container");
  if (!jobsData.jobs.length) {
    jobsContainer.appendChild(h(`<div class="empty-state">Keine Jobs bisher</div>`));
  } else {
    jobsData.jobs.slice(0, 6).forEach((job) => jobsContainer.appendChild(jobCard(job)));
  }
  return wrap;
}

function jobCard(job) {
  const statusBadge = job.status === "running" ? "running" : job.status === "success" ? "ok" : "failed";
  const card = h(`
    <div class="card job-card" data-job-id="${job.id}">
      <div class="job-title"><span>${job.kind === "backup" ? "💾" : "♻️"} ${job.label}</span>
        <span class="badge ${statusBadge}">${job.status}</span></div>
      <div class="muted" style="font-size:.8rem">${job.step_name}</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div style="width:${job.percent}%"></div></div>
        <div class="progress-meta">
          <span>${job.current_step}/${job.total_steps}</span>
          <span>${job.status === "running" ? "Verstrichen: " + fmtDuration(job.elapsed_seconds) + (job.eta_seconds != null ? " · ETA " + fmtDuration(job.eta_seconds) : "") : fmtDuration(job.elapsed_seconds)}</span>
        </div>
      </div>
      ${job.error ? `<div class="error-msg">${job.error}</div>` : ""}
    </div>
  `);
  if (job.status === "running") pollJob(job.id, card);
  return card;
}

function pollJob(jobId, card) {
  const interval = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (!card.isConnected) { clearInterval(interval); return; }
      const fresh = jobCardInner(job);
      card.innerHTML = fresh.innerHTML;
      if (job.status !== "running") {
        clearInterval(interval);
        toast(`${job.label}: ${job.status === "success" ? "erfolgreich" : "fehlgeschlagen"}`,
              job.status === "success" ? "ok" : "error");
      }
    } catch (e) { clearInterval(interval); }
  }, 1500);
}
function jobCardInner(job) {
  const statusBadge = job.status === "running" ? "running" : job.status === "success" ? "ok" : "failed";
  return h(`
    <div class="job-card">
      <div class="job-title"><span>${job.kind === "backup" ? "💾" : "♻️"} ${job.label}</span>
        <span class="badge ${statusBadge}">${job.status}</span></div>
      <div class="muted" style="font-size:.8rem">${job.step_name}</div>
      <div class="progress-wrap">
        <div class="progress-bar"><div style="width:${job.percent}%"></div></div>
        <div class="progress-meta">
          <span>${job.current_step}/${job.total_steps}</span>
          <span>${job.status === "running" ? "Verstrichen: " + fmtDuration(job.elapsed_seconds) + (job.eta_seconds != null ? " · ETA " + fmtDuration(job.eta_seconds) : "") : fmtDuration(job.elapsed_seconds)}</span>
        </div>
      </div>
      ${job.error ? `<div class="error-msg">${job.error}</div>` : ""}
    </div>
  `);
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
      e.target.disabled = true;
      try {
        const res = await api(`/api/containers/${encodeURIComponent(c.name)}/backup`, { method: "POST" });
        toast(`Backup für ${c.name} gestartet`);
        watchJobToast(res.job_id);
      } catch (err) { toast(err.message, "error"); }
      e.target.disabled = false;
    });
    tbody.appendChild(row);
  });

  wrap.querySelector("#backup-all-btn").addEventListener("click", async () => {
    try {
      const res = await api("/api/backups/landscape", { method: "POST", body: JSON.stringify({}) });
      toast("Landschafts-Backup gestartet");
      watchJobToast(res.job_id);
    } catch (err) { toast(err.message, "error"); }
  });

  return wrap;
}

function watchJobToast(jobId) {
  const interval = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (job.status !== "running") {
        clearInterval(interval);
        toast(`${job.label}: ${job.status === "success" ? "erfolgreich abgeschlossen" : "fehlgeschlagen - " + job.error}`,
              job.status === "success" ? "ok" : "error");
      }
    } catch (e) { clearInterval(interval); }
  }, 2000);
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
          await api(`/api/backups/${v.id}`, { method: "DELETE" });
          toast("Backup gelöscht");
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
      const res = await api(`/api/backups/${version.id}/restore`, {
        method: "POST", body: JSON.stringify({ new_name: newName || null, start }),
      });
      toast("Wiederherstellung gestartet");
      watchJobToast(res.job_id);
      overlay.remove();
    } catch (e) { toast(e.message, "error"); }
  });
  document.body.appendChild(overlay);
}

async function openLandscapeMembersModal(version) {
  const data = await api(`/api/backups/${version.id}/members`);
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>Landschafts-Mitglieder</h3>
        <p class="muted">Jedes Mitglied wird als eigenes Container-Backup wiederhergestellt.</p>
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
  overlay.querySelector("#close-btn").addEventListener("click", () => overlay.remove());
  document.body.appendChild(overlay);
}

// ---------- Schedules ----------
async function schedulesPage() {
  const data = await api("/api/schedules");
  const wrap = h(`<div>
    <div class="page-header"><h2>Zeitpläne</h2>
      <div class="actions"><button class="btn primary" id="new-schedule-btn">Neuer Zeitplan</button></div>
    </div>
    <div class="card" style="padding:0">
      <table>
        <thead><tr><th>Name</th><th>Ziel</th><th>Cron</th><th>Aufbewahrung</th><th>Letzter Lauf</th><th>Status</th><th></th></tr></thead>
        <tbody id="sched-tbody"></tbody>
      </table>
    </div>
  </div>`);
  const tbody = wrap.querySelector("#sched-tbody");
  if (!data.schedules.length) tbody.appendChild(h(`<tr><td colspan="7"><div class="empty-state">Keine Zeitpläne konfiguriert</div></td></tr>`));
  data.schedules.forEach((s) => {
    const row = h(`<tr>
      <td>${s.name}</td>
      <td>${s.target_type === "container" ? "Container: " + s.target_ref : "Gesamte Landschaft"}</td>
      <td class="mono">${s.cron_expression}</td>
      <td>${s.retention_count > 0 ? s.retention_count + " Versionen" : ""}${s.retention_days > 0 ? " / " + s.retention_days + " Tage" : ""}</td>
      <td>${fmtDate(s.last_run_at)}</td>
      <td>${s.last_status ? `<span class="badge ${s.last_status === "ok" ? "ok" : "failed"}">${s.last_status}</span>` : '<span class="badge neutral">nie ausgeführt</span>'}
          ${s.enabled ? "" : '<span class="badge neutral">deaktiviert</span>'}</td>
      <td style="display:flex; gap:8px;">
        <button class="btn run-btn">Jetzt ausführen</button>
        <button class="btn danger del-btn">Löschen</button>
      </td>
    </tr>`);
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
  wrap.querySelector("#new-schedule-btn").addEventListener("click", () => openScheduleModal());
  return wrap;
}

async function openScheduleModal() {
  let containers = [];
  try { containers = (await api("/api/containers")).containers; } catch (e) {}
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>Neuer Zeitplan</h3>
        <div class="field"><label>Name</label><input type="text" id="s-name" /></div>
        <div class="field"><label>Ziel</label>
          <select id="s-target-type">
            <option value="landscape">Gesamte Docker-Landschaft</option>
            <option value="container">Einzelner Container</option>
          </select>
        </div>
        <div class="field" id="s-container-field" style="display:none">
          <label>Container</label>
          <select id="s-target-ref">${containers.map((c) => `<option value="${c.name}">${c.name}</option>`).join("")}</select>
        </div>
        <div class="field"><label>Cron-Ausdruck (Minute Stunde Tag Monat Wochentag)</label>
          <input type="text" id="s-cron" placeholder="0 3 * * *" value="0 3 * * *" />
          <div class="muted" style="font-size:.75rem; margin-top:4px;">Beispiel: "0 3 * * *" = täglich um 03:00 Uhr</div>
        </div>
        <div class="field"><label>Aufbewahrung: Anzahl Versionen (0 = unbegrenzt)</label><input type="text" id="s-ret-count" value="7" /></div>
        <div class="field"><label>Aufbewahrung: Tage (0 = deaktiviert)</label><input type="text" id="s-ret-days" value="0" /></div>
        <div class="row-actions">
          <button class="btn" id="cancel-btn">Abbrechen</button>
          <button class="btn primary" id="save-btn">Erstellen</button>
        </div>
      </div>
    </div>
  `);
  overlay.querySelector("#s-target-type").addEventListener("change", (e) => {
    overlay.querySelector("#s-container-field").style.display = e.target.value === "container" ? "block" : "none";
  });
  overlay.querySelector("#cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#save-btn").addEventListener("click", async () => {
    const payload = {
      name: overlay.querySelector("#s-name").value.trim() || "Backup",
      target_type: overlay.querySelector("#s-target-type").value,
      target_ref: overlay.querySelector("#s-target-ref").value || null,
      cron_expression: overlay.querySelector("#s-cron").value.trim(),
      retention_count: parseInt(overlay.querySelector("#s-ret-count").value || "0", 10),
      retention_days: parseInt(overlay.querySelector("#s-ret-days").value || "0", 10),
      enabled: true,
    };
    try {
      await api("/api/schedules", { method: "POST", body: JSON.stringify(payload) });
      overlay.remove();
      navigate("schedules");
    } catch (e) { toast(e.message, "error"); }
  });
  document.body.appendChild(overlay);
}

// ---------- Settings ----------
async function settingsPage() {
  const [overview, targetsData] = await Promise.all([
    api("/api/settings/overview"), api("/api/settings/storage-targets"),
  ]);
  const wrap = h(`<div>
    <div class="page-header"><h2>Einstellungen</h2></div>

    <div class="section-title">Speicherort</div>
    <div class="card mono">${overview.backups_dir}</div>

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
  const typeLabels = { local_path: "SMB/NFS-Pfad (lokal gemountet)", s3: "S3-kompatibel", rclone: "rclone (Google Drive, OneDrive, ...)" };
  if (!targetsData.targets.length) tbody.appendChild(h(`<tr><td colspan="5"><div class="empty-state">Keine externen Ziele konfiguriert</div></td></tr>`));
  targetsData.targets.forEach((t) => {
    const row = h(`<tr>
      <td>${t.name}${t.enabled ? "" : ' <span class="badge neutral">deaktiviert</span>'}</td>
      <td>${typeLabels[t.type] || t.type}</td>
      <td>${t.last_sync_status ? `<span class="badge ${t.last_sync_status === "ok" ? "ok" : "failed"}">${t.last_sync_status}</span>` : '<span class="badge neutral">noch nicht synchronisiert</span>'}</td>
      <td>${fmtDate(t.last_sync_at)}</td>
      <td style="display:flex; gap:8px;">
        <button class="btn test-btn">Testen</button>
        <button class="btn danger del-btn">Löschen</button>
      </td>
    </tr>`);
    row.querySelector(".test-btn").addEventListener("click", async () => {
      try { await api(`/api/settings/storage-targets/${t.id}/test`, { method: "POST" }); toast("Verbindung erfolgreich"); }
      catch (e) { toast(e.message, "error"); }
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

function openStorageTargetModal() {
  const overlay = h(`
    <div class="modal-overlay">
      <div class="modal">
        <h3>Neues Speicherziel</h3>
        <div class="field"><label>Name</label><input type="text" id="t-name" /></div>
        <div class="field"><label>Typ</label>
          <select id="t-type">
            <option value="local_path">SMB/NFS-Pfad (im Container gemountet)</option>
            <option value="s3">S3-kompatibel (AWS S3, MinIO, Wasabi, ...)</option>
            <option value="rclone">rclone-Remote (Google Drive, OneDrive, SFTP, ...)</option>
          </select>
        </div>
        <div id="t-config-fields"></div>
        <div class="row-actions">
          <button class="btn" id="cancel-btn">Abbrechen</button>
          <button class="btn primary" id="save-btn">Speichern</button>
        </div>
      </div>
    </div>
  `);
  const fieldsEl = overlay.querySelector("#t-config-fields");
  function renderFields(type) {
    if (type === "local_path") {
      fieldsEl.innerHTML = `
        <div class="field"><label>Pfad im Container (z.B. gemountete SMB/NFS-Freigabe)</label>
          <input type="text" id="cfg-path" placeholder="/mnt/remote-backup" /></div>`;
    } else if (type === "s3") {
      fieldsEl.innerHTML = `
        <div class="field"><label>Bucket</label><input type="text" id="cfg-bucket" /></div>
        <div class="field"><label>Endpoint-URL (leer = AWS S3)</label><input type="text" id="cfg-endpoint" placeholder="https://s3.eu-central-1.amazonaws.com" /></div>
        <div class="field"><label>Region</label><input type="text" id="cfg-region" placeholder="eu-central-1" /></div>
        <div class="field"><label>Access Key</label><input type="text" id="cfg-access" /></div>
        <div class="field"><label>Secret Key</label><input type="password" id="cfg-secret" /></div>
        <div class="field"><label>Präfix (optional)</label><input type="text" id="cfg-prefix" /></div>`;
    } else {
      fieldsEl.innerHTML = `
        <div class="field"><label>rclone Remote-Name (aus rclone.conf)</label><input type="text" id="cfg-remote" placeholder="gdrive" /></div>
        <div class="field"><label>Remote-Pfad</label><input type="text" id="cfg-remote-path" placeholder="docker-backups" /></div>
        <p class="muted" style="font-size:.8rem">Der Remote muss vorher per <span class="mono">rclone config</span> in der gemounteten rclone.conf eingerichtet sein (unterstützt Google Drive, OneDrive, SFTP, WebDAV, u.v.m.).</p>`;
    }
  }
  renderFields("local_path");
  overlay.querySelector("#t-type").addEventListener("change", (e) => renderFields(e.target.value));

  overlay.querySelector("#cancel-btn").addEventListener("click", () => overlay.remove());
  overlay.querySelector("#save-btn").addEventListener("click", async () => {
    const type = overlay.querySelector("#t-type").value;
    let config = {};
    if (type === "local_path") config = { path: overlay.querySelector("#cfg-path").value.trim() };
    else if (type === "s3") config = {
      bucket: overlay.querySelector("#cfg-bucket").value.trim(),
      endpoint_url: overlay.querySelector("#cfg-endpoint").value.trim(),
      region: overlay.querySelector("#cfg-region").value.trim(),
      access_key: overlay.querySelector("#cfg-access").value.trim(),
      secret_key: overlay.querySelector("#cfg-secret").value,
      prefix: overlay.querySelector("#cfg-prefix").value.trim(),
    };
    else config = {
      remote: overlay.querySelector("#cfg-remote").value.trim(),
      remote_path: overlay.querySelector("#cfg-remote-path").value.trim(),
    };
    const payload = { name: overlay.querySelector("#t-name").value.trim() || type, type, config, enabled: true };
    try {
      await api("/api/settings/storage-targets", { method: "POST", body: JSON.stringify(payload) });
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
  } catch (e) {
    render(loginScreen());
  }
}
boot();
