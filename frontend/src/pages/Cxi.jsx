import React, { useMemo, useState } from "react";
import { api, daysAgo, download, fmt, qs } from "../api";
import Chart, { axis, base } from "../components/Chart";
import MapView from "../components/MapView";
import { Field, Modal, Panel, Table, Tabs, can, useAsync, useAuth } from "../components/ui";

const MEASURES = { index: "Complexity index", entries: "Entries", peak: "Peak count", interaction_h: "Interaction hours", crossings: "Crossing pairs",
  proximity: "Proximity events", level_changes: "Level changes", heading_changes: "Heading changes", pru_score: "PRU score" };

function shade(v, lo, hi) {
  const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1)));
  return `rgba(36,87,166,${(0.06 + 0.9 * t).toFixed(2)})`;
}

function SectorDetail({ id, rng, onClose }) {
  const { user } = useAuth();
  const s = useAsync(() => api.get(`/v1/cxi/sectors/${id}${qs({ date_from: rng.from, date_to: rng.to })}`), [id]);
  const [fl, setFl] = useState(null);
  const d = s.data;
  const opt = useMemo(() => d && base({
    legend: { data: ["Entries", "Crossing pairs", "Level changes", "Mean index"] },
    xAxis: { type: "category", data: d.trend.map((x) => x.day), ...axis() },
    yAxis: [{ type: "value", ...axis() }, { type: "value", name: "index", ...axis({ splitLine: { show: false } }) }],
    series: [{ name: "Entries", type: "bar", data: d.trend.map((x) => x.entries), itemStyle: { color: "#dfe6ef" } },
      { name: "Crossing pairs", type: "line", data: d.trend.map((x) => x.crossings) }, { name: "Level changes", type: "line", data: d.trend.map((x) => x.level_changes) },
      { name: "Mean index", type: "line", yAxisIndex: 1, data: d.trend.map((x) => x.index), lineStyle: { width: 2.5 } }],
  }), [d]);
  return (
    <Modal title={d ? `${d.sector.name} · ${d.sector.unit}` : "Sector"} wide onClose={onClose}>
      {d && <>
        <Chart option={opt} height={300} name={`cxi_${id}`} />
        <h3 style={{ margin: "10px 0 6px" }}>Busiest hours</h3>
        <Table rows={d.busiest} maxHeight={300} onRow={can(user, "reviewer") ? async (r) => setFl({ at: r.hour, rows: await api.get(`/v1/cxi/sectors/${id}/flights?at=${r.hour.slice(0, 13)}`) }) : undefined} columns={[
          { label: "Hour (UTC)", render: (r) => r.hour.replace("T", " ") }, { label: "Index", num: true, render: (r) => fmt(r.index, 2) }, { key: "entries", label: "Entries", num: true },
          { key: "peak", label: "Peak", num: true }, { key: "crossings", label: "Crossings", num: true }, { key: "proximity", label: "Proximity", num: true },
          { key: "level_changes", label: "Level changes", num: true }, { key: "heading_changes", label: "Heading changes", num: true }]} />
        <div className="row end" style={{ marginTop: 8 }}><button className="btn ghost sm" onClick={() => download(`/v1/cxi/sectors/${id}${qs({ date_from: rng.from, date_to: rng.to, fmt: "csv" })}`)}>Export hours (CSV)</button></div>
        {fl && <><h3 style={{ margin: "10px 0 6px" }}>Flights in the sector, {fl.at.replace("T", " ")}</h3>
          <Table rows={fl.rows} maxHeight={260} columns={[{ key: "callsign", label: "Callsign" }, { key: "adep", label: "From" }, { key: "ades", label: "To" },
            { key: "minutes", label: "Minutes inside", num: true }, { label: "Levels", render: (r) => `FL${r.min_fl}–${r.max_fl}` }]} /></>}
      </>}
    </Modal>
  );
}

