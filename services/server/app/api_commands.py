"""Validation and normalization of commands submitted through the programmatic vehicle API."""
from __future__ import annotations

import math
from typing import Any, Optional

MAX_MISSION_WAYPOINTS = 500
MAX_SPEED_MS = 50.0

# Command types accepted by POST /api/vehicles/{id}/command, mapped to the
# permission each requires. The UI WebSocket shares this table.
COMMAND_PERMISSIONS: dict[str, str] = {
    "waypoint": "send_waypoint",
    "trajectory": "send_waypoint",
    "set_velocity": "send_waypoint",
    "set_heading": "send_waypoint",
    "rtb": "send_rtb",
    "land_on_boat": "send_rtb",
    "set_mode": "set_vehicle_mode",
    "cancel_sar": "cancel_sar",
    "clear_sar_pattern": "cancel_sar",
    "search_grid": "search_grid",
    "mob": "trigger_mob",
    "mission_plan": "upload_mission",
    "ship_relative_trajectory": "upload_mission",
    "arm": "arm_disarm",
    "disarm": "arm_disarm",
    "takeoff": "arm_disarm",
    "rc_override": "rc_override",
}

# Subset a script may send; "mob" and the legacy "trajectory" stay UI-only.
API_COMMAND_TYPES = frozenset(COMMAND_PERMISSIONS) - {"mob", "trajectory"}

_NO_ARGUMENT_COMMANDS = frozenset({"rtb", "land_on_boat", "cancel_sar", "clear_sar_pattern", "arm", "disarm"})


class CommandValidationError(ValueError):
    """Raised when an API command is malformed."""


