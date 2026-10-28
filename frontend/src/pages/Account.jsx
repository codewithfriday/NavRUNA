import React, { useState } from "react";
import { api, dt } from "../api";
import { Panel, Table, useAsync, useAuth, useToast } from "../components/ui";
import { setLang } from "../i18n";

export default function Account({ forced }) {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const [pw, setPw] = useState({ old_password: "", new_password: "", again: "" });
  const tokens = useAsync(() => (forced ? Promise.resolve([]) : api.get("/v1/auth/tokens")), []);
  const [newTok, setNewTok] = useState(null);
  const [tokName, setTokName] = useState("");
  const change = async () => {
    if (pw.new_password !== pw.again) return toast("The two new passwords differ", "error");
    try {
      await api.post("/v1/auth/password", { old_password: pw.old_password, new_password: pw.new_password });
      toast("Password changed");
      setUser({ ...user, must_change_password: false });
    } catch (e) { toast(e.message, "error"); }
  };
  const lang = async (l) => {
    const u = await api.put("/v1/auth/preferences", { language: l });
    setLang(l);
    setUser(u);
  };
  return (
    <div>
      <div className="pagehead"><div><h1>Account</h1><p>{user.username} · {user.role}{user.scope?.length ? ` · limited to ${user.scope.join(", ")}` : ""}</p></div></div>
      {forced && <div className="notice">Choose a new password before you continue.</div>}
      <Panel title="Password" sub="At least 12 characters, mixing three of: lower case, upper case, digits, symbols.">
        <div className="formgrid">
          <label className="field"><span>Current password</span><input type="password" value={pw.old_password} onChange={(e) => setPw({ ...pw, old_password: e.target.value })} /></label>
          <label className="field"><span>New password</span><input type="password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></label>
          <label className="field"><span>New password again</span><input type="password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} /></label>
        </div>
        <div className="row end" style={{ marginTop: 10 }}><button className="btn" onClick={change}>Change password</button></div>
      </Panel>
      {!forced && (
        <>
          <Panel title="Language">
            <div className="row">
              <button className={`btn ${user.language === "en" ? "" : "ghost"}`} onClick={() => lang("en")}>English</button>
              <button className={`btn ${user.language === "id" ? "" : "ghost"}`} onClick={() => lang("id")}>Bahasa Indonesia</button>
            </div>
          </Panel>
          <Panel title="Personal access tokens" sub="For scripts and other systems using the NavRUNA API. A token has your rights and an expiry date.">
            <div className="row top" style={{ marginBottom: 10 }}>
              <label className="field"><span>Name</span><input value={tokName} onChange={(e) => setTokName(e.target.value)} placeholder="e.g. job monitor" /></label>
              <button className="btn" disabled={!tokName} onClick={async () => { const r = await api.post("/v1/auth/tokens", { name: tokName, days: 90 }); setNewTok(r.token); tokens.reload(); }}>Create 90-day token</button>
            </div>
            {newTok && <div className="notice">Copy this token now; it is not shown again:<pre className="raw">{newTok}</pre></div>}
            <Table rows={tokens.data || []} columns={[
              { key: "name", label: "Name" }, { key: "prefix", label: "Starts with" },
              { label: "Expires", render: (r) => dt(r.expires_at) }, { label: "Last used", render: (r) => dt(r.last_used) },
              { label: "", render: (r) => (r.revoked ? "revoked" : <button className="btn danger sm" onClick={async () => { await api.del(`/v1/auth/tokens/${r.id}`); tokens.reload(); }}>Revoke</button>) },
            ]} empty="No tokens" />
          </Panel>
        </>
      )}
    </div>
  );
}
