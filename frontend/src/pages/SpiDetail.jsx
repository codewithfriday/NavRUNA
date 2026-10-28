import React, { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, download, fmt, qs } from "../api";
import Chart, { axis, base } from "../components/Chart";
import { LEVEL_COLORS } from "../components/MapView";
import { Confirm, Field, Level, Modal, Panel, StatusBadge, Table, can, useAsync, useAuth, useToast } from "../components/ui";
import { SpiForm } from "./Spis";

export default function SpiDetail() {
  const { code } = useParams();
  const [sp] = useSearchParams();
  const { user } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const spi = useAsync(() => api.get(`/v1/spis/${code}`), [code]);
  const s = spi.data;
  const [period, setPeriod] = useState(sp.get("period") || "M");
  const [dims, setDims] = useState("");
  const [sel, setSel] = useState(sp.get("breakdown") || "");
  const total = useAsync(() => api.get(`/v1/spis/${code}/observations${qs({ period, breakdown: sel })}`), [code, period, sel]);
  const bd = useAsync(() => (dims ? api.get(`/v1/spis/${code}/observations${qs({ period, dims })}`) : Promise.resolve([])), [code, period, dims]);
  const [drill, setDrill] = useState(null);
  const [form, setForm] = useState(null);
  const [decide, setDecide] = useState(null);
  const [manual, setManual] = useState(null);
  const obs = total.data || [];
  const option = useMemo(() => {
    const lv = (k) => obs.map((o) => (o.levels && o.levels[k] !== undefined ? o.levels[k] : null));
    return base({
      legend: { data: ["Value", "Baseline", "Level 1", "Level 2", "Level 3", "Target"] },
      tooltip: { trigger: "axis", formatter: (ps) => { const o = obs[ps[0].dataIndex]; return `${o.start} – ${o.end}<br>${fmt(o.value, 3)} ${s?.unit || ""}${o.numerator !== null ? `<br>${fmt(o.numerator, 0)} / ${fmt(o.denominator, 0)}` : ""}<br>${["within limits", "alert level 1", "alert level 2", "alert level 3"][o.alert_state]}${o.reliable ? "" : "<br>below minimum denominator"}${o.confirmed_share !== null ? `<br>${Math.round(100 * o.confirmed_share)} % confirmed` : ""}${o.source === "manual" ? `<br>manual: ${o.note}` : ""}`; } },
      xAxis: { type: "category", data: obs.map((o) => o.start), ...axis() }, yAxis: { type: "value", name: s?.unit, ...axis() },
      series: [
        { name: "Value", type: "line", data: obs.map((o) => ({ value: o.value, itemStyle: { color: o.alert_state ? LEVEL_COLORS[o.alert_state] : "#2457a6" }, symbolSize: o.alert_state ? 11 : 6 })), lineStyle: { color: "#2457a6", width: 2 }, z: 4 },
        { name: "Baseline", type: "line", data: obs.map((o) => o.baseline), symbol: "none", lineStyle: { type: "dashed", color: "#6a7889" } },
        { name: "Level 1", type: "line", data: lv("l1"), symbol: "none", lineStyle: { type: "dotted", color: LEVEL_COLORS[1] } },
        { name: "Level 2", type: "line", data: lv("l2"), symbol: "none", lineStyle: { type: "dotted", color: LEVEL_COLORS[2] } },
        { name: "Level 3", type: "line", data: lv("l3"), symbol: "none", lineStyle: { type: "dotted", color: LEVEL_COLORS[3] } },
        ...(s?.target !== null && s?.target !== undefined ? [{ name: "Target", type: "line", data: obs.map(() => s.target), symbol: "none", lineStyle: { color: "#2e7d4f" } }] : []),
      ],
    });
  }, [obs, s]);
  if (!s) return <div className="muted">Loading…</div>;
  const draft = (s.versions || []).find((v) => v.status === "draft");
  const openDrill = async (o) => setDrill({ o, data: await api.get(`/v1/spis/${code}/drilldown${qs({ period, start: o.start, breakdown: o.breakdown_key })}`) });
  return (
    <div>
      <div className="pagehead">
        <div><div className="small"><Link to="/spis">SPI register</Link> /</div><h1>{s.code} {s.name}</h1><p>{s.description}</p></div>
        <div className="row">
          {can(user, "analyst") && !draft && <button className="btn ghost" onClick={() => setForm({ ...s, change_note: "" })}>New version</button>}
          {can(user, "analyst") && draft && <button className="btn ghost" onClick={() => setForm({ ...draft, edit: true })}>Edit draft v{draft.version}</button>}
          {can(user, "reviewer") && draft && <button className="btn" onClick={() => setDecide(draft)}>Review draft v{draft.version}</button>}
          {can(user, "analyst") && <button className="btn ghost" onClick={() => setManual({ period, start: "", value: "", note: "" })}>Enter a value</button>}
        </div>
      </div>
      <Panel title="Trend" sub={sel ? `Breakdown ${sel.replaceAll("|", ", ")}` : "All (no breakdown)"} actions={
        <div className="row">
          {s.periods.map((p) => <button key={p} className={`btn sm ${period === p ? "" : "ghost"}`} onClick={() => setPeriod(p)}>{{ D: "Daily", W: "Weekly", M: "Monthly" }[p]}</button>)}
          {sel && <button className="btn sm ghost" onClick={() => setSel("")}>Show total</button>}
          <button className="btn sm ghost" onClick={() => download(`/v1/spis/${code}/observations${qs({ period, breakdown: sel, fmt: "csv" })}`)}>CSV</button>
        </div>}>
        <Chart option={option} height={340} name={`${code}_${period}`} onClick={(p) => obs[p.dataIndex] && openDrill(obs[p.dataIndex])} />
        <p className="small muted">Click a point to see the flights or events behind it.</p>
      </Panel>
      <Panel title="Breakdown" actions={<select value={dims} onChange={(e) => setDims(e.target.value)} aria-label="breakdown">
        <option value="">Choose a breakdown</option>{s.breakdowns.map((b) => <option key={b} value={b}>{b.replaceAll("+", " × ")}</option>)}</select>}>
        {dims && (() => {
          const rows = bd.data || [];
          const last = rows.length ? rows.reduce((m, o) => (o.start > m ? o.start : m), "") : "";
          const latest = rows.filter((o) => o.start === last).sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
          return <Table rows={latest} onRow={(o) => o.breakdown_key && setSel(o.breakdown_key)} empty="No values" columns={[
            { label: "Breakdown", render: (o) => Object.values(o.breakdown).join(" · ") }, { label: `Value (${last})`, num: true, render: (o) => fmt(o.value, 3) },
            { label: "Numerator", num: true, render: (o) => fmt(o.numerator, 0) }, { label: "Denominator", num: true, render: (o) => fmt(o.denominator, 0) },
            { label: "Baseline", num: true, render: (o) => fmt(o.baseline, 3) }, { label: "State", render: (o) => <Level state={o.alert_state} /> },
            { label: "", render: (o) => (o.reliable ? "" : <span className="badge">too few</span>) }]} />;
        })()}
      </Panel>
      <Panel title="Definition" sub={`Version ${s.version}, approved by ${s.approved_by || "–"}`}>
        <dl className="facts">
          <dt>Calculation</dt><dd>{s.formula_title} ({s.formula}), × {s.scale} {s.unit}</dd><dt>Owner</dt><dd>{s.owner_unit} {s.owner_person && `· ${s.owner_person}`}</dd>
          <dt>Periods</dt><dd>{s.periods.join(", ")}</dd><dt>Breakdowns</dt><dd>{s.breakdowns.join("; ") || "none"}</dd>
          <dt>Alert method</dt><dd>{s.alert_method} {s.alert_params?.levels ? `(${s.alert_params.levels.join(" / ")})` : ""}, baseline {s.baseline_window} days</dd>
          <dt>Minimum denominator</dt><dd>{s.min_denominator}</dd><dt>Target</dt><dd>{s.target ?? "none"} {s.target_date && `by ${s.target_date}`} · {s.target_status?.status}</dd>
          <dt>Classification</dt><dd>{s.classification}</dd>{s.nasp_goal && <><dt>Safety plan goal</dt><dd>{s.nasp_goal}</dd></>}
        </dl>
        <h3 style={{ margin: "14px 0 6px" }}>Versions</h3>
        <Table rows={s.versions || []} columns={[{ key: "version", label: "v" }, { label: "Status", render: (v) => <StatusBadge s={v.status} /> },
          { key: "created_by", label: "Author" }, { key: "approved_by", label: "Approved by" }, { key: "change_note", label: "Reason" }]} />
      </Panel>
      {form && <SpiForm initial={form} isEdit={!!form.edit} onClose={() => setForm(null)} onSaved={() => { setForm(null); spi.reload(); }} />}
      {decide && <Confirm title={`Draft ${decide.code} v${decide.version}`} needText="" placeholder="Comment for the record" yes="Approve" onClose={() => setDecide(null)}
        onYes={async (c) => { try { await api.post(`/v1/spis/${code}/approve`, { decision: "approve", comment: c }); toast("Approved; history is being recomputed"); setDecide(null); spi.reload(); } catch (e) { toast(e.message, "error"); } }}>
        <p>{decide.name}: {decide.formula}, alert method {decide.alert_method}. Author {decide.created_by}. {decide.change_note}</p>
        <button className="btn danger sm" onClick={async () => { await api.post(`/v1/spis/${code}/approve`, { decision: "reject", comment: "" }); setDecide(null); spi.reload(); }}>Reject draft</button>
      </Confirm>}
      {manual && <Modal title="Enter a value from outside NavRUNA" onClose={() => setManual(null)}>
        <div className="formgrid">
          <Field label="Period"><select value={manual.period} onChange={(e) => setManual({ ...manual, period: e.target.value })}>{s.periods.map((p) => <option key={p}>{p}</option>)}</select></Field>
          <Field label="Period start"><input type="date" value={manual.start} onChange={(e) => setManual({ ...manual, start: e.target.value })} /></Field>
          <Field label="Value"><input type="number" value={manual.value} onChange={(e) => setManual({ ...manual, value: e.target.value })} /></Field>
        </div>
        <Field label="Source"><input value={manual.note} onChange={(e) => setManual({ ...manual, note: e.target.value })} /></Field>
        <div className="row end" style={{ marginTop: 10 }}><button className="btn" disabled={!manual.start || manual.value === "" || !manual.note}
          onClick={async () => { try { await api.post(`/v1/spis/${code}/manual`, { ...manual, value: Number(manual.value) }); setManual(null); total.reload(); } catch (e) { toast(e.message, "error"); } }}>Save value</button></div>
      </Modal>}
      {drill && <Modal title={`Behind ${drill.o.start}`} wide onClose={() => setDrill(null)}>
        {drill.data.kind === "go_arounds" && <Table rows={drill.data.rows} onRow={(r) => nav(`/ga/approach/${r.id}`)} columns={[{ key: "time", label: "Time" }, { key: "aerodrome", label: "Aerodrome" },
          { key: "runway", label: "Runway" }, { key: "callsign", label: "Callsign" }, { key: "operator", label: "Operator" }, { key: "height_ft", label: "Height (ft)", num: true }, { key: "validation", label: "Validation" }]} />}
        {drill.data.kind === "flights" && <Table rows={drill.data.rows} onRow={(r) => nav(`/tdm/routes/${r.route_id}?day=${r.day}`)} columns={[{ key: "day", label: "Day" }, { key: "callsign", label: "Callsign" },
          { key: "operator", label: "Operator" }, { key: "max_dev_nm", label: "Max deviation (NM)", num: true }]} />}
        {drill.data.kind === "none" && <p>{drill.data.note}</p>}
      </Modal>}
    </div>
  );
}
