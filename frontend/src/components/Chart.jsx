import React, { useEffect, useRef } from "react";
import * as echarts from "echarts";

// Colour-blind-safe categorical order (FR-VIS-04); identity is also carried by legends and line styles.
export const COLORS = ["#0F7482", "#D98E1F", "#5FB3BE", "#B0306A", "#6250D6", "#1D7A46", "#E87BA4", "#3D5163"];
const AXIS = { axisLine: { lineStyle: { color: "#9aa8b2" } }, axisLabel: { color: "#3d5163" }, splitLine: { lineStyle: { color: "#e7edf0" } },
  nameTextStyle: { color: "#3d5163" } };

export function base(extra = {}) {
  return {
    color: COLORS,
    textStyle: { fontFamily: "Segoe UI, Helvetica Neue, Roboto, Arial, sans-serif" },
    grid: { left: 56, right: 24, top: 52, bottom: 44, containLabel: true },
    tooltip: { trigger: "axis", confine: true },
    legend: { top: 0, left: 0, type: "scroll", textStyle: { color: "#3d5163" } },
    ...extra,
  };
}
export const axis = (o = {}) => ({ ...AXIS, ...o });

function save(url, name) {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
}

function toCSV(option) {
  // export series data as CSV (FR-VIS-03)
  const lines = [];
  const xs = option.xAxis && !Array.isArray(option.xAxis) && option.xAxis.data;
  (option.series || []).forEach((s) => {
    lines.push(`# ${s.name || s.type}`);
    (s.data || []).forEach((d, i) => {
      const v = Array.isArray(d) ? d.join(",") : (d && typeof d === "object" ? JSON.stringify(d.value ?? d) : d);
      lines.push(xs ? `${xs[i]},${v}` : `${i},${v}`);
    });
  });
  return lines.join("\n");
}

export default function Chart({ option, height = 320, name = "chart", onClick }) {
  const el = useRef(null);
  const inst = useRef(null);
  useEffect(() => {
    inst.current = echarts.init(el.current, null, { renderer: "canvas" });
    const ro = new ResizeObserver(() => inst.current && inst.current.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); inst.current.dispose(); };
  }, []);
  useEffect(() => {
    if (!inst.current) return;
    inst.current.setOption(option, true);
    inst.current.off("click");
    if (onClick) inst.current.on("click", onClick);
  }, [option, onClick]);
  const png = () => save(inst.current.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#fff" }), `${name}.png`);
  const svg = () => {
    const tmp = document.createElement("div");
    const c = echarts.init(tmp, null, { renderer: "svg", ssr: true, width: el.current.clientWidth, height });
    c.setOption({ ...option, animation: false });
    const s = c.renderToSVGString();
    c.dispose();
    save(URL.createObjectURL(new Blob([s], { type: "image/svg+xml" })), `${name}.svg`);
  };
  const csv = () => save(URL.createObjectURL(new Blob([toCSV(option)], { type: "text/csv" })), `${name}.csv`);
  return (
    <div className="chartbox">
      <div className="tools">
        <button className="btn ghost sm" onClick={png} title="Download PNG">PNG</button>
        <button className="btn ghost sm" onClick={svg} title="Download SVG">SVG</button>
        <button className="btn ghost sm" onClick={csv} title="Download data as CSV">CSV</button>
      </div>
      <div ref={el} style={{ height, width: "100%" }} />
    </div>
  );
}
