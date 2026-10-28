import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, daysAgo, download, fmt, pct, qs } from "../api";
import Chart, { axis, base } from "../components/Chart";
import MapView, { LEVEL_COLORS } from "../components/MapView";
import { Field, Level, Panel, Rwy, StatusBadge, Table, Tabs, can, useAsync, useAuth, useToast } from "../components/ui";
import { t } from "../i18n";

const BAND_COL = { high: "#2457a6", mid: "#c98a12", low: "#b4232c" };

function Filters({ f, setF, aerodromes, runways, showHours = true, user }) {
  const up = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className="filters">
      <Field label={t("Aerodrome")}>
        <select value={f.aerodrome} onChange={(e) => setF({ ...f, aerodrome: e.target.value, runway: "" })}>
          <option value="">All monitored</option>{aerodromes.map((a) => <option key={a}>{a}</option>)}
        </select>
      </Field>
      <Field label={t("Runway")}>
        <select value={f.runway} onChange={up("runway")} disabled={!f.aerodrome}>
          <option value="">All</option>{runways.map((r) => <option key={r}>{r}</option>)}
        </select>
      </Field>
      {can(user, "reviewer") && <Field label={t("Operator")}><input value={f.operator} onChange={up("operator")} placeholder="e.g. GIA" style={{ width: 90 }} /></Field>}
      <Field label={t("From")}><input type="date" value={f.from} onChange={up("from")} /></Field>
      <Field label={t("To")}><input type="date" value={f.to} onChange={up("to")} /></Field>
      {showHours && <Field label="Local hours"><div className="row" style={{ gap: 4 }}>
        <input type="number" min="0" max="23" value={f.hf} onChange={up("hf")} style={{ width: 58 }} aria-label="from hour" />–
        <input type="number" min="0" max="23" value={f.ht} onChange={up("ht")} style={{ width: 58 }} aria-label="to hour" /></div></Field>}
    </div>
  );
}

function MapTab({ f }) {
  const [hours, setHours] = useState(24);
  const nav = useNavigate();
  const pts = useAsync(() => api.get(`/v1/ga/map${qs(f.useRange ? { date_from: f.from, date_to: f.to, aerodrome: f.aerodrome } : { hours, aerodrome: f.aerodrome })}`), [hours, f.aerodrome, f.from, f.to, f.useRange]);
  const cor = useAsync(() => (f.aerodrome ? api.get(`/v1/ga/corridors?aerodrome=${f.aerodrome}`) : Promise.resolve(null)), [f.aerodrome]);
  useEffect(() => { if (f.useRange) return undefined; const i = setInterval(pts.reload, 15 * 60 * 1000); return () => clearInterval(i); }, [pts.reload, f.useRange]);
  return (
    <Panel title="Where go-arounds started" sub="Initiation points, coloured by height band. Refreshes every 15 minutes." actions={
      <div className="row">{[24, 72, 168].map((h) => <button key={h} className={`btn sm ${hours === h && !f.useRange ? "" : "ghost"}`} onClick={() => setHours(h)}>Last {h === 168 ? "7 days" : `${h} h`}</button>)}</div>}>
      <MapView height={520} deps={[pts.data, cor.data]} view={[-5, 112, 5]} draw={(L, map, layer) => {
        const b = L.latLngBounds([]);
        (cor.data?.features || []).forEach((ft) => {
          L.geoJSON(ft, { style: { color: "#2457a6", weight: 1, fillOpacity: 0.06 } }).bindTooltip(`Runway ${ft.properties.runway} final corridor`).addTo(layer);
          const [la, lo] = ft.properties.threshold;
          L.marker([la, lo], { icon: L.divIcon({ className: "cplabel", html: ft.properties.runway, iconSize: [30, 12] }) }).addTo(layer);
          b.extend([la, lo]);
        });
        (pts.data || []).forEach((p) => {
          L.circleMarker([p.lat, p.lon], { radius: 6, color: "#fff", weight: 1.5, fillColor: BAND_COL[p.ga_band] || "#6a7889", fillOpacity: 0.95 })
            .bindTooltip(`<b>${p.aerodrome} ${p.runway}</b> ${p.time_utc?.slice(0, 16).replace("T", " ")} UTC<br>${p.callsign} · started at ${fmt(p.ga_height_ft, 0)} ft, ${fmt(p.ga_dist_nm, 1)} NM out${p.ts ? "<br>thunderstorm reported" : ""}`)
            .on("click", () => nav(`/ga/approach/${p.id}`)).addTo(layer);
          b.extend([p.lat, p.lon]);
        });
        return b;
      }} />
      <div className="legend"><span><i style={{ background: BAND_COL.high }} />above 1,000 ft</span><span><i style={{ background: BAND_COL.mid }} />500–1,000 ft</span>
        <span><i style={{ background: BAND_COL.low }} />below 500 ft</span><span>{(pts.data || []).length} go-arounds · click a point to replay</span></div>
    </Panel>
  );
}

