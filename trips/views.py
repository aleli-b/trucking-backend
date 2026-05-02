import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from trips.services.plan import ASSUMPTIONS, build_plan

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["POST", "GET"])
def plan_trip(request):
    """
    POST JSON body (React-friendly; CSRF exempt for SPA — add auth in production).

    {
      "current": {"lat": ..., "lon": ...},
      "pickup": {"lat": ..., "lon": ...},
      "dropoff": {"lat": ..., "lon": ...},
      "cycle_used_hours": 12.5,
      "trip_start": "2026-05-02T14:00:00Z",
      "log_timezone": "America/Chicago"
    }

    GET returns schema / assumptions for discovery.
    """
    if request.method == "GET":
        return JsonResponse(
            {
                "endpoint": "POST same URL with JSON body",
                "assumptions": ASSUMPTIONS,
                "body_schema": {
                    "current": {"lat": "number", "lon": "number"},
                    "pickup": {"lat": "number", "lon": "number"},
                    "dropoff": {"lat": "number", "lon": "number"},
                    "cycle_used_hours": "number 0..70 (hours already used in 8-day / 70-hour window)",
                    "trip_start": "optional ISO-8601 UTC; default now",
                    "log_timezone": "optional IANA tz for daily_logs; default America/Chicago",
                },
            }
        )

    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError as exc:
        logger.warning("plan_trip invalid_json: %s", exc)
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    result = build_plan(body)
    status = 200 if result.get("ok") else 400
    if not result.get("ok"):
        logger.warning(
            "plan_trip failed status=%s error=%s content_type=%r body_len=%s",
            status,
            result.get("error"),
            request.content_type,
            len(request.body or b""),
        )
    return JsonResponse(result, status=status)
