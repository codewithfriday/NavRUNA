import React, { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, daysAgo, download, fmt, pct, qs } from "../api";
import Chart, { axis, base } from "../components/Chart";
import { Field, Modal, Panel, Rwy, StatusBadge, Table, Tabs, can, useAsync, useAuth, useToast } from "../components/ui";

function CellDetail({ id, onClose, onLabelled }) {
  const { user } = useAuth();
  const toast = useToast();
  const c = useAsync(() => api.get(`/v1/ews/cells/${id}`), [id]);
  const [lab, setLab] = useState({ label: "real", cause: "", note: "" });
  const d = c.data;
  const opt = useMemo(() => d && base({
    legend: { data: ["This cell", "Usual (median)"] }, grid: { left: 190, right: 20, top: 30, bottom: 30, containLabel: false },
    yAxis: { type: "category", data: d.explanation.map((e) => e.label || e.feature), ...axis() }, xAxis: { type: "value", ...axis() },
    tooltip: { trigger: "axis" },
    series: [{ name: "This cell", type: "bar", data: d.explanation.map((e) => e.value), itemStyle: { color: "#b4232c" } },
      { name: "Usual (median)", type: "bar", data: d.explanation.map((e) => e.baseline), itemStyle: { color: "#9fb0c4" } }],
  }), [d]);
  return (
    <Modal title={d ? `${d.aerodrome} · ${d.day} · ${d.block_label} · ${d.operator}` : "Cell"} wide onClose={onClose}>
      {d && <div className="stack">
        <p>Score {fmt(d.score, 3)} (share of training cells less unusual). {d.movements} movements. Features furthest from their usual value for the same aerodrome, block and season:</p>
        {d.explanation.length ? <Chart option={opt} height={60 + 40 * d.explanation.length} name={`ews_${id}`} /> : <p className="muted">No explanation available.</p>}
        <Table rows={d.explanation} columns={[{ label: "Feature", render: (e) => e.label || e.feature }, { key: "value", label: "Value", num: true }, { key: "baseline", label: "Usual", num: true },
          { label: "Distance (MAD)", num: true, render: (e) => fmt(e.z, 1) }]} />
        {d.coverage && <p className="small muted">Coverage that day: {d.coverage.flights} flights seen (usual {fmt(d.coverage.expected, 0)}), {pct(d.coverage.gap_share)} with gaps, {pct(d.coverage.low_share)} of arrivals reaching 1,000 ft.</p>}
        {d.go_arounds.length > 0 && <p>Go-arounds in this cell: {d.go_arounds.map((g) => <Link key={g.id} to={`/ga/approach/${g.id}`} style={{ marginRight: 8 }}>{g.time?.slice(11)} {g.runway} {g.callsign}</Link>)}</p>}
        {can(user, "analyst") && <div className="panel" style={{ marginBottom: 0 }}>
          <h3>Label this alert</h3>
          <div className="formgrid" style={{ marginTop: 8 }}>
            <Field label="Judgement"><select value={lab.label} onChange={(e) => setLab({ ...lab, label: e.target.value })}><option value="real">Real safety concern</option><option value="known_cause">Known cause</option><option value="false_alarm">False alarm</option></select></Field>
            {lab.label === "known_cause" && <Field label="Cause"><select value={lab.cause} onChange={(e) => setLab({ ...lab, cause: e.target.value })}><option value="">Choose</option><option value="weather">Weather</option><option value="runway_closure">Runway closure</option><option value="notam">NOTAM</option><option value="data_issue">Data issue</option><option value="other">Other</option></select></Field>}
          </div>
          <textarea style={{ marginTop: 8 }} placeholder="Note" value={lab.note} onChange={(e) => setLab({ ...lab, note: e.target.value })} />
          <div className="row end" style={{ marginTop: 8 }}><button className="btn" onClick={async () => { try { await api.put(`/v1/ews/cells/${id}/label`, lab); toast("Labelled"); onLabelled(); } catch (e) { toast(e.message, "error"); } }}>Save label</button></div>
        </div>}
        {d.label && <p className="small">Labelled <b>{d.label.replace("_", " ")}</b>{d.label_cause && ` (${d.label_cause})`}{d.label_by && ` by ${d.label_by}`}: {d.label_note}</p>}
      </div>}
    </Modal>
  );
}

