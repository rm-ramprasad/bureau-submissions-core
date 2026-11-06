// Shared by every page: sign-in (any OpenID Connect provider, authorization code + PKCE), API calls, the page shell
// (sidebar, top bar, NBFC selector), flash messages and formatting. Loaded before the page's own script and before
// Alpine.

// config.json (same origin, written by the deploy step; locally { "auth": "none" }):
//   { "auth": "oidc", "authority": "<issuer URL>", "clientId": "<client id>", "scope": "<API scope, optional>",
//     "apiBase": "<API address, optional>", "useIdToken": false }
// useIdToken: send the ID token instead of the access token (providers whose access tokens carry no audience, e.g.
// Cognito); the API checks the audience either way.
// Read synchronously so every script after this one sees it, as the old config.js did.
const CFG = (() => {
  try {
    const xhr = new XMLHttpRequest();
    xhr.open("GET", "/config.json", false);
    xhr.send();
    if (xhr.status === 200) return JSON.parse(xhr.responseText.replace(/^\uFEFF/, ""));   // PowerShell 5 writes a BOM
  } catch (e) { /* no config: development */ }
  return { auth: "none" };
})();
const SIGN_IN = CFG.auth === "oidc";
const API_BASE = (CFG.apiBase || "").replace(/\/$/, "");    // the Function App (another origin, CORS); "" = same origin

// ---------------------------------------------------------------- sign-in
const Auth = {
  manager: null,

  mgr() {
    if (!this.manager && SIGN_IN) {
      this.manager = new oidc.UserManager({
        authority: CFG.authority, client_id: CFG.clientId,
        response_type: "code", scope: `openid profile email ${CFG.scope || ""}`.trim(),
        redirect_uri: location.origin + "/callback.html", loadUserInfo: false, automaticSilentRenew: false,
        userStore: new oidc.WebStorageStateStore({ store: window.sessionStorage }),   // gone when the tab closes
      });
    }
    return this.manager;
  },

  // The access token for the API (scope CFG.scope), or a redirect to the sign-in page (the promise then never
  // resolves).
  async token() {
    if (!SIGN_IN) return null;
    const user = await this.mgr().getUser();
    if (user && !user.expired) return CFG.useIdToken ? user.id_token : user.access_token;
    await this.signIn();
    return new Promise(() => {});
  },

  signIn() {
    Splash.show("Taking you to sign in…");
    return this.mgr().signinRedirect({ state: location.pathname + location.search });
  },

  async signOut() {
    try { await Api.post("/session/end"); } catch (e) { /* signing out anyway */ }
    if (!SIGN_IN) { location.href = "/"; return; }
    await this.mgr().signoutRedirect({ post_logout_redirect_uri: location.origin + "/signed-out.html" });
  },
};

// ---------------------------------------------------------------- API
class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

const Api = {
  async request(method, path, params = {}, body = null) {
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") query.append(k, v);
    const headers = { "Accept": "application/json" };
    const token = await Auth.token();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    if (body) headers["Content-Type"] = "application/json";
    const resp = await fetch(`${API_BASE}/api${path}${query.toString() ? "?" + query : ""}`,
                             { method, headers, body: body ? JSON.stringify(body) : null });
    if (resp.status === 401 && SIGN_IN) { await Auth.signIn(); return new Promise(() => {}); }
    return resp;
  },

  // POST a file as multipart form data. Returns the JSON body; a 422 (a rejected workbook) is returned, not thrown,
  // so the page can show what was wrong with it.
  async upload(path, file) {
    const form = new FormData();
    form.append("file", file);
    const headers = { "Accept": "application/json" };
    const token = await Auth.token();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const resp = await fetch(`${API_BASE}/api${path}`, { method: "POST", headers, body: form });
    if (resp.status === 401 && SIGN_IN) { await Auth.signIn(); return new Promise(() => {}); }
    if (resp.status === 422) return { ...(await resp.json().catch(() => ({}))), rejected: true };
    return this.json(resp);
  },

  async json(resp) {
    const data = await resp.json().catch(() => ({}));
    if (resp.status === 403) App.denied(data.error);
    if (!resp.ok) throw new ApiError(data.error || `Request failed (${resp.status})`, resp.status);
    return data;
  },

  // GET with the selected NBFC (unless the page passes tenant itself)
  async get(path, params = {}) {
    return this.json(await this.request("GET", path, { tenant: App.store().tenant, ...params }));
  },

  async post(path, body = {}) {
    return this.json(await this.request("POST", path, {}, { tenant: App.store().tenant, ...body }));
  },

  // Downloads: the API answers with a short-lived link (Blob storage SAS), or streams the file (local development).
  async download(path, params = {}) {
    const resp = await this.request("GET", path, { tenant: App.store().tenant, ...params });
    if ((resp.headers.get("Content-Type") || "").includes("application/json")) {
      const data = await this.json(resp);
      window.location.href = data.url;
      return;
    }
    if (!resp.ok) throw new ApiError(`Download failed (${resp.status})`, resp.status);
    const name = /filename="([^"]+)"/.exec(resp.headers.get("Content-Disposition") || "")?.[1] || "download";
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(await resp.blob()),
                                                               download: name });
    link.click();
    URL.revokeObjectURL(link.href);
  },
};

