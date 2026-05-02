# Trucking backend (Spotter)

Django API for trip planning: driving route from OSRM, fuel splits, simplified US property HOS (11/14, 30-minute break, 10-hour reset, 70/8 with 34-hour cycle restart), and JSON payloads for a separate React app (map, ELD-style daily grids).

---

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py runserver
```

- **GET** `http://127.0.0.1:8000/api/trips/plan/` — Describes the POST body and assumptions.
- **POST** `http://127.0.0.1:8000/api/trips/plan/` — Send JSON; receive route, timeline, stops, and `daily_logs`.

With `DEBUG=True`, CORS allows any origin so a local React dev server can call the API. The plan endpoint is CSRF-exempt for SPA POSTs; tighten this before production.

---

## File reference

### Repository root

| File | Purpose |
|------|--------|
| `manage.py` | Django entrypoint: `runserver`, `migrate`, `shell`, etc. Sets `DJANGO_SETTINGS_MODULE` to `spotter.settings`. |
| `requirements.txt` | Pip dependencies (Django, django-cors-headers). |
| `db.sqlite3` | Created after first `migrate` (default SQLite DB). Not used by the trip planner logic today, but present if you use admin/auth later. |

### Project package `spotter/`

Django project configuration (created by `startproject`).

| File | Purpose |
|------|--------|
| `spotter/__init__.py` | Marks `spotter` as a Python package. |
| `spotter/settings.py` | Installed apps (`trips`, `corsheaders`), middleware, database, templates, `DEBUG`, `CORS_*` for the React frontend in development. |
| `spotter/urls.py` | Root URL table: mounts `admin/` and `api/trips/` (includes `trips.urls`). |
| `spotter/wsgi.py` | WSGI application object for production servers (e.g. gunicorn). |
| `spotter/asgi.py` | ASGI application object for async servers / websockets if you add them later. |

### App `trips/`

Trip planning API and domain logic.

| File | Purpose |
|------|--------|
| `trips/__init__.py` | Marks `trips` as a package. |
| `trips/apps.py` | Django `AppConfig` for the `trips` app (label, name). |
| `trips/views.py` | `plan_trip` view: **GET** returns schema/assumptions; **POST** parses JSON, calls `build_plan`, returns `JsonResponse`. |
| `trips/urls.py` | App URLconf: maps `plan/` to `views.plan_trip` (full path under project: `/api/trips/plan/`). |

### App services `trips/services/`

| File | Purpose |
|------|--------|
| `trips/services/__init__.py` | Package marker for service modules. |
| `trips/services/geo.py` | Small geodesy helpers: haversine distance along a polyline, cumulative length, interpolate `(lon, lat)` at a distance along OSRM coordinates. |
| `trips/services/osrm.py` | Calls the **public OSRM demo** router (`router.project-osrm.org`) for `current → pickup → dropoff`: returns distance, duration, GeoJSON coordinates, and per-leg distance/duration for splitting fuel. No API key. |
| `trips/services/hos.py` | Hours-of-service **simulator**: consumes an ordered list of “drive” and “on_duty” tasks, emits a UTC **timeline** (`D` / `ON` / `OFF`) with labels (`10_hour_reset`, `30_min_break`, `34_hour_cycle_restart`, etc.). |
| `trips/services/plan.py` | **Orchestration**: validates request body, fetches OSRM route, builds drive chunks with **fuel stops every 1000 miles** (plus 1 h pickup / 1 h dropoff after the appropriate legs), runs `simulate`, enriches events with positions along the route, builds **`daily_logs`** (segments clipped to local calendar days), **`stops`** for the map, and **`summary`** stats. |

---

## What the API returns (for your React app)

- **`route`**: Total distance/duration and GeoJSON LineString for the map.
- **`timeline`**: Every duty interval with UTC timestamps, labels, and optional `lon`/`lat` and distance-along-route for drawing.
- **`stops`**: Fixed pins (current, pickup, dropoff) plus regulatory stops (fuel, breaks, resets).
- **`daily_logs`**: Per-day lists of segments with local times and minute-of-day for drawing log grids.

Assumptions and limits of the HOS model are described in **`GET /api/trips/plan/`** and in the `assumptions` object on successful POST responses.

---

## Routing note

OSRM’s public demo is rate-limited and not meant for high-volume production. For production, point routing at your own OSRM/GraphHopper/ORS instance and adjust `trips/services/osrm.py` accordingly.
