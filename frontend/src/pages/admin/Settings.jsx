import React, { useState } from "react";
import { api } from "../../api";
import { Field, Panel, useAsync, useToast } from "../../components/ui";

const TYPES = { SPI: "SPI alert levels", GA: "Go-arounds", TDM: "Route changes", EWS: "Early warning", COVERAGE: "Coverage anomalies", JOB: "Failed jobs" };

export default function Settings() {
  const s = useAsync(() => api.get("/v1/admin/settings"), []);
  const toast = useToast();
  const [res, setRes] = useState({});
  const [emails, setEmails] = useState(null);
  const d = s.data;
  if (!d) return <div className="muted">Loading…</div>;
  const test = async (w) => { setRes({ ...res, [w]: "testing…" }); const r = await api.post(`/v1/admin/test/${w}`); setRes((x) => ({ ...x, [w]: r })); };
  const show = (w) => { const r = res[w]; if (!r) return null; if (typeof r === "string") return <span className="small">{r}</span>;
    return <span className={`badge ${r.ok ? "good" : "bad"}`}>{r.ok ? "OK" : r.configured === false ? "not configured" : "failed"}</span>; };
  const detail = (w) => { const r = res[w]; return r && typeof r === "object" && <pre className="raw" style={{ marginTop: 6 }}>{JSON.stringify(r, null, 1)}</pre>; };
  const routing = async (t, ch, v) => {
    const next = { ...d.alert_routing, [t]: { ...d.alert_routing[t], [ch]: v } };
    await api.put("/v1/admin/settings/alert_routing", { value: next }); s.reload();
  };
  const c = d.connections;
  return (
    <div>
      <div className="pagehead"><div><h1>Connections and alerts</h1><p>Addresses and secrets come from the server's environment (.env) and are never stored in the database; here you can check them and choose where each alert goes.</p></div></div>
      <div className="grid2">
        <Panel title="Data sources">
          <dl className="facts">
            <dt>NavTrace</dt><dd>{c.navtrace.demo ? "demo data (NAVRUNA_DEMO=1)" : c.navtrace.configured ? `configured · ${c.navtrace.table}` : "not configured"} <button className="btn sm ghost" onClick={() => test("navtrace")}>Test</button> {show("navtrace")}</dd>
            <dt>BMKG weather</dt><dd>{c.bmkg.configured ? `token ${c.bmkg.token}` : "no token"} <button className="btn sm ghost" onClick={() => test("bmkg")}>Test</button> {show("bmkg")}</dd>
            <dt>NavEarth WFS</dt><dd className="small">{c.navearth.url} <button className="btn sm ghost" onClick={() => test("navearth")}>Test</button> {show("navearth")}</dd>
            <dt>Flight data API</dt><dd>{c.flight_api.configured ? c.flight_api.url : "not configured (optional)"}</dd>
            <dt>Scheduler</dt><dd>{d.scheduler.enabled ? `daily run at ${d.scheduler.daily_utc} UTC` : "off"}</dd>
          </dl>
          {detail("navtrace")}{detail("bmkg")}{detail("navearth")}
        </Panel>
        <Panel title="Alert channels">
          <dl className="facts">
            <dt>E-mail</dt><dd>{d.channels.email.configured ? `${d.channels.email.host}, ${d.channels.email.recipients} recipient(s)` : "not configured"} <button className="btn sm ghost" onClick={() => test("email")}>Send test</button> {show("email")}</dd>
            <dt>Webhook</dt><dd className="small">{d.channels.webhook.configured ? `${d.channels.webhook.url}${d.channels.webhook.signed ? " · signed (HMAC-SHA256)" : " · unsigned"}` : "not configured"} <button className="btn sm ghost" onClick={() => test("webhook")}>Send test</button> {show("webhook")}</dd>
            <dt>Telegram</dt><dd>{d.channels.telegram.configured ? `bot ${d.channels.telegram.token} → chat ${d.channels.telegram.chat}` : "not configured"} <button className="btn sm ghost" onClick={() => test("telegram")}>Send test</button> {show("telegram")}</dd>
          </dl>
          <p className="small muted" style={{ marginTop: 8 }}>Messages leaving NavRUNA carry only aggregated information: no callsigns, registrations or operator names.</p>
        </Panel>
      </div>
      <Panel title="Where each alert goes">
        <div className="tablewrap"><table className="t"><thead><tr><th>Alert type</th><th>E-mail</th><th>Webhook</th><th>Telegram</th></tr></thead>
          <tbody>{Object.entries(TYPES).map(([t, l]) => <tr key={t}><td>{l}</td>{["email", "webhook", "telegram"].map((ch) =>
            <td key={ch}><input type="checkbox" checked={!!d.alert_routing[t]?.[ch]} onChange={(e) => routing(t, ch, e.target.checked)} aria-label={`${l} by ${ch}`} /></td>)}</tr>)}</tbody></table></div>
        <p className="small muted">Every alert also appears in the alert list. An unchanged alert is re-sent only after the quiet period (parameter alert.quiet_hours).</p>
      </Panel>
      <Panel title="Monthly SRB report">
        <div className="row top">
          <Field label="Draft on day of month"><input type="number" min="1" max="28" defaultValue={d.srb_report_day.day} onBlur={async (e) => { await api.put("/v1/admin/settings/srb_report_day", { value: { day: Number(e.target.value) } }); toast("Saved"); }} style={{ width: 90 }} /></Field>
          <Field label="Distribution list (e-mail, comma-separated)"><input style={{ minWidth: 380 }} value={emails ?? d.srb_distribution.emails.join(", ")} onChange={(e) => setEmails(e.target.value)} /></Field>
          <button className="btn" onClick={async () => { await api.put("/v1/admin/settings/srb_distribution", { value: { emails: (emails || "").split(",").map((x) => x.trim()).filter(Boolean) } }); toast("Saved"); s.reload(); }}>Save list</button>
        </div>
      </Panel>
    </div>
  );
}
