import React, { useMemo, useState } from "react";
import { api, dt } from "../../api";
import { Panel, Table, can, useAsync, useAuth, useToast } from "../../components/ui";

export default function Params() {
  const { user } = useAuth();
  const p = useAsync(() => api.get("/v1/admin/params"), []);
  const [vals, setVals] = useState({});
  const [reason, setReason] = useState("");
  const [group, setGroup] = useState("Go-around");
  const toast = useToast();
  const groups = useMemo(() => [...new Set((p.data?.items || []).map((i) => i.group))], [p.data]);
  const items = (p.data?.items || []).filter((i) => i.group === group);
  const changed = Object.keys(vals).length;
  const save = async () => {
    try { const r = await api.post("/v1/admin/params", { values: vals, reason }); toast(`Parameter set v${r.version} is now active`); setVals({}); setReason(""); p.reload(); }
    catch (e) { toast(e.message, "error"); }
  };
  return (
    <div>
      <div className="pagehead"><div><h1>Parameters</h1><p>Every threshold in NavRUNA, with its default, unit and allowed range. A change creates a new version; results record the version they were computed with. Re-run jobs to apply a change to past data.</p></div>
        <span className="badge info">Active version {p.data?.version}</span></div>
      <div className="row" style={{ marginBottom: 12 }}>{groups.map((g) => <button key={g} className={`btn sm ${g === group ? "" : "ghost"}`} onClick={() => setGroup(g)}>{g}</button>)}</div>
      <Panel>
        {items.map((i) => {
          const v = vals[i.key] ?? i.value;
          return (
            <div key={i.key} className={`param ${vals[i.key] !== undefined ? "changed" : ""}`}>
              <div><div>{i.description}</div><div className="k">{i.key}</div></div>
              <div><input type="number" step="any" value={v} disabled={!can(user, "analyst")} onChange={(e) => setVals({ ...vals, [i.key]: e.target.value === "" ? "" : Number(e.target.value) })} style={{ width: "100%" }} />
                <div className="help">{i.unit} · {i.min}–{i.max}</div></div>
              <div className="help">default {String(i.default)}</div>
            </div>
          );
        })}
      </Panel>
      {can(user, "analyst") && changed > 0 && <Panel title={`${changed} change(s)`}>
        <div className="row"><input style={{ flex: 1 }} placeholder="Reason for the change (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="btn ghost" onClick={() => setVals({})}>Discard</button><button className="btn" disabled={!reason.trim()} onClick={save}>Save as new version</button></div>
      </Panel>}
      <Panel title="Versions">
        <Table rows={p.data?.versions || []} columns={[{ key: "version", label: "Version" }, { key: "author", label: "Author" }, { key: "reason", label: "Reason" },
          { label: "Created", render: (r) => dt(r.created_at) },
          { label: "", render: (r) => (r.active ? <span className="badge good">active</span> : can(user, "analyst") && <button className="btn sm ghost" onClick={async () => { await api.post(`/v1/admin/params/${r.version}/activate`); p.reload(); }}>Make active</button>) }]} />
      </Panel>
    </div>
  );
}
