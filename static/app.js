const API = ""; // same-origin

const SITE_LABELS = {
  piratebay:     "🏴 Pirate Bay",
  kickass:       "🥊 KickAss",
  glodls:        "Glodls",
  nyaasi:        "🌸 Nyaa.si",
  torlock:       "Torlock",
  "1337x":       "1337x",
  torrentgalaxy: "Torrent Galaxy",
  zooqle:        "Zooqle",
  bitsearch:     "Bitsearch",
  torrentfunk:   "TorrentFunk",
  magnetdl:      "MagnetDL",
  yts:           "YTS",
  limetorrent:   "LimeTorrent",
  audiobookbay:  "🎧 AudiobookBay",
};

// Sites working from AU (via Tor where needed) — shown first
const PREFERRED_SITES = ["piratebay", "audiobookbay", "nyaasi", "torlock", "kickass", "glodls", "1337x", "torrentgalaxy", "zooqle", "bitsearch", "torrentfunk"];
// Sites that block Tor exit nodes — shown last
const KNOWN_BLOCKED_SITES = ["magnetdl", "yts", "limetorrent"];

// Sites that work via Tor proxy
const TOR_SITES = ["piratebay", "audiobookbay", "kickass", "glodls"];

const _STATUS_ICONS = {
  ok:       { icon: "✓", label: "OK",       cls: "ok"   },
  degraded: { icon: "⚠", label: "Degraded", cls: "warn" },
  disabled: { icon: "✗", label: "Blocked",  cls: "bad"  },
  unknown:  { icon: "?", label: "Unknown",  cls: ""     },
};
let _siteHealthMap = {};

function $(id){ return document.getElementById(id); }
function esc(s){
  return (s ?? "").toString().replace(/[&<>"']/g, m => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]
  ));
}

function setStatus(msg){
  const el = $("status");
  if (el) el.innerHTML = msg;
}

function pill(kind, text){
  return `<span class="pill ${kind}">${esc(text)}</span>`;
}

