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

const _SVG_MOON = `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
const _SVG_SUN  = `<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`;

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

function emptyState(icon, text){
  return `<div class="emptyState"><div class="emptyStateIcon">${icon}</div><div class="emptyStateText">${esc(text)}</div></div>`;
}

const _TOAST_DURATIONS = { ok: 3000, bad: 7000, warn: 5000, info: 4000 };
const _TOAST_ICONS     = { ok: "✓", bad: "✕", warn: "⚠", info: "ℹ" };

function toast(msg, kind="info"){
  const host = $("toastHost");
  if(!host) return;
  const dur  = _TOAST_DURATIONS[kind] ?? 4000;
  const icon = _TOAST_ICONS[kind] ?? "ℹ";
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<div class="toastBody"><span class="toastIcon">${icon}</span><div class="msg">${esc(msg)}</div></div><div class="toastProgress"><div class="toastProgressFill ${kind}"></div></div>`;
  host.appendChild(el);
  let gone = false;
  const dismiss = () => {
    if(gone) return; gone = true;
    el.classList.remove("show");
    setTimeout(() => el.remove(), 240);
  };
  el.addEventListener("click", dismiss);
  requestAnimationFrame(() => {
    el.classList.add("show");
    const fill = el.querySelector(".toastProgressFill");
    if(fill) requestAnimationFrame(() => {
      fill.style.transition = `width ${dur}ms linear`;
      fill.style.width = "0%";
    });
  });
  setTimeout(dismiss, dur);
}

function showModal(id, show){
  const m = $(id);
  if(!m) return;
  if(show) m.classList.add("show");
  else m.classList.remove("show");
}

// ─── Auth state ───────────────────────────────────────────────────────────────
let _authToken = "";
let _authUser = null; // { username, display_name, role }
let _apiKey = ""; // cached from localStorage on showApp()

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
  const key = _apiKey || getApiKey();
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
  _apiKey = "";
  // Clear stored credentials on explicit logout
  setApiKey("");
  setNdCreds("", "");
  if($("ndUser")) $("ndUser").value = "";
  if($("ndPass")) $("ndPass").value = "";
  if($("settingsApiKey")) $("settingsApiKey").value = "";
  if($("settingsNdUser")) $("settingsNdUser").value = "";
  if($("settingsNdPass")) $("settingsNdPass").value = "";
  showLoginScreen();
}
window.doLogout = doLogout;

function showLoginScreen(){
  $("appShell").style.display = "none";
  $("loginScreen").style.display = "flex";
  const bar = $("mobileTabBar");
  if(bar) bar.style.display = "none";
  if($("loginUser")) $("loginUser").value = "";
  if($("loginPass")) $("loginPass").value = "";
  if($("loginError")) $("loginError").textContent = "";
}

async function loadUserSettings(){
  try{
    const res = await apiFetch(`${API}/api/v1/user/settings`);
    if(!res.ok) return;
    const data = await res.json().catch(()=>({}));
    if(data.api_key){ setApiKey(data.api_key); _apiKey = data.api_key; }
    if(data.nd_user != null && data.nd_pass != null){ setNdCreds(data.nd_user, data.nd_pass); }
    // Push into visible inputs (settings panel may now be visible)
    if($("settingsApiKey")) $("settingsApiKey").value = getApiKey();
    if($("settingsNdUser")) $("settingsNdUser").value = getNdUser();
    if($("settingsNdPass")) $("settingsNdPass").value = getNdPass();
    if($("ndUser")) $("ndUser").value = getNdUser();
    if($("ndPass")) $("ndPass").value = getNdPass();
  }catch(_){}
}

const _TYPE_LABELS = { movie: "Movie", series: "TV", episode: "TV" };

async function loadRecentlyAdded(){
  const host = $("recentlyAddedList");
  if(!host) return;
  try{
    const res = await apiFetch(`${API}/api/v1/jellyfin/recently-added`);
    if(!res.ok){ host.innerHTML = `<span style="color:var(--muted);">Unavailable</span>`; return; }
    const data = await res.json().catch(()=>({}));
    const items = Array.isArray(data.items) ? data.items : [];
    if(!items.length){ host.innerHTML = `<span style="color:var(--muted);">Nothing found</span>`; return; }
    host.innerHTML = items.map(it => {
      const typeLabel = _TYPE_LABELS[it.type] || it.type || "";
      const year = it.year ? ` <span style="color:var(--muted);">${esc(String(it.year))}</span>` : "";
      const typePill = typeLabel ? ` <span class="pill ${it.type === "movie" ? "ok" : "info"}" style="font-size:10px;padding:2px 6px;">${esc(typeLabel)}</span>` : "";
      return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line);">
        ${it.thumb_url ? `<img src="${esc(it.thumb_url)}" style="width:32px;height:32px;object-fit:cover;border-radius:4px;flex-shrink:0;" loading="lazy" onerror="this.style.display='none'">` : ""}
        <div style="flex:1;min-width:0;">
          <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;">${esc(it.name)}${year}</span>
        </div>
        ${typePill}
      </div>`;
    }).join("");
  }catch(_){
    if(host) host.innerHTML = `<span style="color:var(--muted);">Unavailable</span>`;
  }
}

async function loadMusicRecentlyAdded(){
  const host = $("musicRecentlyAddedList");
  if(!host) return;
  try{
    const res = await apiFetch(`${API}/api/v1/navidrome/recently-added`);
    if(!res.ok){ host.innerHTML = `<span style="color:var(--muted);">Unavailable</span>`; return; }
    const data = await res.json().catch(()=>({}));
    const albums = Array.isArray(data.albums) ? data.albums : [];
    if(!albums.length){ host.innerHTML = emptyState('<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>', "No recently added music"); return; }
    host.innerHTML = albums.map(al => {
      const year = al.year ? ` <span class="pill" style="font-size:10px;padding:2px 6px;background:rgba(99,102,241,.15);color:#818cf8;">${esc(String(al.year))}</span>` : "";
      const playStat = al.play_count > 0
        ? `<span class="ndPlayStat">${al.play_count} play${al.play_count !== 1 ? "s" : ""}${al.last_played ? ` · ${new Date(al.last_played).toLocaleDateString()}` : ""}</span>`
        : "";
      return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--line);">
        ${al.cover_url ? `<img src="${esc(al.cover_url)}" style="width:32px;height:32px;object-fit:cover;border-radius:4px;flex-shrink:0;" loading="lazy" onerror="this.style.display='none'">` : `<div style="width:32px;height:32px;border-radius:4px;background:var(--panel);flex-shrink:0;"></div>`}
        <div style="flex:1;min-width:0;">
          <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;font-size:13px;">${esc(al.name)}</span>
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block;">${esc(al.artist)}</span>
          ${playStat}
        </div>
        ${year}
      </div>`;
    }).join("");
  }catch(_){
    if(host) host.innerHTML = `<span style="color:var(--muted);">Unavailable</span>`;
  }
}

let _ytFolders = [];

async function loadYtFolders(){
  try{
    const res = await apiFetch(`${API}/api/v1/youtube/folders`);
    if(!res.ok) return;
    const data = await res.json().catch(()=>({}));
    _ytFolders = Array.isArray(data.folders) ? data.folders : [];
  }catch(_){}

  // Wire custom dropdown on first load
  const input = $("ytSubfolder");
  const dd = $("ytFolderDropdown");
  if(!input || !dd) return;

  const _showDd = (filter) => {
    const q = (filter || "").toLowerCase();
    const matches = q ? _ytFolders.filter(f => f.toLowerCase().includes(q)) : _ytFolders;
    if(!matches.length){ dd.style.display = "none"; return; }
    dd.innerHTML = matches.map(f =>
      `<div class="ytFolderOpt" style="padding:10px 14px;cursor:pointer;font-size:14px;">${esc(f)}</div>`
    ).join("");
    dd.style.display = "block";
  };

  input.addEventListener("focus", () => _showDd(input.value));
  input.addEventListener("input", () => _showDd(input.value));
  dd.addEventListener("mousedown", (e) => {
    const opt = e.target.closest(".ytFolderOpt");
    if(!opt) return;
    e.preventDefault();
    input.value = opt.textContent;
    dd.style.display = "none";
  });
  document.addEventListener("click", (e) => {
    if(!input.contains(e.target) && !dd.contains(e.target)) dd.style.display = "none";
  }, true);
}

async function showApp(){
  $("loginScreen").style.display = "none";
  $("appShell").style.display = "block";
  const bar = $("mobileTabBar");
  if(bar) bar.style.display = "";

  // Update user display
  const u = _authUser;
  if($("userDisplayName") && u){
    $("userDisplayName").textContent = u.display_name || u.username;
  }

  if($("filesTabBtn")) $("filesTabBtn").style.display = "inline-flex";

  // Handle ?request= deep link
  const _reqParam = new URLSearchParams(location.search).get("request");
  if(_reqParam){
    showTab("music");
    switchMusicSection("request");
    if($("musicUrl")) $("musicUrl").value = _reqParam;
    history.replaceState(null, "", location.pathname);
  } else {
    showTab("torrent");
  }

  // Restore saved credentials
  _apiKey = getApiKey();
  if($("ndUser")) $("ndUser").value = getNdUser();
  if($("ndPass")) $("ndPass").value = getNdPass();
  // Pre-fill settings panel inputs
  if($("settingsApiKey")) $("settingsApiKey").value = _apiKey;
  if($("settingsNdUser")) $("settingsNdUser").value = getNdUser();
  if($("settingsNdPass")) $("settingsNdPass").value = getNdPass();
  await loadUserSettings();
  wireDownloadButtons();
  loadSites();
  startQueuePolling();
  pollServices();
  setInterval(pollServices, 8000);
  loadRecentlyAdded();
  setInterval(loadRecentlyAdded, 300000); // 5-minute refresh
  loadMusicRecentlyAdded();
  setInterval(loadMusicRecentlyAdded, 300000);

  requestNotifyPermission();

  // Check if Navidrome is linked; show gentle modal if not
  checkNavidromeStatus();
}

// ─── Music sub-section ───────────────────────────────────────────────────────
let _activeMusicSection = "search";
const _MUSIC_SECTIONS = ["search", "request", "playlist", "youtube"];

function switchMusicSection(name){
  _activeMusicSection = name;
  _MUSIC_SECTIONS.forEach(s => {
    const el = $(`musicSection${s.charAt(0).toUpperCase()+s.slice(1)}`);
    if(el) el.style.display = (s === name) ? "" : "none";
  });
  document.querySelectorAll("#musicNav .segBtn").forEach(b => {
    b.classList.toggle("active", b.dataset.ms === name);
  });
  if(name === "youtube"){ loadYtQueue(); loadYtFolders(); }
}

// ─── Tab switching ────────────────────────────────────────────────────────────
let _activeTab = "torrent";
let _tabHistory = []; // for swipe-back (max 5)
let _skipTabHistory = false; // set true when showTab is called by swipe-back

const _TABS = ["torrent", "music", "queue", "files", "settings"];

