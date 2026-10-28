import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, daysAgo, download, fmt, pct, qs } from "../api";
import MapView from "../components/MapView";
import { Field, Modal, Panel, Rwy, Table, Tabs, can, useAsync, useAuth, useToast } from "../components/ui";

export default function Tdm() {
  const { user } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const [tab, setTab] = useState("routes");
  const [rng, setRng] = useState({ from: daysAgo(30), to: daysAgo(0) });
  const routes = useAsync(() => api.get("/v1/tdm/routes"), []);
  const hz = useAsync(() => (tab === "hazards" ? api.get(`/v1/tdm/hazards${qs({ date_from: rng.from, date_to: rng.to })}`) : Promise.resolve([])), [tab, rng.from, rng.to]);
  const hs = useAsync(() => (tab === "hotspots" ? api.get(`/v1/tdm/hotspots${qs({ date_from: rng.from, date_to: rng.to })}`) : Promise.resolve([])), [tab, rng.from, rng.to]);
  const [prop, setProp] = useState(null);
  const [add, setAdd] = useState({ adep: "", ades: "", unit: "" });
  return (
    <div>
      <div className="pagehead"><div><h1>Route deviations</h1><p>Nominal paths learned from the last 90 days of each monitored route; every day's flights are measured against them at critical points.</p></div>
        {can(user, "analyst") && <button className="btn ghost" onClick={async () => { try { setProp(await api.get("/v1/tdm/routes/proposals")); } catch (e) { toast(e.message, "error"); } }}>Busiest pairs in NavTrace</button>}</div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "routes", label: "Routes" }, { id: "hazards", label: "Hazard candidates (SRM)" }, { id: "hotspots", label: "Hot spots" }]} />
      {tab !== "routes" && <div className="filters"><Field label="From"><input type="date" value={rng.from} onChange={(e) => setRng({ ...rng, from: e.target.value })} /></Field>
        <Field label="To"><input type="date" value={rng.to} onChange={(e) => setRng({ ...rng, to: e.target.value })} /></Field></div>}
      {tab === "routes" && <Panel>
        <Table rows={routes.data || []} onRow={(r) => nav(`/tdm/routes/${r.id}`)} empty="No monitored routes yet" columns={[
          { label: "Route", render: (r) => <span className="row" style={{ gap: 6 }}><Rwy icao={r.adep} />→<Rwy icao={r.ades} /></span> }, { key: "unit", label: "Unit" },
          { label: "Nominal paths", render: (r) => (r.cluster_set ? `v${r.cluster_set.version}, ${r.cluster_set.from} to ${r.cluster_set.to}` : "not built yet") },
          { label: "Training flights", num: true, render: (r) => fmt(r.cluster_set?.flights, 0) },
          { label: "Unclustered", num: true, render: (r) => (r.cluster_set ? pct(r.cluster_set.unclustered / r.cluster_set.flights) : "–") },
          { label: "Active", render: (r) => (r.active ? "yes" : "no") }]} />
        {can(user, "analyst") && <div className="row top" style={{ marginTop: 12 }}>
          <Field label="From (ICAO)"><input value={add.adep} onChange={(e) => setAdd({ ...add, adep: e.target.value.toUpperCase() })} style={{ width: 90 }} /></Field>
          <Field label="To (ICAO)"><input value={add.ades} onChange={(e) => setAdd({ ...add, ades: e.target.value.toUpperCase() })} style={{ width: 90 }} /></Field>
          <Field label="Unit"><input value={add.unit} onChange={(e) => setAdd({ ...add, unit: e.target.value })} /></Field>
          <button className="btn" disabled={add.adep.length !== 4 || add.ades.length !== 4} onClick={async () => { try { await api.post("/v1/tdm/routes", add); routes.reload(); } catch (e) { toast(e.message, "error"); } }}>Monitor route</button>
        </div>}
      </Panel>}
      {tab === "hazards" && <Panel title="Hazard candidates" sub="Critical points where at least 5 % of flights deviated beyond the limit; for hazard identification by the SRM unit (FR-TDM-023)."
        actions={<button className="btn ghost sm" onClick={() => download(`/v1/tdm/hazards${qs({ date_from: rng.from, date_to: rng.to, fmt: "xlsx" })}`)}>Export Excel</button>}>
        <Table rows={hz.data || []} onRow={(r) => nav(`/tdm/routes/${r.route_id}`)} empty="No critical point above the threshold" columns={[
          { key: "route", label: "Route" }, { key: "point", label: "Critical point" }, { label: "In TMA", render: (r) => (r.in_tma ? "yes" : "") },
          { key: "flights", label: "Flights", num: true }, { key: "affected", label: "Affected", num: true }, { label: "Share", num: true, render: (r) => pct(r.share, 1) },
          { key: "pattern", label: "Pattern" }, { key: "period", label: "Period" }]} />
      </Panel>}
      {tab === "hotspots" && <Panel title="Deviation hot spots" sub="Critical points grouped on a 5 NM grid, sized by the share of flights deviating.">
        <MapView height={520} deps={[hs.data]} draw={(L, map, layer) => {
          const b = L.latLngBounds([]);
          (hs.data || []).forEach((c) => {
            L.circleMarker([c.lat, c.lon], { radius: 5 + 30 * Math.sqrt(c.share), color: "#b4232c", weight: 1, fillOpacity: 0.35 })
              .bindTooltip(`${c.points.join(", ")}<br>${pct(c.share, 1)} of ${c.n} passages deviated, mostly ${c.direction}`).addTo(layer);
            b.extend([c.lat, c.lon]);
          });
          return b;
        }} />
      </Panel>}
      {prop && <Modal title="Busiest aerodrome pairs, last 90 days" wide onClose={() => setProp(null)}>
        <Table rows={prop} maxHeight={480} columns={[{ key: "adep", label: "From" }, { key: "ades", label: "To" }, { key: "flights", label: "Flights", num: true },
          { label: "", render: (r) => (r.monitored ? "monitored" : <button className="btn sm" onClick={async () => { await api.post("/v1/tdm/routes", { adep: r.adep, ades: r.ades }); r.monitored = true; setProp([...prop]); routes.reload(); }}>Monitor</button>) }]} />
      </Modal>}
    </div>
  );
}