export default function Ews() {
  const [sp] = useSearchParams();
  const [tab, setTab] = useState("list");
  const [f, setF] = useState({ aerodrome: sp.get("aerodrome") || "", from: daysAgo(90), to: daysAgo(0), label: "" });
  const [year, setYear] = useState(new Date().getFullYear());
  const [open, setOpen] = useState(null);
  const ads = useAsync(() => api.get("/v1/ref/aerodromes?monitored=true"), []);
  const list = useAsync(() => api.get(`/v1/ews/anomalies${qs({ date_from: f.from, date_to: f.to, aerodrome: f.aerodrome, label: f.label })}`), [f.from, f.to, f.aerodrome, f.label]);
  const ap = f.aerodrome || (ads.data || [])[0]?.icao;
  const cal = useAsync(() => (tab === "calendar" && ap ? api.get(`/v1/ews/calendar?aerodrome=${ap}&year=${year}`) : Promise.resolve([])), [tab, ap, year]);
  const prec = useAsync(() => api.get(`/v1/ews/precision${qs({ date_from: f.from, date_to: f.to })}`), [f.from, f.to]);
  const calOpt = useMemo(() => base({
    tooltip: { trigger: "item", formatter: (p) => `${p.data[0]}<br>score ${fmt(p.data[1], 3)}${p.data[2] ? "<br>alert" : ""}${p.data[3] ? "<br>coverage anomaly, not scored" : ""}` },
    visualMap: { min: 0, max: 1, orient: "horizontal", left: 0, bottom: 0, inRange: { color: ["#eef2f7", "#9fb0c4", "#c98a12", "#b4232c"] }, text: ["unusual", "usual"] },
    calendar: { range: String(year), cellSize: ["auto", 16], top: 30, left: 40, right: 10, itemStyle: { borderColor: "#fff" }, yearLabel: { show: false } },
    series: [{ type: "heatmap", coordinateSystem: "calendar", data: (cal.data || []).filter((x) => x.score !== null).map((x) => [x.day, x.score, x.alert, x.coverage_anomaly]) },
      { type: "scatter", coordinateSystem: "calendar", symbol: "rect", symbolSize: 8, itemStyle: { color: "#172235" }, data: (cal.data || []).filter((x) => x.coverage_anomaly).map((x) => [x.day, 1, false, true]) }],
  }), [cal.data, year]);
  return (
    <div>
      <div className="pagehead"><div><h1>Early warning</h1><p>Aerodrome × day × 4-hour block × operator cells scored by an Isolation Forest against each aerodrome's last 180 days. Days with unusual ADS-B coverage are reported separately and not scored.</p></div></div>
      <div className="filters">
        <Field label="Aerodrome"><select value={f.aerodrome} onChange={(e) => setF({ ...f, aerodrome: e.target.value })}><option value="">All</option>{(ads.data || []).map((a) => <option key={a.icao}>{a.icao}</option>)}</select></Field>
        <Field label="From"><input type="date" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></Field>
        <Field label="To"><input type="date" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>
        <Field label="Label"><select value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })}><option value="">Any</option><option value="none">Not labelled</option><option value="real">Real</option><option value="known_cause">Known cause</option><option value="false_alarm">False alarm</option></select></Field>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "list", label: "Anomalies" }, { id: "calendar", label: "Calendar" }, { id: "precision", label: "Precision" }]} />
      {tab === "list" && <Panel actions={<button className="btn ghost sm" onClick={() => download(`/v1/ews/anomalies${qs({ date_from: f.from, date_to: f.to, aerodrome: f.aerodrome, fmt: "csv" })}`)}>Export CSV</button>}>
        <Table rows={list.data || []} onRow={(r) => setOpen(r.id)} empty="No anomalies in this period" maxHeight={600} columns={[
          { key: "day", label: "Day" }, { label: "Aerodrome", render: (r) => <Rwy icao={r.aerodrome} /> }, { key: "block_label", label: "Block" }, { key: "operator", label: "Operator" },
          { key: "movements", label: "Movements", num: true }, { label: "Score", num: true, render: (r) => fmt(r.score, 3) },
          { label: "Furthest from usual", render: (r) => r.explanation.slice(0, 3).map((e) => e.label || e.feature).join(", ") },
          { label: "Label", render: (r) => (r.label ? <span className="badge">{r.label.replace("_", " ")}</span> : <StatusBadge s="pending" />) }]} />
      </Panel>}
      {tab === "calendar" && <Panel title={`${ap || ""} daily score`} actions={<Field label="Year"><input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} style={{ width: 90 }} /></Field>}>
        <Chart option={calOpt} height={210} name={`ews_calendar_${ap}_${year}`} />
        <p className="small muted">Dark squares: coverage anomaly, day not scored.</p>
      </Panel>}
      {tab === "precision" && <Panel title="Alert labels" sub="Labels decide whether the alert threshold should move (FR-EWS-013).">
        <Table rows={prec.data || []} columns={[{ key: "aerodrome", label: "Aerodrome" }, { key: "alerts", label: "Alerts", num: true }, { key: "labelled", label: "Labelled", num: true },
          { key: "real", label: "Real", num: true }, { key: "known_cause", label: "Known cause", num: true }, { key: "false_alarm", label: "False alarm", num: true }, { label: "Precision", num: true, render: (r) => pct(r.precision, 0) }]} />
      </Panel>}
      {open && <CellDetail id={open} onClose={() => setOpen(null)} onLabelled={() => { setOpen(null); list.reload(); }} />}
    </div>
  );
}
