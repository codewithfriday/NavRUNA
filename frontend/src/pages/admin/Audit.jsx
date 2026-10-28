import React, { useState } from "react";
import { api, download, qs } from "../../api";
import { Field, Panel, Table, useAsync } from "../../components/ui";

export default function Audit() {
  const [f, setF] = useState({ user_name: "", action: "", date_from: "", date_to: "" });
  const rows = useAsync(() => api.get(`/v1/admin/audit${qs(f)}`), [JSON.stringify(f)]);
  return (
    <div>
      <div className="pagehead"><div><h1>Audit log</h1><p>Every change to data, parameters, definitions, validations and reports, and every export, with before and after values. The log cannot be edited.</p></div>
        <button className="btn ghost" onClick={() => download(`/v1/admin/audit${qs({ ...f, fmt: "xlsx", limit: 5000 })}`)}>Export Excel</button></div>
      <div className="filters">
        {Object.entries({ user_name: "User", action: "Action contains" }).map(([k, l]) => <Field key={k} label={l}><input value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>)}
        <Field label="From"><input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
        <Field label="To"><input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
      </div>
      <Panel>
        <Table rows={rows.data || []} maxHeight={680} columns={[{ label: "Time (UTC)", render: (r) => r.at.replace("T", " ") }, { key: "user", label: "User" }, { key: "action", label: "Action" },
          { label: "Object", render: (r) => `${r.object} ${r.id}` }, { label: "Detail", render: (r) => <code className="small">{JSON.stringify(r.detail).slice(0, 220)}</code> }]} />
      </Panel>
    </div>
  );
}
