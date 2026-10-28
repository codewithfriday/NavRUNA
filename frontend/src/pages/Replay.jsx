import React, { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, fmt } from "../api";
import Chart, { axis, base } from "../components/Chart";
import MapView, { heightColor } from "../components/MapView";
import { Panel, Rwy, StatusBadge, can, useAsync, useAuth, useToast } from "../components/ui";

export default function Replay() {
  const { id } = useParams();
  const { user } = useAuth();
  const toast = useToast();
  const a = useAsync(() => api.get(`/v1/ga/approaches/${id}`), [id]);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(8);
  const [note, setNote] = useState("");
  const timer = useRef(null);
  const d = a.data;
  const tr = d?.track || [];
  useEffect(() => { if (tr.length) setI(tr.length - 1); }, [tr.length]);
  useEffect(() => {
    clearInterval(timer.current);
    if (playing) timer.current = setInterval(() => setI((x) => { if (x >= tr.length - 1) { setPlaying(false); return x; } return x + 1; }), 1000 / speed * 5 / 5);
    return () => clearInterval(timer.current);
  }, [playing, speed, tr.length]);
  const near = tr.filter((p) => p.d > -2 && p.d < 12 && Math.abs(p.lat_nm) < 4);
  const profile = useMemo(() => base({
    legend: { data: ["Height", "3° glide path"] }, tooltip: { trigger: "axis" },
    grid: { left: 56, right: 20, top: 40, bottom: 44, containLabel: true },
    xAxis: { type: "value", name: "NM to threshold", inverse: true, min: -1, max: 10, ...axis() },
    yAxis: { type: "value", name: "ft above threshold", min: 0, ...axis() },
    series: [
      { name: "Height", type: "line", showSymbol: false, data: near.map((p) => [p.d, p.h]), lineStyle: { color: "#2457a6", width: 2 } },
      { name: "3° glide path", type: "line", showSymbol: false, data: [[0, 50], [10, 50 + 10 * 318.4]], lineStyle: { type: "dashed", color: "#6a7889" } },
      { name: "now", type: "scatter", data: tr[i] && tr[i].d > -1 && tr[i].d < 10 ? [[tr[i].d, tr[i].h]] : [], symbolSize: 11, itemStyle: { color: "#e0a526", borderColor: "#172235" } },
      ...(d?.ga_dist_nm != null ? [{ name: "start of go-around", type: "scatter", data: [[d.ga_dist_nm, d.ga_height_ft]], symbol: "diamond", symbolSize: 13, itemStyle: { color: "#b4232c" } }] : []),
      { name: "gates", type: "line", data: [], markLine: { symbol: "none", silent: true, lineStyle: { color: "#c9d2dd" }, label: { formatter: "{b}" }, data: [6, 4, 2].map((g) => ({ name: `${g} NM`, xAxis: g })) } },
    ],
  }), [near.length, i, d]);
  const speeds = useMemo(() => base({
    legend: { data: ["Ground speed (kt)", "Vertical rate (ft/min)"] }, tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: tr.map((p) => new Date(p.t * 1000).toISOString().slice(11, 19)), ...axis() },
    yAxis: [{ type: "value", name: "kt", ...axis() }, { type: "value", name: "ft/min", ...axis({ splitLine: { show: false } }) }],
    series: [
      { name: "Ground speed (kt)", type: "line", showSymbol: false, data: tr.map((p) => p.gs), lineStyle: { color: "#2457a6" } },
      { name: "Vertical rate (ft/min)", type: "line", yAxisIndex: 1, showSymbol: false, lineStyle: { color: "#c98a12" },
        data: tr.map((p, k) => (k ? Math.round(((p.h - tr[k - 1].h) / Math.max(1, p.t - tr[k - 1].t)) * 60) : 0)) },
      { name: "now", type: "line", data: [], markLine: { symbol: "none", data: [{ xAxis: i }], lineStyle: { color: "#e0a526", width: 2 }, label: { show: false } } },
    ],
  }), [tr.length, i]);
  if (!d) return <div className="muted">Loading…</div>;
  const validate = async (status) => {
    try { await api.put(`/v1/ga/approaches/${id}/validation`, { status, note }); toast(`Marked ${status}`); a.reload(); } catch (e) { toast(e.message, "error"); }
  };
  const p = tr[i];
  return (
    <div>
      <div className="pagehead">
        <div>
          <div className="small"><Link to="/ga?tab=events">Go-arounds</Link> /</div>
          <h1>{d.callsign} · {d.outcome.replace("_", " ")} · approach {d.approach_no} of {d.n_approaches}</h1>
          <p>{d.time_utc?.replace("T", " ")} UTC ({d.time_local} local) · {d.operator} {d.aircraft_type}</p>
        </div>
        <span className="rwy big"><span className="bars" aria-hidden="true"><i /><i /><i /></span><b>{d.aerodrome}</b><em>{d.runway}</em></span>
      </div>
      <div className="split">
        <Panel title="Track" sub="Coloured by height above the threshold; the amber point is the replay position.">
          <MapView height={430} airspace={false} deps={[tr.length, i]} draw={(L, map, layer) => {
            const b = L.latLngBounds([]);
            for (let k = 1; k < tr.length; k++) {
              L.polyline([[tr[k - 1].lat, tr[k - 1].lon], [tr[k].lat, tr[k].lon]], { color: heightColor(tr[k].h), weight: 3 }).addTo(layer);
              b.extend([tr[k].lat, tr[k].lon]);
            }
            if (d.runway_ref) {
              const r = d.runway_ref, bb = (r.bearing * Math.PI) / 180, k = Math.cos((r.lat * Math.PI) / 180);
              L.polyline([[r.lat, r.lon], [r.lat - (10 * Math.cos(bb)) / 60, r.lon - (10 * Math.sin(bb)) / 60 / k]], { color: "#172235", weight: 1, dashArray: "4 4" }).addTo(layer);
              [6, 4, 2].forEach((g) => L.circleMarker([r.lat - (g * Math.cos(bb)) / 60, r.lon - (g * Math.sin(bb)) / 60 / k], { radius: 3, color: "#172235", weight: 1 }).bindTooltip(`${g} NM`).addTo(layer));
            }
            if (p) L.circleMarker([p.lat, p.lon], { radius: 7, color: "#172235", weight: 2, fillColor: "#e0a526", fillOpacity: 1 }).addTo(layer);
            return i === tr.length - 1 ? b : null;
          }} />
          <div className="player" style={{ marginTop: 8 }}>
            <button className="btn sm" onClick={() => { if (i >= tr.length - 1) setI(0); setPlaying(!playing); }}>{playing ? "Pause" : "Play"}</button>
            <input type="range" min="0" max={Math.max(0, tr.length - 1)} value={i} onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }} aria-label="replay position" />
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="speed">{[2, 4, 8, 16, 32].map((s) => <option key={s} value={s}>{s}×</option>)}</select>
            {p && <span className="small">{new Date(p.t * 1000).toISOString().slice(11, 19)} UTC · {fmt(p.h, 0)} ft · {fmt(p.gs, 0)} kt · {fmt(p.d, 1)} NM out</span>}
          </div>
          {d.track_error && <div className="notice">Track not available: {d.track_error}</div>}
        </Panel>
        <div>
          <Panel title="Event">
            <dl className="facts">
              {d.outcome === "go_around" && <><dt>Started</dt><dd>{fmt(d.ga_height_ft, 0)} ft above threshold, {fmt(d.ga_dist_nm, 2)} NM out ({d.ga_band} band)</dd>
                <dt>Then</dt><dd>{d.ga_outcome.replace("_", " ")}{d.ga_delay_min ? `, ${fmt(d.ga_delay_min, 0)} min later, ${fmt(d.ga_extra_nm, 0)} NM extra` : ""}</dd></>}
              <dt>Lowest height</dt><dd>{fmt(d.min_height_ft, 0)} ft</dd>
              <dt>Preceding arrival</dt><dd>{d.leader ? <Link to={`/ga/approach/${d.leader.id}`}>{d.leader.callsign}</Link> : "–"} {d.ttl_s ? `${fmt(d.ttl_s, 0)} s ahead (≈${fmt(d.dtl_nm, 1)} NM)` : ""}</dd>
              <dt>Last departure</dt><dd>{d.since_dep_s ? `${fmt(d.since_dep_s, 0)} s before` : "–"}</dd>
              <dt>Wind on runway</dt><dd>{d.head_kt !== null ? `${d.head_kt < 0 ? `tailwind ${fmt(-d.head_kt, 0)}` : `headwind ${fmt(d.head_kt, 0)}`} kt, crosswind ${fmt(d.cross_kt, 0)} kt${d.gust_kt ? `, gusts ${fmt(d.gust_kt, 0)} kt` : ""}` : "–"}</dd>
              <dt>Unstable at 1,000 ft</dt><dd>{d.unstable === null ? "no data" : d.unstable ? "yes" : "no"}</dd>
            </dl>
            {d.weather && <><div className="small muted" style={{ marginTop: 8 }}>{d.weather.kind} nearest in time</div><pre className="raw">{d.weather.raw}</pre></>}
            {d.flight_approaches.length > 1 && <p className="small" style={{ marginTop: 8 }}>This flight's approaches: {d.flight_approaches.map((o) => <Link key={o.id} to={`/ga/approach/${o.id}`} style={{ marginRight: 8 }}>{o.approach_no}: {o.runway} {o.outcome.replace("_", " ")}</Link>)}</p>}
          </Panel>
          {d.outcome === "go_around" && (
            <Panel title="Validation" actions={<StatusBadge s={d.validation || "–"} />}>
              {d.validation_note && <p className="small">{d.validated_by && `${d.validated_by}: `}{d.validation_note}</p>}
              {can(user, "analyst") && <div className="stack">
                <textarea placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
                <div className="row"><button className="btn good" onClick={() => validate("confirmed")}>Confirm go-around</button>
                  <button className="btn danger" disabled={!note.trim()} onClick={() => validate("rejected")}>Reject</button></div>
              </div>}
            </Panel>
          )}
        </div>
      </div>
      <div className="grid2">
        <Panel title="Vertical profile"><Chart option={profile} height={300} name={`profile_${id}`} /></Panel>
        <Panel title="Speed and vertical rate"><Chart option={speeds} height={300} name={`speeds_${id}`} /></Panel>
      </div>
      <Panel title="Gate values" sub="Lateral offset from the centreline, deviation from a 3° path, ground speed and vertical rate (FR-GA-009).">
        <div className="tablewrap"><table className="t"><thead><tr><th>Gate</th><th className="num">Lateral (m)</th><th className="num">Glide-path deviation (ft)</th><th className="num">Ground speed (kt)</th><th className="num">Vertical rate (ft/min)</th></tr></thead>
          <tbody>{[["6", "6 NM"], ["4", "4 NM"], ["2", "2 NM"], ["h1000", "1,000 ft"], ["h500", "500 ft"]].map(([k, l]) => { const g = d.gates?.[k]; return (
            <tr key={k}><td>{l}</td><td className="num">{fmt(g?.lat_m, 0)}</td><td className="num">{fmt(g?.dev_ft, 0)}</td><td className="num">{fmt(g?.gs, 0)}</td><td className="num">{fmt(g?.vr, 0)}</td></tr>); })}</tbody></table></div>
        <p className="small muted">Computed with parameter set v{d.param_version} and runway data v{d.ref_version}.</p>
      </Panel>
    </div>
  );
}
