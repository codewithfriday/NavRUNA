import React, { useState } from "react";
import { api, download, dt, fmt } from "../../api";
import { resetSectors } from "../../components/MapView";
import { Field, Modal, Panel, Rwy, Table, Tabs, can, useAsync, useAuth, useToast } from "../../components/ui";

function Runways({ user }) {
  const [icao, setIcao] = useState("");
  const [issues, setIssues] = useState(false);
  const rw = useAsync(() => api.get(`/v1/ref/runways?${icao ? `icao=${icao}&` : ""}${issues ? "issues_only=true" : ""}`), [icao, issues]);
  const [edit, setEdit] = useState(null);
  const [rep, setRep] = useState(null);
  const toast = useToast();
  const upload = async (e) => {
    const fd = new FormData(); fd.append("file", e.target.files[0]);
    try { setRep(await api.post("/v1/ref/runways/import", fd)); rw.reload(); } catch (ex) { toast(ex.message, "error"); }
  };
  return (
    <Panel title="Runway thresholds" sub="Positions, true bearings and elevations of every runway end. Missing bearings are computed from the opposite threshold; missing elevations take the aerodrome elevation."
      actions={<div className="row">
        <input placeholder="ICAO" value={icao} onChange={(e) => setIcao(e.target.value.toUpperCase())} style={{ width: 80 }} />
        <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={issues} onChange={(e) => setIssues(e.target.checked)} />only with issues</label>
        <button className="btn ghost sm" onClick={() => download("/v1/ref/runways/export")}>Export CSV</button>
        {can(user, "analyst") && <label className="btn sm">Import CSV<input type="file" accept=".csv" hidden onChange={upload} /></label>}</div>}>
      {rep && <div className="notice info">Imported {rep.runway_ends} runway ends at {rep.aerodromes} aerodromes (version {rep.version}). Bearings computed: {rep.derived_bearing.length}. Elevations from the aerodrome: {rep.derived_elevation.length}. Incomplete: {rep.incomplete.join(", ") || "none"}. Length mismatches: {rep.length_mismatch.length}.</div>}
      <Table rows={rw.data || []} maxHeight={560} onRow={can(user, "analyst") ? setEdit : undefined} columns={[
        { label: "Runway", render: (r) => <Rwy icao={r.icao} rwy={r.runway} /> }, { label: "Threshold", render: (r) => `${r.thr_lat.toFixed(5)}, ${r.thr_lon.toFixed(5)}` },
        { label: "Bearing (°T)", num: true, render: (r) => fmt(r.true_brg, 2) }, { label: "Elevation (ft)", num: true, render: (r) => fmt(r.thr_elev_ft, 0) },
        { label: "Length (m)", num: true, render: (r) => fmt(r.length_m, 0) }, { label: "Filled in", render: (r) => r.derived.join("; ") },
        { label: "Issues", render: (r) => r.issues.map((i, k) => <div key={k} className="small" style={{ color: "#b4232c" }}>{i}</div>) }, { key: "version", label: "v" }]} />
      {edit && <Modal title={`Edit ${edit.icao} ${edit.runway}`} onClose={() => setEdit(null)}>
        <div className="formgrid">{[["thr_lat", "Threshold latitude"], ["thr_lon", "Threshold longitude"], ["thr_elev_ft", "Elevation (ft)"], ["true_brg", "True bearing (°)"], ["length_m", "Length (m)"]].map(([k, l]) =>
          <Field key={k} label={l}><input type="number" step="any" value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value === "" ? null : Number(e.target.value) })} /></Field>)}
          <Field label="Notes"><input value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></Field></div>
        <div className="row end" style={{ marginTop: 10 }}><button className="btn" onClick={async () => { try { await api.put(`/v1/ref/runways/${edit.id}`, edit); setEdit(null); rw.reload(); } catch (e) { toast(e.message, "error"); } }}>Save</button></div>
      </Modal>}
    </Panel>
  );
}