def _number(value: Any, name: str, *, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise CommandValidationError(f"{name} must be a number")
    try:
        number = float(value)
    except ValueError:
        raise CommandValidationError(f"{name} must be a number") from None
    if not math.isfinite(number):
        raise CommandValidationError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise CommandValidationError(f"{name} must be >= {minimum:g}")
    if maximum is not None and number > maximum:
        raise CommandValidationError(f"{name} must be <= {maximum:g}")
    return number


def _lat_lon(lat: Any, lon: Any, label: str) -> tuple[float, float]:
    return (
        _number(lat, f"{label}latitude", minimum=-90.0, maximum=90.0),
        _number(lon, f"{label}longitude", minimum=-180.0, maximum=180.0),
    )


def _list(value: Any, name: str) -> list:
    if not isinstance(value, list) or not value:
        raise CommandValidationError(f"{name} must be a non-empty list")
    if len(value) > MAX_MISSION_WAYPOINTS:
        raise CommandValidationError(f"{name} may contain at most {MAX_MISSION_WAYPOINTS} entries")
    return value


def normalize_api_command(raw: Any, *, default_altitude: Optional[float] = None) -> dict[str, Any]:
    """Validate an API command and return the clean dict forwarded to the vehicle.

    ``default_altitude`` fills a waypoint's missing altitude (the vehicle's current altitude).
    """
    if not isinstance(raw, dict):
        raise CommandValidationError("command must be a JSON object")
    cmd_type = raw.get("type")
    if cmd_type not in API_COMMAND_TYPES:
        raise CommandValidationError(f"unsupported command type: {cmd_type!r}")

    if cmd_type in _NO_ARGUMENT_COMMANDS:
        return {"type": cmd_type}

    if cmd_type == "waypoint":
        target = raw.get("target")
        if not isinstance(target, dict):
            raise CommandValidationError("target must be an object with latitude, longitude and altitude")
        lat, lon = _lat_lon(target.get("latitude"), target.get("longitude"), "target ")
        altitude = target.get("altitude")
        if altitude is None:
            altitude = default_altitude if default_altitude is not None else 0.0
        return {"type": cmd_type, "target": {
            "latitude": lat, "longitude": lon, "altitude": _number(altitude, "target altitude", minimum=-500.0, maximum=20000.0),
        }}

    if cmd_type == "mission_plan":
        waypoints = []
        for index, item in enumerate(_list(raw.get("waypoints"), "waypoints")):
            if not isinstance(item, dict):
                raise CommandValidationError(f"waypoints[{index}] must be an object")
            lat, lon = _lat_lon(item.get("latitude"), item.get("longitude"), f"waypoints[{index}] ")
            altitude = item.get("altitude")
            waypoints.append({
                "command_id": 16,
                "item_type": "waypoint",
                "latitude": lat,
                "longitude": lon,
                "altitude": _number(altitude if altitude is not None else (default_altitude or 0.0), f"waypoints[{index}] altitude", minimum=-500.0, maximum=20000.0),
                "hold_time_s": _number(item.get("hold_time_s", 0.0), "hold_time_s", minimum=0.0),
                "acceptance_radius_m": _number(item.get("acceptance_radius_m", 8.0), "acceptance_radius_m", minimum=1.0),
                "yaw_deg": _number(item.get("yaw_deg", 0.0), "yaw_deg"),
            })
        return {
            "type": cmd_type,
            "auto_arm_start": bool(raw.get("auto_arm_start", True)),
            "force_guided_on_complete": bool(raw.get("force_guided_on_complete", False)),
            "waypoints": waypoints,
        }

    if cmd_type == "ship_relative_trajectory":
        ship_vehicle_id = raw.get("ship_vehicle_id")
        if not isinstance(ship_vehicle_id, str) or not ship_vehicle_id:
            raise CommandValidationError("ship_vehicle_id is required")
        local_waypoints = []
        for index, item in enumerate(_list(raw.get("local_waypoints"), "local_waypoints")):
            if not isinstance(item, dict):
                raise CommandValidationError(f"local_waypoints[{index}] must be an object")
            point = {axis: _number(item.get(axis, 0.0), f"local_waypoints[{index}].{axis}") for axis in ("x", "y", "z")}
            if item.get("yaw_deg") is not None:
                point["yaw_deg"] = _number(item["yaw_deg"], f"local_waypoints[{index}].yaw_deg")
            local_waypoints.append(point)
        return {
            "type": cmd_type,
            "ship_vehicle_id": ship_vehicle_id,
            "local_waypoints": local_waypoints,
            "arrival_radius_m": _number(raw.get("arrival_radius_m", 6.0), "arrival_radius_m", minimum=0.5),
            "update_hz": _number(raw.get("update_hz", 10.0), "update_hz", minimum=0.1, maximum=20.0),
            "face_ship": bool(raw.get("face_ship", False)),
            "loop_count": int(_number(raw.get("loop_count", 1), "loop_count", minimum=1, maximum=1000)),
            "hold_last_waypoint": bool(raw.get("hold_last_waypoint", False)),
        }

    if cmd_type == "search_grid":
        lat, lon = _lat_lon(raw.get("lat"), raw.get("lon"), "")
        return {
            "type": cmd_type,
            "lat": lat,
            "lon": lon,
            "grid_size_m": _number(raw.get("grid_size_m", 200.0), "grid_size_m", minimum=1.0, maximum=10000.0),
            "swath_m": _number(raw.get("swath_m", 20.0), "swath_m", minimum=1.0, maximum=1000.0),
            "altitude_m": _number(raw.get("altitude_m", 30.0), "altitude_m", minimum=0.0, maximum=20000.0),
        }

    if cmd_type == "takeoff":
        return {"type": cmd_type, "altitude_m": _number(raw.get("altitude_m", 15.0), "altitude_m", minimum=1.0, maximum=20000.0)}

    if cmd_type == "set_mode":
        mode = raw.get("mode")
        if not isinstance(mode, str) or not mode.strip() or len(mode) > 32:
            raise CommandValidationError("mode must be a non-empty string of at most 32 characters")
        return {"type": cmd_type, "mode": mode.strip()}

    if cmd_type == "set_velocity":
        speed = _number(raw.get("speed_ms"), "speed_ms", minimum=0.0, maximum=MAX_SPEED_MS)
        heading = _number(raw.get("heading_deg"), "heading_deg") % 360.0
        vertical = _number(raw.get("vertical_ms", 0.0), "vertical_ms", minimum=-MAX_SPEED_MS, maximum=MAX_SPEED_MS)
        return {
            "type": cmd_type,
            "speed_ms": speed,
            "heading_deg": heading,
            "vertical_ms": vertical,
            "velocity_north_ms": speed * math.cos(math.radians(heading)),
            "velocity_east_ms": speed * math.sin(math.radians(heading)),
            "velocity_down_ms": -vertical,
        }

    if cmd_type == "set_heading":
        return {"type": cmd_type, "heading_deg": _number(raw.get("heading_deg"), "heading_deg") % 360.0}

    # rc_override: {"channels": {"1": 1500, ...}}; 0 releases a channel back to the pilot.
    channels = raw.get("channels")
    if not isinstance(channels, dict) or not channels:
        raise CommandValidationError("channels must be a non-empty object mapping channel number to PWM")
    cleaned: dict[str, int] = {}
    for key, value in channels.items():
        try:
            channel = int(key)
        except (TypeError, ValueError):
            raise CommandValidationError(f"invalid channel number: {key!r}") from None
        if not 1 <= channel <= 18:
            raise CommandValidationError(f"channel {channel} out of range 1-18")
        pwm = int(_number(value, f"channel {channel} PWM", minimum=0.0, maximum=2200.0))
        if pwm != 0 and pwm < 800:
            raise CommandValidationError(f"channel {channel} PWM must be 0 (release) or 800-2200")
        cleaned[str(channel)] = pwm
    return {"type": cmd_type, "channels": cleaned}
