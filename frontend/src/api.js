// API client: bearer token, sliding idle session (30 min idle, FR-CORE-030), error messages from the server.
const KEY = "navruna_token";
let token = sessionStorage.getItem(KEY) || "";
let onLogout = () => {};
let lastActivity = Date.now();
let lastRefresh = Date.now();

export function setToken(t) {
  token = t || "";
  if (t) sessionStorage.setItem(KEY, t);
  else sessionStorage.removeItem(KEY);
}
export const getToken = () => token;
export function onSessionEnd(fn) { onLogout = fn; }

["click", "keydown", "mousemove"].forEach((e) =>
  window.addEventListener(e, () => { lastActivity = Date.now(); }, { passive: true })
);

// refresh the token every 5 minutes while the user is active; idle users time out on the server
setInterval(async () => {
  if (!token) return;
  if (Date.now() - lastActivity < 5 * 60 * 1000 && Date.now() - lastRefresh > 4 * 60 * 1000) {
    try {
      const r = await api.post("/v1/auth/refresh");
      setToken(r.token);
      lastRefresh = Date.now();
    } catch { /* handled by 401 path */ }
  }
}, 60 * 1000);

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function request(method, path, body, opts = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(`/api${path}`, { method, headers, body: payload });
  if (res.status === 401 && path !== "/v1/auth/login") {
    setToken("");
    onLogout("Your session ended. Sign in again to continue.");
    throw new ApiError(401, "Session ended");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* */ }
    throw new ApiError(res.status, msg);
  }
  if (opts.raw) return res;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b) => request("POST", p, b),
  patch: (p, b) => request("PATCH", p, b),
  put: (p, b) => request("PUT", p, b),
  del: (p) => request("DELETE", p),
  raw: (p) => request("GET", p, undefined, { raw: true }),
};

export async function download(path, filename) {
  const res = await api.raw(path);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename="?([^";]+)"?/);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename || (m ? m[1] : "download");
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function qs(filters) {
  const p = new URLSearchParams();
  Object.entries(filters || {}).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) return;
    (Array.isArray(v) ? v : [v]).forEach((x) => p.append(k, x));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? "–" :
  Number(v).toLocaleString("en-GB", { minimumFractionDigits: Number.isInteger(Number(v)) ? 0 : d, maximumFractionDigits: d }));
export const pct = (v, d = 0) => (v === null || v === undefined ? "–" : `${(100 * v).toFixed(d)}%`);
export const dt = (s) => (s ? new Date(s + (s.endsWith("Z") ? "" : "Z")).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "–");

export const today = () => new Date().toISOString().slice(0, 10);
export const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