function toast(msg, kind="info"){
  const host = $("toastHost");
  if(!host) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<div class="pill ${kind}">${esc(kind.toUpperCase())}</div><div class="msg">${esc(msg)}</div>`;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 200);
  }, 2600);
}

function showModal(id, show){
  const m = $(id);
  if(!m) return;
  if(show) m.classList.add("show");
  else m.classList.remove("show");
}

function setReviewBusy(on, text="Working…"){
  const ov = $("reviewBusy");
  const tx = $("reviewBusyText");
  if(!ov) return;
  ov.style.display = on ? "block" : "none";
  if(tx) tx.textContent = text;
}

// ─── Auth state ───────────────────────────────────────────────────────────────
let _authToken = "";
let _authUser = null; // { username, display_name, role }

function getAuthToken(){ return _authToken; }
function getApiKey(){ return (localStorage.getItem("torrentApiKey") || "").trim(); }
function setApiKey(v){ localStorage.setItem("torrentApiKey", (v || "").trim()); }
function getNdUser(){ return (localStorage.getItem("ndUser") || "").trim(); }
function getNdPass(){ return (localStorage.getItem("ndPass") || "").trim(); }
function setNdCreds(u, p){ localStorage.setItem("ndUser", (u||"").trim()); localStorage.setItem("ndPass", (p||"").trim()); }

function _saveAuth(token, user){
  _authToken = token;
  _authUser = user;
  localStorage.setItem("_jwt", token);
  localStorage.setItem("_user", JSON.stringify(user));
}

function _clearAuth(){
  _authToken = "";
  _authUser = null;
  localStorage.removeItem("_jwt");
  localStorage.removeItem("_user");
}

function _loadStoredAuth(){
  const t = localStorage.getItem("_jwt");
  const u = localStorage.getItem("_user");
  if(t && u){
    try{
      _authToken = t;
      _authUser = JSON.parse(u);
      return true;
    }catch(e){}
  }
  return false;
}

function isAdmin(){
  return _authUser && _authUser.role === "admin";
}

// ─── Authenticated fetch ──────────────────────────────────────────────────────
async function apiFetch(url, opts={}){
  const headers = new Headers(opts.headers || {});
  const key = getApiKey();
  if(key) headers.set("X-API-Key", key);
  if(_authToken) headers.set("Authorization", `Bearer ${_authToken}`);
  const timeoutMs = opts._timeout || 30000;
  delete opts._timeout;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...opts, headers, signal: controller.signal });
    clearTimeout(timer);
    return res;
  } catch(e) {
    clearTimeout(timer);
    throw e;
  }
}

// ─── Login / logout ───────────────────────────────────────────────────────────
async function doLogin(){
  const username = ($("loginUser")?.value || "").trim();
  const password = ($("loginPass")?.value || "");
  const errEl = $("loginError");
  const btn = $("loginBtn");

  if(errEl) errEl.textContent = "";
  if(!username || !password){
    if(errEl) errEl.textContent = "Username and password required.";
    return;
  }

  if(btn){ btn.disabled = true; btn.textContent = "Signing in…"; }

  try{
    const res = await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json().catch(() => ({}));

    if(!res.ok || !data?.success){
      const msg = data?.detail || data?.message || `Sign in failed (HTTP ${res.status})`;
      if(errEl) errEl.textContent = msg;
      if(btn){ btn.disabled = false; btn.textContent = "Sign In"; }
      return;
    }

    _saveAuth(data.token, {
      username: data.username,
      display_name: data.display_name,
      role: data.role,
    });

    showApp();
  }catch(e){
    if(errEl) errEl.textContent = "Could not reach server. Try again.";
    if(btn){ btn.disabled = false; btn.textContent = "Sign In"; }
  }
}

function doLogout(){
  // Fire and forget the server-side logout (stateless, just courtesy)
  apiFetch(`${API}/api/v1/auth/logout`, { method: "POST" }).catch(() => {});
  _clearAuth();
  // Clear stored credentials on explicit logout
  setApiKey("");
  setNdCreds("", "");
  if($("apiKey")) $("apiKey").value = "";
  if($("ndUser")) $("ndUser").value = "";
  if($("ndPass")) $("ndPass").value = "";
  showLoginScreen();
}

function showLoginScreen(){
  $("appShell").style.display = "none";
  $("loginScreen").style.display = "flex";
  if($("loginUser")) $("loginUser").value = "";
  if($("loginPass")) $("loginPass").value = "";
  if($("loginError")) $("loginError").textContent = "";
}

function showApp(){
  $("loginScreen").style.display = "none";
  $("appShell").style.display = "block";

  // Update user display
  const u = _authUser;
  if($("userDisplay") && u){
    $("userDisplay").textContent = `${u.display_name || u.username} (${u.role})`;
  }

  // All UI visible to all logged-in users
  if($("adminOnlySearch")) $("adminOnlySearch").style.display = "block";
  if($("adminQueueCard"))  $("adminQueueCard").style.display  = "block";
  if($("adminNotesCard"))  $("adminNotesCard").style.display  = "block";
  if($("filesTabBtn")) $("filesTabBtn").style.display = "inline-flex";

  showTab("search");

  // Restore saved credentials
  if($("apiKey")) $("apiKey").value = getApiKey();
  if($("ndUser")) $("ndUser").value = getNdUser();
  if($("ndPass")) $("ndPass").value = getNdPass();

  wireDownloadButtons();
  loadSites();
  startQueuePolling();
  pollServices();
  setInterval(pollServices, 8000);
}

// ─── Tab switching ────────────────────────────────────────────────────────────
let _activeTab = "search";

function showTab(tab){
  _activeTab = tab;
  $("searchTab").style.display = (tab === "search") ? "block" : "none";
  $("filesTab").style.display  = (tab === "files")  ? "block" : "none";
  $("sitesTab").style.display  = (tab === "sites")  ? "block" : "none";

  // Update active button styling
  ["searchTabBtn","filesTabBtn","sitesTabBtn"].forEach(id => {
    const el = $(id);
    if(el) el.classList.remove("active");
  });
  const activeBtn = tab === "search" ? "searchTabBtn" : tab === "files" ? "filesTabBtn" : "sitesTabBtn";
  $(activeBtn)?.classList.add("active");

  if(tab === "files") initFileManager();
  if(tab === "sites") renderSitesTab();
}

// ─── Utility ─────────────────────────────────────────────────────────────────
function fmtRate(kib){
  const n = Number(kib);
  if(!Number.isFinite(n) || n <= 0) return "";
  if(n >= 1024) return `${(n/1024).toFixed(1)} MiB/s`;
  return `${Math.round(n)} KiB/s`;
}

// ─── Search results ───────────────────────────────────────────────────────────
function row(item, site){
  const title = item.name || item.title || "(untitled)";
  const size  = item.size || "";
  const seeds = item.seeders ?? item.seeds ?? "";
  const leech = item.leechers ?? item.leeches ?? "";
  const cat   = item.category || "";
  const date  = item.date || "";
  const magnet = item.magnet || "";
  const torrent = item.torrent || "";

  const links = [
    item.url ? `<a href="${esc(item.url)}" target="_blank" rel="noreferrer">View</a>` : "",
    torrent ? `<a href="${esc(torrent)}" target="_blank" rel="noreferrer">.torrent</a>` : "",
    magnet ? `<a href="${esc(magnet)}" target="_blank" rel="noreferrer">Magnet</a>` : ""
  ].filter(Boolean).join(" • ");

  const canDownload = (magnet && magnet.startsWith("magnet:?")) || !!torrent;
  const dlDisabled = canDownload ? "" : "disabled";

  return `
    <div class="track"
      data-site="${esc(site)}"
      data-title="${esc(title)}"
      data-magnet="${esc(magnet)}"
      data-torrent="${esc(torrent)}">
      <div class="meta">
        <p class="t">${esc(title)}</p>
        <p class="a">${esc(cat)}${date ? " • " + esc(date) : ""}</p>
        <div class="mini">${esc(size)}${links ? " • " + links : ""}</div>
      </div>
      <div class="rightActions">
        <span class="pill ok">S ${esc(seeds)}</span>
        <span class="pill warn">L ${esc(leech)}</span>
        <button class="btn sm primary dlBtn" ${dlDisabled}>Download</button>
      </div>
    </div>
  `;
}

async function loadSites(){
  try{
    const res = await apiFetch(`${API}/api/v1/sites`);
    const data = await res.json();
    const allSites = data.supported_sites || [];
    const sel = $("site");
    sel.innerHTML = "";

    const preferred = allSites.filter(s => PREFERRED_SITES.includes(s));
    const middle    = allSites.filter(s => !PREFERRED_SITES.includes(s) && !KNOWN_BLOCKED_SITES.includes(s));
    const blocked   = allSites.filter(s => KNOWN_BLOCKED_SITES.includes(s));

    const addSep = (label) => {
      const o = document.createElement("option");
      o.disabled = true;
      o.textContent = `── ${label} ──`;
      sel.appendChild(o);
    };
    const addOpt = (s, suffix="") => {
      const o = document.createElement("option");
      o.value = s;
      o.textContent = (SITE_LABELS[s] || s) + suffix;
      if(suffix) o.style.color = "var(--muted)";
      sel.appendChild(o);
    };

    if(preferred.length) { addSep("Working"); preferred.forEach(s => addOpt(s, TOR_SITES.includes(s) ? " · Tor" : "")); }
    if(middle.length)    { addSep("Unverified"); middle.forEach(s => addOpt(s)); }
    if(blocked.length)   { addSep("Blocked"); blocked.forEach(s => addOpt(s, " ✗")); }

    const defaultSite = preferred.includes("piratebay") ? "piratebay" : (preferred[0] || allSites[0]);
    if([...sel.options].some(o => o.value === defaultSite)) sel.value = defaultSite;

    setStatus(sel.options.length ? pill("ok","Ready") : pill("warn","No supported sites returned"));
    renderSitesTab();
    if(typeof updateNdCredsVisibility === "function") updateNdCredsVisibility();
  }catch(e){
    setStatus(pill("bad", "Failed to load sites"));
  }
}

function renderSitesTab(){
  const host = $("sitesTabContent");
  if(!host) return;
  const allKnown = [...PREFERRED_SITES, ...KNOWN_BLOCKED_SITES];
  host.innerHTML = allKnown.map(s => {
    const h = _siteHealthMap[s];
    const label = SITE_LABELS[s] || s;
    const isTor = TOR_SITES.includes(s);
    const isBlocked = KNOWN_BLOCKED_SITES.includes(s);
    let inf, note;
    if(h && h.status === "ok")       { inf = _STATUS_ICONS.ok;       note = isTor ? "Via Tor" : "Direct"; }
    else if(h && h.status === "degraded") { inf = _STATUS_ICONS.degraded; note = h.reason || "Degraded"; }
    else if(h && h.status === "disabled") { inf = _STATUS_ICONS.disabled; note = h.reason || "Blocked"; }
    else if(isBlocked)               { inf = _STATUS_ICONS.disabled;  note = "Blocks Tor exits"; }
    else if(isTor)                   { inf = _STATUS_ICONS.ok;        note = "Via Tor"; }
    else                             { inf = _STATUS_ICONS.ok;        note = "Direct"; }

    return `<div class="siteRow">
      <div>
        <div class="siteRowName">${esc(label)}</div>
        <div class="mini" style="color:var(--muted);margin-top:2px;">${esc(note)}</div>
      </div>
      <span class="pill ${inf.cls}">${inf.icon} ${inf.label}</span>
    </div>`;
  }).join("");
}

async function doSearch(){
  const site = $("site")?.value || "piratebay";
  const q = ($("q")?.value || "").trim();
  const limit = $("limit")?.value || "10";

  if(!q){
    setStatus(pill("warn", "Enter a search query"));
    return;
  }

  setStatus(pill("info", "Searching…"));
  const results = $("results");
  if(results) results.innerHTML = "";

  const url = `${API}/api/v1/search?site=${encodeURIComponent(site)}&query=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`;

  if(site === "audiobookbay"){
    setStatus(pill("warn", "Searching AudiobookBay via Tor — may take up to 2 mins if retrying…"));
  } else {
    setStatus(pill("info", "Searching…"));
  }

  try{
    const res = await apiFetch(url, { _timeout: 150000 });
    const data = await res.json().catch(() => ({}));
    if(!res.ok){
      setStatus(pill("bad", data?.error || data?.detail || `HTTP ${res.status}`));
      return;
    }
    const items = data?.data || [];
    if(items.length === 0){
      setStatus(pill("warn", "No results"));
      return;
    }
    if(results) results.innerHTML = items.map(it => row(it, site)).join("");
    setStatus(pill("ok", `Found ${items.length} result(s)`));
  }catch(e){
    setStatus(pill("bad", "Request failed"));
  }
}

async function triggerDownload(trackEl, btn){
  const site   = trackEl.getAttribute("data-site") || "piratebay";
  const title  = trackEl.getAttribute("data-title") || "(untitled)";
  const magnet = trackEl.getAttribute("data-magnet") || "";
  const torrent = trackEl.getAttribute("data-torrent") || "";

  const hasMagnet = magnet.startsWith("magnet:?");
  const hasTorrent = !!torrent;

  if(!hasMagnet && !hasTorrent){
    btn.disabled = true;
    btn.textContent = "N/A";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Queuing…";
  toast("Queuing download…", "info");

  try{
    const res = await apiFetch(`${API}/api/v1/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site, title, magnet: hasMagnet ? magnet : "", torrent: hasTorrent ? torrent : "", nd_user: getNdUser(), nd_pass: getNdPass() }),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok || !data?.success){
      const msg = data?.message || data?.detail || data?.error || `HTTP ${res.status}`;
      btn.disabled = false;
      btn.textContent = "Download";
      toast(msg, "bad");
      setStatus(pill("bad", msg));
      return;
    }
    btn.textContent = "Queued";
    toast(data?.deduped ? "Already queued (deduped)" : "Queued", data?.deduped ? "warn" : "ok");
  }catch(e){
    btn.disabled = false;
    btn.textContent = "Download";
    setStatus(pill("bad", "Download request failed"));
  }
}