function TrendTab({ f }) {
  const [per, setPer] = useState("W");
  const q = { aerodrome: f.aerodrome, runway: f.runway, operator: f.operator, per, date_from: f.from, date_to: f.to, hour_from: f.hf || undefined, hour_to: f.ht || undefined };
  const tr = useAsync(() => api.get(`/v1/ga/trend${qs(q)}`), [JSON.stringify(q)]);
  const rows = tr.data || [];
  const option = useMemo(() => base({
    tooltip: { trigger: "axis", formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `${r.start}<br>${r.go_arounds} go-arounds / ${r.approaches} approaches<br>rate ${fmt(r.rate, 2)} · baseline ${fmt(r.baseline, 2)}${r.alert ? `<br><b>alert level ${r.alert}</b> (p ${r.p})` : ""}${r.too_few ? "<br>too few movements to test" : ""}`; } },
    legend: { data: ["Go-arounds per 1,000 approaches", "90-day baseline", "Approaches"] },
    xAxis: { type: "category", data: rows.map((r) => r.start), ...axis() },
    yAxis: [{ type: "value", name: "per 1,000", ...axis() }, { type: "value", name: "approaches", ...axis({ splitLine: { show: false } }) }],
    series: [
      { name: "Approaches", type: "bar", yAxisIndex: 1, data: rows.map((r) => r.approaches), itemStyle: { color: "#dfe6ef" }, barCategoryGap: "30%" },
      { name: "Go-arounds per 1,000 approaches", type: "line", data: rows.map((r) => ({ value: r.rate, itemStyle: { color: r.alert ? LEVEL_COLORS[r.alert] : "#2457a6" }, symbolSize: r.alert ? 11 : 5 })), lineStyle: { color: "#2457a6", width: 2 }, z: 3 },
      { name: "90-day baseline", type: "line", data: rows.map((r) => r.baseline), symbol: "none", lineStyle: { type: "dashed", color: "#6a7889" } },
    ],
  }), [rows]);
  const alerts = rows.filter((r) => r.alert);
  return (
    <Panel title={`${per === "W" ? "Weekly" : "Monthly"} go-around rate`} sub="Points coloured by alert level: the period's count is improbably high for a Poisson count at the 90-day baseline rate."
      actions={<div className="row"><button className={`btn sm ${per === "W" ? "" : "ghost"}`} onClick={() => setPer("W")}>Weekly</button><button className={`btn sm ${per === "M" ? "" : "ghost"}`} onClick={() => setPer("M")}>Monthly</button></div>}>
      <Chart option={option} height={360} name="ga_trend" />
      {alerts.length > 0 && <p className="small">{alerts.length} period(s) above an alert level: {alerts.map((a) => `${a.start} (level ${a.alert})`).join(", ")}.</p>}
    </Panel>
  );
}

