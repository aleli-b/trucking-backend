"""Build OSRM route, split fuel stops, run HOS simulation, shape API payload."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trips.services import geo
from trips.services.hos import TimelineEvent, simulate
from trips.services.osrm import OsrmRoute, fetch_route

EPS = 1e-6

FUEL_INTERVAL_M = 1000 * 1609.344  # miles to meters
PICKUP_H = 1.0
DROPOFF_H = 1.0
FUEL_ON_DUTY_H = 0.5

ASSUMPTIONS = {
    "driver_type": "property_carrying",
    "cycle": "70_hours_8_days",
    "adverse_driving": False,
    "pickup_on_duty_hours": PICKUP_H,
    "dropoff_on_duty_hours": DROPOFF_H,
    "fuel_interval_miles": 1000,
    "fuel_on_duty_hours": FUEL_ON_DUTY_H,
    "hos_model": "11_drive_14_window_8_30min_break_10_reset_34_cycle_restart",
    "routing_engine": "osrm_demo_public",
    "daily_clock_at_trip_start": "fresh_11_14_break_only_cycle_used_is_input",
}


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_plan(body: dict[str, Any]) -> dict[str, Any]:
    err = _validate_body(body)
    if err:
        return {"ok": False, "error": err}

    current = (float(body["current"]["lat"]), float(body["current"]["lon"]))
    pickup = (float(body["pickup"]["lat"]), float(body["pickup"]["lon"]))
    dropoff = (float(body["dropoff"]["lat"]), float(body["dropoff"]["lon"]))
    cycle_used = float(body["cycle_used_hours"])
    trip_start = _parse_iso_datetime(body.get("trip_start")) or datetime.now(timezone.utc)
    log_tz = str(body.get("log_timezone") or "America/Chicago")

    try:
        osrm = fetch_route(current, pickup, dropoff)
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}

    try:
        tasks = _build_tasks(osrm)
        timeline = simulate(
            trip_start,
            tasks,
            initial_cycle_used_h=cycle_used,
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    coords = osrm.geometry_coordinates
    enriched = _enrich_with_route(coords, timeline, osrm.distance_m)
    daily = _daily_logs(enriched, log_tz)
    stops = _stops_for_map(enriched, current, pickup, dropoff)

    return {
        "ok": True,
        "assumptions": ASSUMPTIONS,
        "trip_start_utc": trip_start.isoformat(),
        "log_timezone": log_tz,
        "route": {
            "distance_m": round(osrm.distance_m, 1),
            "duration_s": round(osrm.duration_s, 1),
            "geometry": {
                "type": "LineString",
                "coordinates": coords,
            },
        },
        "timeline": enriched,
        "stops": stops,
        "daily_logs": daily,
        "summary": _summary(osrm, enriched),
    }


def _validate_body(body: dict[str, Any]) -> str | None:
    if not isinstance(body, dict):
        return "body_must_be_object"
    for key in ("current", "pickup", "dropoff"):
        loc = body.get(key)
        if not isinstance(loc, dict):
            return f"missing_{key}"
        try:
            lat = float(loc["lat"])
            lon = float(loc["lon"])
        except (KeyError, TypeError, ValueError):
            return f"invalid_{key}_lat_lon"
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return f"out_of_range_{key}"
    try:
        cu = float(body.get("cycle_used_hours", -1))
    except (TypeError, ValueError):
        return "invalid_cycle_used_hours"
    if cu < 0 or cu > 70:
        return "cycle_used_hours_out_of_range"
    return None


def _build_tasks(osrm: OsrmRoute) -> list[tuple[str, float, str, float]]:
    legs = osrm.legs
    if not legs:
        raise ValueError("osrm_no_legs")
    total_m = sum(float(leg.get("distance", 0.0)) for leg in legs)
    tasks: list[tuple[str, float, str, float]] = []
    global_m = 0.0

    for leg_idx, leg in enumerate(legs):
        dist = float(leg.get("distance", 0.0))
        dur_s = float(leg.get("duration", 0.0))
        if dist <= EPS:
            if dur_s > EPS:
                tasks.append(("drive", dur_s / 3600.0, f"leg_{leg_idx}_drive", 0.0))
        else:
            pos_in_leg = 0.0
            while pos_in_leg < dist - EPS:
                abs_start = global_m + pos_in_leg
                k = int(math.floor(abs_start / FUEL_INTERVAL_M)) + 1
                next_fuel = k * FUEL_INTERVAL_M
                end_leg_abs = global_m + dist
                ended_at_fuel_boundary = False
                if next_fuel + EPS < end_leg_abs and next_fuel > abs_start + EPS:
                    chunk_m = next_fuel - abs_start
                    ended_at_fuel_boundary = True
                else:
                    chunk_m = end_leg_abs - abs_start

                chunk_h = dur_s * (chunk_m / dist) / 3600.0 if dist > 0 else 0.0
                tasks.append(("drive", chunk_h, f"leg_{leg_idx}_drive", chunk_m))
                pos_in_leg += chunk_m

                arrived_abs = global_m + pos_in_leg
                if ended_at_fuel_boundary and arrived_abs < total_m - 50.0:
                    tasks.append(("on_duty", FUEL_ON_DUTY_H, "fuel", 0.0))

        global_m += dist

        if leg_idx == 0:
            tasks.append(("on_duty", PICKUP_H, "pickup", 0.0))
        if leg_idx == len(legs) - 1:
            tasks.append(("on_duty", DROPOFF_H, "dropoff", 0.0))

    return tasks


def _enrich_with_route(
    coords: list[list[float]],
    timeline: list[TimelineEvent],
    total_route_m: float,
) -> list[dict[str, Any]]:
    cum = 0.0
    out: list[dict[str, Any]] = []
    for e in timeline:
        row: dict[str, Any] = {
            "duty": e.duty,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "label": e.label,
            "distance_m": round(e.distance_m, 1),
            "distance_along_route_start_m": None,
            "distance_along_route_end_m": None,
            "lon_start": None,
            "lat_start": None,
            "lon_end": None,
            "lat_end": None,
        }
        if e.duty == "D" and e.distance_m > EPS:
            d0 = cum
            d1 = min(cum + e.distance_m, total_route_m)
            lon0, lat0 = geo.interpolate_at_distance_m(coords, d0)
            lon1, lat1 = geo.interpolate_at_distance_m(coords, d1)
            cum = d1
            row["distance_along_route_start_m"] = round(d0, 1)
            row["distance_along_route_end_m"] = round(d1, 1)
            row["lon_start"], row["lat_start"] = lon0, lat0
            row["lon_end"], row["lat_end"] = lon1, lat1
        else:
            lon0, lat0 = geo.interpolate_at_distance_m(coords, min(cum, max(total_route_m, 0.0)))
            row["lon_start"], row["lat_start"] = lon0, lat0
            row["lon_end"], row["lat_end"] = lon0, lat0
        out.append(row)
    return out


def _merge_adjacent_segments(segs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not segs:
        return []
    merged = [dict(segs[0])]
    for s in segs[1:]:
        last = merged[-1]
        if last["duty"] == s["duty"] and abs(last["end_minute_of_day"] - s["start_minute_of_day"]) < 1e-3:
            last["end_local"] = s["end_local"]
            last["end_minute_of_day"] = s["end_minute_of_day"]
        else:
            merged.append(dict(s))
    return merged


def _daily_logs(enriched: list[dict[str, Any]], tz_name: str) -> list[dict[str, Any]]:
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    events: list[dict[str, Any]] = []
    for row in enriched:
        events.append(
            {
                "duty": row["duty"],
                "start": datetime.fromisoformat(row["start"]),
                "end": datetime.fromisoformat(row["end"]),
            }
        )
    if not events:
        return []

    min_d = min(e["start"].astimezone(tz).date() for e in events)
    max_d = max(e["end"].astimezone(tz).date() for e in events)

    days: list[dict[str, Any]] = []
    d = min_d
    while d <= max_d:
        day_start = datetime.combine(d, time.min, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        day_start_utc = day_start.astimezone(timezone.utc)
        day_end_utc = day_end.astimezone(timezone.utc)
        segs: list[dict[str, Any]] = []
        for e in events:
            s = max(e["start"], day_start_utc)
            en = min(e["end"], day_end_utc)
            if s < en:
                sl = s.astimezone(tz)
                enl = en.astimezone(tz)
                segs.append(
                    {
                        "duty": e["duty"],
                        "start_local": sl.isoformat(),
                        "end_local": enl.isoformat(),
                        "start_minute_of_day": (sl - day_start).total_seconds() / 60.0,
                        "end_minute_of_day": (enl - day_start).total_seconds() / 60.0,
                    }
                )
        segs.sort(key=lambda x: x["start_minute_of_day"])
        days.append({"date": d.isoformat(), "segments": _merge_adjacent_segments(segs)})
        d += timedelta(days=1)
    return days


def _stops_for_map(
    enriched: list[dict[str, Any]],
    current: tuple[float, float],
    pickup: tuple[float, float],
    dropoff: tuple[float, float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [
        {"kind": "current", "lat": current[0], "lon": current[1]},
        {"kind": "pickup", "lat": pickup[0], "lon": pickup[1]},
        {"kind": "dropoff", "lat": dropoff[0], "lon": dropoff[1]},
    ]
    for row in enriched:
        if row["label"] not in ("fuel", "30_min_break", "10_hour_reset", "34_hour_cycle_restart"):
            continue
        if row["lon_start"] is None:
            continue
        t0 = datetime.fromisoformat(row["start"])
        t1 = datetime.fromisoformat(row["end"])
        hrs = (t1 - t0).total_seconds() / 3600.0
        out.append(
            {
                "kind": row["label"],
                "lat": row["lat_start"],
                "lon": row["lon_start"],
                "start": row["start"],
                "end": row["end"],
                "duration_hours": round(hrs, 2),
            }
        )
    return out


def _summary(osrm: OsrmRoute, enriched: list[dict[str, Any]]) -> dict[str, Any]:
    if not enriched:
        return {
            "trip_end_utc": None,
            "osrm_driving_duration_s": round(osrm.duration_s, 1),
            "simulated_trip_duration_hours": 0.0,
        }
    total_sim_h = 0.0
    for row in enriched:
        t0 = datetime.fromisoformat(row["start"])
        t1 = datetime.fromisoformat(row["end"])
        total_sim_h += (t1 - t0).total_seconds() / 3600.0
    return {
        "trip_end_utc": enriched[-1]["end"],
        "osrm_driving_duration_s": round(osrm.duration_s, 1),
        "simulated_trip_duration_hours": round(total_sim_h, 2),
    }