function wireDownloadButtons(){
  const results = $("results");
  if(!results) return;
  results.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".dlBtn");
    if(!btn) return;
    const trackEl = btn.closest(".track");
    if(!trackEl) return;
    triggerDownload(trackEl, btn);
  });
}

// ─── Queue panel ──────────────────────────────────────────────────────────────
function statusPillFor(st){
  if(st === "imported")    return pill("ok",   "imported");
  if(st === "processing")  return pill("info",  "processing");
  if(st === "ready")       return pill("warn",  "ready");
  if(st === "completed")   return pill("ok",    "completed");
  if(st === "downloading") return pill("info",  "downloading");
  if(st === "failed")      return pill("bad",   "failed");
  if(st === "cancelled")   return pill("warn",  "cancelled");
  return pill("warn", "queued");
}

function renderQueue(items){
  const host = $("queue");
  const qs = $("queueStatus");
  if(!host) return;

  if(!Array.isArray(items) || items.length === 0){
    host.innerHTML = `<div class="mini" style="color:var(--muted);">No downloads yet.</div>`;
    if(qs) qs.textContent = "";
    return;
  }

  if(qs) qs.textContent = `Showing ${items.length} item(s)`;

  host.innerHTML = items.map(it => {
    const title = it.title || "(untitled)";
    const st = it.status || "queued";
    const prog = Number.isFinite(it.progress) ? Math.max(0, Math.min(100, it.progress)) : 0;
    const err = it.error ? `<div class="mini" style="color: var(--red);">Error: ${esc(it.error)}</div>` : "";
    const state = it.transmission_state ? ` • ${esc(it.transmission_state)}` : "";
    const dl = it.rate_download_kib ? fmtRate(it.rate_download_kib) : "";
    const ul = it.rate_upload_kib ? fmtRate(it.rate_upload_kib) : "";
    const eta = it.eta ? esc(it.eta) : "";
    const speedLine = (dl || ul || eta || state)
      ? `<div class="mini" style="color:var(--muted);">${dl ? `DL ${esc(dl)}` : ""}${ul ? ` • UL ${esc(ul)}` : ""}${eta ? ` • ETA ${eta}` : ""}${state}</div>`
      : "";
    const canCancel = (st === "queued" || st === "downloading");
    const cancelBtn = canCancel ? `<button class="btn sm cancelBtn" data-id="${esc(it.id)}">Cancel</button>` : "";
    const canReview = (st === "ready" || st === "completed");
    const reviewBtn = canReview ? `<button class="btn sm primary reviewBtn" data-id="${esc(it.id)}">Review</button>` : "";

    return `
      <div class="queueItem">
        <div class="queueTop">
          <div class="queueTitle">${esc(title)}</div>
          <div class="queueMeta">${statusPillFor(st)} ${reviewBtn} ${cancelBtn}</div>
        </div>
        ${speedLine}
        ${["downloading","completed","ready","processing","imported"].includes(st) ? `
          <div class="progressBar"><div style="width:${prog}%;"></div></div>
          <div class="mini">${esc(prog)}%</div>
        ` : ""}
        ${err}
      </div>
    `;
  }).join("");
}

function renderServicesHealth(payload){
  const host = $("servicesBox");
  if(!host) return;
  const s = payload?.services || {};
  const one = (name, obj) => {
    const ok = !!obj?.ok;
    const code = obj?.status;
    const err = obj?.error;
    const right = ok ? pill("ok", code ? `OK ${code}` : "OK") : pill("bad", err || (code ? `HTTP ${code}` : "down"));
    return `<div style="display:flex;justify-content:space-between;gap:10px;margin:6px 0;">
      <span>${esc(name)}</span><span>${right}</span>
    </div>`;
  };
  host.innerHTML =
    one("Transmission", s.transmission) +
    one("Jellyfin", s.jellyfin) +
    one("Navidrome", s.navidrome);
}

async function pollServices(){
  try{
    const res = await apiFetch(`${API}/api/services/health`);
    const data = await res.json().catch(() => ({}));
    if(res.ok) renderServicesHealth(data);
  }catch(e){
    const host = $("servicesBox");
    if(host) host.innerHTML = `<span class="pill bad">DOWN</span> <span style="margin-left:8px;">Services probe failed</span>`;
  }
}

let currentReviewId = null;
let reviewData = null;
let fileActions = new Map();

