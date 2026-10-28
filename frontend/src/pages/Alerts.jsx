import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, download, dt, qs } from "../api";
import { Confirm, Level, Panel, StatusBadge, Table, useAsync, useToast } from "../components/ui";

export default function Alerts() {
  const [status, setStatus] = useState("open,acknowledged");
  const [source, setSource] = useState("");
  const al = useAsync(() => api.get(`/v1/alerts${qs({ status, source })}`), [status, source]);
  const [ack, setAck] = useState(null);
  const toast = useToast();
  const nav = useNavigate();
  return (
    <div>
      <div className="pagehead"><div><h1>Alerts</h1><p>Raised by SPI alert levels, route changes, early warning, coverage checks and failed jobs. Acknowledge each one with what was done.</p></div></div>
      <div className="filters">
        <label className="field"><span>Status</span><select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="open,acknowledged">Open and acknowledged</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option><option value="closed">Closed</option><option value="">All</option></select></label>
        <label className="field"><span>Source</span><select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="">All</option>{["SPI", "TDM", "EWS", "COVERAGE", "JOB"].map((s) => <option key={s}>{s}</option>)}</select></label>
        <button className="btn ghost sm" onClick={() => download(`/v1/alerts${qs({ status, source, fmt: "csv" })}`)}>Export CSV</button>
      </div>
      <Panel>
        <Table rows={al.data || []} empty="No alerts" columns={[
          { label: "Level", render: (r) => <Level state={r.level} /> }, { key: "source", label: "Source" },
          { label: "What", render: (r) => <button className="link" onClick={() => nav(r.link || "/alerts")}>{r.title}</button> },
          { label: "First seen", render: (r) => dt(r.first_seen) }, { label: "Last seen", render: (r) => dt(r.last_seen) },
          { label: "Status", render: (r) => <StatusBadge s={r.status} /> },
          { label: "Action", render: (r) => (r.ack_comment ? <span className="small">{r.ack_by}: {r.ack_comment}</span> : "") },
          { label: "Sent to", render: (r) => (r.delivery || []).map((d) => `${d.channel}${d.ok ? "" : " (failed)"}`).join(", ") },
          { label: "", render: (r) => r.status !== "closed" && <button className="btn sm ghost" onClick={() => setAck(r)}>Acknowledge</button> }]} />
      </Panel>
      {ack && <Confirm title="Acknowledge alert" needText="" placeholder="What was done, or why no action is needed" yes="Acknowledge" onClose={() => setAck(null)}
        onYes={async (c) => { try { await api.post(`/v1/alerts/${ack.id}/acknowledge`, { comment: c }); setAck(null); al.reload(); } catch (e) { toast(e.message, "error"); } }}>
        <p>{ack.title}</p>
        <button className="btn sm ghost" onClick={async () => { await api.post(`/v1/alerts/${ack.id}/acknowledge`, { comment: "Closed", close: true }); setAck(null); al.reload(); }}>Close without comment</button>
      </Confirm>}
    </div>
  );
}