function Aerodromes({ user }) {
  const ads = useAsync(() => api.get("/v1/ref/aerodromes"), []);
  const [q, setQ] = useState("");
  const toast = useToast();
  const save = async (a, patch) => { try { await api.put(`/v1/ref/aerodromes/${a.icao}`, { name: a.name, monitored: a.monitored, utc_offset: a.utc_offset, unit: a.unit, ...patch }); ads.reload(); } catch (e) { toast(e.message, "error"); } };
  const rows = (ads.data || []).filter((a) => !q || a.icao.includes(q) || (a.name || "").toUpperCase().includes(q)).sort((a, b) => (b.monitored - a.monitored) || a.icao.localeCompare(b.icao));
  return (
    <Panel title="Aerodromes" sub="Monitored aerodromes are processed for go-arounds and early warning; adding one needs only its runway data." actions={<input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} />}>
      <Table rows={rows} maxHeight={560} columns={[{ label: "Aerodrome", render: (a) => <Rwy icao={a.icao} /> }, { key: "name", label: "Name" },
        { label: "Runways", render: (a) => a.runways.join(" ") },
        { label: "UTC offset", render: (a) => (can(user, "analyst") ? <select value={a.utc_offset} onChange={(e) => save(a, { utc_offset: Number(e.target.value) })}>{[7, 8, 9].map((o) => <option key={o} value={o}>+{o} ({{ 7: "WIB", 8: "WITA", 9: "WIT" }[o]})</option>)}</select> : `+${a.utc_offset}`) },
        { label: "Approach unit", render: (a) => (can(user, "analyst") ? <input defaultValue={a.unit} onBlur={(e) => e.target.value !== a.unit && save(a, { unit: e.target.value })} style={{ width: 150 }} /> : a.unit) },
        { label: "Monitored", render: (a) => <input type="checkbox" checked={a.monitored} disabled={!can(user, "analyst")} onChange={(e) => save(a, { monitored: e.target.checked })} aria-label={`monitor ${a.icao}`} /> },
        { label: "", render: (a) => (a.issues ? <span className="badge warn">{a.issues} runway issue(s)</span> : "") }]} />
    </Panel>
  );
}