function rebuildFilesUI(){
  const keepHost = $("filesKeep");
  const delHost = $("filesDelete");
  if(!keepHost || !delHost) return;

  // Render all files in one list, sorted: keep first then delete
  const all = [...fileActions.values()].sort((a,b) => {
    if(a.action === b.action) return a.rel.localeCompare(b.rel);
    return a.action === "keep" ? -1 : 1;
  });

  const rowHtml = (f) => {
    const size = Number.isFinite(f.size) ? `${(f.size/1024/1024).toFixed(2)} MB` : "";
    const shownName = f.newName ? `→ ${f.newName}` : "";
    const renameBtn = (f.action === "keep")
      ? `<button type="button" class="btn sm renameBtn" data-rel="${esc(f.rel)}">Rename</button>`
      : "";
    const parts = f.rel.split("/");
    const displayName = parts[parts.length - 1] || f.rel;
    const parentPath = parts.length > 1 ? parts.slice(0, -1).join("/") + "/" : "";
    const badge = f.action === "keep"
      ? `<span class="pill ok" style="font-size:10px;padding:2px 6px;">Keep</span>`
      : `<span class="pill bad" style="font-size:10px;padding:2px 6px;">Delete</span>`;
    return `
      <div class="fileRow">
        <input type="checkbox" data-rel="${esc(f.rel)}" ${f.action === "keep" ? "checked" : ""} title="Toggle keep/delete" />
        <div class="fn" title="${esc(f.rel)}">
          ${parentPath ? `<div class="fileRowDir mono">${esc(parentPath)}</div>` : ""}
          <div class="fileRowName mono">${esc(displayName)}</div>
          ${shownName ? `<div class="mini" style="color:var(--muted); margin-top:2px;">${esc(shownName)}</div>` : ""}
          <div class="fileRowMeta">
            ${badge}
            ${size ? `<span class="fs">${esc(size)}</span>` : ""}
            ${renameBtn}
          </div>
        </div>
      </div>
    `;
  };

  const html = all.map(rowHtml).join("") || `<div class="mini" style="color:var(--muted);">No files</div>`;
  keepHost.innerHTML = html;
  delHost.innerHTML = ""; // single list mode - all in keepHost
}

function computeDestPreview(){
  if(!reviewData) return "";
  const destType = ($("destType")?.value || "movies").toLowerCase();
  const title = ($("titleInput")?.value || "").trim();
  const year = ($("yearInput")?.value || "").trim();
  const season = parseInt(($("seasonInput")?.value || "1"), 10);
  const customRow = $("customDestRow");
  if(customRow) customRow.style.display = (destType === "other") ? "block" : "none";
  const seasonCol = $("seasonCol");
  const yearCol = $("yearCol");
  const isTV = (destType === "tv");
  if(seasonCol) seasonCol.style.display = isTV ? "block" : "none";
  if(yearCol) yearCol.style.display = (destType === "other" || destType === "music") ? "none" : "block";
  if(destType === "movies"){
    const y = year ? ` (${year.slice(0,4)})` : "";
    return `/mnt/media/Movies/${title || "Untitled"}${y}`;
  }
  if(destType === "tv"){
    const s = Number.isFinite(season) && season > 0 ? season : 1;
    const y = year ? ` (${year.slice(0,4)})` : "";
    return `/mnt/media/TV/${(title || "Untitled Show")}${y}/Season ${String(s).padStart(2,"0")}`;
  }
  if(destType === "music"){
    return `/mnt/media/Music/_incoming`;
  }
  const sub = ($("customSubfolder")?.value || "").trim().replace(/^\/+/, "");
  return `/mnt/media/${sub || "Other"}`;
}

function updateMusicMetaVisibility(){
  const destType = (($("destType")?.value || "movies") + "").trim().toLowerCase();
  const box = $("musicMetaBox");
  if(!box) return;
  if(destType !== "music"){ box.style.display = "none"; return; }
  box.style.display = "block";
  const kind = ($("musicKind")?.value || "music").toLowerCase();
  const mf = $("musicFields");
  const af = $("audiobookFields");
  if(mf) mf.style.display = (kind === "music") ? "block" : "none";
  if(af) af.style.display = (kind === "audiobook") ? "block" : "none";
}

function applySuggestedToInputs(){
  const suggested = (reviewData && reviewData.suggested) ? reviewData.suggested : {};
  const destType = ($("destType")?.value || "movies").toLowerCase();
  if(destType === "tv"){
    $("titleInput").value = (suggested.show || suggested.title || reviewData.title || "Untitled Show").toString();
    $("seasonInput").value = String(Number(suggested.season || 1) || 1);
  }else if(destType === "movies"){
    $("titleInput").value = (suggested.title || reviewData.title || "Untitled").toString();
    $("yearInput").value = (suggested.year || "").toString();
  }else{
    $("titleInput").value = (reviewData.title || "Untitled").toString();
    $("yearInput").value = "";
  }
  $("reviewDest").textContent = computeDestPreview();
}

function updateDestPreview(){
  const p = computeDestPreview();
  if($("reviewDest")) $("reviewDest").textContent = p;
}

function updateOptionsVisibility(){
  const destType = (($("destType")?.value || "movies") + "").trim().toLowerCase();
  const tvRow = $("tvAutoSplitRow");
  const musicRow = $("musicRunBeetsRow");
  const musicHint = $("musicLayoutHint");
  if(tvRow) tvRow.style.display = "none";
  if(musicRow) musicRow.style.display = "none";
  if(musicHint) musicHint.style.display = "none";
  if(destType === "tv"){
    if(tvRow) tvRow.style.display = "flex";
  } else if(destType === "music"){
    if(musicRow) musicRow.style.display = "flex";
    if(musicHint) musicHint.style.display = "block";
  }
}