// ---------------------------------------------------------------- formatting
const F = {
  pad: (n) => String(n).padStart(2, "0"),
  dt(iso) {                                   // UTC from the API -> local "dd-mm-yyyy hh:mm"
    if (!iso) return "";
    const d = new Date(iso);
    return `${F.pad(d.getDate())}-${F.pad(d.getMonth() + 1)}-${d.getFullYear()} ${F.pad(d.getHours())}:${F.pad(d.getMinutes())}`;
  },
  d(iso) {                                    // "2026-09-30" -> "30-09-2026"
    if (!iso) return "";
    const [y, m, day] = iso.slice(0, 10).split("-");
    return `${day}-${m}-${y}`;
  },
  num: (n) => (n === null || n === undefined ? "" : Number(n).toLocaleString("en-IN")),
  pct: (x) => (x === null || x === undefined ? "—" : `${Math.round(x * 100)}%`),
  age(seconds) {
    if (seconds === null || seconds === undefined) return "";
    const minutes = Math.floor(seconds / 60), days = Math.floor(minutes / 1440), hours = Math.floor((minutes % 1440) / 60);
    return days ? `${days}d ${hours}h` : hours ? `${hours}h` : `${minutes}m`;
  },
  title: (s) => (s ? String(s).toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : ""),
  param: (name) => new URLSearchParams(location.search).get(name),
  url(path, params = {}) {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ""));
    return q.toString() ? `${path}?${q}` : path;
  },
};

// ---------------------------------------------------------------- app state, flash messages
const App = {
  store: () => Alpine.store("app"),

  flash(text, kind = "ok") {
    App.store().flashes = [{ text, kind, id: Math.random() }];      // the latest message replaces older ones
    window.scrollTo({ top: 0, behavior: "smooth" });
  },

  // A message shown on the next page (e.g. after creating a request and moving to its exceptions step)
  flashNext(text, kind = "ok") {
    try { sessionStorage.setItem("flash", JSON.stringify({ text, kind })); } catch (e) { /* not shown */ }
  },

  error(err) { App.flash(err.message || String(err), "error"); },

  async copy(text) {
    try { await navigator.clipboard.writeText(text); App.flash("Copied to the clipboard."); }
    catch (e) { App.flash("Could not copy: select the text and copy it instead.", "error"); }
  },

  go(path, message) {
    if (message) App.flashNext(message);
    location.href = path;
  },

  denied(message) {
    document.body.innerHTML = `<div class="center-page"><div class="login-box"><h1>No access</h1>
      <p>${(message || "Your account is not registered for Bureau Submission.").replace(/</g, "&lt;")}</p>
      <p><button onclick="Auth.signOut()">Sign out</button></p></div></div>`;
    throw new ApiError(message, 403);
  },

  // The deployment's NBFC (from /meta); the API applies it to every call whatever the page sends.
  tenantName() {
    const s = App.store();
    return s.meta?.tenants.find((t) => t.tenant_id === s.tenant)?.display_name || s.tenant;
  },
};

// ---------------------------------------------------------------- sign-in splash and welcome
// The RAR Fincare arrows (traced from Logo.jpg) assemble in turn while signing in; right after a sign-in the splash
// turns into a welcome note, then fades into the page. Shown once per sign-in (the "signed-in" flag set by
// callback.html), and while redirecting to Microsoft.
const ARROWS_SVG = `<svg class="arrows" viewBox="94 6 66 60" aria-hidden="true"><g fill="none" stroke-width="8">
  <path class="a-shaft" pathLength="1" d="M99 63L128 35" stroke="#25257a"/>
  <path class="a-front" pathLength="1" d="M112 29.5H137V55" stroke="#359a66"/>
  <path class="a-back" pathLength="1" d="M127 15.5H151V41" stroke="#359a66"/></g></svg>`;

