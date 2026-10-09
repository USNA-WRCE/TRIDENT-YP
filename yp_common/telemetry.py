"""Telemetry and event message builders shared by every vehicle bridge.

Wire format (vehicle -> server, JSON over /ws/vehicle/{vehicle_id}):

    {"op": "telemetry", "vehicle_id", "vehicle_type", "stamp",
     "position": {"latitude", "longitude", "altitude"},   # optional
     "heading": deg,                                        # optional
     "behavior": str,                                       # what the vehicle is doing
     "mode": str, "armed": bool,                            # optional
     "battery": {"percentage", "voltage", "current"},       # optional, percentage 0..1
     "gps": {"fix_type", "fix_type_label", "satellites", "h_acc_m", "v_acc_m"}}  # optional

    {"op": "event", "vehicle_id", "vehicle_type", "stamp", "event": "mission_complete" | "land_on_boat_touchdown"}

Optional blocks are omitted when unknown; the server merges partial messages.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

BEHAVIOR_IDLE = "idle"
BEHAVIOR_WAYPOINT = "waypoint"
BEHAVIOR_SEARCH_GRID = "search_grid"
BEHAVIOR_MOB_SEARCH = "mob_search"
BEHAVIOR_RETURN_TO_BOAT = "return_to_boat"
BEHAVIOR_LANDING = "landing"
BEHAVIOR_SHIP_RELATIVE = "ship_relative_mission"
BEHAVIOR_ABSOLUTE_MISSION = "absolute_mission"
BEHAVIOR_TAKEOFF = "takeoff"
BEHAVIOR_MANUAL = "manual"

EVENT_MISSION_COMPLETE = "mission_complete"
EVENT_LAND_ON_BOAT_TOUCHDOWN = "land_on_boat_touchdown"

# Search-style behaviors that the UI can cancel with "End SAR Mission".
SAR_BEHAVIORS = frozenset({BEHAVIOR_SEARCH_GRID, BEHAVIOR_MOB_SEARCH})

GPS_FIX_LABELS = {0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed", 7: "Static", 8: "PPP"}


def behavior_for_command(command_type: Optional[str], source: Optional[str] = None) -> Optional[str]:
    """Behavior a vehicle enters when it accepts `command_type`; None if the command doesn't change it."""
    if command_type == "waypoint":
        return BEHAVIOR_RETURN_TO_BOAT if source == "rtb_follow" else BEHAVIOR_WAYPOINT
    return {
        "rtb_follow": BEHAVIOR_RETURN_TO_BOAT,
        "land_on_boat_step": BEHAVIOR_LANDING,
        "search_grid": BEHAVIOR_SEARCH_GRID,
        "mob": BEHAVIOR_MOB_SEARCH,
        "ship_relative_trajectory": BEHAVIOR_SHIP_RELATIVE,
        "mission_plan": BEHAVIOR_ABSOLUTE_MISSION,
        "takeoff": BEHAVIOR_TAKEOFF,
        "cancel_sar": BEHAVIOR_IDLE,
        "disarm": BEHAVIOR_IDLE,
    }.get(command_type or "")


def build_telemetry(
    vehicle_id: str,
    vehicle_type: str,
    *,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    altitude: Optional[float] = None,
    heading: Optional[float] = None,
    behavior: Optional[str] = None,
    mode: Optional[str] = None,
    armed: Optional[bool] = None,
    battery: Optional[dict[str, Any]] = None,
    gps: Optional[dict[str, Any]] = None,
    stamp: Optional[float] = None,
    simulated: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "op": "telemetry",
        "vehicle_id": vehicle_id,
        "vehicle_type": vehicle_type,
        "stamp": time.time() if stamp is None else stamp,
    }
    if simulated:
        payload["simulated"] = True
    if latitude is not None and longitude is not None:
        payload["position"] = {"latitude": latitude, "longitude": longitude, "altitude": 0.0 if altitude is None else altitude}
    if heading is not None:
        payload["heading"] = heading
    if behavior is not None:
        payload["behavior"] = behavior
    if mode is not None:
        payload["mode"] = mode
    if armed is not None:
        payload["armed"] = armed
    if battery:
        payload["battery"] = battery
    if gps:
        payload["gps"] = gps
    return payload