async function openReview(id){
  try{
    toast("Preparing review…", "info");
    setReviewBusy(true, "Preparing file list and TMDb matches…");
    const res = await apiFetch(`${API}/api/v1/prepare/${encodeURIComponent(id)}`);
    const data = await res.json().catch(()=>({}));
    if(!res.ok || !data?.success){
      toast(data?.detail || `Prepare failed (HTTP ${res.status})`, "bad");
      setReviewBusy(false);
      return;
    }

    currentReviewId = id;
    reviewData = data;

    try{
      const ui = data.ui_settings || {};
      if(ui.music_kind_guess && $("musicKind")) $("musicKind").value = ui.music_kind_guess;
      if(typeof ui.tv_auto_split_default !== "undefined") $("tvAutoSplit").checked = !!ui.tv_auto_split_default;
      else $("tvAutoSplit").checked = true;
      if(typeof ui.music_recommend_beets !== "undefined") $("musicRunBeets").checked = !!ui.music_recommend_beets;
      else $("musicRunBeets").checked = true;
    }catch(e){}

    $("reviewSub").textContent = `${(data.transmission_state || data.category || "").toUpperCase()} • ${data.title || ""}`;

    const guess = (data.category || "movies").toLowerCase();
    const destTypeEl = $("destType");
    if(destTypeEl){
      if(["movies","tv","music"].includes(guess)) destTypeEl.value = guess;
      else destTypeEl.value = "movies";
    }

    updateOptionsVisibility();
    updateMusicMetaVisibility();

    const sel = $("matchSelect");
    const tmdbHint = $("tmdbHint");
    if(sel){
      sel.innerHTML = "";
      const candidates = Array.isArray(data.candidates) ? data.candidates : [];
      const suggested = data.suggested || {};
      const opts = [];
      if(suggested && (suggested.tmdb_id || suggested.title || suggested.show)){
        opts.push({ value: "suggested", label: `Suggested: ${suggested.label || suggested.title || suggested.show || "match"}`, meta: suggested });
      }
      candidates.forEach((c, idx) => {
        opts.push({ value: `c${idx}`, label: c.label || c.title || c.show || `Match ${idx+1}`, meta: c });
      });
      if(opts.length === 0){
        const o = document.createElement("option");
        o.value = "none";
        o.textContent = "No TMDb match found (using title fallback)";
        sel.appendChild(o);
        if(tmdbHint) tmdbHint.textContent = data.tmdb_debug ? `TMDb: ${data.tmdb_debug}` : "TMDb returned no matches.";
      }else{
        opts.forEach(o2 => {
          const o = document.createElement("option");
          o.value = o2.value;
          o.textContent = o2.label;
          o.dataset.meta = JSON.stringify(o2.meta || {});
          sel.appendChild(o);
        });
        if(tmdbHint) tmdbHint.textContent = data.tmdb_debug ? `TMDb: ${data.tmdb_debug}` : "";
      }
    }

    fileActions = new Map();
    const files = Array.isArray(data.files) ? data.files : [];
    for(const f of files){
      const rel = (f.rel || "").toString();
      if(!rel) continue;
      fileActions.set(rel, {
        rel,
        size: Number(f.size || 0),
        action: (f.default_action === "keep") ? "keep" : "delete",
        newName: ""
      });
    }
    rebuildFilesUI();
    applySuggestedToInputs();
    updateDestPreview();
    showModal("reviewModal", true);
    setReviewBusy(false);
    toast("Review ready", "ok");
  }catch(e){
    toast("Prepare failed", "bad");
    setReviewBusy(false);
  }
}

async function approveMove(){
  if(!currentReviewId || !reviewData) return;
  try{
    toast("Processing…", "info");
    setReviewBusy(true, "Moving files into library…");

    let meta = {};
    const sel = $("matchSelect");
    if(sel && sel.selectedOptions && sel.selectedOptions[0]){
      try{ meta = JSON.parse(sel.selectedOptions[0].dataset.meta || "{}"); }catch(e){ meta = {}; }
    }

    meta.options = meta.options || {};
    meta.options.tv_auto_split = !!($("tvAutoSplit")?.checked);
    meta.options.music_run_beets = !!($("musicRunBeets")?.checked);

    const destType = ($("destType")?.value || "movies").toLowerCase();
    const title = ($("titleInput")?.value || "").trim();
    const year  = ($("yearInput")?.value || "").trim();
    const season = parseInt(($("seasonInput")?.value || "1"), 10);
    const customSubfolder = ($("customSubfolder")?.value || "").trim();

    const keep = [];
    const del = [];
    for(const v of fileActions.values()){
      if(v.action === "keep") keep.push(v.rel);
      else del.push(v.rel);
    }

    if(destType === "music"){
      const kind = ($("musicKind")?.value || "music").toLowerCase();
      meta.music = meta.music || {};
      meta.music.kind = kind;
      if(kind === "audiobook"){
        meta.music.author = ($("abAuthor")?.value || "").trim();
        meta.music.book   = ($("abBook")?.value || "").trim();
        meta.music.year   = ($("abYear")?.value || "").trim();
      }else{
        meta.music.artist = ($("musicArtist")?.value || "").trim();
        meta.music.album  = ($("musicAlbum")?.value || "").trim();
        meta.music.year   = ($("musicYear")?.value || "").trim();
      }
    }

    const overrides = {};
    for(const v of fileActions.values()){
      if(v.action === "keep" && v.newName && v.newName.trim()){
        overrides[v.rel] = v.newName.trim();
      }
    }

    const payload = {
      id: currentReviewId,
      dest_type: destType,
      custom_subfolder: customSubfolder,
      meta,
      name_override: { title, year, season: Number.isFinite(season) ? season : 1 },
      keep_files: keep,
      delete_files: del,
      file_name_overrides: overrides,
    };

    const res = await apiFetch(`${API}/api/v1/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok || !data?.success){
      toast(data?.detail || data?.message || `Move failed (HTTP ${res.status})`, "bad");
      setReviewBusy(false);
      return;
    }

    const nd = data?.navidrome_scan;
    const pl = data?.navidrome_playlist;
    const deduped = data?.deduped;
    if(destType === "music"){
      if(deduped && pl){
        toast(`Already in library 📚 Playlist "${pl}" created for your account`, "ok");
      } else if(deduped){
        toast("Already in library — skipped download. Playlist creation failed.", "warn");
      } else if(pl){
        toast(`Imported 🎧 Playlist created: "${pl}"`, "ok");
      } else {
        toast(nd ? "Imported + Navidrome scan triggered" : "Imported (Navidrome scan not triggered)", nd ? "ok" : "warn");
      }
    }else{
      toast("Imported + Jellyfin refresh triggered", "ok");
    }

    if(data.tv_summary && data.tv_summary.seasons){
      const lines = [];
      lines.push("Moved:");
      lines.push(data.tv_summary.show_base.replace("/mnt/media/", "") + "/");
      data.tv_summary.seasons.forEach(s => {
        lines.push(`  Season ${String(s.season).padStart(2,"0")}/ (${s.files} files)`);
      });
      toast(lines.join(" "), "ok");
    }else if(data.destination_dir){
      toast(`Moved to: ${data.destination_dir}`, "ok");
    }

    showModal("reviewModal", false);
    currentReviewId = null;
    reviewData = null;
    loadQueueOnce();
  }catch(e){
    toast("Move failed", "bad");
    setReviewBusy(false);
  }
}

async function loadQueueOnce(){
  const key = getApiKey();
  if(!key){
    const host = $("queue");
    if(host) host.innerHTML = `<div class="mini" style="color:var(--muted);">Set API key to view queue.</div>`;
    return;
  }
  try{
    const res = await apiFetch(`${API}/api/v1/queue`);
    const data = await res.json().catch(() => ([]));
    if(!res.ok){
      const host = $("queue");
      if(host) host.innerHTML = `<div class="mini" style="color:var(--muted);">Queue unavailable (HTTP ${res.status}).</div>`;
      return;
    }
    renderQueue(data);
  }catch(e){
    const host = $("queue");
    if(host) host.innerHTML = `<div class="mini" style="color:var(--muted);">Queue request failed.</div>`;
  }
}

async function cancelDownloadById(id){
  try{
    const res = await apiFetch(`${API}/api/v1/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok || !data?.success){
      const msg = data?.message || data?.detail || `HTTP ${res.status}`;
      setStatus(pill("bad", msg));
      return;
    }
    setStatus(pill("ok", "Cancelled"));
    loadQueueOnce();
  }catch(e){
    setStatus(pill("bad", "Cancel failed"));
  }
}