const Splash = {
  el: null,

  show(text) {
    if (!this.el) {
      this.el = document.createElement("div");
      this.el.className = "splash";
      this.el.setAttribute("role", "status");
      document.body.append(this.el);
    }
    this.el.innerHTML = `${ARROWS_SVG}<p class="splash-text"></p>`;
    this.el.querySelector(".splash-text").textContent = text;
  },

  welcome(me) {
    if (!this.el) return;
    const hour = new Date().getHours();
    const part = hour < 12 ? "morning" : hour < 17 ? "afternoon" : "evening";
    const first = (me.display_name || me.username || "").trim().split(/\s+/)[0];
    const last = me.last_login_at ? `Last signed in ${F.dt(me.last_login_at)}` : "Welcome to Bureau Submission";
    this.el.innerHTML = `<img class="splash-logo" src="Logo.jpg" alt="RAR Fincare">
      <h1 class="splash-hello"></h1><p class="splash-text"></p>`;
    this.el.querySelector(".splash-hello").textContent = `Good ${part}${first ? ", " + first : ""}`;
    this.el.querySelector(".splash-text").textContent = last;     // the logo already names the NBFC
    const done = () => this.hide();
    this.el.addEventListener("click", done, { once: true });
    document.addEventListener("keydown", done, { once: true });
    setTimeout(done, 1600);
  },

  hide() {
    const el = this.el;
    if (!el) return;
    this.el = null;
    el.classList.add("leaving");
    setTimeout(() => el.remove(), 400);
  },
};

const JUST_SIGNED_IN = (() => { try { return !!sessionStorage.getItem("signed-in"); } catch (e) { return false; } })();
if (JUST_SIGNED_IN && document.body) Splash.show("Signing you in…");

document.addEventListener("alpine:init", () => {
  try { localStorage.removeItem("tenant"); } catch (e) { /* none */ }     // left over from the NBFC selector
  Alpine.store("app", { tenant: "", meta: null, user: null, health: null, attention: null, bellOpen: false, flashes: [] });
  try {
    const next = JSON.parse(sessionStorage.getItem("flash") || "null");
    sessionStorage.removeItem("flash");
    if (next) Alpine.store("app").flashes.push({ ...next, id: 0 });
  } catch (e) { /* none */ }
  Api.get("/attention").then((a) => { Alpine.store("app").attention = a; }).catch(() => {});   // the bell
  Promise.all([Api.get("/me"), Api.get("/meta"), Api.get("/health")]).then(([me, meta, health]) => {
    const s = Alpine.store("app");
    Object.assign(s, { user: me, meta, health, tenant: meta.tenant || "" });
    if (sessionStorage.getItem("signed-in")) {      // right after the sign-in redirect: record the login once
      sessionStorage.removeItem("signed-in");
      Api.post("/session").catch(() => {});        // after /me, so the welcome shows the previous sign-in
      Splash.welcome(me);
    }
  }).catch((e) => { Splash.hide(); if (e.status !== 403) App.error(e); });
});

// ---------------------------------------------------------------- page shell
const ICONS = {
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  new: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 11v6M9 14h6"/>',
  exceptions: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>',
  tracker: '<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  files: '<ellipse cx="12" cy="5.5" rx="8" ry="2.5"/><path d="M4 5.5v13c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5v-13M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5"/>',
  audit: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  admin: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  pulse: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  view: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5M8.5 11h5"/>',
};
const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

const NAV = [
  ["Main"], ["howto", "howto.html", "How to", "help"], ["dashboard", "index.html", "Dashboard", "dashboard"],
  ["new", "batch.html", "New request", "new"],
  ["Reports"], ["tracker", "tracker.html", "Submission tracker", "tracker"],
  ["tudf", "tudf.html", "File viewer", "view"], ["files", "files.html", "Submitted files", "files"],
  ["audit", "audit.html", "Audit", "audit"],
  ["Admin"], ["rules", "rules.html", "Rules & policies", "admin"], ["bureaus", "bureaus.html", "Bureau profiles", "admin"],
];

// The bell's count: items needing attention plus a slow queue.
function bellCount() {
  const s = Alpine.store("app");
  return (s.attention?.count || 0) + (s.health?.slow ? 1 : 0);
}