function showTab(tab){
  if(tab !== _activeTab && !_skipTabHistory){
    _tabHistory.push(_activeTab);
    if(_tabHistory.length > 5) _tabHistory.shift();
  }
  _activeTab = tab;
  _TABS.forEach(t => {
    const el = $(`${t}Tab`);
    if(el) el.style.display = (tab === t) ? "block" : "none";
  });

  _TABS.forEach(t => $(`${t}TabBtn`)?.classList.remove("active"));
  $(`${tab}TabBtn`)?.classList.add("active");

  // Sync mobile tab bar
  document.querySelectorAll(".mobileTabBtn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });

  if(tab === "files") initFileManager();
  if(tab === "queue"){
    _queueBadgeCount = 0;
    updateQueueBadge();
    loadQueueTab();
  }
  if(tab === "music"){
    loadMusicRecentlyAdded();
    if(_activeMusicSection === "youtube"){ loadYtQueue(); loadYtFolders(); }
  }
  if(tab === "torrent") loadRecentlyAdded();
  if(tab === "settings"){
    const ak = getApiKey();
    const nu = getNdUser();
    const np = getNdPass();
    if($("settingsApiKey") && !$("settingsApiKey").value) $("settingsApiKey").value = ak;
    if($("settingsNdUser") && !$("settingsNdUser").value) $("settingsNdUser").value = nu;
    if($("settingsNdPass") && !$("settingsNdPass").value) $("settingsNdPass").value = np;
  }
}
window.showTab = showTab;

function refreshCurrentTab(){
  switch(_activeTab){
    case "torrent":  loadRecentlyAdded && loadRecentlyAdded(); break;
    case "music":    loadMusicRecentlyAdded(); break;
    case "queue":    loadQueueOnce(); loadMusicQueueOnce(); loadNowPlaying(); break;
    case "files":    loadFmDir(_fmState.currentPath); break;
  }
}

// ─── Utility ─────────────────────────────────────────────────────────────────
function fmtRate(kib){
  const n = Number(kib);
  if(!Number.isFinite(n) || n <= 0) return "";
  if(n >= 1024) return `${(n/1024).toFixed(1)} MiB/s`;
  return `${Math.round(n)} KiB/s`;
}

// ─── Search results ───────────────────────────────────────────────────────────
function row(item, site, isOwned){
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
  const ownedClass = isOwned ? " already-downloaded" : "";
  const ownedBadge = isOwned ? `<span class="pill bad" style="font-size:11px;">In Library</span>` : "";

  return `
    <div class="track${ownedClass}"
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
        ${ownedBadge}
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
    if(typeof updateNdCredsVisibility === "function") updateNdCredsVisibility();
  }catch(e){
    setStatus(pill("bad", "Failed to load sites"));
  }
}


// ─── Search filters ───────────────────────────────────────────────────────────
let _searchFilterQuality = "any";
let _searchFilterCategory = "any";
let _searchResults = []; // raw items from last search
let _searchSite = "";
let _libraryOwnership = new Map(); // title → bool (owned in Jellyfin)

function _matchesQuality(title, q){
  if(q === "any") return true;
  const t = title.toLowerCase();
  if(q === "720p")  return t.includes("720");
  if(q === "1080p") return t.includes("1080");
  if(q === "4k")    return t.includes("2160") || t.includes("4k") || t.includes("uhd");
  return true;
}

function _matchesCategory(item, c){
  if(c === "any") return true;
  const cat = (item.category || "").toLowerCase();
  const title = (item.name || item.title || "").toLowerCase();
  const combined = cat + " " + title;
  if(c === "movies") return combined.includes("movie") || combined.includes("film") || combined.includes("bluray") || combined.includes("blu-ray");
  if(c === "tv")     return combined.includes("tv") || combined.includes("series") || combined.includes("season") || combined.includes("episode") || combined.includes("s0") || /s\d{2}e\d{2}/i.test(combined);
  if(c === "music")  return combined.includes("music") || combined.includes("album") || combined.includes("flac") || combined.includes("mp3") || combined.includes("discography");
  if(c === "books")  return combined.includes("book") || combined.includes("epub") || combined.includes("pdf") || combined.includes("ebook") || combined.includes("audiobook");
  return true;
}

function renderResults(){
  const resultsEl = $("results");
  if(!resultsEl) return;
  const filtered = _searchResults.filter(it => {
    const title = it.name || it.title || "";
    return _matchesQuality(title, _searchFilterQuality) && _matchesCategory(it, _searchFilterCategory);
  });
  if(filtered.length === 0){
    resultsEl.innerHTML = "";
    setStatus(pill("warn", `No results match filters (${_searchResults.length} total)`));
  } else {
    let ownedCount = 0;
    resultsEl.innerHTML = filtered.map(it => {
      const title = it.name || it.title || "";
      const owned = _libraryOwnership.get(title) === true;
      if(owned) ownedCount++;
      return row(it, _searchSite, owned);
    }).join("");
    let statusMsg = pill("ok", `Showing ${filtered.length} of ${_searchResults.length} result(s)`);
    if(ownedCount > 0){
      statusMsg += ` <span class="pill info" style="font-size:11px;">${ownedCount} of ${filtered.length} already in Jellyfin</span>`;
    }
    setStatus(statusMsg);
    const libBtn = $("libRefreshBtn");
    if(libBtn) libBtn.style.display = "inline-flex";
  }
}

async function _checkLibraryOwnership(items){
  const titles = items.map(it => it.name || it.title || "").filter(Boolean);
  if(!titles.length) return new Map();
  try{
    const res = await apiFetch(`${API}/api/v1/jellyfin/check-titles`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({titles}),
    });
    if(!res.ok) return new Map();
    const data = await res.json().catch(() => ({}));
    const m = new Map();
    for(const [k, v] of Object.entries(data.results || {})){
      m.set(k, !!v);
    }
    return m;
  }catch(e){
    return new Map();
  }
}

async function doSearch(){
  const site = $("site")?.value || "piratebay";
  const q = ($("q")?.value || "").trim();
  const limit = $("limit")?.value || "10";

  if(!q){
    setStatus(pill("warn", "Enter a search query"));
    return;
  }

  // Reset filters
  _searchFilterQuality = "any";
  _searchFilterCategory = "any";
  $("filterQualityControl")?.querySelectorAll(".segBtn").forEach((b,i) => b.classList.toggle("active", i === 0));
  $("filterCategoryControl")?.querySelectorAll(".segBtn").forEach((b,i) => b.classList.toggle("active", i === 0));

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
    _searchResults = items;
    _searchSite = site;
    _libraryOwnership = await _checkLibraryOwnership(items);
    renderResults();
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

  // Duplicate checks before queuing
  const _normT = s => (s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  const _nt = _normT(title);
  const inQueue = _nt.length >= 3 && _lastTorrentItems.find(it => {
    const n = _normT(it.title || "");
    return n === _nt || (n.length > 4 && (n.includes(_nt) || _nt.includes(n)));
  });
  if(inQueue && !confirm(`"${title}" is already in the download queue (${inQueue.status}). Download again?`)) return;

  if(_libraryOwnership.get(title) === true && !confirm(`"${title}" appears to already be in your Jellyfin library. Download anyway?`)) return;

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
let _lastTorrentItems = [];   // raw data from last poll
let _lastMusicData   = {};    // raw data from last music poll
let _lastYtItems     = [];    // raw data from last YouTube queue poll
let _lastQueueHash = "";      // hash of last rendered torrent queue
let _lastMusicHash = "";      // hash of last rendered music queue
let _musicQueueDoneCollapsed = true;
let _ytQueueDoneCollapsed = true;
let _explicitFetchQueue = Promise.resolve(); // serializes badge fetches — one at a time

// Notification state — null means first poll, skip firing notifications
let _prevTorrentStates = null;  // Map<id, status>
let _prevMusicStates   = null;  // Map<request_id, status>
let _prevYtStatuses    = null;  // Map<id, status>
let _queueBadgeCount   = 0;     // completions/failures since last queue visit

// Filter state
const _qf = { type: "all", status: "all", text: "" };

const _TORRENT_DONE_STATES  = new Set(["completed","imported","ready","cancelled"]);
const _TORRENT_ACTIVE_STATES = new Set(["queued","downloading","processing"]);
const _TORRENT_FAILED_STATES = new Set(["failed"]);

function _cleanSearchTitle(title){
  return (title || "")
    .replace(/\b(2160p|1080p|1080i|720p|480p|4k|uhd|hdr|bluray|blu[\-.]ray|webrip|web[\-.]dl|web|hdtv|dvdrip|dvd|bdrip|bdremux|remux|proper|repack|internal|extended|x264|x265|h264|h265|hevc|avc|xvid|divx|aac|ac3|dts|atmos|truehd|mp3|flac|10bit)\b/gi, "")
    .replace(/\b(19|20)\d{2}\b/g, "")
    .replace(/[._\-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function statusPillFor(st, prog){
  if(st === "imported")    return pill("ok",   "imported");
  if(st === "processing")  return pill("info",  "processing");
  if(st === "ready")       return pill("warn",  "ready");
  if(st === "completed")   return pill("ok",    "completed");
  if(st === "downloading") return pill("info",  prog != null ? `↓ ${Math.floor(prog)}%` : "downloading");
  if(st === "failed")      return pill("bad",   "failed");
  if(st === "cancelled")   return pill("warn",  "cancelled");
  return pill("warn", "queued");
}

function relTime(epochSecs){
  if(!epochSecs) return "";
  const diff = Math.floor(Date.now() / 1000 - epochSecs);
  if(diff < 5)   return "just now";
  if(diff < 60)  return `${diff}s ago`;
  if(diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if(diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

function torrentMatchesFilter(it){
  const st = it.status || "queued";
  const title = (it.title || "").toLowerCase();
  // type filter
  if(_qf.type === "music") return false;
  // status filter
  if(_qf.status === "active"  && !_TORRENT_ACTIVE_STATES.has(st)) return false;
  if(_qf.status === "done"    && !_TORRENT_DONE_STATES.has(st))   return false;
  if(_qf.status === "failed"  && !_TORRENT_FAILED_STATES.has(st)) return false;
  // text filter
  if(_qf.text && !title.includes(_qf.text.toLowerCase())) return false;
  return true;
}

function musicItemMatchesFilter(item, st){
  if(_qf.type === "torrent") return false;
  const title = (item.title || item.url || item.request_id || "").toLowerCase();
  const stUp = (st || "").toUpperCase();
  if(_qf.status === "active"  && !["PROCESSING","QUEUED"].includes(stUp)) return false;
  if(_qf.status === "done"    && stUp !== "DONE") return false;
  if(_qf.status === "failed"  && !["FAILED","RETRY_LATER"].includes(stUp)) return false;
  if(_qf.text && !title.includes(_qf.text.toLowerCase())) return false;
  return true;
}

function renderTorrentItems(items){
  const host = $("queue");
  if(!host) return;
  if(!Array.isArray(items) || items.length === 0){
    host.innerHTML = emptyState('<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v13M7 11l5 5 5-5"/><path d="M3 21h18"/></svg>', "No active downloads");
    return;
  }

  // Separate done vs active/failed
  const active = items.filter(it => !_TORRENT_DONE_STATES.has(it.status || "queued"));
  const done   = items.filter(it =>  _TORRENT_DONE_STATES.has(it.status || "queued"));

  const renderItem = (it) => {
    const title = it.title || "(untitled)";
    const st = it.status || "queued";
    const prog = Number.isFinite(it.progress) ? Math.max(0, Math.min(100, it.progress)) : 0;
    const err = it.error ? `<div class="mini" style="color:var(--red);">Error: ${esc(it.error)}</div>` : "";
    const state = it.transmission_state ? ` • ${esc(it.transmission_state)}` : "";
    const dl = it.rate_download_kib ? fmtRate(it.rate_download_kib) : "";
    const ul = it.rate_upload_kib ? fmtRate(it.rate_upload_kib) : "";
    const eta = it.eta ? esc(it.eta) : "";
    const ts = it.updated_at ? `<span class="mini" style="color:var(--muted);">${esc(relTime(it.updated_at))}</span>` : "";
    const seeds = Number.isFinite(it.seeders) && it.seeders >= 0 ? `${it.seeders}↑` : "";
    const peers = Number.isFinite(it.leechers) && it.leechers >= 0 ? `${it.leechers}↓` : "";
    const peersStr = (seeds || peers) ? ` • ${[seeds, peers].filter(Boolean).join(" ")}` : "";
    const speedLine = (dl || ul || eta || state || peersStr)
      ? `<div class="mini" style="color:var(--muted);">${dl?`DL ${esc(dl)}`:""}${ul?` • UL ${esc(ul)}`:""}${eta?` • ETA ${eta}`:""}${state}${peersStr}</div>`
      : "";
    const canCancel = (st === "queued" || st === "downloading");
    const cancelBtn = canCancel ? `<button class="btn sm cancelBtn" data-id="${esc(it.id)}">Cancel</button>` : "";
    const canReview = (st === "ready" || st === "completed");
    const reviewBtn = canReview ? `<button class="btn sm primary reviewBtn" data-id="${esc(it.id)}">Review</button>` : "";
    const canRetry  = (st === "failed" || st === "cancelled");
    const retryBtn  = canRetry  ? `<button class="btn sm retryTorrentBtn" data-id="${esc(it.id)}">Retry</button>` : "";
    const canReSearch = _TORRENT_DONE_STATES.has(st) || st === "failed" || st === "cancelled";
    const cleanedTitle = _cleanSearchTitle(title);
    const reSearchBtn = (canReSearch && cleanedTitle) ? `<button class="btn sm reSearchBtn" data-title="${esc(cleanedTitle)}">Re-search</button>` : "";
    const canRemove = _TORRENT_DONE_STATES.has(st) || st === "failed" || st === "cancelled";
    const removeBtn = canRemove ? `<button class="btn sm removeTorrentBtn" data-id="${esc(it.id)}" title="Remove from queue" style="padding:6px 8px;">&#x2715;</button>` : "";

    return `<div class="queueItem" data-id="${esc(it.id)}" data-status="${esc(st)}">
      <div class="queueTop">
        <div class="queueTitle">${esc(title)}</div>
        <div class="queueMeta">
          <span class="pill type-torrent">TORRENT</span>
          ${statusPillFor(st, st === "downloading" ? prog : null)} ${ts} ${reviewBtn} ${retryBtn} ${reSearchBtn} ${cancelBtn} ${removeBtn}
        </div>
      </div>
      ${speedLine}
      ${["downloading","completed","ready","processing","imported"].includes(st) ? `
        <div class="progressBar" style="margin-top:6px;"><div class="progressFill" style="width:${prog}%;transition:width .6s ease;"></div></div>
        ${st === "downloading" ? `<div class="mini" style="color:var(--muted);margin-top:3px;">${Math.floor(prog)}%</div>` : ""}
      ` : ""}
      ${err}
    </div>`;
  };

  let html = active.map(renderItem).join("");

  if(done.length){
    const collapsed = host.dataset.doneCollapsed !== "0";
    html += `<div class="queueSectionHead">
      <button class="segBtn" id="toggleDoneBtn">
        ${collapsed ? `&#9660; Show ${done.length} completed` : `&#9650; Hide completed`}
      </button>
    </div>`;
    if(!collapsed){
      html += done.map(renderItem).join("");
    }
  }

  host.innerHTML = html;

  $("toggleDoneBtn")?.addEventListener("click", () => {
    host.dataset.doneCollapsed = host.dataset.doneCollapsed === "0" ? "1" : "0";
    renderTorrentItems(items);
  });
}

function renderQueue(items){
  _lastTorrentItems = Array.isArray(items) ? items : [];
  const qs = $("queueStatus");
  const active = _lastTorrentItems.filter(it => _TORRENT_ACTIVE_STATES.has(it.status || "queued"));
  if(qs) qs.textContent = active.length ? `${active.length} active` : "";
  _checkTorrentNotifications(_lastTorrentItems);
  updateQueueBadge();
  const filtered = _lastTorrentItems.filter(torrentMatchesFilter);
  const hash = _lastTorrentItems.map(it => `${it.id}:${it.status}:${Math.floor(it.progress||0)}`).join("|");
  if(hash === _lastQueueHash) return;
  _lastQueueHash = hash;
  renderTorrentItems(filtered);
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
let _selectedMatchMeta = {};
let fileActions = new Map();
const _reviewDirtyFields = new Set(); // fields the user has manually edited

function setDestType(val){
  const inp = $("destType");
  if(inp) inp.value = val;
  document.querySelectorAll("#destTypeControl .segBtn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.dest === val);
  });
}

function setMusicKind(val){
  const inp = $("musicKind");
  if(inp) inp.value = val;
  document.querySelectorAll("#musicKindControl .segBtn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mk === val);
  });
}

function rebuildFilesUI(){
  const keepHost = $("filesKeep");
  if(!keepHost) return;

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
  const mph = $("musicPathHint");    if(mph) mph.style.display = (kind === "music") ? "" : "none";
  const aph = $("audiobookPathHint"); if(aph) aph.style.display = (kind === "audiobook") ? "" : "none";
}

function applySuggestedToInputs(){
  const suggested = (reviewData && reviewData.suggested) ? reviewData.suggested : {};
  const destType = ($("destType")?.value || "movies").toLowerCase();
  const set = (id, val) => {
    if(!_reviewDirtyFields.has(id) && $(id)) $(id).value = String(val);
  };
  if(destType === "tv"){
    set("titleInput", suggested.show || suggested.title || reviewData.title || "Untitled Show");
    set("seasonInput", String(Number(suggested.season || 1) || 1));
    set("yearInput", suggested.year || (suggested.first_air_date || "").slice(0,4) || "");
  }else if(destType === "movies"){
    set("titleInput", suggested.title || reviewData.title || "Untitled");
    set("yearInput", suggested.year || "");
  }else{
    set("titleInput", reviewData.title || "Untitled");
    set("yearInput", "");
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
  // Toggle TMDb section visibility
  const modal = $("reviewModal");
  if(modal){
    if(destType === "music") modal.classList.add("reviewMusicMode");
    else modal.classList.remove("reviewMusicMode");
  }
  // Hide folder name editor in music mode (beets handles naming)
  const nameEditor = $("nameEditor");
  if(nameEditor) nameEditor.style.display = (destType === "music") ? "none" : "";
}

async function openReview(id){
  try{
    const res = await apiFetch(`${API}/api/v1/prepare/${encodeURIComponent(id)}`);
    const data = await res.json().catch(()=>({}));
    if(!res.ok || !data?.success){
      toast(data?.detail || `Prepare failed (HTTP ${res.status})`, "bad");
      return;
    }

    currentReviewId = id;
    reviewData = data;

    try{
      const ui = data.ui_settings || {};
      if(ui.music_kind_guess) setMusicKind(ui.music_kind_guess);
      if(typeof ui.tv_auto_split_default !== "undefined") $("tvAutoSplit").checked = !!ui.tv_auto_split_default;
      else $("tvAutoSplit").checked = true;
      if(typeof ui.music_recommend_beets !== "undefined") $("musicRunBeets").checked = !!ui.music_recommend_beets;
      else $("musicRunBeets").checked = true;
    }catch(e){}

    $("reviewSub").textContent = data.title || "";
    _reviewDirtyFields.clear();

    const guess = (data.category || "movies").toLowerCase();
    setDestType(["movies","tv","music"].includes(guess) ? guess : "movies");

    updateOptionsVisibility();
    updateMusicMetaVisibility();

    const matchList = $("matchSelect");
    const tmdbHint = $("tmdbHint");
    _selectedMatchMeta = {};
    if(matchList){
      matchList.innerHTML = "";
      const candidates = Array.isArray(data.candidates) ? data.candidates : [];
      const suggested = data.suggested || {};
      const opts = [];
      if(suggested && (suggested.tmdb_id || suggested.title || suggested.show)){
        opts.push({ label: `Suggested: ${suggested.label || suggested.title || suggested.show || "match"}`, meta: suggested });
      }
      candidates.forEach((c, idx) => {
        opts.push({ label: c.label || c.title || c.show || `Match ${idx+1}`, meta: c });
      });
      if(opts.length === 0){
        matchList.innerHTML = `<div class="tmdbMatchCard tmdbMatchNone">No TMDb match — using title fallback</div>`;
        _selectedMatchMeta = {};
        if(tmdbHint) tmdbHint.textContent = data.tmdb_debug ? `TMDb: ${data.tmdb_debug}` : "TMDb returned no matches.";
      }else{
        opts.forEach((o2, i) => {
          const card = document.createElement("div");
          card.className = "tmdbMatchCard" + (i === 0 ? " active" : "");
          card.dataset.meta = JSON.stringify(o2.meta || {});
          card.textContent = o2.label;
          matchList.appendChild(card);
        });
        _selectedMatchMeta = opts[0]?.meta || {};
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
    const ov = $("reviewLoadOverlay"); if(ov) ov.style.display = "none";
    setTimeout(() => $("titleInput")?.focus(), 80);

    // Speculatively start TV folder lookup if category hints TV
    const _tvFolderPromise = ((data.category || "").toLowerCase() === "tv")
      ? checkTvFolderExists(data.suggested?.show || data.title || "")
      : null;

    // Fire metadata detection in background (non-blocking)
    detectAndFillMetadata(data.title || data.name || "", _tvFolderPromise);
  }catch(e){
    toast("Prepare failed", "bad");
    const ov = $("reviewLoadOverlay"); if(ov) ov.style.display = "none";
  }
}

async function checkTvFolderExists(showTitle){
  if(!showTitle) return null;
  try{
    const params = new URLSearchParams({ root: "TV", path: "" });
    const res = await apiFetch(`${API}/api/v1/files/list?${params}`);
    if(!res.ok) return null;
    const data = await res.json().catch(() => ({}));
    const entries = Array.isArray(data.entries) ? data.entries : [];
    const norm = s => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    const showNorm = norm(showTitle);
    const exact = entries.find(e => e.is_dir && norm(e.name) === showNorm);
    if(exact) return exact.name;
    // Fuzzy: one fully contains the other, min 80% length overlap
    const fuzzy = entries.find(e => e.is_dir && (() => {
      const en = norm(e.name);
      return (en.includes(showNorm) || showNorm.includes(en)) && en.length >= Math.floor(showNorm.length * 0.8);
    })());
    return fuzzy ? fuzzy.name : null;
  }catch(e){ return null; }
}

async function detectAndFillMetadata(filename, tvFolderPromise){
  const panel = $("metaDetectPanel");
  if(!panel || !filename) return;

  // Show loading state
  panel.style.display = "flex";
  panel.innerHTML = `<span class="metaDetectBadge low">Detecting metadata\u2026</span>`;

  let meta = null;
  try{
    const res = await apiFetch(`${API}/api/v1/detect-metadata`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ filename }),
    });
    if(res.ok) meta = await res.json().catch(() => null);
  }catch(e){ meta = null; }

  if(!meta || meta.confidence === "low" || (!meta.title)){
    panel.innerHTML = `<div class="metaDetectInfo"><span class="metaDetectBadge low">Could not detect metadata</span></div>`;
    return;
  }

  const conf = meta.confidence;
  const badgeClass = conf === "high" ? "high" : "medium";
  const badgeText  = conf === "high" ? "Auto-detected" : "Please verify";

  // Auto-fill fields — skip any field the user has already edited
  const safeFill = (id, val) => {
    if(!_reviewDirtyFields.has(id) && $(id)) $(id).value = String(val);
  };
  if(meta.type === "music"){
    if(!_reviewDirtyFields.has("destType")) setDestType("music");
    updateOptionsVisibility && updateOptionsVisibility();
    updateMusicMetaVisibility && updateMusicMetaVisibility();
    // Try to detect audiobook from "Author - Title" pattern in filename
    const isAudiobook = /\.m4b$/i.test(filename) || ($("musicKind")?.value === "audiobook");
    if(isAudiobook){
      setMusicKind("audiobook");
      updateMusicMetaVisibility && updateMusicMetaVisibility();
      // Try to split "Author - Title"
      const parts = (meta.title || "").split(" - ");
      if(parts.length >= 2){
        safeFill("abAuthor", parts[0].trim());
        safeFill("abBook",   parts.slice(1).join(" - ").trim());
      } else {
        safeFill("abBook", meta.title || "");
      }
      if(meta.year) safeFill("abYear", meta.year);
    } else {
      safeFill("titleInput", meta.title || "");
      if(meta.year) safeFill("yearInput", meta.year);
    }
  }else if(meta.type === "tv"){
    if(!_reviewDirtyFields.has("destType")) setDestType("tv");
    safeFill("titleInput", meta.title || "");
    safeFill("seasonInput", String(meta.season || 1));
    if(meta.year) safeFill("yearInput", meta.year);
    updateOptionsVisibility && updateOptionsVisibility();
    updateMusicMetaVisibility && updateMusicMetaVisibility();
  }else{
    if(!_reviewDirtyFields.has("destType")) setDestType("movies");
    safeFill("titleInput", meta.title || "");
    if(meta.year) safeFill("yearInput", meta.year);
    updateOptionsVisibility && updateOptionsVisibility();
    updateMusicMetaVisibility && updateMusicMetaVisibility();
  }
  updateDestPreview && updateDestPreview();

  // Build panel HTML
  let posterHtml = "";
  if(meta.tmdb_poster){
    posterHtml = `<img class="metaPoster" src="${meta.tmdb_poster}" alt="poster" loading="lazy">`;
  }
  const overview = meta.tmdb_overview ? `<div class="mini">${esc(meta.tmdb_overview)}</div>` : "";
  const yearStr  = meta.year ? ` (${meta.year})` : "";
  const typeStr  = meta.type === "tv" ? " \u2022 TV" : meta.type === "music" ? " \u2022 Music" : " \u2022 Movie";
  const seStr    = (meta.season != null) ? ` S${String(meta.season).padStart(2,"0")}` : "";

  panel.innerHTML = `
    ${posterHtml}
    <div class="metaDetectInfo">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <span class="metaDetectBadge ${badgeClass}">${badgeText}</span>
        <span class="mini" style="font-weight:600;">${esc(meta.title)}${yearStr}${typeStr}${seStr}</span>
      </div>
      ${overview}
      <button class="btn sm" id="clearMetaBtn" style="margin-top:6px;font-size:11px;">Clear auto-fill</button>
    </div>`;

  $("clearMetaBtn")?.addEventListener("click", () => {
    panel.style.display = "none";
    panel.innerHTML = "";
    if($("titleInput"))  $("titleInput").value  = (reviewData?.title || "");
    if($("yearInput"))   $("yearInput").value   = "";
    if($("seasonInput")) $("seasonInput").value = "1";
    updateDestPreview && updateDestPreview();
  });

  // For TV shows: check if a matching folder already exists in /mnt/media/TV
  if(meta.type === "tv" && meta.title){
    (tvFolderPromise || checkTvFolderExists(meta.title)).then(existingFolder => {
      if(!existingFolder) return;
      const info = panel.querySelector(".metaDetectInfo");
      if(!info) return;
      const sugg = document.createElement("div");
      sugg.style.cssText = "margin-top:8px; display:flex; align-items:center; gap:8px; flex-wrap:wrap;";
      sugg.innerHTML = `<span class="mini" style="color:var(--muted);">Existing: <span class="mono">/mnt/media/TV/${esc(existingFolder)}/</span></span><button class="btn sm" id="useTvFolderBtn">Use this folder</button>`;
      info.appendChild(sugg);
      $("useTvFolderBtn")?.addEventListener("click", () => {
        if($("titleInput")) $("titleInput").value = existingFolder;
        updateDestPreview && updateDestPreview();
        toast(`Using: ${existingFolder}`, "ok");
      });
    });
  }
}

async function approveMove(){
  if(!currentReviewId || !reviewData) return;
  const btn = $("approveMove");
  const btnOrigText = btn?.textContent ?? "Approve & Move";
  if(btn){ btn.disabled = true; btn.textContent = "Moving…"; }
  try{

    let meta = {};
    try{ meta = JSON.parse(JSON.stringify(_selectedMatchMeta || {})); }catch(e){ meta = {}; }

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
      if(btn){ btn.disabled = false; btn.textContent = btnOrigText; }
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

    if(btn){ btn.disabled = false; btn.textContent = "Done ✓"; }
    setTimeout(() => showModal("reviewModal", false), 600);
    const row = document.querySelector(`[data-id="${currentReviewId}"]`);
    if(row){
      row.style.transition = "opacity .4s";
      row.style.opacity = "0";
      row.addEventListener("transitionend", () => row.remove(), { once: true });
    }
    currentReviewId = null;
    reviewData = null;
    loadQueueOnce();
  }catch(e){
    toast("Move failed", "bad");
    if(btn){ btn.disabled = false; btn.textContent = btnOrigText; }
  }
}

async function loadQueueOnce(){
  const key = _apiKey || getApiKey();
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

async function torrentRetry(id){
  const res = await apiFetch(`${API}/api/v1/queue/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  const data = await res.json().catch(()=>({}));
  if(!res.ok || !data?.success){
    toast(data?.detail || `Retry failed (HTTP ${res.status})`, "bad");
    return;
  }
  toast("Re-queued", "ok");
  loadQueueOnce();
}

async function torrentRemove(id){
  const res = await apiFetch(`${API}/api/v1/queue/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  const data = await res.json().catch(()=>({}));
  if(!res.ok || !data?.success){
    toast(data?.detail || `Remove failed (HTTP ${res.status})`, "bad");
    return;
  }
  loadQueueOnce();
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

function formatSecs(s){
  if(!s || s < 0) return "0:00";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  if(h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  return `${m}:${String(sec).padStart(2,"0")}`;
}

async function loadNowPlaying(){
  const widget = $("nowPlayingWidget");
  const content = $("nowPlayingContent");
  const countEl = $("nowPlayingCount");
  if(!widget || !content) return;
  try{
    const res = await apiFetch(`${API}/api/v1/jellyfin/now-playing`);
    if(!res.ok){ widget.style.display = "none"; return; }
    const data = await res.json().catch(()=>({}));
    const sessions = Array.isArray(data.sessions) ? data.sessions : [];
    if(!sessions.length){ widget.style.display = "none"; return; }
    widget.style.display = "";
    if(countEl) countEl.textContent = sessions.length > 1 ? `${sessions.length} streams` : "";
    content.innerHTML = sessions.map(s => {
      const pct = s.progress_pct ?? 0;
      const pos = s.position_s ? formatSecs(s.position_s) : "";
      const dur = s.duration_s ? formatSecs(s.duration_s) : "";
      const timeStr = (pos && dur) ? `${pos} / ${dur}` : (pos || "");
      const methodCls = s.play_method === "Direct" ? "ok" : s.play_method === "Remux" ? "warn" : "bad";
      const thumb = s.thumb_url
        ? `<img class="nowPlayingThumb" src="${esc(s.thumb_url)}" loading="lazy" onerror="this.style.display='none'">`
        : `<div class="nowPlayingThumbPlaceholder"></div>`;
      return `<div class="nowPlayingItem">
        ${thumb}
        <div class="nowPlayingMeta">
          <div class="nowPlayingTitle" title="${esc(s.title)}">${esc(s.title)}</div>
          ${s.subtitle ? `<div class="nowPlayingSubtitle" title="${esc(s.subtitle)}">${esc(s.subtitle)}</div>` : ""}
          <div class="nowPlayingUser">${esc(s.user || "")} ${pill(methodCls, s.play_method || "?")}</div>
          ${timeStr ? `<div class="nowPlayingTime">${esc(timeStr)}</div>` : ""}
          ${s.duration_s > 0 ? `<div class="progressBar" style="margin-top:4px;"><div class="progressFill" style="width:${pct}%"></div></div>` : ""}
        </div>
      </div>`;
    }).join("");
  }catch(_){
    if(widget) widget.style.display = "none";
  }
}

function startQueuePolling(){
  loadQueueOnce();
  loadMusicQueueOnce();
  loadNowPlaying();
  setInterval(loadQueueOnce, 5000);
  setInterval(loadMusicQueueOnce, 6000);
  setInterval(loadYtQueue, 5000);
  setInterval(loadYtQueueForQueueTab, 7000);
  setInterval(loadNowPlaying, 10000);
}

// ─── File Manager ─────────────────────────────────────────────────────────────
const _FM_ICON_DIR  = `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>`;
const _FM_ICON_FILE = `<svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;
const _FM_ICON_DIR_LG  = `<svg width="38" height="38" fill="none" stroke="currentColor" stroke-width="1.3" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>`;
const _FM_ICON_FILE_LG = `<svg width="38" height="38" fill="none" stroke="currentColor" stroke-width="1.3" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>`;

let _fmState = {
  roots: [],
  currentRoot: null,
  currentPath: "",
  viewMode: "list",   // "list" | "grid"
  sortBy:  "name",    // "name" | "size" | "modified"
  sortDir: "asc",     // "asc" | "desc"
  allEntries: [],     // cached for client-side filter
  searchResults: null, // last recursive search entries, or null when in dir mode
  pendingRename: null,  // { root, path, name }
  pendingDelete: null,  // { root, path, name, is_dir }
  selectionMode: false,
  selectedItems: new Map(), // rel → { rel, name, is_dir }
};


async function fmSearch(query){
  if(!query.trim()) return;
  const host = $("fmList");
  if(host) host.innerHTML = `<div class="mini" style="color:var(--muted);padding:12px;">Searching all folders for "${esc(query)}"…</div>`;
  try{
    // Search from root always (not just current path) for "search all folders"
    const params = new URLSearchParams({ root: _fmState.currentRoot || "", path: "", query: query.trim(), recursive: "true" });
    const res = await apiFetch(`${API}/api/v1/files/search?${params}`);
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ toast(data.detail || "Search failed","bad"); return; }
    const entries = Array.isArray(data.entries) ? data.entries : [];
    if(!host) return;
    _fmState.searchResults = entries;
    if(entries.length === 0){
      host.innerHTML = `<div class="mini" style="color:var(--muted);padding:12px;">No results for "${esc(query)}"</div>`;
      return;
    }
    const truncNote = data.truncated ? `<div class="mini" style="color:var(--muted);padding:4px 0 8px;">Showing first ${entries.length} results — refine your search for more.</div>` : "";
    host.className = "fmList";
    host.innerHTML = truncNote + entries.map(e => {
      const icon = e.is_dir ? _FM_ICON_DIR : _FM_ICON_FILE;
      const size = (!e.is_dir && e.size != null) ? `<span class="fmSize">${fmtSize(e.size)}</span>` : `<span class="fmSize"></span>`;
      // Show full relative path as subtitle
      const parentPath = (e.path || "").split("/").slice(0,-1).join("/");
      const pathSub = parentPath ? `<span class="mini" style="color:var(--muted);display:block;margin-top:1px;">${esc(parentPath)}/</span>` : "";
      // Navigate to parent dir on click, or open dir if it's a dir
      const navPath = e.is_dir ? (e.path || "") : parentPath;
      return `<div class="fmEntry fmSearchResult" data-rel="${esc(e.path || "")}" data-is-dir="${e.is_dir ? "1" : "0"}" data-name="${esc(e.name)}" data-nav="${esc(navPath)}" style="cursor:pointer;">
        <span class="fmIcon">${icon}</span>
        <span class="fmName"><span style="display:block;">${esc(e.name)}</span>${pathSub}</span>
        ${size}
        <span class="fmDate"></span>
        <span class="fmActions">
          ${e.is_dir ? `<button class="btn sm fmOpenBtn" data-rel="${esc(e.path || "")}">Open</button>` : ""}
          ${!e.is_dir ? `<button class="btn sm fmRenameBtn" data-rel="${esc(e.path || "")}" data-name="${esc(e.name)}">Rename</button>` : ""}
          <button class="btn sm fmDeleteBtn" data-rel="${esc(e.path || "")}" data-name="${esc(e.name)}" data-is-dir="${e.is_dir ? "1" : "0"}" style="border-color:rgba(255,69,58,.4);color:var(--red);">Delete</button>
        </span>
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
  // Cache for client-side filter
  _fmState.allEntries = entries || [];
  _fmState.searchResults = null; // clear search mode when entering a directory

  // Reset search input
  const si = $("fmSearchInput");
  if(si) si.value = "";

  // Hide legacy filter pill
  const searchBar = $("fmSearchBar");
  if(searchBar) searchBar.style.display = "none";

  _fmApplyFilters();
}

function _fmApplyFilters(){
  const sortBy  = _fmState.sortBy  || "name";
  const sortDir = _fmState.sortDir || "asc";
  const q = ($("fmSearchInput")?.value || "").trim().toLowerCase();

  let entries = [...(_fmState.allEntries || [])];

  // Client-side name filter
  if(q) entries = entries.filter(e => e.name.toLowerCase().includes(q));

  // Sort — directories always float to top, then apply chosen sort within each group
  entries.sort((a, b) => {
    if(a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    let cmp = 0;
    if(sortBy === "size"){
      cmp = (a.size || 0) - (b.size || 0);
    } else if(sortBy === "modified"){
      cmp = (a.modified || 0) - (b.modified || 0);
    } else {
      cmp = a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    }
    return sortDir === "desc" ? -cmp : cmp;
  });

  _renderFmEntriesFiltered(entries);
}

function _renderFmEntriesFiltered(entries){
  const fmList = $("fmList");
  if(!fmList) return;
  const filterCount = $("fmFilterCount");

  if(!entries || entries.length === 0){
    fmList.className = "fmList";
    fmList.innerHTML = emptyState('<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>', "This folder is empty");
    if(filterCount) filterCount.textContent = "";
    return;
  }

  if(filterCount) filterCount.textContent = `${entries.length} item${entries.length !== 1 ? "s" : ""}`;

  const sel = _fmState.selectionMode;
  if(_fmState.viewMode === "grid"){
    fmList.className = "fmGrid";
    fmList.innerHTML = entries.map(e => {
      const icon = e.is_dir ? _FM_ICON_DIR_LG : _FM_ICON_FILE_LG;
      const size = (e.size != null) ? fmtSize(e.size) : "";
      const checked = sel && _fmState.selectedItems.has(e.rel) ? "checked" : "";
      const selectedCls = (sel && _fmState.selectedItems.has(e.rel)) ? " fm-selected" : "";
      const checkHtml = sel ? `<input type="checkbox" class="fmSelectCheck" ${checked} tabindex="-1">` : "";
      return `
        <div class="fmGridItem${selectedCls}" data-rel="${esc(e.rel)}" data-is-dir="${e.is_dir ? "1" : "0"}" data-name="${esc(e.name)}">
          ${checkHtml}
          <div class="fmGridIcon">${icon}</div>
          <div class="fmGridName" title="${esc(e.name)}">${esc(e.name)}</div>
          ${size ? `<div class="fmGridMeta">${size}</div>` : ""}
          ${sel ? "" : `<div class="fmGridActions">
            <button class="btn sm fmRenameBtn" data-rel="${esc(e.rel)}" data-name="${esc(e.name)}" title="Rename">&#8942;</button>
            <button class="btn sm fmDeleteBtn" data-rel="${esc(e.rel)}" data-name="${esc(e.name)}" data-is-dir="${e.is_dir ? "1" : "0"}" style="border-color:rgba(255,69,58,.4);color:var(--red);" title="Delete">&times;</button>
          </div>`}
        </div>
      `;
    }).join("");
  } else {
    fmList.className = "fmList";
    fmList.innerHTML = entries.map(e => {
      const icon = e.is_dir ? _FM_ICON_DIR : _FM_ICON_FILE;
      const size = (e.size != null)
        ? `<span class="fmSize">${fmtSize(e.size)}</span>`
        : `<span class="fmSize"></span>`;
      const date = e.modified
        ? `<span class="fmDate">${new Date(e.modified * 1000).toLocaleDateString()}</span>`
        : `<span class="fmDate"></span>`;
      const sizeMobile = (e.is_dir && e.size != null)
        ? `<span class="fmSizeMobile">${fmtSize(e.size)}</span>`
        : "";
      const checked = sel && _fmState.selectedItems.has(e.rel) ? "checked" : "";
      const selectedCls = (sel && _fmState.selectedItems.has(e.rel)) ? " fm-selected" : "";
      const checkHtml = sel ? `<input type="checkbox" class="fmSelectCheck" ${checked} tabindex="-1">` : "";
      return `
        <div class="fmEntry${selectedCls}" data-rel="${esc(e.rel)}" data-is-dir="${e.is_dir ? "1" : "0"}" data-name="${esc(e.name)}">
          ${checkHtml}
          <span class="fmIcon">${icon}</span>
          <span class="fmName">${esc(e.name)}${sizeMobile}</span>
          ${size}${date}
          ${sel ? "" : `<span class="fmActions">
            <button class="btn sm fmRenameBtn" data-rel="${esc(e.rel)}" data-name="${esc(e.name)}">Rename</button>
            <button class="btn sm fmDeleteBtn" data-rel="${esc(e.rel)}" data-name="${esc(e.name)}" data-is-dir="${e.is_dir ? "1" : "0"}" style="border-color:rgba(255,69,58,.4);color:var(--red);">Delete</button>
          </span>`}
        </div>
      `;
    }).join("");
  }
}

function _fmCurrentFilteredEntries(){
  const q = ($("fmSearchInput")?.value || "").trim().toLowerCase();
  if(!q) return _fmState.allEntries;
  return _fmState.allEntries.filter(e => e.name.toLowerCase().includes(q));
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

// ─── FM Multi-select ──────────────────────────────────────────────────────────
function enterFmSelectionMode(){
  if(_fmState.selectionMode) return;
  _fmState.selectionMode = true;
  _fmState.selectedItems = new Map();
  document.body.classList.add("fm-selection-active");
  const toolbar = $("fmSelectionToolbar");
  if(toolbar) toolbar.style.display = "";
  updateSelectionToolbar();
  // Re-render entries to show checkboxes
  const entries = _fmState.searchResults || _fmState.allEntries;
  _renderFmEntriesFiltered(entries);
}

function exitFmSelectionMode(){
  if(!_fmState.selectionMode) return;
  _fmState.selectionMode = false;
  _fmState.selectedItems = new Map();
  document.body.classList.remove("fm-selection-active");
  const toolbar = $("fmSelectionToolbar");
  if(toolbar) toolbar.style.display = "none";
  const entries = _fmState.searchResults || _fmState.allEntries;
  _renderFmEntriesFiltered(entries);
}

function toggleFmSelection(rel, name, isDir){
  if(_fmState.selectedItems.has(rel)){
    _fmState.selectedItems.delete(rel);
  } else {
    _fmState.selectedItems.set(rel, { rel, name, is_dir: isDir });
  }
  // Update visual state of the specific item without full re-render
  const el = document.querySelector(`[data-rel="${CSS.escape(rel)}"]`);
  if(el){
    const cb = el.querySelector(".fmSelectCheck");
    if(cb) cb.checked = _fmState.selectedItems.has(rel);
    el.classList.toggle("fm-selected", _fmState.selectedItems.has(rel));
  }
  updateSelectionToolbar();
}

function updateSelectionToolbar(){
  const count = _fmState.selectedItems.size;
  const countEl = $("fmSelectionCount");
  const delBtn = $("fmSelectionDeleteBtn");
  const allBtn = $("fmSelectAllBtn");
  if(countEl) countEl.textContent = `${count} selected`;
  if(delBtn) delBtn.disabled = count === 0;
  const total = (_fmState.searchResults || _fmState.allEntries).length;
  if(allBtn) allBtn.textContent = (count > 0 && count === total) ? "Deselect All" : "Select All";
}

function openDeleteBatchModal(){
  const items = Array.from(_fmState.selectedItems.values());
  if(!items.length) return;
  const summary = $("deleteBatchSummary");
  if(summary){
    const dirCount = items.filter(i => i.is_dir).length;
    const fileCount = items.length - dirCount;
    const parts = [];
    if(fileCount) parts.push(`${fileCount} file${fileCount !== 1 ? "s" : ""}`);
    if(dirCount)  parts.push(`${dirCount} folder${dirCount !== 1 ? "s" : ""}`);
    summary.textContent = `Delete ${parts.join(" and ")}?`;
  }
  const recRow = $("deleteBatchRecursiveRow");
  const hasDirs = items.some(i => i.is_dir);
  if(recRow) recRow.style.display = hasDirs ? "flex" : "none";
  if($("deleteBatchRecursive")) $("deleteBatchRecursive").checked = false;
  showModal("deleteBatchModal", true);
}

async function doDeleteBatch(){
  const items = Array.from(_fmState.selectedItems.values());
  if(!items.length) return;
  const recursive = !!($("deleteBatchRecursive")?.checked);
  const payload = {
    root: _fmState.currentRoot,
    items: items.map(i => ({ path: i.rel, is_dir: i.is_dir, recursive: i.is_dir ? recursive : false })),
  };
  const confirmBtn = $("deleteBatchConfirm");
  if(confirmBtn){ confirmBtn.disabled = true; confirmBtn.textContent = "Deleting…"; }
  try{
    const res = await apiFetch(`${API}/api/v1/files/delete-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    showModal("deleteBatchModal", false);
    if(!res.ok){
      toast(data?.detail || `Delete failed (HTTP ${res.status})`, "bad");
      return;
    }
    const failed = Array.isArray(data.failed) ? data.failed.length : 0;
    if(failed){
      toast(`Deleted ${data.deleted}, failed ${failed}`, "warn");
    } else {
      toast(`Deleted ${data.deleted} item${data.deleted !== 1 ? "s" : ""}`, "ok");
    }
    exitFmSelectionMode();
    await loadFmDir(_fmState.currentPath);
  }catch(e){
    toast("Batch delete request failed.", "bad");
    showModal("deleteBatchModal", false);
  }finally{
    if(confirmBtn){ confirmBtn.disabled = false; confirmBtn.textContent = "Delete"; }
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
  $("mobileLogoutBtn")?.addEventListener("click", () => doLogout());

  // ── Tabs ──
  $("torrentTabBtn")?.addEventListener("click", () => showTab("torrent"));
  $("musicTabBtn")?.addEventListener("click",  () => showTab("music"));
  $("queueTabBtn")?.addEventListener("click",  () => showTab("queue"));
  $("filesTabBtn")?.addEventListener("click",  () => showTab("files"));

  // ── User dropdown ──
  ["click", "touchend"].forEach(evt => {
    $("userDropdownBtn")?.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      $("userDropdownMenu")?.classList.toggle("open");
    });
  });
  ["click", "touchstart"].forEach(evt => {
    document.addEventListener(evt, (e) => {
      if(!$("userDropdown")?.contains(e.target)){
        $("userDropdownMenu")?.classList.remove("open");
      }
    });
  });
  $("settingsMenuBtn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    $("userDropdownMenu")?.classList.remove("open");
    showTab("settings");
  });

  // ── Settings sidebar nav ──
  document.querySelectorAll(".settingsNavBtn").forEach(btn => {
    btn.addEventListener("click", () => {
      const panel = btn.dataset.sp;
      document.querySelectorAll(".settingsPanelSection").forEach(s => s.style.display = "none");
      const el = $(`settingsPanel${panel.charAt(0).toUpperCase()+panel.slice(1)}`);
      if(el) el.style.display = "";
      document.querySelectorAll(".settingsNavBtn").forEach(b => b.classList.toggle("active", b === btn));
      if(panel === "storage") loadStorageStats();
      if(panel === "sites")   renderSettingsSites();
      if(panel === "navidrome"){
        if($("settingsNdUser") && !$("settingsNdUser").value) $("settingsNdUser").value = getNdUser();
        if($("settingsNdPass") && !$("settingsNdPass").value) $("settingsNdPass").value = getNdPass();
      }
    });
  });

  // ── Settings: General — API key save ──
  $("settingsApiKeySave")?.addEventListener("click", () => {
    const v = ($("settingsApiKey")?.value || "").trim();
    setApiKey(v);
    _apiKey = v;
    toast(v ? "API key saved" : "API key cleared", v ? "ok" : "warn");
    loadQueueOnce();
    apiFetch(`${API}/api/v1/user/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: v }),
    }).catch(() => toast("Settings may not have synced to server", "warn"));
  });

  // ── Settings: Navidrome — save & link ──
  $("settingsNdSave")?.addEventListener("click", async () => {
    const nav_user = ($("settingsNdUser")?.value || "").trim();
    const nav_pass = ($("settingsNdPass")?.value || "").trim();
    const hint = $("settingsNdHint");
    const btn = $("settingsNdSave");
    if(!nav_user || !nav_pass){
      if(hint) hint.innerHTML = `<span style="color:var(--red);">Username and password required.</span>`;
      return;
    }
    if(btn){ btn.disabled = true; btn.textContent = "Saving…"; }
    if(hint) hint.textContent = "";
    try{
      const res = await apiFetch(`${API}/api/v1/auth/navidrome/link`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nav_user, nav_pass }),
      });
      const data = await res.json().catch(()=>({}));
      if(!res.ok){
        if(hint) hint.innerHTML = `<span style="color:var(--red);">${esc(data?.detail || `Failed (HTTP ${res.status})`)}</span>`;
      } else {
        setNdCreds(nav_user, nav_pass);
        if($("ndUser")) $("ndUser").value = nav_user;
        if($("ndPass")) $("ndPass").value = nav_pass;
        if(hint) hint.innerHTML = `<span style="color:var(--green);">✓ Linked as ${esc(nav_user)}</span>`;
        toast(`Navidrome linked as ${nav_user}`, "ok");
        apiFetch(`${API}/api/v1/user/settings`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ nd_user: nav_user, nd_pass: nav_pass }),
        }).catch(() => toast("Settings may not have synced to server", "warn"));
      }
    }catch(e){
      if(hint) hint.innerHTML = `<span style="color:var(--red);">Could not reach server.</span>`;
    }finally{
      if(btn){ btn.disabled = false; btn.textContent = "Save & Link"; }
    }
  });

  // ── Settings: Navidrome — password toggle ──
  $("settingsNdPassToggle")?.addEventListener("click", () => {
    const inp = $("settingsNdPass");
    const icon = $("settingsNdPassEyeIcon");
    if(!inp) return;
    if(inp.type === "password"){
      inp.type = "text";
      icon.innerHTML = '<path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>';
    } else {
      inp.type = "password";
      icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
    }
  });

  // ── Settings: Storage — refresh ──
  $("storageRefreshBtn")?.addEventListener("click", () => {
    // Force refresh by clearing cache timestamp
    _storageCacheTs = 0;
    loadStorageStats();
  });

  // ── Search filters ──
  $("filterQualityControl")?.addEventListener("click", e => {
    const btn = e.target.closest(".segBtn[data-fq]");
    if(!btn) return;
    _searchFilterQuality = btn.dataset.fq;
    $("filterQualityControl").querySelectorAll(".segBtn").forEach(b => b.classList.toggle("active", b === btn));
    if(_searchResults.length) renderResults();
  });
  $("filterCategoryControl")?.addEventListener("click", e => {
    const btn = e.target.closest(".segBtn[data-fc]");
    if(!btn) return;
    _searchFilterCategory = btn.dataset.fc;
    $("filterCategoryControl").querySelectorAll(".segBtn").forEach(b => b.classList.toggle("active", b === btn));
    if(_searchResults.length) renderResults();
  });

  // ── Search ──
  $("go")?.addEventListener("click", doSearch);
  $("q")?.addEventListener("keydown", (e) => { if(e.key === "Enter") doSearch(); });

  // ── Library refresh button (injected after #status) ──
  const _statusDiv = $("status");
  if(_statusDiv){
    const _libBtn = document.createElement("button");
    _libBtn.id = "libRefreshBtn";
    _libBtn.className = "btn sm";
    _libBtn.textContent = "\u21bb Library";
    _libBtn.style.cssText = "margin-top:6px; font-size:11px; display:none;";
    _statusDiv.insertAdjacentElement("afterend", _libBtn);
    _libBtn.addEventListener("click", async () => {
      _libBtn.disabled = true;
      _libBtn.textContent = "Refreshing\u2026";
      try{
        const res = await apiFetch(`${API}/api/v1/jellyfin/refresh-library`, {method:"POST"});
        const data = await res.json().catch(() => ({}));
        toast(`Library refreshed (${data.library_size || 0} titles)`, "ok");
        if(_searchResults.length){
          _libraryOwnership = await _checkLibraryOwnership(_searchResults);
          renderResults();
        }
      }catch(e){
        toast("Library refresh failed", "bad");
      }finally{
        _libBtn.disabled = false;
        _libBtn.textContent = "\u21bb Library";
      }
    });
  }

  // ── Review modal ──
  $("closeReview")?.addEventListener("click", () => {
    showModal("reviewModal", false);
    const p = $("metaDetectPanel"); if(p){ p.style.display="none"; p.innerHTML=""; }
  });
  $("approveMove")?.addEventListener("click", approveMove);

  // Destination type segControl
  $("destTypeControl")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".segBtn[data-dest]");
    if(!btn) return;
    setDestType(btn.dataset.dest);
    applySuggestedToInputs();
    updateOptionsVisibility();
    updateMusicMetaVisibility();
    updateDestPreview();
  });

  // TMDb match card selection
  $("matchSelect")?.addEventListener("click", (e) => {
    const card = e.target?.closest?.(".tmdbMatchCard");
    if(!card || card.classList.contains("tmdbMatchNone")) return;
    $("matchSelect").querySelectorAll(".tmdbMatchCard").forEach(c => c.classList.remove("active"));
    card.classList.add("active");
    try{ _selectedMatchMeta = JSON.parse(card.dataset.meta || "{}"); }catch(_){ _selectedMatchMeta = {}; }
    if(reviewData) reviewData.suggested = _selectedMatchMeta;
    applySuggestedToInputs();
    updateDestPreview();
    updateOptionsVisibility();
    updateMusicMetaVisibility();
  });

  // Music kind segControl
  $("musicKindControl")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".segBtn[data-mk]");
    if(!btn) return;
    setMusicKind(btn.dataset.mk);
    updateMusicMetaVisibility();
  });

  // Merged: backdrop close + inline file rename
  $("reviewModal")?.addEventListener("click", (e) => {
    if(e.target?.id === "reviewModal"){ showModal("reviewModal", false); return; }

    const renameBtn = e.target?.closest?.(".renameBtn");
    if(!renameBtn) return;
    const rel = renameBtn.getAttribute("data-rel");
    if(!rel || !fileActions.has(rel)) return;
    const v = fileActions.get(rel);
    const existingForm = renameBtn.closest(".fileRow")?.querySelector(".inlineRenameForm");
    if(existingForm){ existingForm.remove(); return; }
    $("filesKeep")?.querySelectorAll(".inlineRenameForm").forEach(f => f.remove());
    const form = document.createElement("div");
    form.className = "inlineRenameForm";
    form.innerHTML = `<input class="inlineRenameInput" value="${esc(v.newName || "")}" placeholder="New filename (with extension)" /><button type="button" class="btn sm primary inlineRenameSave">Save</button><button type="button" class="btn sm inlineRenameCancel">&times;</button>`;
    renameBtn.closest(".fileRow")?.appendChild(form);
    const inp = form.querySelector(".inlineRenameInput");
    inp?.focus(); inp?.select();
    const doSave = () => { v.newName = (inp?.value || "").trim(); fileActions.set(rel, v); rebuildFilesUI(); };
    form.querySelector(".inlineRenameSave")?.addEventListener("click", doSave);
    form.querySelector(".inlineRenameCancel")?.addEventListener("click", () => form.remove());
    inp?.addEventListener("keydown", ev => { if(ev.key === "Enter") doSave(); if(ev.key === "Escape") form.remove(); });
  });

  // ── Queue ──
  $("queue")?.addEventListener("click", (e) => {
    const r = e.target?.closest?.(".reviewBtn");
    if(r){
      const id = r.getAttribute("data-id");
      if(id){
        r.disabled = true;
        r.textContent = "Loading…";
        const _ov = $("reviewLoadOverlay"); if(_ov) _ov.style.display = "flex";
        openReview(id).finally(() => {
          r.disabled = false;
          r.textContent = "Review";
          const _ov2 = $("reviewLoadOverlay"); if(_ov2) _ov2.style.display = "none";
        });
      }
      return;
    }
    const cancelBtn = e.target?.closest?.(".cancelBtn");
    if(cancelBtn){ cancelDownloadById(cancelBtn.getAttribute("data-id")); return; }

    const retryBtn = e.target?.closest?.(".retryTorrentBtn");
    if(retryBtn){
      retryBtn.disabled = true; retryBtn.textContent = "Retrying…";
      torrentRetry(retryBtn.getAttribute("data-id")).finally(()=>{ retryBtn.disabled=false; retryBtn.textContent="Retry"; });
      return;
    }
    const removeBtn = e.target?.closest?.(".removeTorrentBtn");
    if(removeBtn){
      removeBtn.disabled = true;
      torrentRemove(removeBtn.getAttribute("data-id"));
      return;
    }
    const reSearchBtn = e.target?.closest?.(".reSearchBtn");
    if(reSearchBtn){
      const t = reSearchBtn.getAttribute("data-title") || "";
      showTab("torrent");
      if($("q")) $("q").value = t;
      doSearch();
      return;
    }
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
  ["customSubfolder","titleInput","yearInput","seasonInput"].forEach(id => {
    $(id)?.addEventListener("input", updateDestPreview);
    $(id)?.addEventListener("change", () => { updateDestPreview(); updateOptionsVisibility(); updateMusicMetaVisibility(); });
  });

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
        if(hint) hint.innerHTML = `<span style="color:var(--green);">✓ Connected as ${esc(u)}</span>`;
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
  // (API key save is now in Settings > General)

  // ── File manager events ──
  $("fmNavUp")?.addEventListener("click", fmNavUp);
  $("fmRefresh")?.addEventListener("click", () => loadFmDir(_fmState.currentPath));
  $("fmMkdir")?.addEventListener("click", doMkdir);
  $("fmSelectModeBtn")?.addEventListener("click", enterFmSelectionMode);

  // View toggle (grid / list)
  $("fmViewList")?.addEventListener("click", () => {
    _fmState.viewMode = "list";
    $("fmViewList")?.classList.add("active");
    $("fmViewGrid")?.classList.remove("active");
    if(_fmState.searchResults){ _renderFmEntriesFiltered(_fmState.searchResults); return; }
    _fmApplyFilters();
  });
  $("fmViewGrid")?.addEventListener("click", () => {
    _fmState.viewMode = "grid";
    $("fmViewGrid")?.classList.add("active");
    $("fmViewList")?.classList.remove("active");
    if(_fmState.searchResults){ _renderFmEntriesFiltered(_fmState.searchResults); return; }
    _fmApplyFilters();
  });

  // Sort field segControl
  $("fmSortGroup")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".segBtn[data-sort]");
    if(!btn) return;
    _fmState.sortBy = btn.dataset.sort;
    $("fmSortGroup").querySelectorAll(".segBtn").forEach(b => b.classList.toggle("active", b === btn));
    _fmApplyFilters();
  });

  // Sort direction toggle
  $("fmSortDirBtn")?.addEventListener("click", () => {
    _fmState.sortDir = _fmState.sortDir === "asc" ? "desc" : "asc";
    const btn = $("fmSortDirBtn");
    if(btn) btn.textContent = _fmState.sortDir === "asc" ? "↑" : "↓";
    _fmApplyFilters();
  });

  // Search input — debounce: 3+ chars triggers recursive search, fewer filters current folder
  let _fmSearchDebounce = null;
  $("fmSearchInput")?.addEventListener("input", () => {
    const q = ($("fmSearchInput")?.value || "").trim();
    clearTimeout(_fmSearchDebounce);
    if(q.length >= 3){
      // Show loading indicator immediately to prevent flash of old content
      const host = $("fmList");
      if(host) host.innerHTML = `<div class="mini" style="color:var(--muted);padding:12px;">Searching…</div>`;
      _fmSearchDebounce = setTimeout(() => fmSearch(q), 400);
    } else {
      _fmState.searchResults = null;
      _fmApplyFilters();
    }
  });

  $("fmRootSelect")?.addEventListener("change", (e) => {
    _fmState.currentRoot = e.target.value;
    _fmState.currentPath = "";
    loadFmDir("");
  });

  // Long-press detection for entering selection mode
  let _fmLongPressTimer = null;
  $("filesTab")?.addEventListener("touchstart", (e) => {
    const entry = e.target?.closest?.(".fmEntry,.fmGridItem");
    if(!entry || _fmState.selectionMode) return;
    _fmLongPressTimer = setTimeout(() => {
      _fmLongPressTimer = null;
      const rel = entry.getAttribute("data-rel");
      const name = entry.getAttribute("data-name");
      const isDir = entry.getAttribute("data-is-dir") === "1";
      if(!rel) return;
      enterFmSelectionMode();
      toggleFmSelection(rel, name, isDir);
    }, 500);
  }, { passive: true });
  $("filesTab")?.addEventListener("touchend", () => { clearTimeout(_fmLongPressTimer); _fmLongPressTimer = null; });
  $("filesTab")?.addEventListener("touchmove", () => { clearTimeout(_fmLongPressTimer); _fmLongPressTimer = null; });

  // File list clicks: navigate into dir OR rename/delete OR selection toggle
  $("filesTab")?.addEventListener("click", (e) => {
    // Breadcrumb navigation
    const crumb = e.target?.closest?.(".fmCrumb");
    if(crumb){ loadFmDir(crumb.getAttribute("data-path") || ""); return; }

    // In selection mode: clicking an entry toggles it
    if(_fmState.selectionMode){
      const entry = e.target?.closest?.(".fmEntry,.fmGridItem");
      if(entry){
        const rel = entry.getAttribute("data-rel");
        const name = entry.getAttribute("data-name");
        const isDir = entry.getAttribute("data-is-dir") === "1";
        if(rel) toggleFmSelection(rel, name, isDir);
      }
      return;
    }

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

    // Open button in search results
    const openBtn = e.target?.closest?.(".fmOpenBtn");
    if(openBtn){ fmNavInto(openBtn.getAttribute("data-rel") || ""); return; }

    // Navigate into directory — list view (or navigate to parent for search results)
    const entry = e.target?.closest?.(".fmEntry");
    if(entry){
      if(entry.classList.contains("fmSearchResult")){
        const navPath = entry.getAttribute("data-nav") || "";
        fmNavInto(navPath);
        return;
      }
      if(entry.getAttribute("data-is-dir") === "1"){
        const rel = entry.getAttribute("data-rel");
        if(rel) fmNavInto(rel);
        return;
      }
    }

    // Navigate into directory — grid view
    const gridItem = e.target?.closest?.(".fmGridItem");
    if(gridItem && gridItem.getAttribute("data-is-dir") === "1"){
      const rel = gridItem.getAttribute("data-rel");
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

  // ── FM selection toolbar ──
  $("fmSelectionCancelBtn")?.addEventListener("click", exitFmSelectionMode);
  $("fmSelectionDeleteBtn")?.addEventListener("click", openDeleteBatchModal);
  $("fmSelectAllBtn")?.addEventListener("click", () => {
    const entries = _fmState.searchResults || _fmState.allEntries;
    const allSelected = entries.length > 0 && entries.every(e => _fmState.selectedItems.has(e.rel));
    if(allSelected){
      _fmState.selectedItems = new Map();
    } else {
      entries.forEach(e => _fmState.selectedItems.set(e.rel, { rel: e.rel, name: e.name, is_dir: e.is_dir }));
    }
    _renderFmEntriesFiltered(entries);
    updateSelectionToolbar();
  });

  // ── Batch delete modal ──
  $("closeBatchDelete")?.addEventListener("click", () => showModal("deleteBatchModal", false));
  $("deleteBatchConfirm")?.addEventListener("click", doDeleteBatch);
  $("deleteBatchModal")?.addEventListener("click", (e) => {
    if(e.target?.id === "deleteBatchModal") showModal("deleteBatchModal", false);
  });

  // ── Music pill nav ──
  $("musicNav")?.addEventListener("click", e => {
    const btn = e.target?.closest?.(".segBtn[data-ms]");
    if(btn) switchMusicSection(btn.dataset.ms);
  });

  // ── Music search ──
  $("musicSearchBtn")?.addEventListener("click", doMusicSearch);
  $("musicQ")?.addEventListener("keydown", e => { if(e.key === "Enter") doMusicSearch(); });

  // ── Music result: Share button (delegated) ──
  $("musicResults")?.addEventListener("click", async (e) => {
    const shareBtn = e.target?.closest?.(".musicShareBtn");
    if(shareBtn){
      const url = shareBtn.getAttribute("data-url");
      if(url){
        const link = `${location.origin}${location.pathname}?request=${encodeURIComponent(url)}`;
        try{
          await navigator.clipboard.writeText(link);
          toast("Share link copied!", "ok");
        }catch(err){
          toast("Copy failed — clipboard not available", "bad");
        }
      }
    }
  });

  // ── Music result: Add button (delegated) ──
  $("musicResults")?.addEventListener("click", async (e) => {
    // Handle rename confirm button
    const confirmBtn = e.target?.closest?.(".musicRenameConfirm");
    if(confirmBtn){
      const form = confirmBtn.closest(".musicRenameForm");
      if(!form) return;
      const url = confirmBtn.getAttribute("data-url");
      const force = confirmBtn.getAttribute("data-force") === "1";
      const artist = (form.querySelector(".musicRenameArtist")?.value || "").trim();
      const titleVal = (form.querySelector(".musicRenameTitle")?.value || "").trim();
      const customTitle = (artist && titleVal) ? `${artist} - ${titleVal}` : (artist || titleVal || null);
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Requesting…";
      const result = await requestMusicTrack(url, customTitle, force);
      if(result.ok){
        confirmBtn.textContent = "Queued";
        toast("Track queued for import", "ok");
        loadMusicQueueOnce();
      } else {
        confirmBtn.disabled = false;
        confirmBtn.textContent = force ? "Force Request" : "Request";
        toast(result.error || "Request failed", "bad");
      }
      return;
    }

    // Handle skip button
    const skipBtn = e.target?.closest?.(".musicRenameSkip");
    if(skipBtn){
      const form = skipBtn.closest(".musicRenameForm");
      if(!form) return;
      const track = form.closest(".track");
      const addBtn = track?.querySelector(".musicAddBtn");
      const url = addBtn?.getAttribute("data-url") || skipBtn.getAttribute("data-url");
      const title = addBtn?.getAttribute("data-title") || "";
      const force = addBtn?.getAttribute("data-force") === "1";
      if(!url) return;
      skipBtn.disabled = true;
      skipBtn.textContent = "Requesting…";
      const result = await requestMusicTrack(url, title, force);
      if(result.ok){
        skipBtn.textContent = "Queued";
        toast("Track queued for import", "ok");
        loadMusicQueueOnce();
      } else {
        skipBtn.disabled = false;
        skipBtn.textContent = "Skip / use original";
        toast(result.error || "Request failed", "bad");
      }
      return;
    }

    // Handle Add button — show inline rename form
    const btn = e.target?.closest?.(".musicAddBtn");
    if(!btn) return;
    const url = btn.getAttribute("data-url");
    const rawTitle = btn.getAttribute("data-title") || "";
    const force = btn.getAttribute("data-force") === "1";
    if(!url) return;

    // Remove any existing rename form in this track
    const track = btn.closest(".track");
    if(!track) return;
    const existing = track.querySelector(".musicRenameForm");
    if(existing){ existing.remove(); return; }

    // Parse "Artist - Title" or "Artist – Title"
    let preArtist = "", preTitle = rawTitle;
    const sepMatch = rawTitle.match(/^(.+?)\s*[–\-]\s*(.+)$/);
    if(sepMatch){ preArtist = sepMatch[1].trim(); preTitle = sepMatch[2].trim(); }

    const form = document.createElement("div");
    form.className = "musicRenameForm";
    form.innerHTML = `
      <div class="row" style="gap:8px;">
        <input class="musicRenameArtist" placeholder="Artist" value="${esc(preArtist)}" />
        <input class="musicRenameTitle" placeholder="Song title" value="${esc(preTitle)}" />
      </div>
      <div style="display:flex; gap:8px; margin-top:6px;">
        <button class="btn primary sm musicRenameConfirm" data-url="${esc(url)}" data-force="${force ? "1" : "0"}">${force ? "Force Request" : "Request"}</button>
        <button class="btn sm musicRenameSkip" data-url="${esc(url)}">Skip / use original</button>
      </div>`;
    track.appendChild(form);
    form.querySelector(".musicRenameArtist")?.focus();
  });

  // ── Music URL blur: queue duplicate check ──
  $("musicUrl")?.addEventListener("blur", () => {
    const url = ($("musicUrl")?.value || "").trim();
    const warnEl = $("musicUrlQueueWarning");
    if(!warnEl) return;
    if(!url){ warnEl.style.display = "none"; return; }
    const data = _lastMusicData || {};
    const queued = Array.isArray(data.queued) ? data.queued : [];
    const processing = data.processing ? [data.processing] : [];
    const history = Array.isArray(data.history) ? data.history : [];
    const allItems = [...processing, ...queued, ...history];
    const match = allItems.find(it => {
      const iUrl = (it.url || "").trim();
      if(!iUrl) return false;
      if(iUrl === url) return true;
      // Compare by YouTube video ID (last 11 chars of typical ID)
      const extractId = u => { const m = u.match(/[?&]v=([^&]{11})/); return m ? m[1] : null; };
      const a = extractId(url), b = extractId(iUrl);
      return a && b && a === b;
    });
    if(match){
      const st = (match._st || match.status || "").toUpperCase();
      const msg = (st === "DONE") ? "Already imported" : "Already in queue";
      warnEl.innerHTML = pill("warn", msg);
      warnEl.style.display = "block";
    } else {
      warnEl.style.display = "none";
    }
  });

  // ── Music request by URL ──
  $("musicRequestBtn")?.addEventListener("click", () => {
    const url = ($("musicUrl")?.value || "").trim();
    const rawCustomTitle = ($("musicCustomTitle")?.value || "").trim();
    const hintEl = $("musicRequestHint");
    if(!url){ if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">URL is required</span>`; return; }

    // Toggle rename form
    if(hintEl){
      const existingForm = hintEl.querySelector(".musicUrlRenameForm");
      if(existingForm){ existingForm.remove(); return; }

      let preArtist = "", preTitle = rawCustomTitle;
      if(rawCustomTitle){
        const m = rawCustomTitle.match(/^(.+?)\s*[–\-]\s*(.+)$/);
        if(m){ preArtist = m[1].trim(); preTitle = m[2].trim(); }
      }

      const form = document.createElement("div");
      form.className = "musicRenameForm musicUrlRenameForm";
      form.style.marginTop = "8px";
      form.innerHTML = `
        <div class="row" style="gap:8px; margin-bottom:6px;">
          <input class="musicUrlRenameArtist" placeholder="Artist" value="${esc(preArtist)}" />
          <input class="musicUrlRenameTitle" placeholder="Song title" value="${esc(preTitle)}" />
        </div>
        <div style="display:flex; gap:8px;">
          <button class="btn primary sm musicUrlRenameConfirm">Request</button>
          <button class="btn sm musicUrlRenameSkip">Skip / use original</button>
        </div>`;
      hintEl.innerHTML = "";
      hintEl.appendChild(form);
      form.querySelector(".musicUrlRenameArtist")?.focus();

      form.querySelector(".musicUrlRenameConfirm")?.addEventListener("click", async () => {
        const artist = (form.querySelector(".musicUrlRenameArtist")?.value || "").trim();
        const titleVal = (form.querySelector(".musicUrlRenameTitle")?.value || "").trim();
        const customTitle = (artist && titleVal) ? `${artist} - ${titleVal}` : (artist || titleVal || "");
        form.remove();
        await _doMusicUrlRequest(url, customTitle);
      });

      form.querySelector(".musicUrlRenameSkip")?.addEventListener("click", async () => {
        form.remove();
        await _doMusicUrlRequest(url, rawCustomTitle);
      });
    }
  });

  // ── CSV dropzone ──
  $("csvFileInput")?.addEventListener("change", (e) => {
    const fn = e.target?.files?.[0]?.name || "";
    if($("csvFileName")) $("csvFileName").textContent = fn;
  });
  const _csvZone = $("csvDropZone");
  if(_csvZone){
    _csvZone.addEventListener("dragover", (e) => { e.preventDefault(); _csvZone.classList.add("drag-over"); });
    _csvZone.addEventListener("dragleave", () => _csvZone.classList.remove("drag-over"));
    _csvZone.addEventListener("drop", (e) => {
      e.preventDefault();
      _csvZone.classList.remove("drag-over");
      const file = e.dataTransfer?.files?.[0];
      if(file){
        const inp = $("csvFileInput");
        const dt = new DataTransfer();
        dt.items.add(file);
        if(inp){ inp.files = dt.files; inp.dispatchEvent(new Event("change")); }
      }
    });
  }

  // ── CSV upload ──
  $("csvUploadBtn")?.addEventListener("click", doUploadCsv);

  // ── Music queue: cancel (delegated) ──
  $("musicQueueHost")?.addEventListener("click", async (e) => {
    const btn = e.target?.closest?.(".musicCancelBtn");
    if(!btn) return;
    const rid = btn.getAttribute("data-rid");
    if(!rid) return;
    btn.disabled = true;
    btn.textContent = "Cancelling…";
    try{
      const res = await apiFetch(`${API}/api/v1/music/queue/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: rid }),
      });
      const data = await res.json().catch(()=>({}));
      if(!res.ok){ toast(data?.detail || "Cancel failed","bad"); btn.disabled=false; btn.textContent="Cancel"; return; }
      toast("Request cancelled","ok");
      loadMusicQueueOnce();
    }catch(e){ toast("Cancel failed","bad"); btn.disabled=false; btn.textContent="Cancel"; }
  });

  // ── YouTube → Jellyfin ──
  $("ytDownloadBtn")?.addEventListener("click", doYtDownload);
  let _ytUrlDebounceTimer = null;
  $("ytUrl")?.addEventListener("input", () => {
    clearTimeout(_ytUrlDebounceTimer);
    _ytUrlDebounceTimer = setTimeout(_onYtUrlChange, 600);
  });
  $("ytPlaylistDownloadAll")?.addEventListener("click", _doPlaylistDownloadAll);

  // ── Navidrome link modal ──
  $("navLinkSkip")?.addEventListener("click", () => showModal("navLinkModal", false));
  $("navLinkModal")?.addEventListener("click", (e) => {
    if(e.target?.id === "navLinkModal") showModal("navLinkModal", false);
  });
  $("navLinkConfirm")?.addEventListener("click", doNavLinkConfirm);
  $("navLinkPass")?.addEventListener("keydown", e => { if(e.key === "Enter") doNavLinkConfirm(); });

  // ── Mobile tab bar ──
  $("mobileTabBar")?.addEventListener("click", (e) => {
    const btn = e.target?.closest?.(".mobileTabBtn");
    if(!btn) return;
    const tab = btn.dataset.tab;
    if(tab) showTab(tab);
  });

  // ── Theme ──
  initTheme();
  $("themeBtn")?.addEventListener("click", toggleTheme);

  // ── Pull-to-refresh ──
  let _ptrStartY = 0, _ptrStartX = 0, _ptrActive = false;
  const _ptrIndicator = $("pullRefreshIndicator");
  if(_ptrIndicator) _ptrIndicator.classList.remove("ptr-visible");
  document.addEventListener("touchstart", (e) => {
    const t = e.touches[0];
    _ptrStartY = t.clientY;
    _ptrStartX = t.clientX;
    _ptrActive = false;
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if(!_ptrIndicator) return;
    const t = e.touches[0];
    const dy = t.clientY - _ptrStartY;
    const dx = Math.abs(t.clientX - _ptrStartX);
    // Only if: started within 80px of top, dragging down more than 50px, mostly vertical
    if(_ptrStartY <= 80 && dy > 50 && dy > dx){
      _ptrActive = true;
      _ptrIndicator.classList.add("ptr-visible");
    }
  }, { passive: true });
  document.addEventListener("touchend", () => {
    if(!_ptrIndicator) return;
    if(_ptrActive){
      _ptrActive = false;
      refreshCurrentTab();
      setTimeout(() => _ptrIndicator.classList.remove("ptr-visible"), 600);
    } else {
      _ptrIndicator.classList.remove("ptr-visible");
    }
  }, { passive: true });

  // ── iOS edge swipe-back gesture ──
  let _swipeStartX = 0, _swipeStartY = 0;
  document.addEventListener("touchstart", (e) => {
    const t = e.touches[0];
    _swipeStartX = t.clientX;
    _swipeStartY = t.clientY;
  }, { passive: true });
  document.addEventListener("touchend", (e) => {
    if(!e.changedTouches.length) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - _swipeStartX;
    const dy = Math.abs(t.clientY - _swipeStartY);
    // Must start within 30px of left edge, swipe right >60px, more horizontal than vertical
    if(_swipeStartX <= 30 && dx > 60 && dx > dy && _tabHistory.length > 0){
      const prev = _tabHistory.pop();
      _skipTabHistory = true;
      showTab(prev);
      _skipTabHistory = false;
    }
  }, { passive: true });

  // ── Keyboard shortcuts ──
  $("closeKbdOverlay")?.addEventListener("click", () => showModal("kbdOverlay", false));
  $("kbdOverlay")?.addEventListener("click", (e) => { if(e.target?.id === "kbdOverlay") showModal("kbdOverlay", false); });
  document.addEventListener("keydown", (e) => {
    const tag = document.activeElement?.tagName?.toLowerCase() || "";
    if(tag === "input" || tag === "textarea" || tag === "select" || document.activeElement?.isContentEditable) return;
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    switch(e.key){
      case "Escape":
        document.querySelectorAll(".modal.show").forEach(m => m.classList.remove("show"));
        if(typeof exitFmSelectionMode === "function") exitFmSelectionMode();
        break;
      case "?":
        showModal("kbdOverlay", true);
        break;
      case "r": case "R":
        if($("appShell")?.style.display !== "none") refreshCurrentTab();
        break;
      case "/":
        e.preventDefault();
        if(_activeTab === "files") $("fmSearchInput")?.focus();
        else if(_activeTab === "music") $("musicQ")?.focus();
        else if(_activeTab === "torrent") $("q")?.focus();
        break;
      case "q": case "Q": showTab("queue"); break;
      case "m": case "M": showTab("music"); break;
      case "f": case "F": showTab("files"); break;
    }
  });

  // ── Init: check stored auth, show login or app ──
  if(_loadStoredAuth()){
    showApp();
  } else {
    showLoginScreen();
  }
});

// ─── Explicit/Clean detection from YouTube title ──────────────────────────────
function _detectExplicit(title){
  if(!title) return null;
  const t = title.toLowerCase();
  if(/\(explicit\)|\[explicit\]|\bexplicit\s+version\b|\s[-–]\s*explicit\b/.test(t)) return true;
  if(/\(clean\)|\[clean\]|\bclean\s+version\b|\s[-–]\s*clean\b|\bradio\s+edit\b/.test(t)) return false;
  return null;
}

function _explicitBadge(title){
  const e = _detectExplicit(title);
  if(e === true)  return `<span class="pill bad" style="font-size:10px;padding:2px 6px;">E</span>`;
  if(e === false) return `<span class="pill warn" style="font-size:10px;padding:2px 6px;">Clean</span>`;
  return "";
}

// ─── Music Search ─────────────────────────────────────────────────────────────
async function doMusicSearch(){
  const q = ($("musicQ")?.value || "").trim();
  const limit = $("musicLimit")?.value || "10";
  const statusEl = $("musicSearchStatus");
  const resultsEl = $("musicResults");

  if(!q){ if(statusEl) statusEl.innerHTML = pill("warn","Enter a search query"); return; }
  if(statusEl) statusEl.innerHTML = pill("info","Searching…");
  if(resultsEl) resultsEl.innerHTML = "";

  // Bias toward explicit versions unless the user explicitly asked for clean
  const qLow = q.toLowerCase();
  const searchQ = (qLow.includes("explicit") || qLow.includes("clean")) ? q : `${q} explicit`;

  try{
    const res = await apiFetch(`${API}/api/v1/music/search?q=${encodeURIComponent(searchQ)}&limit=${encodeURIComponent(limit)}`, { _timeout: 60000 });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){ if(statusEl) statusEl.innerHTML = pill("bad", data?.detail || `HTTP ${res.status}`); return; }
    const results = data.results || [];
    if(!results.length){ if(statusEl) statusEl.innerHTML = pill("warn","No results"); return; }
    if(statusEl) statusEl.innerHTML = pill("ok", `${results.length} result(s)`);
    if(resultsEl) resultsEl.innerHTML = results.map(r => musicResultRow(r)).join("");
  }catch(e){
    if(statusEl) statusEl.innerHTML = pill("bad","Search failed");
  }
}

function musicResultRow(r){
  const rawTitle = r.title || "";
  const title = r.clean_title || rawTitle;
  const uploader = r.uploader || "";
  const dur = r.duration ? formatMusicDuration(r.duration) : "";
  const dupSt = r.duplicate_status || "no_match";
  const isDup = dupSt === "exact_duplicate";
  const isPossible = dupSt === "possible_duplicate";

  let dupBadge = "";
  let extraClass = "";
  if(isDup){ dupBadge = `<span class="pill bad" style="font-size:11px;">In Library</span>`; extraClass = "already-downloaded"; }
  else if(isPossible){ dupBadge = `<span class="pill warn" style="font-size:11px;">Possible Dup</span>`; extraClass = "possible-duplicate"; }

  const explicitBadge = _explicitBadge(rawTitle);

  // Warn if result looks like a clean/radio-edit version the user didn't want
  const rtLow = rawTitle.toLowerCase();
  const cleanWarn = (rtLow.includes("clean") || rtLow.includes("radio edit"))
    ? `<div class="mini" style="color:var(--yellow);margin-top:2px;">⚠ Clean/Radio Edit version</div>`
    : "";

  const addBtn = isDup
    ? `<button class="btn sm musicAddBtn" data-url="${esc(r.url||r.webpage_url||'')}" data-title="${esc(rawTitle)}" data-force="1">Force</button>`
    : `<button class="btn sm primary musicAddBtn" data-url="${esc(r.url||r.webpage_url||'')}" data-title="${esc(rawTitle)}">Add</button>`;

  const trackUrl = r.url || r.webpage_url || "";
  const shareBtn = trackUrl
    ? `<button class="btn sm musicShareBtn" data-url="${esc(trackUrl)}" title="Copy share link" aria-label="Copy share link"><svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg></button>`
    : "";

  return `<div class="track ${extraClass}">
    <div class="meta">
      <p class="t">${esc(title)} ${explicitBadge}</p>
      <p class="a">${esc(uploader)}${dur ? " &bull; " + esc(dur) : ""}</p>
      ${cleanWarn}
      ${r.duplicate_reason ? `<div class="mini" style="color:var(--muted);">${esc(r.duplicate_reason)}</div>` : ""}
    </div>
    <div class="rightActions">
      ${dupBadge}
      ${shareBtn}
      ${addBtn}
    </div>
  </div>`;
}

function formatMusicDuration(secs){
  if(!secs) return "";
  const s = Math.round(Number(secs));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if(h > 0) return `${h}:${String(m % 60).padStart(2,"0")}:${String(s % 60).padStart(2,"0")}`;
  return `${m}:${String(s % 60).padStart(2,"0")}`;
}

// ─── Music URL submit helper ──────────────────────────────────────────────────
async function _doMusicUrlRequest(url, customTitle){
  const hintEl = $("musicRequestHint");
  const dupEl = $("musicUrlDupWarning");
  const btn = $("musicRequestBtn");
  if(btn){ btn.disabled = true; btn.textContent = "Requesting…"; }
  if(dupEl) dupEl.style.display = "none";
  const result = await requestMusicTrack(url, customTitle, false);
  if(result.ok){
    if(hintEl) hintEl.innerHTML = `<span style="color:var(--green);">Queued: ${esc(result.request_id)}</span>`;
    if($("musicUrl")) $("musicUrl").value = "";
    if($("musicCustomTitle")) $("musicCustomTitle").value = "";
    toast("Track queued for import", "ok");
    loadMusicQueueOnce();
  } else if(result.duplicate){
    if(dupEl){
      dupEl.style.display = "block";
      dupEl.innerHTML = `<div class="pill warn" style="display:inline-flex;gap:8px;">Already in library
        <button class="btn sm" id="musicForceAddBtn" style="padding:2px 8px;font-size:11px;">Force Add</button>
      </div>`;
      $("musicForceAddBtn")?.addEventListener("click", async () => {
        const r2 = await requestMusicTrack(url, customTitle, true);
        if(r2.ok){ dupEl.style.display="none"; toast("Force-queued","ok"); loadMusicQueueOnce(); }
        else toast(r2.error||"Failed","bad");
      }, { once: true });
    }
  } else {
    if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">${esc(result.error||"Request failed")}</span>`;
  }
  if(btn){ btn.disabled = false; btn.textContent = "Request Track"; }
}

// ─── Music Request ────────────────────────────────────────────────────────────
async function requestMusicTrack(url, customTitle, force){
  try{
    const body = { url };
    if(customTitle) body.custom_title = customTitle;
    if(force) body.force = true;
    const res = await apiFetch(`${API}/api/v1/music/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(()=>({}));
    if(res.status === 409 && !force){
      return { ok: false, duplicate: true, data };
    }
    if(!res.ok){
      return { ok: false, error: data?.detail || `HTTP ${res.status}` };
    }
    return { ok: true, request_id: data.request_id };
  }catch(e){
    return { ok: false, error: "Request failed" };
  }
}

// ─── CSV Upload ───────────────────────────────────────────────────────────────
function renderCsvSkippedReport(playlistName, queued, skipped){
  const host = $("csvSkippedReport");
  if(!host) return;
  if(!skipped || !skipped.length){ host.style.display = "none"; return; }
  const total = queued + skipped.length;
  host.style.display = "";
  const id = "csvSkippedList_" + Date.now();
  const stat = (num, lbl, cls) =>
    `<span class="csvSkippedStat${cls ? " " + cls : ""}"><span class="num">${num}</span><span class="lbl">${lbl}</span></span>`;
  host.innerHTML = `<div class="csvSkippedCard">
    <div class="csvSkippedSummary">
      ${stat(queued, "queued", "ok")}
      ${stat(skipped.length, "skipped", "warn")}
      ${stat(total, "total", "")}
    </div>
    <button class="btn sm" style="margin-top:8px;" onclick="
      const el=document.getElementById('${id}');
      if(el){el.style.display=el.style.display==='none'?'':'none';}
      this.textContent=this.textContent.includes('Show')?'Hide skipped tracks':'Show skipped tracks';
    ">Show skipped tracks</button>
    <div id="${id}" class="csvSkippedList" style="display:none;">
      ${skipped.map(t => `<div class="csvSkippedTrack">
        <span class="artist">${esc(t.artist || "")}${t.title ? ` — ${esc(t.title)}` : ""}</span>
        <span class="reason">${esc(t.reason || "low confidence")}</span>
      </div>`).join("")}
    </div>
  </div>`;
}

async function doUploadCsv(){
  const fileInput = $("csvFileInput");
  const hintEl = $("csvUploadHint");
  const btn = $("csvUploadBtn");
  const playlistName = ($("csvPlaylistName")?.value || "My Playlist").trim();

  if(!fileInput?.files?.length){
    if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">Select a CSV file first</span>`;
    return;
  }

  const file = fileInput.files[0];
  if(btn){ btn.disabled = true; btn.textContent = "Uploading…"; }

  try{
    const formData = new FormData();
    formData.append("file", file);
    formData.append("playlist_name", playlistName);
    formData.append("make_navidrome_playlist", "1");

    const res = await apiFetch(`${API}/api/v1/music/requests/csv`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){
      if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">${esc(data?.detail || `HTTP ${res.status}`)}</span>`;
    } else {
      const skipped = Array.isArray(data.skipped) ? data.skipped : [];
      const queued = data.queued_count ?? 0;
      const skipCount = skipped.length;
      const skipNote = skipCount > 0 ? ` (${skipCount} track${skipCount !== 1 ? "s" : ""} skipped — low confidence)` : "";
      if(hintEl) hintEl.innerHTML = `<span style="color:var(--green);">Queued: "${esc(data.playlist_name)}"${esc(skipNote)}</span>`;
      renderCsvSkippedReport(data.playlist_name, queued, skipped);
      fileInput.value = "";
      if($("csvFileName")) $("csvFileName").textContent = "";
      toast(`CSV playlist "${data.playlist_name}" queued${skipNote}`, "ok");
      loadMusicQueueOnce();
    }
  }catch(e){
    if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">Upload failed</span>`;
  }finally{
    if(btn){ btn.disabled = false; btn.textContent = "Upload Playlist"; }
  }
}

// ─── Music Queue ──────────────────────────────────────────────────────────────
async function loadMusicQueueOnce(){
  try{
    const res = await apiFetch(`${API}/api/v1/music/queue`);
    const data = await res.json().catch(()=>({}));
    if(res.ok){
      _lastMusicData = data;
      _checkMusicNotifications(data);
      renderMusicQueue(data);
    }
  }catch(e){}
}

async function _doExplicitFetch(videoId, requestId){
  const badgeId = `explicitBadge_${requestId}`;
  try{
    const r = await apiFetch(`${API}/api/v1/music/track-info?video_id=${encodeURIComponent(videoId)}`);
    const data = r.ok ? await r.json() : null;
    if(!data) return;
    const el = document.getElementById(badgeId);
    if(!el) return;
    if(data.explicit === true)       el.innerHTML = `<span class="pill bad" style="font-size:10px;padding:2px 6px;">E</span>`;
    else if(data.explicit === false)  el.innerHTML = `<span class="pill warn" style="font-size:10px;padding:2px 6px;">Clean</span>`;
    else el.innerHTML = "";
  }catch(e){}
}

function renderMusicQueue(data){
  const host = $("musicQueueHost");
  if(!host) return;

  const mHash = JSON.stringify({
    proc: data.processing?.request_id,
    q: (data.queued||[]).map(x=>x.request_id).join(","),
    h: (data.history||[]).slice(0,5).map(x=>x.request_id+x.status).join(",")
  });
  if(mHash === _lastMusicHash) return;
  _lastMusicHash = mHash;

  const workerBadge = $("musicWorkerBadge");
  const running = data.worker?.running;
  if(workerBadge){
    workerBadge.innerHTML = running
      ? `<span class="pill ok" style="font-size:11px;">worker running</span>`
      : `<span class="pill warn" style="font-size:11px;">worker idle</span>`;
  }

  const processing = data.processing;
  const queued = Array.isArray(data.queued) ? data.queued : [];
  const history = Array.isArray(data.history) ? data.history : [];

  // Collect all items for filtering
  const allItems = [];
  if(processing) allItems.push({ ...processing, _st: data.processing_stale ? "STALE" : "PROCESSING" });
  queued.forEach(it => allItems.push({ ...it, _st: "QUEUED" }));
  history.forEach(it => allItems.push({ ...it, _st: (it.status || "UNKNOWN").toUpperCase() }));

  const visible = allItems.filter(it => musicItemMatchesFilter(it, it._st));

  if(!visible.length && !allItems.length){
    host.innerHTML = emptyState('<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>', "No tracks in queue");
    return;
  }
  if(!visible.length){
    host.innerHTML = `<div class="mini" style="color:var(--muted);">No items match the current filter.</div>`;
    return;
  }

  const _MUSIC_DONE_STATES = new Set(["DONE", "FAILED", "RETRY_LATER"]);
  const activeItems = visible.filter(it => !_MUSIC_DONE_STATES.has(it._st));
  const doneItems   = visible.filter(it =>  _MUSIC_DONE_STATES.has(it._st));

  const renderMusicItem = (item, st) => {
    const title = item.title || item.url || item.request_id || "(untitled)";
    const cls = st === "DONE" ? "ok" : (st === "FAILED" || st === "STALE") ? "bad" : st === "QUEUED" ? "warn" : "info";
    const ts = item.updated_at ? `<span class="mini" style="color:var(--muted);">${esc(relTime(item.updated_at))}</span>` : "";
    const isQueued = st === "QUEUED";
    const dragAttrs = isQueued ? `draggable="true" data-rid="${esc(item.request_id)}"` : "";
    const handle = isQueued ? `<span class="mqDragHandle" aria-hidden="true">⠿</span>` : `<span class="mqDragHandle mqDragHandle--hidden" aria-hidden="true">⠿</span>`;
    const badgeId = `explicitBadge_${item.request_id}`;
    const videoId = (item.url || "").match(/[?&]v=([a-zA-Z0-9_-]{11})/)?.[1] || "";
    const badgePlaceholder = `<span id="${badgeId}">${_explicitBadge(title)}</span>`;
    const artCircle = `<div class="mqArtCircle" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg></div>`;
    return `<div class="queueItem${isQueued ? " mqDraggable" : ""}" ${dragAttrs}>
      <div class="queueTop">
        ${handle}
        ${artCircle}
        <div class="queueTitle">${esc(title)} ${badgePlaceholder}</div>
        <div class="queueMeta">
          <span class="pill type-music">MUSIC</span>
          ${pill(cls, st)} ${ts}
          ${isQueued
            ? `<button class="btn sm musicCancelBtn" data-rid="${esc(item.request_id)}">Cancel</button>`
            : ""}
          ${st === "RETRY_LATER"
            ? `<button class="btn sm musicRetryBtn" data-rid="${esc(item.request_id)}">Retry</button>`
            : ""}
          ${(st === "DONE" || st === "FAILED" || st === "STALE" || st === "RETRY_LATER")
            ? `<button class="btn sm" style="color:var(--muted);" onclick="doMusicDismiss('${esc(item.request_id)}')">✕</button>`
            : ""}
        </div>
      </div>
      ${item.message ? `<div class="mini" style="color:var(--muted);margin-top:4px;">${esc(item.message)}</div>` : ""}
    </div>`;
  };

  // Background-fetch explicit status — serialized via _explicitFetchQueue (one at a time)
  const _fetchExplicitBadge = (item, st) => {
    if (st === "QUEUED") return;
    const videoId = (item.url || "").match(/[?&]v=([a-zA-Z0-9_-]{11})/)?.[1] || "";
    if (!videoId) return;
    _explicitFetchQueue = _explicitFetchQueue.then(() => _doExplicitFetch(videoId, item.request_id));
  };

  let html = activeItems.map(it => renderMusicItem(it, it._st)).join("");

  if(doneItems.length){
    html += `<div class="queueSectionHead">
      <button class="segBtn" id="toggleMusicDoneBtn">
        ${_musicQueueDoneCollapsed
          ? `&#9660; Show ${doneItems.length} completed`
          : `&#9650; Hide completed`}
      </button>
    </div>`;
    if(!_musicQueueDoneCollapsed){
      html += doneItems.map(it => renderMusicItem(it, it._st)).join("");
    }
  }

  host.innerHTML = html;
  updateQueueBadge();

  // Fire background track-info fetches after DOM is set
  activeItems.forEach(it => _fetchExplicitBadge(it, it._st));
  if (!_musicQueueDoneCollapsed) doneItems.forEach(it => _fetchExplicitBadge(it, it._st));

  $("toggleMusicDoneBtn")?.addEventListener("click", () => {
    _musicQueueDoneCollapsed = !_musicQueueDoneCollapsed;
    _lastMusicHash = "";
    renderMusicQueue(_lastMusicData);
  });

  // Wire drag-to-reorder on QUEUED items
  let _dragRid = null;
  host.querySelectorAll(".mqDraggable").forEach(el => {
    el.addEventListener("dragstart", e => {
      _dragRid = el.getAttribute("data-rid");
      el.classList.add("mqDragging");
      e.dataTransfer.effectAllowed = "move";
    });
    el.addEventListener("dragend", () => {
      _dragRid = null;
      el.classList.remove("mqDragging");
      host.querySelectorAll(".mqDragOver").forEach(x => x.classList.remove("mqDragOver"));
    });
    el.addEventListener("dragover", e => {
      if(!_dragRid || el.getAttribute("data-rid") === _dragRid) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      host.querySelectorAll(".mqDragOver").forEach(x => x.classList.remove("mqDragOver"));
      el.classList.add("mqDragOver");
    });
    el.addEventListener("dragleave", () => el.classList.remove("mqDragOver"));
    el.addEventListener("drop", async e => {
      e.preventDefault();
      el.classList.remove("mqDragOver");
      const beforeId = el.getAttribute("data-rid");
      const rid = _dragRid;
      if(!rid || rid === beforeId) return;
      try{
        await apiFetch(`${API}/api/v1/music/queue/reorder`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: rid, before_id: beforeId }),
        });
        loadMusicQueueOnce();
      }catch(err){ toast("Reorder failed", "bad"); }
    });

    // Touch equivalents for iOS Safari
    el.addEventListener("touchstart", e => {
      _dragRid = el.getAttribute("data-rid");
      el.classList.add("mqDragging");
    }, { passive: true });

    el.addEventListener("touchmove", e => {
      if(!_dragRid) return;
      e.preventDefault();
      const touch = e.touches[0];
      const target = document.elementFromPoint(touch.clientX, touch.clientY);
      const over = target?.closest?.(".mqDraggable");
      host.querySelectorAll(".mqDragOver").forEach(x => x.classList.remove("mqDragOver"));
      if(over && over.getAttribute("data-rid") !== _dragRid) over.classList.add("mqDragOver");
    }, { passive: false });

    el.addEventListener("touchend", async e => {
      const rid = _dragRid;
      _dragRid = null;
      el.classList.remove("mqDragging");
      const touch = e.changedTouches[0];
      const target = document.elementFromPoint(touch.clientX, touch.clientY);
      const over = target?.closest?.(".mqDraggable");
      host.querySelectorAll(".mqDragOver").forEach(x => x.classList.remove("mqDragOver"));
      const beforeId = over ? over.getAttribute("data-rid") : null;
      if(!rid || !beforeId || rid === beforeId) return;
      try{
        await apiFetch(`${API}/api/v1/music/queue/reorder`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ request_id: rid, before_id: beforeId }),
        });
        loadMusicQueueOnce();
      }catch(err){ toast("Reorder failed", "bad"); }
    });
  });

  // Wire retry buttons
  host.querySelectorAll(".musicRetryBtn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const rid = btn.getAttribute("data-rid");
      btn.disabled = true; btn.textContent = "Retrying…";
      try{
        const res = await apiFetch(`${API}/api/v1/music/retry`, {
          method: "POST",
          headers: { "Content-Type":"application/json" },
          body: JSON.stringify({ request_id: rid }),
        });
        const d = await res.json().catch(()=>({}));
        if(res.ok){ toast("Re-queued","ok"); loadMusicQueueOnce(); }
        else{ toast(d?.detail||"Retry failed","bad"); btn.disabled=false; btn.textContent="Retry"; }
      }catch(e){ toast("Retry failed","bad"); btn.disabled=false; btn.textContent="Retry"; }
    });
  });
}

// ─── Queue tab loader + filter bar ───────────────────────────────────────────
function applyQueueFilter(){
  renderTorrentItems(_lastTorrentItems.filter(torrentMatchesFilter));
  _lastMusicHash = "";
  renderMusicQueue(_lastMusicData);
}

function initQueueFilterBar(){
  const bar = $("queueFilterBar");
  if(!bar || bar.dataset.wired) return;
  bar.dataset.wired = "1";

  // Type buttons
  bar.querySelectorAll("[data-qf-type]").forEach(btn => {
    btn.addEventListener("click", () => {
      _qf.type = btn.getAttribute("data-qf-type");
      bar.querySelectorAll("[data-qf-type]").forEach(b => b.classList.toggle("active", b === btn));
      applyQueueFilter();
    });
  });

  // Status buttons
  bar.querySelectorAll("[data-qf-status]").forEach(btn => {
    btn.addEventListener("click", () => {
      _qf.status = btn.getAttribute("data-qf-status");
      bar.querySelectorAll("[data-qf-status]").forEach(b => b.classList.toggle("active", b === btn));
      applyQueueFilter();
    });
  });

  // Text search
  const txt = $("queueFilterText");
  txt?.addEventListener("input", () => {
    _qf.text = (txt.value || "").trim();
    applyQueueFilter();
  });
}

function loadQueueTab(){
  initQueueFilterBar();
  loadQueueOnce();
  loadMusicQueueOnce();
  loadYtQueueForQueueTab();
}

async function loadYtQueueForQueueTab(){
  const host = $("videoQueueHost");
  const badge = $("videoQueueBadge");
  if(!host) return;
  try{
    const res = await apiFetch(`${API}/api/v1/youtube/queue`);
    if(!res.ok) return;
    const items = await res.json().catch(() => []);
    _lastYtItems = Array.isArray(items) ? items : [];
    // Check for new completions/failures to increment notification badge
    if(_prevYtStatuses !== null){
      for(const it of _lastYtItems){
        const prev = _prevYtStatuses.get(it.id);
        if(prev && prev !== it.status && (it.status === "done" || it.status === "failed")){
          if(_activeTab !== "queue"){ _queueBadgeCount++; }
        }
      }
    }
    _prevYtStatuses = new Map(_lastYtItems.map(it => [it.id, it.status]));
    updateQueueBadge();
    if(!_lastYtItems.length){ host.innerHTML = emptyState('<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>', "No video downloads"); if(badge) badge.textContent = ""; return; }

    // Auto-clear done/failed items older than 60 seconds
    const now = Date.now() / 1000;
    const filtered = _lastYtItems.filter(it =>
      !((it.status === "done" || it.status === "failed") && it.created_at && (now - it.created_at) > 3600)
    );

    const activeItems = filtered.filter(it => it.status !== "done" && it.status !== "failed");
    const doneItems   = filtered.filter(it => it.status === "done"  || it.status === "failed");

    if(badge) badge.textContent = activeItems.length ? `${activeItems.length} active` : "";
    if(!filtered.length){ host.innerHTML = ""; return; }

    function renderYtItem(it){
      const statusPill = it.status === "done"
        ? pill("ok", "DONE")
        : it.status === "failed"
          ? pill("bad", "FAILED")
          : it.status === "downloading"
            ? pill("info", "DOWNLOADING")
            : pill("info", "QUEUED");
      const errLine = it.error ? `<div class="mini" style="color:var(--red); margin-top:4px; word-break:break-all;">${esc(it.error.slice(-200))}</div>` : "";
      const retryBtn = it.status === "failed"
        ? `<button class="btn sm" onclick="doYtRetry('${esc(it.url)}','${esc(it.subfolder||'')}','${esc(it.title||'')}')">↺ Retry</button>`
        : "";
      const dismissBtn = (it.status === "done" || it.status === "failed")
        ? `<button class="btn sm" style="color:var(--muted);" onclick="doYtDismiss('${esc(it.id)}')">✕</button>`
        : "";
      const destPath = esc(it.subfolder ? `/mnt/media/YouTube/${it.subfolder}` : "/mnt/media/YouTube");
      return `<div class="queueItem" data-id="${esc(it.id)}" data-status="${esc(it.status)}">
        <div class="queueTop">
          <div class="ytThumbPlaceholder" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg></div>
          <div class="queueItemContent">
            <div class="queueTitle">${esc(it.title || it.url)}</div>
            <div class="queueItemPath">${destPath}</div>
          </div>
          <div class="queueMeta">
            <span class="pill type-video">VIDEO</span>
            ${statusPill}
            ${retryBtn}
            ${dismissBtn}
          </div>
        </div>
        ${errLine}
      </div>`;
    }

    let html = activeItems.map(renderYtItem).join("");

    if(doneItems.length){
      html += `<div class="queueSectionHead">
        <button class="segBtn" id="toggleYtDoneBtn">
          ${_ytQueueDoneCollapsed
            ? `&#9660; Show ${doneItems.length} completed`
            : `&#9650; Hide completed`}
        </button>
      </div>`;
      if(!_ytQueueDoneCollapsed){
        html += doneItems.map(renderYtItem).join("");
      }
    }

    host.innerHTML = html;

    $("toggleYtDoneBtn")?.addEventListener("click", () => {
      _ytQueueDoneCollapsed = !_ytQueueDoneCollapsed;
      loadYtQueueForQueueTab();
    });
  }catch(e){}
}

window.doYtRetry = async function(url, subfolder, title){
  try{
    await apiFetch(`${API}/api/v1/youtube/download`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url, subfolder, title, jellyfin_rescan: true})
    });
    loadYtQueueForQueueTab();
  }catch(e){}
};

window.doYtDismiss = function(id){
  _lastYtItems = _lastYtItems.filter(it => it.id !== id);
  loadYtQueueForQueueTab();
};

window.doMusicDismiss = function(rid){
  if(_lastMusicData.history)
    _lastMusicData.history = _lastMusicData.history.filter(it => it.request_id !== rid);
  if(_lastMusicData.processing?.request_id === rid)
    _lastMusicData.processing = null;
  _lastMusicHash = "";
};

// ─── Navidrome Link Modal ─────────────────────────────────────────────────────
async function checkNavidromeStatus(){
  try{
    const res = await apiFetch(`${API}/api/v1/auth/navidrome/status`);
    if(!res.ok) return;
    const data = await res.json().catch(()=>({}));
    if(data.linked === false){
      // Show gentle modal (clear inputs first)
      if($("navLinkUser")) $("navLinkUser").value = "";
      if($("navLinkPass")) $("navLinkPass").value = "";
      if($("navLinkError")) $("navLinkError").textContent = "";
      showModal("navLinkModal", true);
    }
  }catch(e){}
}

async function doNavLinkConfirm(){
  const nav_user = ($("navLinkUser")?.value || "").trim();
  const nav_pass = ($("navLinkPass")?.value || "").trim();
  const errEl = $("navLinkError");
  const btn = $("navLinkConfirm");

  if(!nav_user || !nav_pass){
    if(errEl) errEl.textContent = "Username and password required.";
    return;
  }
  if(btn){ btn.disabled = true; btn.textContent = "Linking…"; }
  if(errEl) errEl.textContent = "";

  try{
    const res = await apiFetch(`${API}/api/v1/auth/navidrome/link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nav_user, nav_pass }),
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){
      if(errEl) errEl.textContent = data?.detail || `Failed (HTTP ${res.status})`;
    } else {
      showModal("navLinkModal", false);
      toast(`Navidrome linked as ${nav_user}`, "ok");
    }
  }catch(e){
    if(errEl) errEl.textContent = "Could not reach server.";
  }finally{
    if(btn){ btn.disabled = false; btn.textContent = "Link Account"; }
  }
}

// ─── Browser push notifications ───────────────────────────────────────────────
function requestNotifyPermission(){
  if(!("Notification" in window)) return;
  if(Notification.permission === "default"){
    Notification.requestPermission().catch(() => {});
  }
}

function pushNotify(title, body){
  // Always toast
  toast(`${title}: ${body}`, title.toLowerCase().includes("fail") ? "bad" : "ok");
  // Browser notification if granted
  if(!("Notification" in window) || Notification.permission !== "granted") return;
  try{
    const n = new Notification(title, { body, icon: "/favicon.ico", silent: false });
    setTimeout(() => n.close(), 8000);
  }catch(e){}
}

function _checkTorrentNotifications(items){
  const current = new Map(items.map(it => [it.id, it.status]));
  if(_prevTorrentStates !== null){
    for(const [id, status] of current){
      const prev = _prevTorrentStates.get(id);
      if(!prev || prev === status) continue;
      const title = items.find(it => it.id === id)?.title || id;
      if(status === "ready"){
        pushNotify("Download ready", title);
        if(_activeTab !== "queue") _queueBadgeCount++;
      } else if(status === "imported"){
        pushNotify("Imported to library", title);
        if(_activeTab !== "queue") _queueBadgeCount++;
      } else if(status === "failed"){
        pushNotify("Download failed", title);
        if(_activeTab !== "queue") _queueBadgeCount++;
      }
    }
  }
  _prevTorrentStates = current;
}

function _checkMusicNotifications(data){
  const current = new Map();
  if(data.processing) current.set(data.processing.request_id, "PROCESSING");
  (data.queued  || []).forEach(it => current.set(it.request_id, "QUEUED"));
  (data.history || []).forEach(it => current.set(it.request_id, (it.status || "UNKNOWN").toUpperCase()));

  if(_prevMusicStates !== null){
    for(const [rid, status] of current){
      const prev = _prevMusicStates.get(rid);
      if(!prev || prev === status) continue;
      const all = [data.processing, ...(data.queued||[]), ...(data.history||[])].filter(Boolean);
      const item = all.find(it => it.request_id === rid);
      const label = item ? (item.title || item.url || rid) : rid;
      if(status === "DONE"){
        pushNotify("Music imported", label);
        if(_activeTab !== "queue") _queueBadgeCount++;
      } else if(status === "FAILED"){
        pushNotify("Music import failed", label);
        if(_activeTab !== "queue") _queueBadgeCount++;
      }
    }
  }
  _prevMusicStates = current;
}

// ─── Queue badge ──────────────────────────────────────────────────────────────
function updateQueueBadge(){
  const torrentActive = _lastTorrentItems.filter(it => _TORRENT_ACTIVE_STATES.has(it.status || "queued")).length;
  const mData = _lastMusicData || {};
  const musicActive = (mData.processing ? 1 : 0) + (Array.isArray(mData.queued) ? mData.queued.length : 0);
  const ytActive = _lastYtItems.filter(it => it.status === "queued" || it.status === "downloading").length;
  const activeTotal = torrentActive + musicActive + ytActive;

  const useNew = _queueBadgeCount > 0;
  const count  = useNew ? _queueBadgeCount : activeTotal;
  const txt = count > 99 ? "99+" : String(count);
  const show = count > 0;

  const _applyBadge = (badge) => {
    if(!badge) return;
    badge.textContent = txt;
    badge.style.display = show ? "inline-flex" : "none";
    badge.style.background = useNew ? "var(--red)" : "";
    badge.style.color      = useNew ? "#fff" : "";
  };
  _applyBadge($("queueBadge"));
  _applyBadge($("mobileQueueBadge"));
}

// ─── Dark / light theme ───────────────────────────────────────────────────────
function initTheme(){
  const saved = localStorage.getItem("theme");
  document.documentElement.dataset.theme = saved === "light" ? "light" : "";
  _updateThemeBtn();
}

function toggleTheme(){
  const next = document.documentElement.dataset.theme === "light" ? "" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("theme", next || "dark");
  _updateThemeBtn();
}

function _updateThemeBtn(){
  const btn = $("themeBtn");
  if(!btn) return;
  const light = document.documentElement.dataset.theme === "light";
  btn.innerHTML = light ? _SVG_MOON : _SVG_SUN;
  btn.title = light ? "Switch to dark mode" : "Switch to light mode";
}

// ─── YouTube → Jellyfin ───────────────────────────────────────────────────────
let _ytPlaylistEntries = [];
let _ytPlaylistFetching = false;

async function _onYtUrlChange(){
  if(_ytPlaylistFetching) return;
  const url = ($("ytUrl")?.value || "").trim();
  const preview = $("ytPlaylistPreview");
  if(!preview) return;
  if(!url || !url.includes("list=")){
    preview.style.display = "none";
    _ytPlaylistEntries = [];
    return;
  }
  const infoEl = $("ytPlaylistInfo");
  if(infoEl) infoEl.textContent = "Fetching playlist info…";
  preview.style.display = "block";
  $("ytPlaylistDownloadAll").disabled = true;
  _ytPlaylistFetching = true;
  try{
    const res = await apiFetch(`${API}/api/v1/youtube/playlist-info`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await res.json().catch(()=>({}));
    if(!res.ok){
      if(infoEl) infoEl.innerHTML = `<span style="color:var(--red);">${esc(data?.detail || "Failed to fetch playlist")}</span>`;
      return;
    }
    _ytPlaylistEntries = data.entries || [];
    const plTitle = data.title ? `"${esc(data.title)}" — ` : "";
    if(infoEl) infoEl.innerHTML = `Playlist detected: ${plTitle}<strong>${data.count || 0} videos</strong> will be queued`;
    $("ytPlaylistDownloadAll").disabled = _ytPlaylistEntries.length === 0;
  }catch(e){
    if(infoEl) infoEl.innerHTML = `<span style="color:var(--red);">Could not reach server</span>`;
  }finally{
    _ytPlaylistFetching = false;
  }
}

async function _doPlaylistDownloadAll(){
  if(!_ytPlaylistEntries.length) return;
  const subfolder = ($("ytSubfolder")?.value || "").trim();
  const jellyfin_rescan = $("ytRescan")?.checked ?? true;
  const btn = $("ytPlaylistDownloadAll");
  if(btn){ btn.disabled = true; btn.textContent = `Queuing ${_ytPlaylistEntries.length} videos…`; }
  let queued = 0;
  for(const entry of _ytPlaylistEntries){
    const videoUrl = `https://www.youtube.com/watch?v=${entry.id}`;
    try{
      const res = await apiFetch(`${API}/api/v1/youtube/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: videoUrl, title: "", subfolder, jellyfin_rescan }),
      });
      if(res.ok) queued++;
    }catch(_){}
  }
  toast(`Queued ${queued} of ${_ytPlaylistEntries.length} videos`, queued > 0 ? "ok" : "bad");
  if(btn){ btn.disabled = false; btn.textContent = "Download all to Jellyfin"; }
  $("ytPlaylistPreview").style.display = "none";
  _ytPlaylistEntries = [];
  if($("ytUrl")) $("ytUrl").value = "";
  setTimeout(loadYtQueue, 800);
}

async function doYtDownload(force = false){
  const url = ($("ytUrl")?.value || "").trim();
  const subfolder = ($("ytSubfolder")?.value || "").trim();
  const title = ($("ytTitle")?.value || "").trim();
  const jellyfin_rescan = $("ytRescan")?.checked ?? true;
  const hintEl = $("ytHint");
  const btn = $("ytDownloadBtn");

  if(!url){
    if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">YouTube URL is required</span>`;
    return;
  }

  if(!force){
    // Client-side: already in queue?
    const vidMatch = url.match(/[?&]v=([a-zA-Z0-9_-]{11})/);
    const vidId = vidMatch?.[1];
    if(vidId && _lastYtItems.some(it => (it.url || "").includes(vidId))){
      toast("Already in video queue", "warn");
    }

    // Server-side: already downloaded to disk?
    try{
      const chk = await apiFetch(`${API}/api/v1/youtube/check?url=${encodeURIComponent(url)}`);
      if(chk.ok){
        const chkData = await chk.json().catch(() => ({}));
        if(chkData.exists){
          if(hintEl) hintEl.innerHTML = `<span style="color:var(--yellow);">&#9888; Already downloaded: ${esc(chkData.path)} <button class="btn sm" style="margin-left:8px;" onclick="doYtDownload(true)">Download again</button></span>`;
          return;
        }
      }
    }catch(e){}
  }

  if(btn){ btn.disabled = true; btn.textContent = "Queuing…"; }
  if(hintEl) hintEl.textContent = "";

  try{
    const res = await apiFetch(`${API}/api/v1/youtube/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, title, subfolder, jellyfin_rescan }),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok || !data?.ok){
      const msg = data?.detail || data?.error || `HTTP ${res.status}`;
      if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">${esc(msg)}</span>`;
    } else {
      if(hintEl) hintEl.innerHTML = `<span style="color:var(--green);">Queued — downloading in background</span>`;
      if($("ytUrl")) $("ytUrl").value = "";
      toast("YouTube download queued", "ok");
      setTimeout(loadYtQueue, 800);
    }
  }catch(e){
    if(hintEl) hintEl.innerHTML = `<span style="color:var(--red);">Request failed</span>`;
  } finally {
    if(btn){ btn.disabled = false; btn.textContent = "Download to Jellyfin"; }
  }
}

// ─── Settings: Storage stats ──────────────────────────────────────────────────
let _storageCacheTs = 0;

async function loadStorageStats(){
  const el = $("storageStatsContent");
  if(!el) return;
  el.innerHTML = `<div class="mini" style="color:var(--muted);">Loading…</div>`;
  try{
    const res = await apiFetch(`${API}/api/v1/storage/stats`);
    if(!res.ok){ el.innerHTML = `<div class="mini" style="color:var(--red);">Failed (HTTP ${res.status})</div>`; return; }
    const d = await res.json().catch(()=>({}));
    _storageCacheTs = Date.now();

    const total = d.total_gb || 0;
    const used  = d.total_used_gb || 0;
    const free  = d.total_free_gb || 0;
    const pct   = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
    const barColor = pct > 90 ? "var(--red)" : pct > 75 ? "var(--orange)" : "var(--blue)";

    const subRows = [
      { label: "Music",  val: d.music_gb },
      { label: "TV",     val: d.tv_gb },
      { label: "Movies", val: d.movies_gb },
    ].map(r => `
      <div class="storageStatRow">
        <span class="label">${esc(r.label)}</span>
        <span class="val">${r.val != null ? r.val + " GB" : "—"}</span>
      </div>`).join("");

    el.innerHTML = `
      <div style="margin-bottom:14px;">
        <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
          <span class="mini" style="font-weight:600;">Total Disk</span>
          <span class="mini">${used} GB used &bull; ${free} GB free &bull; ${total} GB total</span>
        </div>
        <div class="progressBar"><div style="width:${pct}%; background:${barColor};"></div></div>
      </div>
      <div class="stack" style="gap:6px;">${subRows}</div>`;
  }catch(e){
    el.innerHTML = `<div class="mini" style="color:var(--red);">Failed to load storage stats</div>`;
  }
}

// ─── Settings: Sites ──────────────────────────────────────────────────────────
function renderSettingsSites(){
  const host = $("settingsSitesList");
  if(!host || host.dataset.rendered) return;
  host.dataset.rendered = "1";

  const savedStr = localStorage.getItem("enabledSites");
  const savedEnabled = savedStr ? JSON.parse(savedStr) : null;
  const allSites = [...PREFERRED_SITES, ...KNOWN_BLOCKED_SITES];

  host.innerHTML = allSites.map(s => {
    const label = SITE_LABELS[s] || s;
    const isTor = TOR_SITES.includes(s);
    const isBlocked = KNOWN_BLOCKED_SITES.includes(s);
    const checked = savedEnabled ? savedEnabled.includes(s) : !isBlocked;
    return `<div class="siteRow">
      <div>
        <div class="siteRowName">${esc(label)}</div>
        <div class="mini" style="color:var(--muted); margin-top:2px;">${isTor ? "Via Tor" : "Direct"}${isBlocked ? " &bull; Often blocks Tor" : ""}</div>
      </div>
      <label class="toggle" title="Enable/disable">
        <input type="checkbox" class="siteToggle" data-site="${esc(s)}" ${checked ? "checked" : ""} />
        <span class="knob" aria-hidden="true"></span>
      </label>
    </div>`;
  }).join("");

  host.addEventListener("change", () => {
    const enabled = [...host.querySelectorAll(".siteToggle")].filter(cb => cb.checked).map(cb => cb.getAttribute("data-site"));
    localStorage.setItem("enabledSites", JSON.stringify(enabled));
    toast("Site preferences saved", "ok");
  });
}

async function loadYtQueue(){
  const host = $("ytQueueList");
  if(host) host.innerHTML = "";
}
