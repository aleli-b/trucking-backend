"""Call the public OSRM demo router (no API key)."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"


@dataclass
class OsrmRoute:
    distance_m: float
    duration_s: float
    geometry_coordinates: list[list[float]]  # [[lon, lat], ...]
    legs: list[dict[str, Any]]


def fetch_route(
    current: tuple[float, float],
    pickup: tuple[float, float],
    dropoff: tuple[float, float],
    timeout_s: float = 30.0,
) -> OsrmRoute:
    """
    Order: current -> pickup -> dropoff.
    Coordinates are (lat, lon) in; OSRM expects lon,lat in URL.
    """
    lonlat = [
        f"{current[1]},{current[0]}",
        f"{pickup[1]},{pickup[0]}",
        f"{dropoff[1]},{dropoff[0]}",
    ]
    path = ";".join(lonlat)
    q = urllib.parse.urlencode(
        {"overview": "full", "geometries": "geojson", "steps": "true"}
    )
    url = f"{OSRM_BASE}/{path}?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "trucking-backend/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode(errors="replace").strip()
        except Exception:
            pass
        if raw:
            try:
                err_payload = json.loads(raw)
            except json.JSONDecodeError:
                err_payload = None
            if isinstance(err_payload, dict) and err_payload.get("code") == "NoRoute":
                msg = err_payload.get("message") or "No drivable route between the given points."
                raise RuntimeError(f"osrm_no_route:{msg}") from e
            raise RuntimeError(f"osrm_http_{e.code}:{raw[:400]}") from e
        raise RuntimeError(f"osrm_http_{e.code}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"osrm_unreachable:{e.reason!s}") from e

    if payload.get("code") != "Ok" or not payload.get("routes"):
        code = payload.get("code", "unknown")
        raise RuntimeError(f"osrm_no_route:{code}")

    route = payload["routes"][0]
    geom = route.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        raise RuntimeError("osrm_empty_geometry")

    legs = route.get("legs") or []
    return OsrmRoute(
        distance_m=float(route.get("distance", 0.0)),
        duration_s=float(route.get("duration", 0.0)),
        geometry_coordinates=[[float(c[0]), float(c[1])] for c in coords],
        legs=legs,
    )


def driving_hours_from_osrm_duration_s(duration_s: float) -> float:
    """Use OSRM duration; guard against zero."""
    if duration_s <= 0:
        return 0.0
    return duration_s / 3600.0


def effective_speed_mph(distance_m: float, duration_s: float) -> float:
    if duration_s <= 0:
        return 55.0
    miles = distance_m / 1609.344
    hours = duration_s / 3600.0
    if hours <= 0:
        return 55.0
    v = miles / hours
    if not math.isfinite(v) or v < 5.0:
        return 55.0
    if v > 85.0:
        return 85.0
    return v