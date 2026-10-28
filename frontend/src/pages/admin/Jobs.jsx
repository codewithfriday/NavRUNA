import React, { useEffect, useState } from "react";
import { api, daysAgo, dt } from "../../api";
import { Field, Panel, Progress, StatusBadge, Table, useAsync, useToast } from "../../components/ui";

export default function Jobs() {
  const jobs = useAsync(() => api.get("/v1/admin/jobs"), []);
  const [f, setF] = useState({ job: "daily", date_from: daysAgo(1), date_to: daysAgo(1), rebuild: false });
  const toast = useToast();
  useEffect(() => { const i = setInterval(() => jobs.reload(), 5000); return () => clearInterval(i); }, [jobs.reload]);
  const start = async () => {
    try { await api.post("/v1/admin/jobs", { job: f.job, date_from: f.date_from, date_to: f.date_to, options: f.job === "tdm" && f.rebuild ? { rebuild: true } : {} }); toast("Job queued"); jobs.reload(); }
    catch (e) { toast(e.message, "error"); }
  };
  return (
    <div>
      <div className="pagehead"><div><h1>Jobs</h1><p>The daily run processes yesterday (UTC) at the configured time; weather is polled every 30 minutes and today's go-arounds every 15. Start any job by hand to re-process a period; jobs replace their own results, so running twice is safe.</p></div></div>
      <Panel title="Start a job">
        <div className="row top">
          <Field label="Job"><select value={f.job} onChange={(e) => setF({ ...f, job: e.target.value })}>{(jobs.data?.available || []).map((j) => <option key={j.name} value={j.name}>{j.title}</option>)}</select></Field>
          <Field label="From"><input type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} /></Field>
          <Field label="To"><input type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} /></Field>
          {f.job === "tdm" && <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={f.rebuild} onChange={(e) => setF({ ...f, rebuild: e.target.checked })} />rebuild nominal paths</label>}
          <button className="btn" onClick={start}>Start</button>
        </div>
        {(jobs.data?.next || []).length > 0 && <p className="small muted" style={{ marginTop: 8 }}>Next scheduled: {jobs.data.next.map((n) => `${n.id} ${dt(n.next)}`).join(" · ")}</p>}
      </Panel>
      <Panel title="History">
        <Table rows={jobs.data?.jobs || []} maxHeight={640} columns={[
          { key: "id", label: "#" }, { key: "title", label: "Job" }, { label: "Period", render: (r) => (r.from ? `${r.from}${r.to && r.to !== r.from ? ` – ${r.to}` : ""}` : "") },
          { label: "Status", render: (r) => <StatusBadge s={r.status} /> }, { label: "Progress", render: (r) => (r.status === "running" ? <Progress value={r.progress} /> : "") },
          { key: "message", label: "Message" }, { key: "rows_written", label: "Rows", num: true },
          { label: "Warnings", render: (r) => (r.warnings.length ? <details><summary>{r.warnings.length}</summary><ul className="small">{r.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></details> : "") },
          { key: "param_version", label: "Params" }, { key: "triggered_by", label: "By" }, { label: "Started", render: (r) => dt(r.started_at) }, { label: "Ended", render: (r) => dt(r.ended_at) },
          { label: "", render: (r) => (["running", "queued"].includes(r.status) ? <button className="btn sm danger" onClick={async () => { await api.post(`/v1/admin/jobs/${r.id}/stop`); jobs.reload(); }}>Stop</button> : "") }]} />
      </Panel>
    </div>
  );
}
