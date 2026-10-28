import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

const ToastCtx = createContext(() => {});
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const push = useCallback((text, kind = "info") => {
    const id = Math.random();
    setItems((x) => [...x, { id, text, kind }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((i) => <div key={i.id} className={`toast ${i.kind}`}>{i.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

const AuthCtx = createContext({ user: null });
export const AuthProvider = AuthCtx.Provider;
export const useAuth = () => useContext(AuthCtx);
export const RANK = { viewer: 0, reviewer: 1, analyst: 2, admin: 3 };
export const can = (user, role) => user && RANK[user.role] >= RANK[role];

export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const toast = useToast();
  const run = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const data = await fn();
      setState({ data, error: null, loading: false });
      return data;
    } catch (e) {
      setState({ data: null, error: e, loading: false });
      if (e.status !== 401) toast(e.message, "error");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { run(); }, [run]);
  return { ...state, reload: run, setData: (d) => setState((s) => ({ ...s, data: typeof d === "function" ? d(s.data) : d })) };
}

export function Modal({ title, children, onClose, wide }) {
  useEffect(() => {
    const k = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="btn ghost sm" onClick={onClose} aria-label="Close">Close</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => {
        const id = typeof t === "string" ? t : t.id;
        const label = typeof t === "string" ? t : t.label;
        return (
          <button key={id} role="tab" aria-selected={value === id} className={value === id ? "on" : ""} onClick={() => onChange(id)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function Empty({ title, children }) {
  return <div className="empty"><h3>{title}</h3><div>{children}</div></div>;
}

export function Badge({ kind = "", children }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

export function StatusBadge({ s }) {
  const k = { done: "good", released: "good", approved: "good", confirmed: "good", running: "info", queued: "info", stopping: "warn",
    review: "warn", pending: "warn", open: "bad", acknowledged: "warn", closed: "", draft: "", retired: "", stopped: "",
    error: "bad", rejected: "bad" }[s] || "";
  const label = { review: "In review", done: "Done", released: "Released", draft: "Draft", approved: "Approved", retired: "Retired",
    confirmed: "Confirmed", rejected: "Rejected", pending: "To review", running: "Running", queued: "Queued", error: "Error",
    stopped: "Stopped", stopping: "Stopping", open: "Open", acknowledged: "Acknowledged", closed: "Closed" }[s] || s || "–";
  return <Badge kind={k}>{label}</Badge>;
}

export function Panel({ title, sub, actions, children, id }) {
  return (
    <section className="panel" id={id}>
      {(title || actions) && (
        <div className="head">
          <div><h3>{title}</h3>{sub && <div className="sub">{sub}</div>}</div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Progress({ value }) {
  return <div className="progress" aria-label="progress"><div style={{ width: `${Math.round(100 * (value || 0))}%` }} /></div>;
}

export function Table({ columns, rows, onRow, empty = "No rows", maxHeight }) {
  return (
    <div className="tablewrap" style={maxHeight ? { maxHeight, overflow: "auto" } : undefined}>
      <table className="t">
        <thead><tr>{columns.map((c) => <th key={c.key || c.label} className={c.num ? "num" : ""}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={columns.length} className="muted">{empty}</td></tr>}
          {rows.map((r, i) => (
            <tr key={r.id ?? r.fid ?? i} className={onRow ? "click" : ""} onClick={onRow ? () => onRow(r) : undefined}>
              {columns.map((c) => <td key={c.key || c.label} className={c.num ? "num" : ""}>{c.render ? c.render(r) : r[c.key]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Alert state chip: colour plus words, never colour alone (NFR-009). */
export function Level({ state, compact }) {
  const s = state ?? null;
  if (s === null) return <span className="lvl none">{compact ? "–" : "no data"}</span>;
  const label = ["Within limits", "Level 1", "Level 2", "Level 3"][s] || `Level ${s}`;
  return <span className={`lvl l${s}`}>{label}</span>;
}

/** Aerodrome / runway marker in the style of a runway designator painted at the threshold. */
export function Rwy({ icao, rwy }) {
  return (
    <span className="rwy" title={rwy ? `${icao} runway ${rwy}` : icao}>
      <span className="bars" aria-hidden="true"><i /><i /><i /></span>
      <b>{icao}</b>{rwy && <em>{rwy}</em>}
    </span>
  );
}

export function Field({ label, children, hint }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

export function DateRange({ from, to, onChange }) {
  return (
    <div className="row">
      <Field label="From"><input type="date" value={from} onChange={(e) => onChange({ from: e.target.value, to })} /></Field>
      <Field label="To"><input type="date" value={to} onChange={(e) => onChange({ from, to: e.target.value })} /></Field>
    </div>
  );
}

export function Kpi({ value, label, sub, tone }) {
  return (
    <div className={`kpi ${tone || ""}`}>
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

export function Confirm({ title, children, onYes, onClose, yes = "Confirm", needText, placeholder }) {
  const [text, setText] = useState("");
  return (
    <Modal title={title} onClose={onClose}>
      <div className="stack">
        {children}
        {needText !== undefined && (
          <textarea rows={3} value={text} placeholder={placeholder || "Comment"} onChange={(e) => setText(e.target.value)} />
        )}
        <div className="row end">
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={needText && !text.trim()} onClick={() => onYes(text)}>{yes}</button>
        </div>
      </div>
    </Modal>
  );
}