class BehaviorTracker:
    """Thread-safe holder for a bridge's current behavior.

    Bridges call `set()` when they accept a behavior-changing command, `finish()` when its worker
    ends, and `observe_mode()` on each heartbeat so a pilot taking over shows up as `manual`.
    """

    # Behaviors that only make sense while the autopilot is in its guided/offboard mode.
    GUIDED_BEHAVIORS = frozenset({
        BEHAVIOR_WAYPOINT, BEHAVIOR_SEARCH_GRID, BEHAVIOR_MOB_SEARCH, BEHAVIOR_RETURN_TO_BOAT,
        BEHAVIOR_LANDING, BEHAVIOR_SHIP_RELATIVE, BEHAVIOR_TAKEOFF,
    })

    def __init__(self, on_change: Optional[Callable[[str], None]] = None, mode_grace_s: float = 3.0) -> None:
        self._lock = threading.Lock()
        self._behavior = BEHAVIOR_IDLE
        self._since = 0.0
        self._auto_seen = False
        self._on_change = on_change
        self._mode_grace_s = mode_grace_s

    def get(self) -> str:
        with self._lock:
            return self._behavior

    def set(self, behavior: Optional[str]) -> None:
        if not behavior:
            return
        self._transition(lambda current: behavior)

    def finish(self, expected: str) -> None:
        """Return to idle only if `expected` is still the active behavior (a newer command wins)."""
        self._transition(lambda current: BEHAVIOR_IDLE if current == expected else current)

    def observe_mode(self, mode: Optional[str], guided_modes: tuple[str, ...] = ("GUIDED",), auto_modes: tuple[str, ...] = ("AUTO",)) -> None:
        if not mode:
            return
        mode = str(mode).upper()

        def next_behavior(current: str) -> str:
            if current == BEHAVIOR_ABSOLUTE_MISSION:
                if mode in auto_modes:
                    self._auto_seen = True
                elif self._auto_seen:
                    return BEHAVIOR_IDLE  # autopilot left AUTO: mission finished or aborted
            elif current in self.GUIDED_BEHAVIORS and mode not in guided_modes and time.monotonic() - self._since > self._mode_grace_s:
                return BEHAVIOR_MANUAL
            return current

        self._transition(next_behavior)

    def _transition(self, resolve: Callable[[str], str]) -> None:
        with self._lock:
            previous = self._behavior
            resolved = resolve(previous)
            if resolved == previous:
                return
            self._behavior = resolved
            self._since = time.monotonic()
            self._auto_seen = False
        if self._on_change:
            self._on_change(resolved)


def build_event(vehicle_id: str, vehicle_type: str, event: str, stamp: Optional[float] = None) -> dict[str, Any]:
    return {
        "op": "event",
        "vehicle_id": vehicle_id,
        "vehicle_type": vehicle_type,
        "stamp": time.time() if stamp is None else stamp,
        "event": event,
    }


def gps_block_from_mavlink(msg: Any) -> dict[str, Any]:
    """Convert a GPS_RAW_INT / GPS2_RAW message into the telemetry `gps` block."""
    fix_type = int(getattr(msg, "fix_type", 0) or 0)
    eph = getattr(msg, "eph", 65535)
    epv = getattr(msg, "epv", 65535)
    h_acc = getattr(msg, "h_acc", None)
    v_acc = getattr(msg, "v_acc", None)
    satellites = getattr(msg, "satellites_visible", 255)
    return {
        "fix_type": fix_type,
        "fix_type_label": GPS_FIX_LABELS.get(fix_type, f"Fix {fix_type}"),
        "satellites": satellites if satellites != 255 else None,
        "h_acc_m": (h_acc / 1000.0) if h_acc else ((eph / 100.0) if eph != 65535 else None),
        "v_acc_m": (v_acc / 1000.0) if v_acc else ((epv / 100.0) if epv != 65535 else None),
    }
