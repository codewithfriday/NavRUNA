import React, { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { api, getToken, onSessionEnd, setToken } from "./api";
import { AuthProvider, can, useToast } from "./components/ui";
import { setLang, t } from "./i18n";
import Login from "./pages/Login";
import Account from "./pages/Account";
import Dashboard from "./pages/Dashboard";
import GoArounds from "./pages/GoArounds";
import Replay from "./pages/Replay";
import Spis from "./pages/Spis";
import SpiDetail from "./pages/SpiDetail";
import Alerts from "./pages/Alerts";
import Tdm from "./pages/Tdm";
import TdmRoute from "./pages/TdmRoute";
import Cxi from "./pages/Cxi";
import Ews from "./pages/Ews";
import Reports from "./pages/Reports";
import AdminUsers from "./pages/admin/Users";
import AdminRefs from "./pages/admin/Refs";
import AdminParams from "./pages/admin/Params";
import AdminJobs from "./pages/admin/Jobs";
import AdminSettings from "./pages/admin/Settings";
import AdminAudit from "./pages/admin/Audit";

function Shell({ user, cfg, logout }) {
  const [alerts, setAlerts] = useState(0);
  useEffect(() => {
    const load = () => api.get("/v1/alerts/counts").then((c) => setAlerts(c.open)).catch(() => {});
    load();
    const i = setInterval(load, 60000);
    return () => clearInterval(i);
  }, []);
  const nav = (to, label) => <NavLink key={to} to={to} className={({ isActive }) => `nav ${isActive ? "active" : ""}`}>{t(label)}</NavLink>;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><rect x="11" y="2" width="10" height="28" fill="#3d4a5e" /><g fill="#fff"><rect x="12.4" y="4" width="1.6" height="6" /><rect x="15.2" y="4" width="1.6" height="6" /><rect x="18" y="4" width="1.6" height="6" /><rect x="15.3" y="13" width="1.4" height="4" /><rect x="15.3" y="20" width="1.4" height="4" /></g></svg>
          <b>NavRUNA</b><small>{cfg?.title}</small>
        </div>
        {cfg?.demo && <span className="demo-flag" title="Synthetic demo data: not real traffic">DEMO DATA</span>}
        <div className="spacer" />
        {alerts > 0 && <NavLink to="/alerts" className="alertbell" title="Open alerts">{alerts} open</NavLink>}
        <span className="user">{user.full_name || user.username} · {user.role}</span>
        <button className="link" onClick={logout}>{t("Sign out")}</button>
      </header>
      <nav className="side" aria-label="Main">
        <div className="group"><div className="group-title">{t("Overview")}</div>{nav("/dashboard", "Dashboard")}{nav("/alerts", "Alerts")}{nav("/reports", "Reports")}</div>
        <div className="group"><div className="group-title">{t("Analysis")}</div>
          {nav("/ga", "Go-arounds")}{nav("/spis", "SPI register")}{nav("/tdm", "Route deviations")}{nav("/cxi", "Complexity")}{nav("/ews", "Early warning")}
        </div>
        {can(user, "analyst") && (
          <div className="group"><div className="group-title">{t("Administration")}</div>
            {nav("/admin/refs", "Reference data")}{nav("/admin/params", "Parameters")}{nav("/admin/jobs", "Jobs")}
            {can(user, "admin") && [nav("/admin/users", "Users"), nav("/admin/settings", "Connections"), nav("/admin/audit", "Audit log")]}
          </div>
        )}
        <div className="group">{nav("/account", "Account")}</div>
        <div className="foot">NavRUNA {cfg?.version}<br />For safety management only; not an operational ATC tool.</div>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/ga" element={<GoArounds />} />
          <Route path="/ga/approach/:id" element={<Replay />} />
          <Route path="/spis" element={<Spis />} />
          <Route path="/spis/:code" element={<SpiDetail />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/tdm" element={<Tdm />} />
          <Route path="/tdm/routes/:id" element={<TdmRoute />} />
          <Route path="/cxi" element={<Cxi />} />
          <Route path="/ews" element={<Ews />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/account" element={<Account />} />
          <Route path="/admin/users" element={<AdminUsers />} />
          <Route path="/admin/refs" element={<AdminRefs />} />
          <Route path="/admin/params" element={<AdminParams />} />
          <Route path="/admin/jobs" element={<AdminJobs />} />
          <Route path="/admin/settings" element={<AdminSettings />} />
          <Route path="/admin/audit" element={<AdminAudit />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [checking, setChecking] = useState(!!getToken());
  const toast = useToast();
  const navigate = useNavigate();
  useEffect(() => { api.get("/config").then(setCfg).catch(() => {}); }, []);
  useEffect(() => {
    onSessionEnd((msg) => { setUser(null); toast(msg, "error"); });
    if (getToken()) {
      api.get("/v1/auth/me").then((u) => { setUser(u); setLang(u.language); }).catch(() => setToken("")).finally(() => setChecking(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const logout = () => { setToken(""); setUser(null); navigate("/"); };
  if (checking) return null;
  if (!user) return <Login cfg={cfg} onLogin={(u) => { setUser(u); setLang(u.language); }} />;
  if (user.must_change_password) {
    return <AuthProvider value={{ user, setUser }}><div className="main" style={{ maxWidth: 520, margin: "40px auto" }}><Account forced /></div></AuthProvider>;
  }
  return <AuthProvider value={{ user, setUser }}><Shell user={user} cfg={cfg} logout={logout} /></AuthProvider>;
}