function EventsTab({ f, user }) {
  const [v, setV] = useState("");
  const [band, setBand] = useState("");
  const nav = useNavigate();
  const q = { date_from: f.from, date_to: f.to, aerodrome: f.aerodrome, runway: f.runway, operator: f.operator, validation: v, band, hour_from: f.hf || undefined, hour_to: f.ht || undefined };
  const ev = useAsync(() => api.get(`/v1/ga/events${qs(q)}`), [JSON.stringify(q)]);
  return (
    <Panel title="Go-around events" sub="Click a row to replay the flight and validate the event." actions={
      <div className="row">
        <select value={v} onChange={(e) => setV(e.target.value)} aria-label="validation"><option value="">Any validation</option><option value="pending">To review</option><option value="confirmed">Confirmed</option><option value="rejected">Rejected</option><option value="none">Not sampled</option></select>
        <select value={band} onChange={(e) => setBand(e.target.value)} aria-label="height band"><option value="">Any height</option><option value="high">Above 1,000 ft</option><option value="mid">500–1,000 ft</option><option value="low">Below 500 ft</option></select>
        <button className="btn ghost sm" onClick={() => download(`/v1/ga/events${qs({ ...q, fmt: "csv" })}`)}>{t("Export CSV")}</button>
        <button className="btn ghost sm" onClick={() => download(`/v1/ga/events${qs({ ...q, fmt: "xlsx" })}`)}>{t("Export Excel")}</button>
      </div>}>
      <Table rows={ev.data || []} onRow={(r) => nav(`/ga/approach/${r.id}`)} maxHeight={560} empty="No go-arounds for these filters" columns={[
        { label: "Time (UTC)", render: (r) => r.time_utc?.slice(0, 16).replace("T", " ") }, { key: "time_local", label: "Local" },
        { label: "Runway", render: (r) => <Rwy icao={r.aerodrome} rwy={r.runway} /> }, { key: "callsign", label: "Callsign" },
        { key: "operator", label: "Operator" }, { key: "aircraft_type", label: "Type" },
        { label: "Started at", num: true, render: (r) => `${fmt(r.ga_height_ft, 0)} ft · ${fmt(r.ga_dist_nm, 1)} NM` },
        { label: "Band", render: (r) => <span style={{ color: BAND_COL[r.ga_band], fontWeight: 600 }}>{r.ga_band}</span> },
        { label: "Then", render: (r) => `${r.ga_outcome.replace("_", " ")}${r.ga_delay_min ? ` after ${fmt(r.ga_delay_min, 0)} min` : ""}` },
        { label: "Weather", render: (r) => [r.ts && "TS", r.head_kt !== null && r.head_kt < -5 && `tail ${fmt(-r.head_kt, 0)} kt`, r.cross_kt > 15 && `cross ${fmt(r.cross_kt, 0)} kt`].filter(Boolean).join(", ") || "–" },
        { label: "Validation", render: (r) => (r.added_manually ? <span className="badge info">added</span> : <StatusBadge s={r.validation || "–"} />) },
      ]} />
    </Panel>
  );
}

function HeatTab({ f }) {
  const [kind, setKind] = useState("hour_runway");
  const h = useAsync(() => api.get(`/v1/ga/heat${qs({ kind, aerodrome: f.aerodrome, date_from: f.from, date_to: f.to })}`), [kind, f.aerodrome, f.from, f.to]);
  const d = h.data;
  const cell = (r, c) => d.cells.find((x) => x.row === r && x.col === c);
  const max = d ? Math.max(1, ...d.cells.map((c) => c.rate || 0)) : 1;
  return (
    <Panel title="Go-around rate heat table" sub="Go-arounds per 1,000 approaches; darker = higher. Hover a cell for counts." actions={
      <div className="row"><button className={`btn sm ${kind === "hour_runway" ? "" : "ghost"}`} onClick={() => setKind("hour_runway")}>Hour × runway</button>
        <button className={`btn sm ${kind === "operator_month" ? "" : "ghost"}`} onClick={() => setKind("operator_month")}>Operator × month</button></div>}>
      {d && d.rows.length ? (
        <div className="tablewrap"><table className="heat">
          <thead><tr><th />{d.cols.map((c) => <th key={c}>{kind === "hour_runway" ? String(c).padStart(2, "0") : c}</th>)}</tr></thead>
          <tbody>{d.rows.map((r) => <tr key={r}><th>{r}</th>{d.cols.map((c) => {
            const x = cell(r, c);
            const a = x && x.rate ? 0.12 + 0.88 * (x.rate / max) : 0;
            return <td key={c} title={x ? `${x.go_arounds} / ${x.approaches} approaches` : "no approaches"}
              style={{ background: x ? `rgba(180,35,44,${a.toFixed(2)})` : "#f3f5f8", color: a > 0.55 ? "#fff" : undefined }}>{x && x.go_arounds ? fmt(x.rate, 1) : x ? "0" : ""}</td>;
          })}</tr>)}</tbody>
        </table></div>
      ) : <p className="muted">No approaches for these filters.</p>}
      {kind === "hour_runway" && <p className="small muted">Hours are local time of each aerodrome.</p>}
    </Panel>
  );
}

