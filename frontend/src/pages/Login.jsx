import React, { useState } from "react";
import { api, setToken } from "../api";
import { t } from "../i18n";

export default function Login({ onLogin, cfg }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/v1/auth/login", { username: u, password: p });
      setToken(r.token);
      onLogin(r.user);
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <div className="art">
        <div>
          <h1>NavRUNA</h1>
          <h2>AirNav Runway Utilization and Network Analytics</h2>
        </div>
        <svg width="360" height="420" viewBox="0 0 360 420" aria-hidden="true">
          <polygon points="150,420 210,420 196,0 164,0" fill="#2b3446" />
          {[0, 1, 2, 3, 4, 5].map((i) => <rect key={i} x={160 + i * 7} y="380" width="4" height="30" fill="#fff" opacity=".85" />)}
          {[0, 1, 2, 3, 4, 5, 6, 7].map((i) => <rect key={i} x="178" y={40 + i * 40} width="4" height="18" fill="#fff" opacity=".5" />)}
          <path d="M20 330 Q120 300 165 200 Q190 140 260 120 Q320 105 340 60" stroke="#e0a526" strokeWidth="2.5" fill="none" strokeDasharray="6 5" />
        </svg>
        <p>Go-arounds, route deviations, sector complexity and early warning from NavTrace ADS-B data, brought together as safety performance indicators for the SMS.</p>
      </div>
      <div className="form">
        <form className="box stack" onSubmit={submit}>
          <h2>{t("Sign in")}</h2>
          {cfg?.demo && <div className="notice">Demo mode: the data are synthetic.</div>}
          <label className="field"><span>{t("Username")}</span><input autoFocus autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} /></label>
          <label className="field"><span>{t("Password")}</span><input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} /></label>
          {err && <div className="notice" role="alert">{err}</div>}
          <button className="btn" disabled={busy || !u || !p}>{busy ? "Signing in…" : t("Sign in")}</button>
          <p className="small muted">Accounts lock for 15 minutes after 5 failed attempts. Sessions end after 30 minutes without activity.</p>
        </form>
      </div>
    </div>
  );
}
