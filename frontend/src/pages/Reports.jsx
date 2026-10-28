import React, { useState } from "react";
import { api, download, dt } from "../api";
import { Field, Panel, StatusBadge, Table, can, useAsync, useAuth, useToast } from "../components/ui";

export default function Reports() {
  const { user } = useAuth();
  const toast = useToast();
  const reps = useAsync(() => api.get("/v1/reports"), []);
  const last = new Date(); last.setDate(0);
  const [month, setMonth] = useState(last.toISOString().slice(0, 7));
  const status = async (r, s) => { try { await api.post(`/v1/reports/${r.id}/status`, { status: s }); reps.reload(); } catch (e) { toast(e.message, "error"); } };
  return (
    <div>
      <div className="pagehead"><div><h1>Reports</h1><p>The monthly Safety Review Board report is drafted automatically on the 3rd of each month, reviewed, then released and sent to the distribution list. Released reports are frozen.</p></div>
        {can(user, "analyst") && <div className="row top"><Field label="Month"><input type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></Field>
          <button className="btn" onClick={async () => { try { await api.post("/v1/reports/srb", { month }); toast("Report being drafted; it appears here when the job finishes"); setTimeout(reps.reload, 8000); } catch (e) { toast(e.message, "error"); } }}>Draft SRB report</button></div>}
      </div>
      <Panel>
        <Table rows={reps.data || []} empty="No reports yet" columns={[
          { key: "title", label: "Report" }, { label: "Status", render: (r) => <StatusBadge s={r.status} /> }, { key: "created_by", label: "Drafted by" },
          { label: "Drafted", render: (r) => dt(r.created_at) }, { label: "Released", render: (r) => (r.released_at ? `${dt(r.released_at)} by ${r.released_by}` : "–") },
          { label: "Summary", render: (r) => `${r.summary?.go_arounds ?? "–"} go-arounds, ${r.summary?.alerts ?? "–"} alerts` },
          { label: "", render: (r) => <div className="row">
            <button className="btn sm ghost" onClick={() => download(`/v1/reports/${r.id}/file`)}>PDF</button>
            {r.status === "draft" && can(user, "analyst") && <button className="btn sm" onClick={() => status(r, "review")}>Send for review</button>}
            {r.status === "review" && can(user, "reviewer") && <><button className="btn sm good" onClick={() => status(r, "released")}>Release</button><button className="btn sm ghost" onClick={() => status(r, "draft")}>Back to draft</button></>}
          </div> }]} />
      </Panel>
    </div>
  );
}