function Sectors({ user }) {
  const s = useAsync(() => api.get("/v1/ref/sectors"), []);
  const [edit, setEdit] = useState(null);
  const toast = useToast();
  return (
    <Panel title="Sectors from NavEarth" sub="UTA (ACC, FL245–FL600), TMA and CTA volumes with the unit named in NavEarth. Where a limit has several values, set the floor or ceiling to use."
      actions={can(user, "admin") && <button className="btn" onClick={async () => { try { const r = await api.post("/v1/ref/navearth/refresh"); resetSectors(); toast(`${r.sectors} sectors, ${r.points ?? 0} fixes loaded${r.errors.length ? `; ${r.errors.join("; ")}` : ""}`); s.reload(); } catch (e) { toast(e.message, "error"); } }}>Refresh from NavEarth</button>}>
      <Table rows={s.data || []} maxHeight={560} onRow={can(user, "analyst") ? setEdit : undefined} columns={[{ key: "kind", label: "Type" }, { key: "name", label: "Sector" }, { key: "unit", label: "Unit" },
        { label: "Published limits", render: (r) => `${r.lower_raw} – ${r.upper_raw}` },
        { label: "Used (ft)", render: (r) => `${fmt(r.override_lower_ft ?? r.lower_ft, 0)} – ${fmt(r.override_upper_ft ?? r.upper_ft, 0)}` },
        { label: "", render: (r) => (r.needs_override && r.override_lower_ft === null ? <span className="badge warn">several floors: set override</span> : r.override_lower_ft !== null ? <span className="badge info">override</span> : "") },
        { label: "Capacity", num: true, render: (r) => r.declared_capacity ?? "–" }, { label: "Area (NM²)", num: true, render: (r) => fmt(r.area_nm2, 0) }]} />
      {edit && <Modal title={edit.name} onClose={() => setEdit(null)}>
        <p className="small">Published: {edit.lower_raw} – {edit.upper_raw}</p>
        <div className="formgrid">
          <Field label="Floor to use (ft)"><input type="number" value={edit.override_lower_ft ?? ""} onChange={(e) => setEdit({ ...edit, override_lower_ft: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
          <Field label="Ceiling to use (ft)"><input type="number" value={edit.override_upper_ft ?? ""} onChange={(e) => setEdit({ ...edit, override_upper_ft: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
          <Field label="Declared capacity (flights/hour)"><input type="number" value={edit.declared_capacity ?? ""} onChange={(e) => setEdit({ ...edit, declared_capacity: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
          <Field label="Active"><select value={edit.active ? "1" : "0"} onChange={(e) => setEdit({ ...edit, active: e.target.value === "1" })}><option value="1">yes</option><option value="0">no</option></select></Field>
        </div>
        <div className="row end" style={{ marginTop: 10 }}><button className="btn" onClick={async () => { await api.put(`/v1/ref/sectors/${edit.id}`, edit); setEdit(null); s.reload(); }}>Save</button></div>
      </Modal>}
    </Panel>
  );
}

function Airlines({ user }) {
  const a = useAsync(() => api.get("/v1/ref/airlines"), []);
  const [n, setN] = useState({ designator: "", name: "", country: "" });
  const toast = useToast();
  return (
    <Panel title="Airline designators" sub="The operator of a flight is the first three letters of its callsign when they match this list; other airline callsigns are 'OTH', registration callsigns 'GA'.">
      {can(user, "analyst") && <div className="row top" style={{ marginBottom: 10 }}>
        <Field label="ICAO designator"><input value={n.designator} onChange={(e) => setN({ ...n, designator: e.target.value.toUpperCase() })} style={{ width: 90 }} /></Field>
        <Field label="Name"><input value={n.name} onChange={(e) => setN({ ...n, name: e.target.value })} /></Field>
        <Field label="Country"><input value={n.country} onChange={(e) => setN({ ...n, country: e.target.value })} style={{ width: 90 }} /></Field>
        <button className="btn" onClick={async () => { try { await api.post("/v1/ref/airlines", n); setN({ designator: "", name: "", country: "" }); a.reload(); } catch (e) { toast(e.message, "error"); } }}>Add</button></div>}
      <Table rows={a.data || []} maxHeight={480} columns={[{ key: "designator", label: "Designator" }, { key: "name", label: "Name" }, { key: "country", label: "Country" },
        { label: "", render: (r) => can(user, "analyst") && <button className="btn sm ghost" onClick={async () => { await api.del(`/v1/ref/airlines/${r.designator}`); a.reload(); }}>Remove</button> }]} />
    </Panel>
  );
}

export default function Refs() {
  const { user } = useAuth();
  const [tab, setTab] = useState("aerodromes");
  const v = useAsync(() => api.get("/v1/ref/versions"), [tab]);
  return (
    <div>
      <div className="pagehead"><div><h1>Reference data</h1><p>Runways, aerodromes, sectors and airlines. Every change creates a new version; results record the version they used.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "aerodromes", label: "Aerodromes" }, { id: "runways", label: "Runways" }, { id: "sectors", label: "Sectors" }, { id: "airlines", label: "Airlines" }, { id: "history", label: "History" }]} />
      {tab === "aerodromes" && <Aerodromes user={user} />}
      {tab === "runways" && <Runways user={user} />}
      {tab === "sectors" && <Sectors user={user} />}
      {tab === "airlines" && <Airlines user={user} />}
      {tab === "history" && <Panel><Table rows={v.data || []} columns={[{ key: "kind", label: "Data" }, { key: "version", label: "Version" }, { key: "note", label: "Change" }, { key: "author", label: "By" }, { label: "When", render: (r) => dt(r.at) }]} /></Panel>}
    </div>
  );
}
