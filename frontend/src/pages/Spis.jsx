import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmt } from "../api";
import { Field, Level, Modal, Panel, StatusBadge, Table, can, useAsync, useAuth, useToast } from "../components/ui";

export const EMPTY_SPI = { code: "", name: "", description: "", owner_unit: "", owner_person: "", formula: "ga_rate", formula_params: {},
  unit: "", scale: 1, breakdowns: [], periods: ["M"], direction: "up_bad", baseline_method: "rolling", baseline_window: 90,
  alert_method: "poisson", alert_params: {}, target: null, target_date: null, min_denominator: 0, classification: "public",
  nasp_goal: "", backfill_months: 24, change_note: "" };

export function SpiForm({ initial, onClose, onSaved, isEdit }) {
  const [s, setS] = useState({ ...EMPTY_SPI, ...initial });
  const formulas = useAsync(() => api.get("/v1/formulas"), []);
  const toast = useToast();
  const f = (formulas.data || []).find((x) => x.name === s.formula);
  const up = (k, conv = (v) => v) => (e) => setS({ ...s, [k]: conv(e.target.value) });
  const save = async () => {
    try {
      const body = { ...s, target: s.target === "" || s.target === null ? null : Number(s.target), scale: Number(s.scale),
        baseline_window: Number(s.baseline_window), min_denominator: Number(s.min_denominator), backfill_months: Number(s.backfill_months) };
      const r = isEdit ? await api.put(`/v1/spis/${s.code}/draft`, body) : await api.post("/v1/spis", body);
      toast("Draft saved; a reviewer must approve it before it takes effect");
      onSaved(r);
    } catch (e) { toast(e.message, "error"); }
  };
  return (
    <Modal title={isEdit ? `Edit draft ${s.code}` : initial?.code ? `New version of ${initial.code}` : "New SPI"} onClose={onClose} wide>
      <div className="formgrid">
        <Field label="Code"><input value={s.code} disabled={!!initial?.code} onChange={up("code", (v) => v.toUpperCase())} placeholder="e.g. GA-06" /></Field>
        <Field label="Name"><input value={s.name} onChange={up("name")} /></Field>
        <Field label="Owner unit"><input value={s.owner_unit} onChange={up("owner_unit")} /></Field>
        <Field label="Owner (person)"><input value={s.owner_person} onChange={up("owner_person")} /></Field>
        <Field label="Calculation (registered formula)">
          <select value={s.formula} onChange={(e) => setS({ ...s, formula: e.target.value, breakdowns: [] })}>
            {(formulas.data || []).map((x) => <option key={x.name} value={x.name}>{x.module} · {x.title}</option>)}
          </select>
        </Field>
        <Field label="Unit"><input value={s.unit} onChange={up("unit")} /></Field>
        <Field label="Scale" hint="1,000 for a rate per 1,000; 100 for a percentage"><input type="number" value={s.scale} onChange={up("scale")} /></Field>
        <Field label="Higher values are"><select value={s.direction} onChange={up("direction")}><option value="up_bad">worse</option><option value="down_bad">better</option></select></Field>
        <Field label="Alert method"><select value={s.alert_method} onChange={up("alert_method")}>
          <option value="poisson">Poisson test against baseline (counts, rates)</option><option value="doc9859">ICAO Doc 9859 mean + 1/2/3 SD</option>
          <option value="ewma">EWMA control chart</option><option value="fixed">Fixed alert levels</option><option value="none">No alerts</option></select></Field>
        {s.alert_method === "fixed" && <Field label="Alert levels (comma-separated)"><input value={(s.alert_params.levels || []).join(",")}
          onChange={(e) => setS({ ...s, alert_params: { levels: e.target.value.split(",").map(Number).filter((x) => !Number.isNaN(x)) } })} /></Field>}
        <Field label="Baseline window (days)"><input type="number" value={s.baseline_window} onChange={up("baseline_window")} /></Field>
        <Field label="Minimum denominator"><input type="number" value={s.min_denominator} onChange={up("min_denominator")} /></Field>
        <Field label="Target (SPT)"><input type="number" value={s.target ?? ""} onChange={up("target")} /></Field>
        <Field label="Target date"><input type="date" value={s.target_date || ""} onChange={up("target_date")} /></Field>
        <Field label="Classification"><select value={s.classification} onChange={up("classification")}><option>public</option><option>anonymised</option><option>restricted</option></select></Field>
        <Field label="National safety plan goal"><input value={s.nasp_goal} onChange={up("nasp_goal")} /></Field>
        <Field label="Recompute history (months)"><input type="number" value={s.backfill_months} onChange={up("backfill_months")} /></Field>
      </div>
      <div className="stack" style={{ marginTop: 12 }}>
        <Field label="Periods"><div className="row">{["D", "W", "M"].map((p) => <label key={p} className="row" style={{ gap: 4 }}><input type="checkbox" checked={s.periods.includes(p)}
          onChange={(e) => setS({ ...s, periods: e.target.checked ? [...s.periods, p] : s.periods.filter((x) => x !== p) })} />{{ D: "daily", W: "weekly", M: "monthly" }[p]}</label>)}</div></Field>
        <Field label="Breakdowns" hint={f ? `Available: ${f.dims.join(", ")}. Combine with + (for example aerodrome+runway), one per line.` : ""}>
          <textarea value={s.breakdowns.join("\n")} onChange={(e) => setS({ ...s, breakdowns: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })} /></Field>
        <Field label="Description"><textarea value={s.description} onChange={up("description")} /></Field>
        <Field label="Reason for the change"><input value={s.change_note} onChange={up("change_note")} /></Field>
      </div>
      <div className="row end" style={{ marginTop: 12 }}><button className="btn ghost" onClick={onClose}>Cancel</button><button className="btn" disabled={!s.code || !s.name} onClick={save}>Save draft</button></div>
    </Modal>
  );
}

