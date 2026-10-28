import React, { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, daysAgo, fmt, pct, qs } from "../api";
import Chart, { COLORS, axis, base } from "../components/Chart";
import MapView from "../components/MapView";
import { Field, Panel, Rwy, Table, Tabs, can, useAsync, useAuth, useToast } from "../components/ui";

function offsetPath(medoid, offs) {
  // shift each medoid point sideways by offs[i] NM (positive = right of travel)
  return medoid.map((p, i) => {
    const q = medoid[Math.min(i + 1, medoid.length - 1)], o = medoid[Math.max(i - 1, 0)];
    const kx = 60 * Math.cos((p[1] * Math.PI) / 180);
    const dx = (q[0] - o[0]) * kx, dy = (q[1] - o[1]) * 60, n = Math.hypot(dx, dy) || 1;
    const rx = dy / n, ry = -dx / n;
    return [p[1] + (ry * offs[i]) / 60, p[0] + (rx * offs[i]) / kx];
  });
}

export default function TdmRoute() {
  const { id } = useParams();
  const [sp] = useSearchParams();
  const { user } = useAuth();
  const toast = useToast();
  const [day, setDay] = useState(sp.get("day") || "");
  const r = useAsync(() => api.get(`/v1/tdm/routes/${id}${qs({ day })}`), [id, day]);
  const [tab, setTab] = useState("map");
  const [cp, setCp] = useState(null);
  const [chg, setChg] = useState({ date: daysAgo(30), days: 30 });
  const cmp = useAsync(() => (tab === "compare" ? api.get(`/v1/tdm/compare${qs({ route_id: id, change_date: chg.date, days: chg.days })}`) : Promise.resolve(null)), [tab, chg.date, chg.days]);
  const hist = useAsync(() => (cp ? api.get(`/v1/tdm/points/${cp.id}/histogram`) : Promise.resolve(null)), [cp?.id]);
  const d = r.data;
  const hopt = useMemo(() => {
    if (!hist.data) return null;
    const bins = (vals, w) => { const m = {}; vals.forEach((v) => { const k = Math.round(v / w) * w; m[k] = (m[k] || 0) + 1; }); return Object.entries(m).map(([k, v]) => [Number(k), v]).sort((a, b) => a[0] - b[0]); };
    return [
      base({ legend: { show: false }, title: { text: "Lateral offset (NM, + = right)", textStyle: { fontSize: 13 } }, xAxis: { type: "value", ...axis() }, yAxis: { type: "value", name: "flights", ...axis() },
        series: [{ type: "bar", data: bins(hist.data.lateral_nm, 0.5), itemStyle: { color: COLORS[0] }, barWidth: "90%" }] }),
      base({ legend: { show: false }, title: { text: "Altitude difference from median profile (ft)", textStyle: { fontSize: 13 } }, xAxis: { type: "value", ...axis() }, yAxis: { type: "value", name: "flights", ...axis() },
        series: [{ type: "bar", data: bins(hist.data.alt_diff_ft, 250), itemStyle: { color: COLORS[1] }, barWidth: "90%" }] })];
  }, [hist.data]);
  if (!d) return <div className="muted">Loading…</div>;
  const color = (cid) => COLORS[(d.clusters.findIndex((c) => c.id === cid) + COLORS.length) % COLORS.length];
  return (
    <div>
      <div className="pagehead">
        <div><div className="small"><Link to="/tdm">Route deviations</Link> /</div><h1 className="row" style={{ gap: 8 }}><Rwy icao={d.adep} /> → <Rwy icao={d.ades} /></h1>
          <p>{d.cluster_set ? `Nominal paths v${d.cluster_set.version} from ${d.cluster_set.flights} flights, ${d.cluster_set.from} to ${d.cluster_set.to}.` : "No nominal paths yet: run the deviation job or rebuild."}</p></div>
        <div className="row">
          <Field label="Day"><input type="date" value={d.day} onChange={(e) => setDay(e.target.value)} /></Field>
          {can(user, "analyst") && <button className="btn ghost" onClick={async () => { await api.post(`/v1/tdm/routes/${id}/rebuild`); toast("Rebuild started; see Jobs"); }}>Rebuild paths</button>}
        </div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "map", label: "Map" }, { id: "points", label: "Critical points" }, { id: "paths", label: "Nominal paths" }, { id: "compare", label: "Before / after a change" }]} />
      {tab === "map" && <Panel title={`Flights on ${d.day}`} sub="Nominal paths with their 5–95 % corridor; flights coloured by their path, red when they deviated beyond the limit, grey dashed when off-pattern.">
        <MapView height={560} deps={[d]} draw={(L, map, layer) => {
          const b = L.latLngBounds([]);
          d.clusters.filter((c) => !c.retired).forEach((c) => {
            const left = offsetPath(c.medoid, c.corridor.p5), right = offsetPath(c.medoid, c.corridor.p95);
            L.polygon([...left, ...right.reverse()], { color: color(c.id), weight: 0, fillOpacity: 0.12 }).addTo(layer);
            L.polyline(c.medoid.map((p) => [p[1], p[0]]), { color: color(c.id), weight: 3 }).bindTooltip(`${c.name}: ${pct(c.share)} of flights`).addTo(layer);
            c.medoid.forEach((p) => b.extend([p[1], p[0]]));
          });
          d.flights.forEach((f) => {
            const col = f.off_pattern ? "#6a7889" : f.flag_lateral || f.flag_vertical ? "#b4232c" : color(f.cluster_id);
            L.polyline(f.track.map((p) => [p[1], p[0]]), { color: col, weight: 1.2, opacity: 0.8, dashArray: f.off_pattern ? "4 4" : null })
              .bindTooltip(`${f.callsign} ${f.operator} · max ${fmt(f.max_dev_nm, 1)} NM from path`).addTo(layer);
          });
          d.critical_points.filter((c) => c.active).forEach((c) => L.circleMarker([c.lat, c.lon], { radius: 4, color: "#172235", weight: 1.5, fillColor: "#fff", fillOpacity: 1 })
            .bindTooltip(c.name, { permanent: false }).on("click", () => { setCp(c); setTab("points"); }).addTo(layer));
          return b;
        }} />
        <div className="legend">{d.clusters.filter((c) => !c.retired).map((c) => <span key={c.id}><i style={{ background: color(c.id) }} />{c.name} ({pct(c.share)})</span>)}<span><i style={{ background: "#b4232c" }} />deviated</span></div>
      </Panel>}
      {tab === "points" && <div className="split">
        <Panel title="Critical points" sub="Waypoints near a nominal path and TMA boundary crossings; switch off points that do not matter.">
          <Table rows={d.critical_points} onRow={setCp} maxHeight={520} columns={[{ key: "name", label: "Point" }, { key: "kind", label: "Kind" },
            { label: "Limit", render: (c) => (c.in_tma ? "TMA (RNP 1)" : "en route (RNP 2)") },
            { label: "", render: (c) => can(user, "analyst") && <button className="btn sm ghost" onClick={async (e) => { e.stopPropagation(); await api.put(`/v1/tdm/points/${c.id}?active=${!c.active}`); r.reload(); }}>{c.active ? "Switch off" : "Switch on"}</button> }]} />
        </Panel>
        <Panel title={cp ? `At ${cp.name}, last 30 days` : "Choose a point"} sub={hist.data ? `${hist.data.n} passages · ${hist.data.flagged_lateral} beyond the lateral limit · ${hist.data.flagged_vertical} beyond the vertical limit` : ""}>
          {hopt && <><Chart option={hopt[0]} height={220} name={`cp_${cp.name}_lateral`} /><Chart option={hopt[1]} height={220} name={`cp_${cp.name}_vertical`} /></>}
        </Panel>
      </div>}
      {tab === "paths" && <Panel title="Nominal paths" sub="Name, or retire a path that should not be used for assignment.">
        <Table rows={d.clusters} columns={[{ label: "Path", render: (c) => <span style={{ color: color(c.id), fontWeight: 600 }}>{c.name}</span> }, { key: "n", label: "Training flights", num: true },
          { label: "Share", num: true, render: (c) => pct(c.share, 1) }, { label: "Median cruise", num: true, render: (c) => `FL${Math.round(Math.max(...c.profile.p50) / 100)}` },
          { label: "", render: (c) => can(user, "analyst") && <div className="row">
            <button className="btn sm ghost" onClick={async () => { const n = window.prompt("Name of the path", c.name); if (n) { await api.put(`/v1/tdm/clusters/${c.id}`, { name: n, retired: c.retired }); r.reload(); } }}>Rename</button>
            <button className="btn sm ghost" onClick={async () => { await api.put(`/v1/tdm/clusters/${c.id}`, { name: c.name, retired: !c.retired }); r.reload(); }}>{c.retired ? "Restore" : "Retire"}</button></div> }]} />
      </Panel>}
      {tab === "compare" && <Panel title="Before and after a change" sub="For an AIRAC date or a route change: path use and lateral offsets at each critical point (two-sample Kolmogorov–Smirnov test)."
        actions={<div className="row"><Field label="Change date"><input type="date" value={chg.date} onChange={(e) => setChg({ ...chg, date: e.target.value })} /></Field>
          <Field label="Days each side"><input type="number" value={chg.days} onChange={(e) => setChg({ ...chg, days: Number(e.target.value) })} style={{ width: 80 }} /></Field></div>}>
        {cmp.data && <>
          <p className="small">Before: {cmp.data.before.from} to {cmp.data.before.to}, {cmp.data.before.flights} flights. After: {cmp.data.after.from} to {cmp.data.after.to}, {cmp.data.after.flights} flights.</p>
          <div className="grid2">
            <Table rows={cmp.data.paths} columns={[{ key: "path", label: "Path" }, { label: "Before", num: true, render: (x) => pct(x.before, 1) }, { label: "After", num: true, render: (x) => pct(x.after, 1) }, { key: "change_pp", label: "Change (points)", num: true }]} />
            <Table rows={cmp.data.points} empty="Not enough passages (10 each side) at any critical point" columns={[{ key: "point", label: "Point" }, { key: "median_before", label: "Median before (NM)", num: true },
              { key: "median_after", label: "Median after (NM)", num: true }, { key: "p", label: "p", num: true }, { label: "", render: (x) => (x.significant ? <span className="badge warn">changed</span> : "") }]} />
          </div></>}
      </Panel>}
    </div>
  );
}
