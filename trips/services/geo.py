"""Geodesy helpers for route sampling (OSRM returns lon, lat)."""

from __future__ import annotations

import math
from typing import Sequence

EARTH_R_M = 6_371_000.0


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(min(1.0, math.sqrt(a)))


def line_length_m(coords: Sequence[Sequence[float]]) -> float:
    if len(coords) < 2:
        return 0.0
    total = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i][0], coords[i][1]
        lon2, lat2 = coords[i + 1][0], coords[i + 1][1]
        total += haversine_m(lon1, lat1, lon2, lat2)
    return total


def interpolate_at_distance_m(
    coords: Sequence[Sequence[float]], distance_m: float
) -> tuple[float, float]:
    """Return (lon, lat) at cumulative distance_m along the polyline."""
    if not coords:
        return 0.0, 0.0
    if distance_m <= 0:
        return float(coords[0][0]), float(coords[0][1])
    acc = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = float(coords[i][0]), float(coords[i][1])
        lon2, lat2 = float(coords[i + 1][0]), float(coords[i + 1][1])
        seg = haversine_m(lon1, lat1, lon2, lat2)
        if acc + seg >= distance_m:
            t = (distance_m - acc) / seg if seg > 0 else 0.0
            return lon1 + t * (lon2 - lon1), lat1 + t * (lat2 - lat1)
        acc += seg
    return float(coords[-1][0]), float(coords[-1][1])