export default function Spis() {
  const { user } = useAuth();
  const [status, setStatus] = useState("approved");
  const spis = useAsync(() => api.get(`/v1/spis?status=${status}`), [status]);
  const [form, setForm] = useState(null);
  const nav = useNavigate();
  return (
    <div>
      <div className="pagehead">
        <div><h1>SPI register</h1><p>Every indicator is defined once as data: a registered calculation, breakdowns, baseline, alert method and target. Changes take effect after a reviewer approves them.</p></div>
        {can(user, "analyst") && <button className="btn" onClick={() => setForm({})}>New SPI</button>}
      </div>
      <Panel actions={<div className="row">{["approved", "draft", "retired", "all"].map((x) => <button key={x} className={`btn sm ${status === x ? "" : "ghost"}`} onClick={() => setStatus(x)}>{x}</button>)}</div>}>
        <Table rows={spis.data || []} onRow={(r) => nav(`/spis/${r.code}`)} columns={[
          { key: "code", label: "Code" }, { key: "name", label: "Name" }, { key: "owner_unit", label: "Owner" }, { key: "formula_title", label: "Calculation" },
          { label: "Periods", render: (r) => r.periods.join(" ") }, { key: "alert_method", label: "Alert method" },
          { label: "Latest", num: true, render: (r) => (r.latest ? `${fmt(r.latest.value, 2)} ${r.unit}` : "–") },
          { label: "State", render: (r) => (r.status === "approved" ? <Level state={r.latest ? r.latest.alert_state : null} /> : <StatusBadge s={r.status} />) },
          { label: "Target", render: (r) => (r.target !== null ? `${r.target} (${r.target_status?.status || ""})` : "–") }, { key: "version", label: "v" }]} />
      </Panel>
      {form && <SpiForm initial={form} onClose={() => setForm(null)} onSaved={() => { setForm(null); setStatus("draft"); spis.reload(); }} />}
    </div>
  );
}