function startQueuePolling(){
  loadQueueOnce();
  setInterval(loadQueueOnce, 5000);
}

// ─── File Manager ─────────────────────────────────────────────────────────────
let _fmState = {
  roots: [],
  currentRoot: null,
  currentPath: "",
  pendingRename: null,  // { root, path, name }
  pendingDelete: null,  // { root, path, name, is_dir }
};


async function fmSearch(query){
  if(!query.trim()) return;
  const host = $("fmList");
  if(host) host.innerHTML = `<div class="mini" style="color:var(--muted);padding:12px;">Searching…</div>`;
  try{
    const params = new URLSearchParams({ root: _fmState.currentRoot || "", path: _fmState.currentPath || "", query: query.trim(), recursive: "true" });
    const res = await apiFetch(`${API}/api/v1/files/search?${params}`);
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ toast(data.detail || "Search failed","bad"); return; }
    const entries = Array.isArray(data.entries) ? data.entries : [];
    if(!host) return;
    if(entries.length === 0){
      host.innerHTML = `<div class="mini" style="color:var(--muted);padding:12px;">No results for "${esc(query)}"</div>`;
      return;
    }
    host.innerHTML = entries.map(e => {
      const icon = e.is_dir ? "📁" : "📄";
      const size = (!e.is_dir && e.size) ? `<span class="fmSize">${(e.size/1024/1024).toFixed(1)} MB</span>` : "";
      return `<div class="fmEntry" data-path="${esc(e.path)}" data-isdir="${e.is_dir}">
        <span class="fmIcon">${icon}</span>
        <span class="fmName" title="${esc(e.path)}">${esc(e.name)}</span>
        <div class="fmActions">
          ${size}
          ${!e.is_dir ? `<button class="btn sm fmDeleteBtn" data-path="${esc(e.path)}">Delete</button>` : ""}
          ${e.is_dir ? `<button class="btn sm" onclick="_fmState.currentRoot='${esc(_fmState?.currentRoot||"")}';loadFmDir('${esc(e.path)}')">Open</button>` : ""}
        </div>
      </div>`;
    }).join("");
  }catch(err){
    toast("Search error: "+err.message,"bad");
  }
}

async function initFileManager(){
  await loadFmRoots();
}

async function loadFmRoots(){
  try{
    const res = await apiFetch(`${API}/api/v1/files/roots`);
    if(res.status === 401){ doLogout(); return; }
    const data = await res.json().catch(() => ({}));
    const roots = data.roots || [];
    _fmState.roots = roots;

    const sel = $("fmRootSelect");
    const row = $("fmRootRow");

    if(roots.length <= 1){
      if(row) row.style.display = "none";
      _fmState.currentRoot = roots[0]?.key || "";
    } else {
      if(row) row.style.display = "block";
      if(sel){
        sel.innerHTML = "";
        roots.forEach(r => {
          const o = document.createElement("option");
          o.value = r.key;
          o.textContent = r.label + (r.exists ? "" : " (missing)");
          sel.appendChild(o);
        });
        _fmState.currentRoot = roots[0]?.key || "";
        sel.value = _fmState.currentRoot;
      }
    }

    _fmState.currentPath = "";
    await loadFmDir();
  }catch(e){
    $("fmList").innerHTML = `<div class="mini" style="color:var(--red);">Failed to load file manager roots.</div>`;
  }
}

async function loadFmDir(path){
  const root = _fmState.currentRoot;
  const p = (path !== undefined) ? path : _fmState.currentPath;
  _fmState.currentPath = p;

  const fmList = $("fmList");
  const pathDisplay = $("fmPathDisplay");
  if(fmList) fmList.innerHTML = `<div class="mini" style="color:var(--muted);">Loading…</div>`;

  const params = new URLSearchParams({ root: root || "", path: p || "" });

  try{
    const res = await apiFetch(`${API}/api/v1/files/list?${params}`);
    if(res.status === 401){ doLogout(); return; }
    const data = await res.json().catch(() => ({}));

    if(!res.ok){
      if(fmList) fmList.innerHTML = `<div class="mini" style="color:var(--red);">${esc(data?.detail || `Error ${res.status}`)}</div>`;
      return;
    }

    _fmState.currentPath = data.current_path || "";

    // Update path display
    const displayPath = _fmState.currentRoot + (_fmState.currentPath ? "/" + _fmState.currentPath : "");
    if(pathDisplay) pathDisplay.textContent = displayPath || "/";

    // Breadcrumb
    renderFmBreadcrumb(_fmState.currentRoot, _fmState.currentPath);

    // Nav up button
    const navUp = $("fmNavUp");
    if(navUp) navUp.disabled = !_fmState.currentPath;

    // Entries
    renderFmEntries(data.entries || [], data.current_path || "");
  }catch(e){
    if(fmList) fmList.innerHTML = `<div class="mini" style="color:var(--red);">Request failed.</div>`;
  }
}

function renderFmBreadcrumb(root, relPath){
  const host = $("fmBreadcrumb");
  if(!host) return;

  const parts = (relPath || "").split("/").filter(Boolean);
  let html = `<span class="fmCrumb" data-path="" style="cursor:pointer;">${esc(root)}</span>`;
  let cumPath = "";
  parts.forEach(part => {
    cumPath += (cumPath ? "/" : "") + part;
    const cp = cumPath;
    html += ` <span style="color:var(--muted);">/</span> <span class="fmCrumb" data-path="${esc(cp)}" style="cursor:pointer;">${esc(part)}</span>`;
  });
  host.innerHTML = html;
}

function renderFmEntries(entries, currentPath){
  const fmList = $("fmList");
  if(!fmList) return;

  if(!entries || entries.length === 0){
    fmList.innerHTML = `<div class="mini" style="color:var(--muted); padding:16px 0;">This folder is empty.</div>`;
    return;
  }

  fmList.innerHTML = entries.map(e => {
    const icon = e.is_dir ? "📁" : "📄";
    const size = (!e.is_dir && e.size != null)
      ? `<span class="fmSize">${fmtSize(e.size)}</span>`
      : "";
    return `
      <div class="fmEntry" data-rel="${esc(e.rel)}" data-is-dir="${e.is_dir ? "1" : "0"}" data-name="${esc(e.name)}">
        <span class="fmIcon">${icon}</span>
        <span class="fmName">${esc(e.name)}</span>
        ${size}
        <span class="fmActions">
          <button class="btn sm fmRenameBtn" data-rel="${esc(e.rel)}" data-name="${esc(e.name)}">Rename</button>
          <button class="btn sm fmDeleteBtn" data-rel="${esc(e.rel)}" data-name="${esc(e.name)}" data-is-dir="${e.is_dir ? "1" : "0"}" style="border-color:rgba(255,69,58,.4);color:var(--red);">Delete</button>
        </span>
      </div>
    `;
  }).join("");
}