// Wraps the page's <main> in the sidebar and top bar. <body data-nav="tracker" data-crumb="...html...">
function renderShell() {
  const main = document.querySelector("main.content");
  if (!main) return;
  const active = document.body.dataset.nav;
  const nav = NAV.map(([key, href, label, ic]) => {
    if (!href && !label) return `<div class="nav-group">${key}</div>`;
    if (!href) return `<span class="nav-item soon" title="Coming in a later step">${icon(ic)}<span>${label}</span><em>soon</em></span>`;
    return `<a class="nav-item ${key === active ? "active" : ""}" href="${href}">${icon(ic)}<span>${label}</span></a>`;
  }).join("");
  const shell = document.createElement("div");
  shell.className = "shell";
  shell.setAttribute("x-data", "{}");
  shell.innerHTML = `
  <aside class="sidebar">
    <div class="brand"><span class="brand-logo"><img src="Logo.jpg" alt="RAR Fincare"></span>
      <span class="brand-mark"><img src="favicon.svg" alt="RAR Fincare"></span>
      <small>Bureau Submission Hub</small></div>
    <nav>${nav}</nav>
    <div class="sidebar-foot">
      <div class="user"><span class="avatar" x-text="($store.app.user?.display_name || '··').slice(0, 2).toUpperCase()"></span>
        <span><strong x-text="$store.app.user?.display_name"></strong><small x-text="$store.app.user?.email || 'Bureau reporting team'"></small></span></div>
      <div class="user-actions" x-show="SIGN_IN"><button class="linkish" @click="Auth.signOut()">Sign out</button></div>
      <div class="powered"><img src="ota-mark.png" alt=""><span>Powered by Open Terrace Analytics</span></div>
    </div>
  </aside>
  <div class="main">
    <header class="topbar">
      <div class="crumbs"><a href="index.html">Bureau reporting</a> <span>/</span> ${document.body.dataset.crumb || ""}</div>
      <div class="topbar-actions">
        <div class="bell" @click.outside="$store.app.bellOpen = false" @keydown.escape="$store.app.bellOpen = false">
          <button type="button" class="bell-button" @click="$store.app.bellOpen = !$store.app.bellOpen"
                  :aria-label="'Needs attention: ' + bellCount()" :title="bellCount() ? bellCount() + ' item(s) need attention' : 'Nothing needs attention'">
            ${icon("bell")}<span class="bell-count" x-show="bellCount()" x-text="bellCount()"></span></button>
          <div class="bell-panel" x-show="$store.app.bellOpen" x-cloak>
            <div class="bell-head"><strong>Needs attention</strong><span class="muted">last 30 days</span></div>
            <div class="bell-item bad" x-show="$store.app.health?.slow"><strong>Runs are waiting.</strong> The oldest has waited
              <span x-text="F.age($store.app.health?.waiting_seconds)"></span>; recovery re-starts it within 15 minutes.</div>
            <template x-for="a in $store.app.attention?.items || []" :key="a.batch_id + a.type">
              <a class="bell-item" :class="a.stage === 'FAILED' ? 'bad' : 'warn'" :href="'batch.html?id=' + a.batch_id">
                <span><span class="mono" x-text="a.batch_id"></span> · <span x-text="F.title(a.type)"></span> · as of
                  <span x-text="F.d(a.as_of_date)"></span></span>
                <span class="bell-reason" x-text="a.reason"></span></a>
            </template>
            <div class="bell-item muted" x-show="!bellCount()">Nothing needs attention.</div>
          </div>
        </div>
        <a class="button" href="batch.html">${icon("plus")} New request</a>
      </div>
    </header>
  </div>`;
  main.replaceWith(shell);
  const flashes = document.createElement("template");
  flashes.innerHTML = `<template x-for="f in $store.app.flashes" :key="f.id">
      <div class="flash" :class="f.kind" x-text="f.text"></div></template>`;
  main.prepend(flashes.content);
  shell.querySelector(".main").append(main);
}

// The request table, the same on the dashboard (recent requests) and the tracker (all of them):
// <div data-request-table="board.recent"></div>, the attribute naming the page's list of requests.
const REQUEST_TABLE = (rows) => `
  <div class="table-wrap" x-show="(${rows} || []).length"><table class="requests">
    <thead><tr><th>Requested</th><th>Request</th><th>As of</th><th>Type</th><th>Stage</th><th class="num">Records</th>
      <th class="num">Accepted</th><th class="num">Rejected</th><th class="num">Excluded</th><th>Files</th><th>By</th></tr></thead>
    <tbody>
      <template x-for="r in ${rows} || []" :key="r.batch_id">
        <tr><td x-text="F.dt(r.created_at)"></td>
          <td class="mono"><a :href="'batch.html?id=' + r.batch_id" x-text="r.batch_id"></a></td>
          <td x-text="F.d(r.as_of_date)"></td>
          <td x-text="r.submission_types.map(F.title).join(', ')"></td>
          <td><span class="badge" :class="'s-' + r.stage" x-text="r.stage_label"></span></td>
          <td class="num" x-text="r.records ? F.num(r.records) : ''"></td>
          <td class="num" x-text="r.records ? F.num(r.accepted) : ''"></td>
          <td class="num" x-text="r.rejected ? F.num(r.rejected) : ''"></td>
          <td class="num" x-text="r.excluded || ''"></td>
          <td x-text="r.files.join(', ') || '—'"></td>
          <td class="muted by" :title="r.requested_by || ''" x-text="r.requested_by || ''"></td></tr>
      </template>
    </tbody>
  </table></div>`;

renderShell();
(function fillRequestTables(root) {                 // also inside <template> (x-if / x-for) contents
  root.querySelectorAll("[data-request-table]").forEach((el) => { el.innerHTML = REQUEST_TABLE(el.dataset.requestTable); });
  root.querySelectorAll("template").forEach((t) => fillRequestTables(t.content));
})(document);