function FactorsTab({ f, user }) {
  const fx = useAsync(() => api.get(`/v1/ga/factors${qs({ aerodrome: f.aerodrome, date_from: f.from, date_to: f.to })}`), [f.aerodrome, f.from, f.to]);
  const models = useAsync(() => api.get("/v1/ga/models"), []);
  const toast = useToast();
  const e = fx.data?.effects || {};
  const groups = Object.entries(e).filter(([, v]) => typeof v === "object");
  const titles = { thunderstorm: "Thunderstorm at or near the aerodrome", tailwind: "Tailwind", crosswind: "Crosswind", time_to_leader: "Time to the preceding arrival",
    season: "Season", glide_path_at_4nm: "Glide-path deviation at 4 NM" };
  return (
    <div>
      <Panel title="Go-around rate by condition" sub={`${fmt(fx.data?.approaches, 0)} approaches, ${fx.data?.go_arounds || 0} go-arounds. Bars show the rate per 1,000 approaches with its 95 % interval.`}>
        <div className="grid2">
          {groups.map(([k, v]) => {
            const cats = Object.keys(v);
            const option = base({
              grid: { left: 110, right: 30, top: 10, bottom: 30, containLabel: false }, legend: { show: false },
              tooltip: { trigger: "item", formatter: (p) => { const r = v[cats[p.dataIndex]]; return `${cats[p.dataIndex]}<br>${r.ga} / ${r.n} approaches<br>${fmt(r.per_1000, 2)} per 1,000 (${fmt(r.lo, 2)}–${fmt(r.hi, 2)})`; } },
              yAxis: { type: "category", data: cats, ...axis() }, xAxis: { type: "value", name: "per 1,000", ...axis() },
              series: [{ type: "bar", data: cats.map((c) => v[c].per_1000), itemStyle: { color: "#2457a6" }, barWidth: 14 },
                { type: "custom", renderItem: (p, api2) => { const y = api2.coord([0, p.dataIndex])[1]; const r = v[cats[p.dataIndex]]; if (r.lo === null) return null;
                  const x0 = api2.coord([r.lo, 0])[0], x1 = api2.coord([r.hi, 0])[0]; return { type: "line", shape: { x1: x0, y1: y, x2: x1, y2: y }, style: { stroke: "#172235", lineWidth: 1.5 } }; }, data: cats.map((c, i) => i), z: 5 }],
            });
            return <div key={k}><h3 style={{ marginBottom: 4 }}>{titles[k] || k}</h3><Chart option={option} height={40 + cats.length * 36} name={`ga_factor_${k}`} /></div>;
          })}
        </div>
        {e.thunderstorm_ratio && <p>With a thunderstorm reported, go-arounds were <b>{fmt(e.thunderstorm_ratio, 1)} times</b> as frequent as without.</p>}
      </Panel>
      <Panel title="Risk models" sub="After-the-event models of go-around probability at the 6, 4 and 2 NM gates (FR-GA-031). Used only after their model card is approved."
        actions={can(user, "analyst") && <button className="btn sm" onClick={async () => { await api.post("/v1/ga/models/train"); toast("Training started; see Jobs"); }}>Train models</button>}>
        <Table rows={models.data || []} empty="No model yet. A model needs at least 300 confirmed go-arounds (validate events first)." columns={[
          { label: "Scope", render: (m) => m.scope }, { key: "version", label: "Version" }, { label: "Status", render: (m) => <StatusBadge s={m.status} /> },
          { label: "PR-AUC at 6 / 4 / 2 NM", render: (m) => ["6", "4", "2"].map((g) => fmt(m.metrics?.gates?.[g]?.gradient_boosting?.pr_auc, 3)).join(" / ") },
          { label: "Precision / recall at 4 NM", render: (m) => { const x = m.metrics?.gates?.["4"]?.gradient_boosting; return x ? `${pct(x.precision)} / ${pct(x.recall)}` : "–"; } },
          { label: "Training data", render: (m) => `${m.card?.data?.from} to ${m.card?.data?.to}, ${m.card?.data?.confirmed_go_arounds} go-arounds` },
          { label: "", render: (m) => can(user, "reviewer") && m.status === "draft" && <button className="btn sm good" onClick={async () => { await api.post(`/v1/ga/models/${m.id}/status`, { status: "approved" }); models.reload(); }}>Approve card</button> },
        ]} />
      </Panel>
    </div>
  );
}

