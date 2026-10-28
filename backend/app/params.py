"""Every threshold named in the SRS as a parameter with default, unit, range and description (FR-CORE-010).

Parameter sets are versioned in the database (ParamSet); `current()` returns the active values merged over
these defaults, so parameters added by a newer version of NavRUNA always have a value.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class P:
    default: float | int | str | bool
    unit: str
    lo: float | None
    hi: float | None
    group: str
    text: str


REGISTRY: dict[str, P] = {
    # ---------------------------------------------------------------- common / data (DR-*)
    "data.smoothing_points": P(3, "points", 1, 15, "Data", "Smoothing window for vertical rate and track angle (DR-003)"),
    "data.qnh_ft_per_hpa": P(27.3, "ft/hPa", 25, 30, "Data", "Height change per hPa of QNH difference (DR-002)"),
    "data.max_wx_age_min": P(75, "min", 15, 240, "Data", "Oldest METAR/SPECI used for a flight"),
    "data.threshold_length_tolerance": P(0.05, "fraction", 0.01, 0.2, "Data", "Allowed difference between threshold-pair distance and published runway length (DR-005)"),
    # ---------------------------------------------------------------- GA
    "ga.corridor_length_nm": P(8.0, "NM", 4, 15, "Go-around", "Length of the final approach corridor (FR-GA-001)"),
    "ga.corridor_halfwidth_nm": P(0.5, "NM", 0.2, 2, "Go-around", "Corridor half-width at the threshold"),
    "ga.corridor_growth": P(0.1, "NM per NM", 0, 0.5, "Go-around", "Growth of the half-width per NM from the threshold"),
    "ga.corridor_ceiling_ft": P(3000, "ft", 1500, 6000, "Go-around", "Corridor ceiling above the threshold"),
    "ga.track_tolerance_deg": P(30, "deg", 10, 60, "Go-around", "Allowed difference between ground track and runway bearing (FR-GA-002)"),
    "ga.min_points": P(3, "points", 2, 10, "Go-around", "Consecutive points in the corridor that make an approach"),
    "ga.descent_required_ft": P(300, "ft", 100, 1500, "Go-around", "Height lost in the 2 minutes before the lowest point"),
    "ga.climb_trigger_ft": P(300, "ft", 100, 1000, "Go-around", "Climb above the lowest height that triggers a go-around (FR-GA-004)"),
    "ga.climb_rate_fpm": P(500, "ft/min", 200, 2000, "Go-around", "Vertical rate that must be held"),
    "ga.confirmation_s": P(20, "s", 0, 120, "Go-around", "Confirmation time for the climb (Data4Safety trigger pattern)"),
    "ga.landing_height_ft": P(50, "ft", 0, 200, "Go-around", "Height below which a slow aircraft counts as landed (FR-GA-005)"),
    "ga.landing_speed_kt": P(80, "kt", 40, 140, "Go-around", "Ground speed below which a low aircraft counts as landed"),
    "ga.min_speed_kt": P(60, "kt", 30, 100, "Go-around", "Flights slower than this are treated as rotorcraft and excluded (FR-GA-006)"),
    "ga.band_high_ft": P(1000, "ft", 500, 3000, "Go-around", "Initiation above this height = high band (FR-GA-008)"),
    "ga.band_low_ft": P(500, "ft", 100, 1000, "Go-around", "Initiation below this height = low band"),
    "ga.coverage_min_share": P(0.8, "fraction", 0.3, 1.0, "Go-around", "Runway-days with a lower share of arrivals reaching 1,000 ft are incomplete (FR-GA-020)"),
    "ga.glide_deg": P(3.0, "deg", 2.5, 4.5, "Go-around", "Reference glide path for gate deviations (FR-GA-009)"),
    "ga.tch_ft": P(50, "ft", 30, 80, "Go-around", "Threshold crossing height of the reference glide path"),
    "ga.unstable_dev_ft": P(75, "ft", 30, 300, "Go-around", "Glide-path deviation at 1,000 ft treated as unstable (1 dot)"),
    "ga.unstable_speed_kt": P(30, "kt", 10, 60, "Go-around", "Ground speed above the runway median at 1,000 ft treated as unstable"),
    "ga.fetch_radius_nm": P(40, "NM", 15, 80, "Go-around", "Radius around the aerodrome read from NavTrace"),
    "ga.fetch_ceiling_ft": P(15000, "ft", 6000, 30000, "Go-around", "Highest altitude read around the aerodrome"),
    "ga.review_all_months": P(3, "months", 0, 24, "Go-around", "Every event is offered for review for this long (FR-GA-016)"),
    "ga.review_sample": P(0.2, "fraction", 0, 1, "Go-around", "Share of later events sampled for review"),
    "ga.min_model_events": P(300, "events", 50, 5000, "Go-around", "Confirmed go-arounds needed before a risk model is trained (FR-GA-031)"),
    # ---------------------------------------------------------------- alerts (FR-GA-025..027, FR-SPI-005/006)
    "alert.poisson_p": P(0.023, "p", 0.001, 0.2, "Alerts", "One-sided Poisson p-value for a rate alert (≈ 2σ)"),
    "alert.ewma_lambda": P(0.2, "", 0.05, 0.5, "Alerts", "EWMA smoothing constant"),
    "alert.ewma_L": P(2.7, "σ", 1.5, 4, "Alerts", "EWMA control limit"),
    "alert.min_baseline_den": P(200, "movements", 0, 10000, "Alerts", "Minimum approaches in the baseline before alerting (FR-GA-027)"),
    "alert.min_period_den": P(20, "movements", 0, 1000, "Alerts", "Minimum approaches in the period before alerting"),
    "alert.quiet_hours": P(24, "h", 1, 168, "Alerts", "Quiet period before an unchanged alert is sent again (FR-CORE-022)"),
    "alert.doc9859_history": P(12, "periods", 4, 36, "Alerts", "Preceding periods used for Doc 9859 standard-deviation alert levels"),
    # ---------------------------------------------------------------- TDM
    "tdm.window_days": P(90, "days", 30, 365, "Deviation", "Training window for nominal paths (FR-TDM-002)"),
    "tdm.resample_points": P(100, "points", 30, 300, "Deviation", "Points per resampled trajectory"),
    "tdm.min_cluster_share": P(0.03, "fraction", 0.005, 0.3, "Deviation", "Minimum cluster size as share of the route's flights"),
    "tdm.min_cluster_size": P(10, "flights", 3, 200, "Deviation", "Minimum cluster size"),
    "tdm.max_train_flights": P(1500, "flights", 200, 5000, "Deviation", "Flights sampled for clustering"),
    "tdm.assign_nm": P(15, "NM", 3, 50, "Deviation", "Mean offset within which a flight is assigned to a nominal path (FR-TDM-010)"),
    "tdm.cp_radius_nm": P(5, "NM", 1, 20, "Deviation", "Waypoints within this distance of a nominal path become critical points (FR-TDM-012)"),
    "tdm.lateral_enroute_nm": P(2.0, "NM", 0.5, 10, "Deviation", "Lateral deviation limit en route (RNP 2)"),
    "tdm.lateral_tma_nm": P(1.0, "NM", 0.3, 5, "Deviation", "Lateral deviation limit inside a TMA (RNP 1)"),
    "tdm.vertical_ft": P(1000, "ft", 300, 5000, "Deviation", "Vertical deviation limit from the median profile"),
    "tdm.hotspot_grid_nm": P(5, "NM", 1, 20, "Deviation", "Hot-spot grid size (FR-TDM-014)"),
    "tdm.share_change_pp": P(10, "points", 2, 50, "Deviation", "Change in a cluster's share between rebuilds that raises an alert (FR-TDM-022)"),
    "tdm.hazard_min_share": P(0.05, "fraction", 0.01, 0.5, "Deviation", "Critical point flagged share listed as hazard candidate (FR-TDM-023)"),
    # ---------------------------------------------------------------- CXI
    "cxi.sample_s": P(10, "s", 5, 60, "Complexity", "Track resampling step (FR-CXI-010)"),
    "cxi.cell_nm": P(20, "NM", 5, 40, "Complexity", "Interaction cell size (PRU)"),
    "cxi.cell_ft": P(3000, "ft", 1000, 6000, "Complexity", "Interaction cell height (PRU)"),
    "cxi.horizontal_deg": P(20, "deg", 5, 90, "Complexity", "Track difference for a horizontal interaction"),
    "cxi.speed_kt": P(35, "kt", 10, 100, "Complexity", "Ground-speed difference for a speed interaction"),
    "cxi.vertical_fpm": P(300, "ft/min", 100, 1500, "Complexity", "Vertical rate separating climbing, cruising and descending"),
    "cxi.crossing_deg": P(30, "deg", 10, 90, "Complexity", "Angle above which two tracks count as crossing"),
    "cxi.crossing_window_s": P(600, "s", 60, 1800, "Complexity", "Time window for a crossing pair"),
    "cxi.vertical_band_ft": P(2000, "ft", 500, 5000, "Complexity", "Vertical band for crossings and proximity"),
    "cxi.proximity_nm": P(10, "NM", 3, 20, "Complexity", "Horizontal distance for a proximity event"),
    "cxi.level_change_ft": P(1000, "ft", 300, 4000, "Complexity", "Level change counted"),
    "cxi.heading_change_deg": P(15, "deg", 5, 90, "Complexity", "Heading change counted"),
    "cxi.transition_alt_ft": P(11000, "ft", 3000, 18000, "Complexity", "Transition altitude: QNH heights below, flight levels above"),
    "cxi.w_entries": P(1.0, "weight", 0, 5, "Complexity", "Index weight: entries"),
    "cxi.w_peak": P(1.0, "weight", 0, 5, "Complexity", "Index weight: peak count"),
    "cxi.w_interactions": P(1.0, "weight", 0, 5, "Complexity", "Index weight: interaction time"),
    "cxi.w_crossings": P(1.0, "weight", 0, 5, "Complexity", "Index weight: crossing pairs"),
    "cxi.w_levels": P(1.0, "weight", 0, 5, "Complexity", "Index weight: level changes"),
    "cxi.w_headings": P(1.0, "weight", 0, 5, "Complexity", "Index weight: heading changes"),
    "cxi.baseline_days": P(90, "days", 14, 365, "Complexity", "Baseline for standardising the index"),
    # ---------------------------------------------------------------- EWS
    "ews.top_operators": P(10, "operators", 3, 30, "Early warning", "Busiest operators scored separately per aerodrome (FR-EWS-001)"),
    "ews.min_movements": P(10, "movements", 3, 50, "Early warning", "Cells with fewer movements are merged (FR-EWS-002)"),
    "ews.train_days": P(180, "days", 60, 730, "Early warning", "Training window (FR-EWS-010)"),
    "ews.min_history_days": P(90, "days", 30, 365, "Early warning", "History needed before the first scores"),
    "ews.score_pct": P(99, "percentile", 90, 99.9, "Early warning", "Score percentile that raises an alert (FR-EWS-012)"),
    "ews.mad_k": P(3, "MAD", 2, 6, "Early warning", "Feature distance from baseline counted as unusual"),
    "ews.min_features": P(2, "features", 1, 5, "Early warning", "Unusual features needed for an alert"),
    "ews.coverage_mad_k": P(3, "MAD", 2, 6, "Early warning", "Coverage measure distance that marks a coverage anomaly (FR-EWS-003)"),
    "ews.gap_s": P(60, "s", 20, 300, "Early warning", "Gap length counted in coverage"),
}


def defaults() -> dict:
    return {k: p.default for k, p in REGISTRY.items()}


def validate(values: dict) -> dict:
    """Clip to range and coerce type; unknown keys are dropped."""
    out = {}
    for k, v in values.items():
        p = REGISTRY.get(k)
        if p is None:
            continue
        if isinstance(p.default, bool):
            out[k] = bool(v)
            continue
        try:
            v = type(p.default)(float(v)) if isinstance(p.default, int) else float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k}: not a number")
        if p.lo is not None and v < p.lo or p.hi is not None and v > p.hi:
            raise ValueError(f"{k}: {v} outside {p.lo}–{p.hi} {p.unit}")
        out[k] = v
    return out


def current(db=None) -> tuple[dict, int]:
    """(values, version) of the active parameter set."""
    from .db import SessionLocal
    from .models import ParamSet
    own = db is None
    db = db or SessionLocal()
    try:
        ps = db.query(ParamSet).filter_by(active=True).order_by(ParamSet.version.desc()).first()
        vals = defaults()
        if ps:
            vals.update({k: v for k, v in (ps.values or {}).items() if k in REGISTRY})
        return vals, (ps.version if ps else 0)
    finally:
        if own:
            db.close()
