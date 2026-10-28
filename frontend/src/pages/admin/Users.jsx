import React, { useState } from "react";
import { api, dt } from "../../api";
import { Field, Modal, Panel, Table, useAsync, useToast } from "../../components/ui";

const EMPTY = { username: "", full_name: "", email: "", role: "viewer", scope: [], password: "", active: true };

export default function Users() {
  const users = useAsync(() => api.get("/v1/admin/users"), []);
  const ads = useAsync(() => api.get("/v1/ref/aerodromes?monitored=true"), []);
  const [edit, setEdit] = useState(null);
  const toast = useToast();
  const save = async () => {
    try {
      const body = { ...edit, password: edit.password || null };
      if (edit.id) await api.put(`/v1/admin/users/${edit.id}`, body); else await api.post("/v1/admin/users", body);
      setEdit(null); users.reload(); toast("Saved");
    } catch (e) { toast(e.message, "error"); }
  };
  return (
    <div>
      <div className="pagehead"><div><h1>Users</h1><p>Administrator, analyst, reviewer and viewer roles. Viewers can be limited to aerodromes or units; they never see callsigns, registrations or operator names.</p></div>
        <button className="btn" onClick={() => setEdit({ ...EMPTY })}>New user</button></div>
      <Panel>
        <Table rows={users.data || []} onRow={(u) => setEdit({ ...u, password: "" })} columns={[
          { key: "username", label: "Username" }, { key: "full_name", label: "Name" }, { key: "role", label: "Role" },
          { label: "Limited to", render: (u) => (u.scope || []).join(", ") || "all" }, { label: "Active", render: (u) => (u.active ? "yes" : "no") },
          { label: "Last sign-in", render: (u) => dt(u.last_login) }, { label: "", render: (u) => (u.locked_until ? <span className="badge bad">locked</span> : "") }]} />
      </Panel>
      {edit && <Modal title={edit.id ? `Edit ${edit.username}` : "New user"} onClose={() => setEdit(null)}>
        <div className="formgrid">
          <Field label="Username"><input value={edit.username} disabled={!!edit.id} onChange={(e) => setEdit({ ...edit, username: e.target.value })} /></Field>
          <Field label="Full name"><input value={edit.full_name} onChange={(e) => setEdit({ ...edit, full_name: e.target.value })} /></Field>
          <Field label="E-mail"><input value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></Field>
          <Field label="Role"><select value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}>{["viewer", "reviewer", "analyst", "admin"].map((r) => <option key={r}>{r}</option>)}</select></Field>
          <Field label={edit.id ? "New password (leave empty to keep)" : "Initial password"} hint="The user must change it at first sign-in"><input type="password" value={edit.password} onChange={(e) => setEdit({ ...edit, password: e.target.value })} /></Field>
          <Field label="Active"><select value={edit.active ? "1" : "0"} onChange={(e) => setEdit({ ...edit, active: e.target.value === "1" })}><option value="1">yes</option><option value="0">no (also revokes tokens)</option></select></Field>
        </div>
        {edit.role === "viewer" && <Field label="Limit to aerodromes or units (comma-separated, empty = all)" hint={`Monitored: ${(ads.data || []).map((a) => a.icao).join(", ")}`}>
          <input value={(edit.scope || []).join(", ")} onChange={(e) => setEdit({ ...edit, scope: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></Field>}
        <div className="row end" style={{ marginTop: 12 }}><button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn" onClick={save}>Save</button></div>
      </Modal>}
    </div>
  );
}