function fmtSize(bytes){
  if(!bytes) return "0 B";
  if(bytes >= 1073741824) return `${(bytes/1073741824).toFixed(1)} GB`;
  if(bytes >= 1048576) return `${(bytes/1048576).toFixed(1)} MB`;
  if(bytes >= 1024) return `${(bytes/1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function fmNavInto(rel){
  loadFmDir(rel);
}

function fmNavUp(){
  const p = _fmState.currentPath;
  if(!p) return;
  const parts = p.split("/").filter(Boolean);
  parts.pop();
  loadFmDir(parts.join("/"));
}

function openRenameModal(rel, name){
  _fmState.pendingRename = { root: _fmState.currentRoot, path: rel, name };
  if($("renameOldName")) $("renameOldName").textContent = `Renaming: ${name}`;
  if($("renameInput")) $("renameInput").value = name;
  if($("renameError")) $("renameError").textContent = "";
  showModal("renameModal", true);
  setTimeout(() => {
    const inp = $("renameInput");
    if(inp){ inp.focus(); inp.select(); }
  }, 80);
}

async function doRename(){
  const { root, path, name } = _fmState.pendingRename || {};
  if(!path) return;
  const newName = ($("renameInput")?.value || "").trim();
  if(!newName){
    if($("renameError")) $("renameError").textContent = "Name cannot be empty.";
    return;
  }

  try{
    const res = await apiFetch(`${API}/api/v1/files/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root, path, new_name: newName }),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok || !data?.success){
      const msg = data?.detail || `Rename failed (HTTP ${res.status})`;
      if($("renameError")) $("renameError").textContent = msg;
      return;
    }
    showModal("renameModal", false);
    _fmState.pendingRename = null;
    toast(`Renamed to: ${newName}`, "ok");
    await loadFmDir(_fmState.currentPath);
  }catch(e){
    if($("renameError")) $("renameError").textContent = "Rename request failed.";
  }
}

function openDeleteModal(rel, name, isDir){
  _fmState.pendingDelete = { root: _fmState.currentRoot, path: rel, name, is_dir: isDir };
  if($("deleteTargetName")) $("deleteTargetName").textContent = `Target: ${name}`;
  const recRow = $("deleteRecursiveRow");
  if(recRow) recRow.style.display = isDir ? "flex" : "none";
  if($("deleteRecursive")) $("deleteRecursive").checked = false;
  showModal("deleteModal", true);
}