export default function Cxi() {
  const [rng, setRng] = useState({ from: daysAgo(30), to: daysAgo(0) });
  const [measure, setMeasure] = useState("index");
  const [kind, setKind] = useState("");
  const [tab, setTab] = useState("heat");
  const [hour, setHour] = useState(`${daysAgo(1)}T08`);
  const [open, setOpen] = useState(null);
  const heat = useAsync(() => api.get(`/v1/cxi/heat${qs({ date_from: rng.from, date_to: rng.to, measure, kind })}`), [rng.from, rng.to, measure, kind]);
  const hr = useAsync(() => (tab === "map" ? api.get(`/v1/cxi/hour?at=${hour}`) : Promise.resolve([])), [tab, hour]);
  const geo = useAsync(() => api.get("/v1/ref/sectors.geojson"), []);
  const h = heat.data;
  const vals = (h?.cells || []).map((c) => c.value);
  const lo = vals.length ? Math.min(...vals) : 0, hi = vals.length ? Math.max(...vals) : 1;
  let lastUnit = null;
  return (
    <div>
      <div className="pagehead"><div><h1>Airspace complexity</h1><p>Per sector and hour: traffic, interactions (EUROCONTROL PRU method), crossings, level and heading changes, combined into an index against each sector's own 90-day baseline. Upper sectors (ACC) and terminal sectors (APP) are shown separately.</p></div></div>
      <div className="filters">
        <Field label="From"><input type="date" value={rng.from} onChange={(e) => setRng({ ...rng, from: e.target.value })} /></Field>
        <Field label="To"><input type="date" value={rng.to} onChange={(e) => setRng({ ...rng, to: e.target.value })} /></Field>
        <Field label="Measure"><select value={measure} onChange={(e) => setMeasure(e.target.value)}>{Object.entries(MEASURES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        <Field label="Sectors"><select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All</option><option value="UTA">Upper (ACC)</option><option value="TMA">TMA (APP)</option><option value="CTA">CTA</option></select></Field>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "heat", label: "Sector × hour" }, { id: "map", label: "Map by hour" }]} />
      {tab === "heat" && <Panel title={`${MEASURES[measure]}, mean per sector-hour`} sub="Hours in UTC. Click a sector for its trend, busiest hours and the flights involved.">
        {h && h.rows.length ? <div className="tablewrap"><table className="heat">
          <thead><tr><th />{Array.from({ length: 24 }, (_, i) => <th key={i}>{String(i).padStart(2, "0")}</th>)}</tr></thead>
          <tbody>{h.rows.map((r) => { const head = r.unit !== lastUnit; lastUnit = r.unit; return (<React.Fragment key={r.id}>
            {head && <tr><th colSpan={25} style={{ textAlign: "left", paddingTop: 10 }}>{r.unit || "No unit"}</th></tr>}
            <tr><th><button className="link" onClick={() => setOpen(r.id)}>{r.name}</button></th>{Array.from({ length: 24 }, (_, i) => {
              const c = h.cells.find((x) => x.sector === r.id && x.hour === i);
              return <td key={i} title={c ? `${r.name} ${i}:00 UTC · ${fmt(c.value, 2)}` : ""} style={{ background: c ? shade(c.value, lo, hi) : "#f3f5f8", color: c && (c.value - lo) / (hi - lo || 1) > 0.6 ? "#fff" : undefined }}>{c ? fmt(c.value, measure === "index" ? 1 : 0) : ""}</td>;
            })}</tr></React.Fragment>); })}</tbody></table></div> : <p className="muted">No complexity results for this period. Run the complexity job (Jobs) first.</p>}
      </Panel>}
      {tab === "map" && <Panel title="Sectors shaded by complexity index" actions={<Field label="Hour (UTC)"><input type="datetime-local" step="3600" value={`${hour}:00`} onChange={(e) => setHour(e.target.value.slice(0, 13))} /></Field>}>
        <MapView height={540} airspace={false} deps={[hr.data, geo.data]} draw={(L, map, layer) => {
          const by = Object.fromEntries((hr.data || []).map((x) => [x.sector, x]));
          const fs = (geo.data?.features || []).filter((f) => !kind || f.properties.kind === kind);
          const g = L.geoJSON({ type: "FeatureCollection", features: fs }, {
            style: (f) => { const x = by[f.properties.id]; const v = x?.index; return { color: "#2457a6", weight: 1, fillColor: v === undefined || v === null ? "#ffffff" : v > 1.5 ? "#b4232c" : v > 0.5 ? "#d1622b" : v > -0.5 ? "#c98a12" : "#2e7d4f", fillOpacity: v === undefined || v === null ? 0.05 : 0.35 }; },
            onEachFeature: (f, l) => { const x = by[f.properties.id]; l.bindTooltip(`<b>${f.properties.name}</b><br>${x ? `index ${fmt(x.index, 2)} · ${x.entries} entries · ${x.crossings} crossings` : "no traffic"}`); l.on("click", () => setOpen(f.properties.id)); } }).addTo(layer);
          return g.getBounds();
        }} />
        <div className="legend"><span><i style={{ background: "#2e7d4f" }} />below baseline</span><span><i style={{ background: "#c98a12" }} />around baseline</span><span><i style={{ background: "#d1622b" }} />0.5–1.5 above</span><span><i style={{ background: "#b4232c" }} />more than 1.5 above</span></div>
      </Panel>}
      {open && <SectorDetail id={open} rng={rng} onClose={() => setOpen(null)} />}
    </div>
  );
}
