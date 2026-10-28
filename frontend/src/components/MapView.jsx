import React, { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "../api";

let landCache = null;
/** Offline coastline (Natural Earth 1:50m), so maps stay readable without internet (FR-CORE-034). */
async function land() {
  if (!landCache) {
    landCache = Promise.all([import("world-atlas/land-50m.json"), import("topojson-client")])
      .then(([t, tc]) => tc.feature(t.default || t, (t.default || t).objects.land)).catch(() => null);
  }
  return landCache;
}
let tileUrl = null;
async function tiles() {
  if (tileUrl === null) {
    try { tileUrl = (await api.get("/config")).tile_url || ""; } catch { tileUrl = ""; }
  }
  return tileUrl;
}
let sectorCache = null;
export function resetSectors() { sectorCache = null; }
async function sectors() {
  if (!sectorCache) sectorCache = api.get("/v1/ref/sectors.geojson").catch(() => ({ features: [] }));
  return sectorCache;
}

/** Height colour ramp: low = approach amber, high = deep blue. */
export function heightColor(ft, max = 5000) {
  const t = Math.max(0, Math.min(1, (ft || 0) / max));
  const a = [224, 165, 38], b = [36, 87, 166];
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;
}
export const LEVEL_COLORS = ["#2e7d4f", "#c98a12", "#d1622b", "#b4232c"];

/** Imperative Leaflet map. `draw(L, map, layer)` adds content to a fresh layer group whenever `deps` change. */
export default function MapView({ draw, deps = [], height = 460, airspace = true, view = [-3, 112, 5] }) {
  const el = useRef(null);
  const map = useRef(null);
  const group = useRef(null);
  useEffect(() => {
    const m = L.map(el.current, { preferCanvas: true, zoomSnap: 0.25 }).setView([view[0], view[1]], view[2]);
    map.current = m;
    m.createPane("land").style.zIndex = 150;
    land().then((g) => { if (g && map.current === m) L.geoJSON(g, { pane: "land", interactive: false, style: { color: "#a9b6c2", weight: 0.8, fillColor: "#eceee8", fillOpacity: 1 } }).addTo(m); });
    tiles().then((u) => { if (u) L.tileLayer(u, { maxZoom: 13, attribution: "© OpenStreetMap contributors" }).addTo(m); });
    group.current = L.layerGroup().addTo(m);
    if (airspace) {
      sectors().then((g) => {
        const style = (f) => ({ color: f.properties.kind === "UTA" ? "#6b7d93" : "#2457a6", weight: 1, opacity: 0.55,
          fill: f.properties.kind !== "UTA", fillOpacity: 0.03, dashArray: f.properties.kind === "UTA" ? "5 4" : null });
        const mk = (kind) => L.geoJSON({ type: "FeatureCollection", features: g.features.filter((f) => f.properties.kind === kind) },
          { style, onEachFeature: (f, l) => l.bindTooltip(`<b>${f.properties.name}</b><br>${f.properties.lower_raw} – ${f.properties.upper_raw}<br>${f.properties.unit}`) });
        const layers = { "TMA / CTA": L.layerGroup([mk("TMA"), mk("CTA")]), "Upper sectors (UTA)": mk("UTA") };
        layers["TMA / CTA"].addTo(m);
        L.control.layers(null, layers, { collapsed: true }).addTo(m);
      });
    }
    return () => m.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!map.current) return;
    group.current.clearLayers();
    const bounds = draw && draw(L, map.current, group.current);
    if (bounds && bounds.isValid && bounds.isValid()) map.current.fitBounds(bounds, { padding: [24, 24], maxZoom: 11 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return <div ref={el} className="mapbox" style={{ height }} />;
}
