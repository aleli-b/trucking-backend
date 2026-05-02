"""
US property-carrying HOS (simplified): 11-hour drive, 14-hour window,
30-minute break after 8 hours driving, 10-hour reset, 70 hours / 8 days
with 34-hour cycle restart. No adverse driving, no sleeper split.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

Duty = Literal["D", "ON", "OFF"]

EPS = 1e-6


@dataclass
class HosState:
    drive_since_10h_reset_h: float = 0.0
    onduty_since_10h_reset_h: float = 0.0
    drive_since_30_break_h: float = 0.0
    cycle_remaining_h: float = 70.0

    def apply_off_period(self, hours_off: float) -> None:
        if hours_off + EPS >= 34:
            self.cycle_remaining_h = 70.0
            self.drive_since_10h_reset_h = 0.0
            self.onduty_since_10h_reset_h = 0.0
            self.drive_since_30_break_h = 0.0
        elif hours_off + EPS >= 10:
            self.drive_since_10h_reset_h = 0.0
            self.onduty_since_10h_reset_h = 0.0
            self.drive_since_30_break_h = 0.0
        elif hours_off + EPS >= 0.5:
            self.drive_since_30_break_h = 0.0


@dataclass
class TimelineEvent:
    duty: Duty
    start: datetime
    end: datetime
    label: str
    distance_m: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "duty": self.duty,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "distance_m": round(self.distance_m, 1),
        }


def _add_hours(dt: datetime, hours: float) -> datetime:
    return dt + timedelta(seconds=hours * 3600.0)


def simulate(
    trip_start_utc: datetime,
    tasks: list[tuple[str, float, str, float]],
    *,
    initial_cycle_used_h: float,
) -> list[TimelineEvent]:
    """
    tasks: list of tuples:
      ("drive", hours, label, distance_m)
      ("on_duty", hours, label, 0.0)
    """
    if initial_cycle_used_h < 0 or initial_cycle_used_h > 70:
        raise ValueError("cycle_used_hours must be between 0 and 70")

    state = HosState(cycle_remaining_h=70.0 - float(initial_cycle_used_h))
    events: list[TimelineEvent] = []
    t = trip_start_utc.astimezone(timezone.utc)

    def append_event(duty: Duty, hours: float, label: str, distance_m: float = 0.0) -> None:
        nonlocal t
        if hours <= EPS:
            return
        end = _add_hours(t, hours)
        events.append(TimelineEvent(duty=duty, start=t, end=end, label=label, distance_m=distance_m))
        t = end

    for kind, hours, label, dist_m in tasks:
        if kind == "on_duty":
            _consume_on_duty(state, append_event, hours, label)
        elif kind == "drive":
            _consume_drive(state, append_event, hours, label, dist_m)
        else:
            raise ValueError(f"unknown_task:{kind}")

    return events


def _consume_on_duty(
    state: HosState,
    append_event: Any,
    hours: float,
    label: str,
) -> None:
    rem = hours
    while rem > EPS:
        if state.cycle_remaining_h <= EPS:
            append_event("OFF", 34.0, "34_hour_cycle_restart")
            state.apply_off_period(34.0)
            continue
        room14 = 14.0 - state.onduty_since_10h_reset_h
        if room14 <= EPS:
            append_event("OFF", 10.0, "10_hour_reset")
            state.apply_off_period(10.0)
            continue
        chunk = min(rem, state.cycle_remaining_h, room14)
        if chunk <= EPS:
            append_event("OFF", 10.0, "10_hour_reset")
            state.apply_off_period(10.0)
            continue
        append_event("ON", chunk, label, 0.0)
        state.onduty_since_10h_reset_h += chunk
        state.cycle_remaining_h -= chunk
        rem -= chunk


def _consume_drive(
    state: HosState,
    append_event: Any,
    hours: float,
    label: str,
    distance_m: float,
) -> None:
    rem_drive = hours
    rem_dist = distance_m

    while rem_drive > EPS:
        if state.cycle_remaining_h <= EPS:
            append_event("OFF", 34.0, "34_hour_cycle_restart")
            state.apply_off_period(34.0)
            continue

        room11 = 11.0 - state.drive_since_10h_reset_h
        room14 = 14.0 - state.onduty_since_10h_reset_h
        room8 = 8.0 - state.drive_since_30_break_h
        room_cycle = state.cycle_remaining_h

        step = min(rem_drive, room11, room14, room8, room_cycle)

        if step > EPS:
            dist_chunk = 0.0
            if rem_drive > EPS:
                dist_chunk = rem_dist * (step / rem_drive)
            append_event("D", step, label, dist_chunk)
            state.drive_since_10h_reset_h += step
            state.onduty_since_10h_reset_h += step
            state.drive_since_30_break_h += step
            state.cycle_remaining_h -= step
            rem_drive -= step
            rem_dist -= dist_chunk
            continue

        # Regulatory pause before more driving (14h/11h before 30-min break)
        if state.cycle_remaining_h <= EPS:
            append_event("OFF", 34.0, "34_hour_cycle_restart")
            state.apply_off_period(34.0)
        elif room14 <= EPS or room11 <= EPS:
            append_event("OFF", 10.0, "10_hour_reset")
            state.apply_off_period(10.0)
        elif room8 <= EPS:
            append_event("OFF", 0.5, "30_min_break")
            state.apply_off_period(0.5)
        else:
            append_event("OFF", 10.0, "10_hour_reset")
            state.apply_off_period(10.0)


def events_to_serializable(events: list[TimelineEvent]) -> list[dict[str, Any]]:
    return [e.to_dict() for e in events]
