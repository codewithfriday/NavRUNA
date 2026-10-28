import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, dt, fmt } from "../api";
import Chart, { axis, base } from "../components/Chart";
import { Kpi, Level, Panel, Rwy, Table, useAsync } from "../components/ui";

function Spark({ data }) {
  if (!data || data.length < 2) return <div style={{ height: 36 }} className="small muted">not enough history</div>;
  const vals = data.map((d) => d.value).filter((v) => v !== null);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const w = 200, h = 36;
  const pts = data.map((d, i) => (d.value === null ? null : [i * (w / (data.length - 1)), h - 3 - ((d.value - lo) / (hi - lo || 1)) * (h - 6)]));
  const path = pts.filter(Boolean).map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const col = ["#2e7d4f", "#c98a12", "#d1622b", "#b4232c"];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="12-period trend">
      <path d={path} fill="none" stroke="#2457a6" strokeWidth="1.6" />
      {pts.map((p, i) => p && data[i].alert_state > 0 && <circle key={i} cx={p[0]} cy={p[1]} r="3" fill={col[data[i].alert_state]} />)}
    </svg>
  );
}

export default function Dashboard() {
  const spis = useAsync(() => api.get("/v1/spis"), []);
  const alerts = useAsync(() => api.get("/v1/alerts?status=open&limit=8"), []);
  const ga = useAsync(() => api.get("/v1/ga/summary"), []);
  const [module, setModule] = useState("");
  const nav = useNavigate();
  const list = (spis.data || []).filter((s) => !module || s.module === module);
  const above = (spis.data || []).filter((s) => s.latest && s.latest.alert_state > 0).length;
  const tot = (ga.data?.aerodromes || []).reduce((a, e) => ({ n: a.n + e.approaches, g: a.g + e.go_arounds, p: a.p + e.pending }), { n: 0, g: 0, p: 0 });
  return (
    <div>
      <div className="pagehead">
        <div><h1>Dashboard</h1><p>Latest value of every approved SPI, its alert state and the open alerts.</p></div>
      </div>
      <div className="kpis">
        <Kpi value={above} label="SPIs above an alert level" tone={above ? "bad" : ""} sub={`of ${(spis.data || []).length} approved`} />
        <Kpi value={(alerts.data || []).length} label="Open alerts" tone={(alerts.data || []).length ? "warn" : ""} />
        <Kpi value={fmt(tot.n, 0)} label="Approaches, last 30 days" sub={`${ga.data?.monitored?.length || 0} monitored aerodromes`} />
        <Kpi value={tot.n ? fmt((1000 * tot.g) / tot.n, 2) : "–"} label="Go-arounds per 1,000 approaches" sub={`${tot.g} go-arounds`} />
        <Kpi value={tot.p} label="Go-arounds to review" tone={tot.p ? "warn" : ""} />
      </div>
      <Panel title="Safety performance indicators" actions={
        <div className="row">{["", "GA", "TDM", "CXI", "EWS"].map((m) => <button key={m} className={`btn sm ${module === m ? "" : "ghost"}`} onClick={() => setModule(m)}>{m || "All"}</button>)}</div>}>
        <div className="spigrid">
          {list.map((s) => (
            <Link key={s.code} to={`/spis/${s.code}`} className={`spicard s${s.latest ? s.latest.alert_state : "x"}`}>
              <span className="code">{s.code} · {s.owner_unit}</span>
              <span className="name">{s.name}</span>
              <span className="val">{s.latest?.value !== null && s.latest?.value !== undefined ? fmt(s.latest.value, 2) : "–"}<small>{s.unit}</small></span>
              <Spark data={s.spark} />
              <span className="meta"><Level state={s.latest ? s.latest.alert_state : null} /><span>{s.latest ? `month of ${s.latest.start.slice(0, 7)}` : "no data yet"}</span></span>
            </Link>
          ))}
        </div>
      </Panel>
      <div className="grid2">
        <Panel title="Open alerts" actions={<Link to="/alerts">All alerts</Link>}>
          <Table rows={alerts.data || []} onRow={(r) => nav(r.link || "/alerts")} empty="No open alerts" columns={[
            { label: "Level", render: (r) => <Level state={r.level} /> }, { key: "title", label: "What" },
            { label: "Since", render: (r) => dt(r.first_seen) }]} />
        </Panel>
        <Panel title="Go-arounds, last 30 days" actions={<Link to="/ga">Go-around analysis</Link>}>
          <Table rows={ga.data?.aerodromes || []} onRow={(r) => nav(`/ga?aerodrome=${r.aerodrome}`)} empty="No approaches processed yet" columns={[
            { label: "Aerodrome", render: (r) => <Rwy icao={r.aerodrome} /> }, { key: "approaches", label: "Approaches", num: true },
            { key: "go_arounds", label: "Go-arounds", num: true }, { label: "Per 1,000", num: true, render: (r) => fmt(r.rate, 2) },
            { key: "low", label: "Below 500 ft", num: true }, { key: "pending", label: "To review", num: true }]} />
        </Panel>
      </div>
    </div>
  );
}