function ValidationTab({ f, user }) {
  const st = useAsync(() => api.get(`/v1/ga/validation-stats${qs({ date_from: f.from, date_to: f.to })}`), [f.from, f.to]);
  const cov = useAsync(() => api.get(`/v1/ga/coverage${qs({ aerodrome: f.aerodrome, date_from: f.from, date_to: f.to })}`), [f.aerodrome, f.from, f.to]);
  const toast = useToast();
  const [m, setM] = useState({ aerodrome: "", runway: "", time_utc: "", callsign: "", height_ft: "", note: "" });
  return (
    <div>
      <Panel title="Detection precision and recall" sub="Precision = confirmed ÷ reviewed. Recall counts go-arounds the analyst added because the detector missed them.">
        <Table rows={st.data || []} columns={[
          { label: "Aerodrome", render: (r) => <Rwy icao={r.aerodrome} /> }, { key: "month", label: "Month" }, { key: "detected", label: "Detected", num: true },
          { key: "reviewed", label: "Reviewed", num: true }, { key: "confirmed", label: "Confirmed", num: true }, { key: "rejected", label: "Rejected", num: true },
          { key: "manual", label: "Added (missed)", num: true }, { label: "Precision", num: true, render: (r) => pct(r.precision, 1) }, { label: "Recall", num: true, render: (r) => pct(r.recall, 1) }]} />
      </Panel>
      {can(user, "analyst") && (
        <Panel title="Add a missed go-around" sub="From the tower log or an occurrence report. It is stored as confirmed and counts toward recall.">
          <div className="formgrid">
            {[["aerodrome", "Aerodrome (ICAO)"], ["runway", "Runway"], ["time_utc", "Time UTC (YYYY-MM-DDTHH:MM)"], ["callsign", "Callsign"], ["height_ft", "Height at start (ft)"]].map(([k, l]) =>
              <Field key={k} label={l}><input value={m[k]} onChange={(e) => setM({ ...m, [k]: e.target.value })} /></Field>)}
            <Field label="Source and note"><input value={m.note} onChange={(e) => setM({ ...m, note: e.target.value })} /></Field>
          </div>
          <div className="row end" style={{ marginTop: 8 }}><button className="btn" disabled={!m.aerodrome || !m.runway || !m.time_utc || !m.note}
            onClick={async () => { try { await api.post("/v1/ga/manual", { ...m, height_ft: m.height_ft ? Number(m.height_ft) : null }); toast("Added"); st.reload(); } catch (e) { toast(e.message, "error"); } }}>Add go-around</button></div>
        </Panel>
      )}
      <Panel title="Low-level ADS-B coverage" sub="Runway-days where fewer than 80 % of approaches reach below 1,000 ft are incomplete and left out of rates and alerts (FR-GA-020).">
        <Table rows={(cov.data || []).filter((c) => c.runway !== "*")} maxHeight={380} columns={[
          { key: "day", label: "Day" }, { label: "Runway", render: (r) => <Rwy icao={r.aerodrome} rwy={r.runway} /> }, { key: "approaches", label: "Approaches", num: true },
          { label: "Reached 1,000 ft", num: true, render: (r) => pct(r.share) }, { label: "Flights with gaps", num: true, render: (r) => pct(r.gap_share) },
          { label: "", render: (r) => (r.incomplete ? <span className="badge bad">incomplete</span> : "") }]} />
      </Panel>
    </div>
  );
}

export default function GoArounds() {
  const { user } = useAuth();
  const [sp] = useSearchParams();
  const [tab, setTab] = useState(sp.get("tab") || "map");
  const [f, setF] = useState({ aerodrome: sp.get("aerodrome") || "", runway: "", operator: "", from: daysAgo(90), to: daysAgo(0), hf: "", ht: "" });
  const ads = useAsync(() => api.get("/v1/ref/aerodromes?monitored=true"), []);
  const aerodromes = (ads.data || []).map((a) => a.icao);
  const runways = ((ads.data || []).find((a) => a.icao === f.aerodrome) || {}).runways || [];
  return (
    <div>
      <div className="pagehead">
        <div><h1>{t("Go-arounds")}</h1><p>Every approach to the monitored runways, with go-arounds found as a sustained climb of 300 ft or more inside the 8 NM final corridor.</p></div>
        {f.aerodrome && <span className="rwy big"><span className="bars" aria-hidden="true"><i /><i /><i /></span><b>{f.aerodrome}</b>{f.runway && <em>{f.runway}</em>}</span>}
      </div>
      <Filters f={f} setF={setF} aerodromes={aerodromes} runways={runways} user={user} showHours={["trend", "events"].includes(tab)} />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "map", label: t("Map") }, { id: "trend", label: t("Trend") }, { id: "events", label: t("Events") },
        { id: "heat", label: t("Heat table") }, { id: "factors", label: t("Risk factors") }, { id: "validation", label: t("Validation") }]} />
      {tab === "map" && <MapTab f={f} />}
      {tab === "trend" && <TrendTab f={f} />}
      {tab === "events" && <EventsTab f={f} user={user} />}
      {tab === "heat" && <HeatTab f={f} />}
      {tab === "factors" && <FactorsTab f={f} user={user} />}
      {tab === "validation" && <ValidationTab f={f} user={user} />}
    </div>
  );
}