async function doDelete(){
  const { root, path, name, is_dir } = _fmState.pendingDelete || {};
  if(!path) return;
  const recursive = is_dir && !!($("deleteRecursive")?.checked);

  try{
    const res = await apiFetch(`${API}/api/v1/files/delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root, path, recursive }),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok || !data?.success){
      const msg = data?.detail || `Delete failed (HTTP ${res.status})`;
      toast(msg, "bad");
      showModal("deleteModal", false);
      return;
    }
    showModal("deleteModal", false);
    _fmState.pendingDelete = null;
    toast(`Deleted: ${name}`, "ok");
    await loadFmDir(_fmState.currentPath);
  }catch(e){
    toast("Delete request failed.", "bad");
    showModal("deleteModal", false);
  }
}

async function doMkdir(){
  const name = prompt("New folder name:");
  if(!name || !name.trim()) return;
  try{
    const res = await apiFetch(`${API}/api/v1/files/mkdir`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        root: _fmState.currentRoot,
        path: _fmState.currentPath,
        name: name.trim()
      }),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok || !data?.success){
      toast(data?.detail || `mkdir failed (HTTP ${res.status})`, "bad");
      return;
    }
    toast(`Created: ${name.trim()}`, "ok");
    await loadFmDir(_fmState.currentPath);
  }catch(e){
    toast("mkdir request failed.", "bad");
  }
}

// ─── Event wiring ─────────────────────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {

  // ── Login ──
  $("loginBtn")?.addEventListener("click", doLogin);
  $("loginPass")?.addEventListener("keydown", e => { if(e.key === "Enter") doLogin(); });
  $("loginUser")?.addEventListener("keydown", e => { if(e.key === "Enter") $("loginPass")?.focus(); });

  // ── Logout ──
  $("logoutBtn")?.addEventListener("click", doLogout);

  // ── Tabs ──
  $("searchTabBtn")?.addEventListener("click", () => showTab("search"));
  $("filesTabBtn")?.addEventListener("click",  () => showTab("files"));
  $("sitesTabBtn")?.addEventListener("click",  () => showTab("sites"));

  $("fmSearchBtn")?.addEventListener("click", () => {
    const q = $("fmSearchInput")?.value || "";
    if(q.trim()) fmSearch(q);
  });
  $("fmSearchInput")?.addEventListener("keydown", e => {
    if(e.key === "Enter") { const q = $("fmSearchInput")?.value || ""; if(q.trim()) fmSearch(q); }
  });

  // ── Search ──
  $("go")?.addEventListener("click", doSearch);
  $("q")?.addEventListener("keydown", (e) => { if(e.key === "Enter") doSearch(); });

  // ── Review modal ──
  $("closeReview")?.addEventListener("click", () => showModal("reviewModal", false));
  $("approveMove")?.addEventListener("click", approveMove);
  $("reviewModal")?.addEventListener("click", (e) => {
    if(e.target?.id === "reviewModal") showModal("reviewModal", false);
  });
  $("reviewModal")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".renameBtn");
    if(!btn) return;
    const rel = btn.getAttribute("data-rel");
    if(!rel || !fileActions.has(rel)) return;
    const v = fileActions.get(rel);
    const current = v.newName || "";
    const suggested = prompt("Rename file (leave blank to clear). Include extension or it will be preserved:", current);
    if(suggested === null) return;
    v.newName = (suggested || "").trim();
    fileActions.set(rel, v);
    rebuildFilesUI();
  });

  // ── Queue ──
  $("queue")?.addEventListener("click", (e) => {
    const r = e.target?.closest?.(".reviewBtn");
    if(r){
      const id = r.getAttribute("data-id");
      if(id){
        r.disabled = true;
        r.textContent = "Loading…";
        openReview(id).finally(() => {
          r.disabled = false;
          r.textContent = "Review";
        });
      }
      return;
    }
  });
  $("queue")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".cancelBtn");
    if(!btn) return;
    const id = btn.getAttribute("data-id");
    if(!id) return;
    cancelDownloadById(id);
  });

  // ── File checkboxes in review ──
  $("reviewModal")?.addEventListener("change", (e) => {
    const cb = e.target?.closest?.("input[type='checkbox'][data-rel]");
    if(!cb) return;
    const rel = cb.getAttribute("data-rel");
    if(!rel || !fileActions.has(rel)) return;
    const v = fileActions.get(rel);
    v.action = cb.checked ? "keep" : "delete";
    fileActions.set(rel, v);
    rebuildFilesUI();
  });

  // ── Dest + name preview ──
  ["destType","customSubfolder","titleInput","yearInput","seasonInput"].forEach(id => {
    $(id)?.addEventListener("input", updateDestPreview);
    $(id)?.addEventListener("change", () => {
      updateDestPreview();
      if(id === "destType"){ applySuggestedToInputs(); updateOptionsVisibility(); updateMusicMetaVisibility(); }
      else { updateOptionsVisibility(); updateMusicMetaVisibility(); }
    });
  });

  $("matchSelect")?.addEventListener("change", () => {
    if(!reviewData) return;
    const sel = $("matchSelect");
    if(sel && sel.selectedOptions && sel.selectedOptions[0]){
      try{
        const meta = JSON.parse(sel.selectedOptions[0].dataset.meta || "{}");
        if(meta && typeof meta === "object") reviewData.suggested = meta;
      }catch(e){}
    }
    applySuggestedToInputs();
    updateDestPreview();
    updateOptionsVisibility();
    updateMusicMetaVisibility();
  });

  $("musicKind")?.addEventListener("change", updateMusicMetaVisibility);

  // ── Rescan ──
  $("rescanBtn")?.addEventListener("click", async () => {
    try{
      toast("Rescanning libraries…", "info");
      const res = await apiFetch(`${API}/api/v1/library/rescan`, { method: "POST" });
      const data = await res.json().catch(()=>({}));
      if(!res.ok || !data?.success){ toast(data?.detail || `Rescan failed (HTTP ${res.status})`, "bad"); return; }
      const jf = data.jellyfin_triggered ? "Jellyfin ✅" : "Jellyfin ⚠️";
      const nd = data.navidrome_triggered ? "Navidrome ✅" : "Navidrome ⚠️";
      const msg = `Rescan triggered: ${jf} • ${nd}`;
      $("rescanHint").textContent = msg;
      toast(msg, "ok");
    }catch(e){ toast("Rescan failed", "bad"); }
  });

  // ── API key save ──
  // Show Navidrome creds only when AudiobookBay is selected
  function updateNdCredsVisibility(){
    const site = $("site")?.value || "";
    const el = $("ndCredsField");
    if(el) el.style.display = (site === "audiobookbay") ? "block" : "none";
  }
  $("site")?.addEventListener("change", updateNdCredsVisibility);
  updateNdCredsVisibility(); // run on load

  $("saveNd")?.addEventListener("click", async () => {
    const u = ($("ndUser")?.value || "").trim();
    const p = ($("ndPass")?.value || "").trim();
    const hint = $("ndSaveHint");
    const btn = $("saveNd");

    if(!u || !p){
      setNdCreds("", "");
      if(hint){ hint.innerHTML = `<span style="color:var(--muted);">Cleared</span>`; }
      return;
    }

    // Show testing state
    if(btn) btn.disabled = true;
    if(hint) hint.innerHTML = `<span style="color:var(--muted);">Testing…</span>`;

    try{
      const res = await apiFetch(`${API}/api/v1/auth/navidrome-test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nd_user: u, nd_pass: p })
      });
      const data = await res.json().catch(()=>({}));
      if(data.ok){
        setNdCreds(u, p);
        if(hint) hint.innerHTML = `<span style="color:var(--green, #32d74b);">✓ Connected as ${esc(u)}</span>`;
      } else {
        // Don't save bad creds
        if(hint) hint.innerHTML = `<span style="color:var(--red);">✗ ${esc(data.error || "Invalid credentials")}</span>`;
      }
    }catch(e){
      if(hint) hint.innerHTML = `<span style="color:var(--red);">✗ Could not reach Navidrome</span>`;
    } finally {
      if(btn) btn.disabled = false;
    }
  });
  $("saveKey")?.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const v = ($("apiKey")?.value || "").trim();
    setApiKey(v);
    setStatus(v ? pill("ok","API key saved (session)") : pill("warn","API key cleared"));
    toast(v ? "API key saved" : "API key cleared", v ? "ok" : "warn");
    loadQueueOnce();
  });

  // ── File manager events ──
  $("fmNavUp")?.addEventListener("click", fmNavUp);
  $("fmRefresh")?.addEventListener("click", () => loadFmDir(_fmState.currentPath));
  $("fmMkdir")?.addEventListener("click", doMkdir);

  $("fmRootSelect")?.addEventListener("change", (e) => {
    _fmState.currentRoot = e.target.value;
    _fmState.currentPath = "";
    loadFmDir("");
  });

  // File list clicks: navigate into dir OR rename/delete
  $("filesTab")?.addEventListener("click", (e) => {
    // Breadcrumb navigation
    const crumb = e.target?.closest?.(".fmCrumb");
    if(crumb){ loadFmDir(crumb.getAttribute("data-path") || ""); return; }

    // Rename button
    const renameBtn = e.target?.closest?.(".fmRenameBtn");
    if(renameBtn){
      const rel = renameBtn.getAttribute("data-rel");
      const name = renameBtn.getAttribute("data-name");
      openRenameModal(rel, name);
      return;
    }

    // Delete button
    const deleteBtn = e.target?.closest?.(".fmDeleteBtn");
    if(deleteBtn){
      const rel = deleteBtn.getAttribute("data-rel");
      const name = deleteBtn.getAttribute("data-name");
      const isDir = deleteBtn.getAttribute("data-is-dir") === "1";
      openDeleteModal(rel, name, isDir);
      return;
    }

    // Navigate into directory (click on folder name/icon)
    const entry = e.target?.closest?.(".fmEntry");
    if(entry && entry.getAttribute("data-is-dir") === "1"){
      const rel = entry.getAttribute("data-rel");
      if(rel) fmNavInto(rel);
    }
  });

  // ── Rename modal ──
  $("closeRename")?.addEventListener("click", () => showModal("renameModal", false));
  $("renameConfirm")?.addEventListener("click", doRename);
  $("renameInput")?.addEventListener("keydown", e => { if(e.key === "Enter") doRename(); });
  $("renameModal")?.addEventListener("click", (e) => {
    if(e.target?.id === "renameModal") showModal("renameModal", false);
  });

  // ── Delete modal ──
  $("closeDelete")?.addEventListener("click", () => showModal("deleteModal", false));
  $("deleteConfirm")?.addEventListener("click", doDelete);
  $("deleteModal")?.addEventListener("click", (e) => {
    if(e.target?.id === "deleteModal") showModal("deleteModal", false);
  });

  // ── Init: check stored auth, show login or app ──
  if(_loadStoredAuth()){
    showApp();
  } else {
    showLoginScreen();
  }
});
