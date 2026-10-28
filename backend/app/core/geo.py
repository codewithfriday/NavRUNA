"""Geometry helpers. Positions are projected to a local plane in nautical miles around a reference point
(equirectangular; error below 0.5 % within 150 NM of the reference, which covers every use in NavRUNA)."""
from __future__ import annotations

import math

import numpy as np

NM_PER_DEG = 60.0
FT_PER_NM = 6076.12
EARTH_NM = 3440.065


def to_xy(lon, lat, lon0: float, lat0: float):
    lon = np.asarray(lon, float)
    lat = np.asarray(lat, float)
    kx = NM_PER_DEG * math.cos(math.radians(lat0))
    return (lon - lon0) * kx, (lat - lat0) * NM_PER_DEG


def to_lonlat(x, y, lon0: float, lat0: float):
    kx = NM_PER_DEG * math.cos(math.radians(lat0))
    return np.asarray(x, float) / kx + lon0, np.asarray(y, float) / NM_PER_DEG + lat0


def haversine_nm(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_NM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def bearing_deg(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    y = np.sin(lon2 - lon1) * np.cos(lat2)
    x = np.cos(lat1) * np.sin(lat2) - np.sin(lat1) * np.cos(lat2) * np.cos(lon2 - lon1)
    return (np.degrees(np.arctan2(y, x)) + 360) % 360


def angle_diff(a, b):
    """Smallest absolute difference between two headings, degrees."""
    d = (np.asarray(a, float) - np.asarray(b, float) + 180) % 360 - 180
    return np.abs(d)


def track_from_xy(x, y):
    """Ground track (degrees true) of each step; the last point repeats the previous value."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2:
        return np.zeros(len(x))
    t = (np.degrees(np.arctan2(np.diff(x), np.diff(y))) + 360) % 360
    return np.append(t, t[-1])


def smooth(v, n: int):
    v = np.asarray(v, float)
    if n <= 1 or len(v) < n:
        return v
    k = np.ones(n) / n
    pad = n // 2
    vp = np.pad(v, (pad, n - 1 - pad), mode="edge")
    return np.convolve(vp, k, mode="valid")


def unwrap_deg(a):
    return np.degrees(np.unwrap(np.radians(np.asarray(a, float))))


def path_length(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 2:
        return 0.0
    return float(np.hypot(np.diff(x), np.diff(y)).sum())


def polygon_rings(geom: dict) -> list[np.ndarray]:
    """Outer rings of a GeoJSON Polygon / MultiPolygon as arrays of [lon, lat]."""
    if not geom:
        return []
    if geom.get("type") == "Polygon":
        return [np.asarray(geom["coordinates"][0], float)]
    if geom.get("type") == "MultiPolygon":
        return [np.asarray(p[0], float) for p in geom["coordinates"]]
    return []


def polygon_area_nm2(geom: dict) -> float:
    total = 0.0
    for ring in polygon_rings(geom):
        lat0 = float(ring[:, 1].mean())
        x, y = to_xy(ring[:, 0], ring[:, 1], float(ring[:, 0].mean()), lat0)
        total += abs(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return total


class PolygonIndex:
    """Fast point-in-polygon for a set of (Multi)Polygons, using matplotlib's Path with a bounding-box prefilter."""

    def __init__(self, geoms: list[dict]):
        from matplotlib.path import Path
        self.items = []
        for i, g in enumerate(geoms):
            for ring in polygon_rings(g):
                self.items.append((i, Path(ring), ring[:, 0].min(), ring[:, 0].max(), ring[:, 1].min(), ring[:, 1].max()))

    def contains(self, i: int, lon, lat) -> np.ndarray:
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        out = np.zeros(len(lon), bool)
        for j, path, x0, x1, y0, y1 in self.items:
            if j != i:
                continue
            m = (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)
            if m.any():
                out[m] |= path.contains_points(np.column_stack([lon[m], lat[m]]))
        return out
