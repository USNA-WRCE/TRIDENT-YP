from __future__ import annotations

import asyncio
import json
import math
import os
import random
import socket
import time
from typing import Any

import websockets

from yp_common.telemetry import (
    BEHAVIOR_IDLE,
    BEHAVIOR_RETURN_TO_BOAT,
    BEHAVIOR_SHIP_RELATIVE,
    EVENT_MISSION_COMPLETE,
    behavior_for_command,
    build_event,
    build_telemetry,
)


SERVER_WS_URL = os.getenv("SERVER_WS_URL", "ws://yp-server:8000/ws/vehicle")
VEHICLE_TYPE = os.getenv("VEHICLE_TYPE", "uav").lower()
VEHICLE_ID = os.getenv("VEHICLE_ID") or f"{VEHICLE_TYPE}-{socket.gethostname()[:12]}"
HOME_LAT = float(os.getenv("HOME_LAT", "38.9822"))
HOME_LON = float(os.getenv("HOME_LON", "-76.4819"))
HOME_ALT = float(os.getenv("HOME_ALT", "45.0" if VEHICLE_TYPE == "uav" else "0.0"))
SEND_HZ = float(os.getenv("SEND_HZ", "5"))
SPAWN_JITTER_DEG = float(os.getenv("SPAWN_JITTER_DEG", "0.004"))
SHIP_STATE_TIMEOUT_S = float(os.getenv("SHIP_STATE_TIMEOUT_S", "2.0"))
TARGET_JITTER_DEG = float(os.getenv("TARGET_JITTER_DEG", "0.006"))

SPEED_MPS = {"uav": 9.0, "usv": 2.8, "ugv": 2.0, "uuv": 1.3}.get(VEHICLE_TYPE, 5.0)


class VehicleSim:
    def __init__(self) -> None:
        jitter = random.uniform(-SPAWN_JITTER_DEG, SPAWN_JITTER_DEG)
        self.lat = HOME_LAT + jitter
        self.lon = HOME_LON + random.uniform(-SPAWN_JITTER_DEG, SPAWN_JITTER_DEG)
        self.alt = HOME_ALT
        self.heading = random.uniform(0, 360)
        self.battery = random.uniform(0.72, 1.0)
        self.target = self.random_target()
        self.mode = "loiter"
        self.behavior = BEHAVIOR_IDLE
        self.local_x = 0.0
        self.local_y = 0.0
        self.last_step = time.time()
        self.mission_waypoints: list[dict[str, float]] = []
        self.mission_complete_pending = False
        self.rtb_follow_heading: float | None = None
        self.rtb_follow_speed_mps: float | None = None
        self.ship_states: dict[str, dict[str, float]] = {}
        self.ship_plan: dict[str, Any] | None = None

    def update_ship_state(self, vehicle: dict[str, Any]) -> None:
        position = vehicle.get("position") or {}
        vehicle_id = vehicle.get("vehicle_id")
        lat, lon = position.get("latitude"), position.get("longitude")
        if not vehicle_id or lat is None or lon is None:
            return
        now = time.time()
        previous = self.ship_states.get(vehicle_id)
        vn = ve = 0.0
        if previous:
            dt = now - previous["stamp"]
            if dt > 0.05:
                vn = (float(lat) - previous["lat"]) * 111_320.0 / dt
                ve = (float(lon) - previous["lon"]) * 111_320.0 * math.cos(math.radians(float(lat))) / dt
            else:
                vn, ve = previous["vn"], previous["ve"]
        heading = vehicle.get("heading")
        self.ship_states[vehicle_id] = {
            "lat": float(lat),
            "lon": float(lon),
            "alt": float(position.get("altitude", 0.0)),
            "heading": float(heading) % 360.0 if heading is not None else (previous or {}).get("heading", 0.0),
            "vn": vn,
            "ve": ve,
            "stamp": now,
        }

    def start_ship_relative(self, command_body: dict[str, Any]) -> None:
        ship_id = command_body.get("ship_vehicle_id")
        waypoints = [wp for wp in command_body.get("local_waypoints") or [] if isinstance(wp, dict)]
        if not ship_id or not waypoints:
            return
        loops = max(1, min(100, int(command_body.get("loop_count", 1))))
        self.ship_plan = {
            "ship_id": ship_id,
            "waypoints": waypoints * loops,
            "index": 0,
            "arrival_radius_m": float(command_body.get("arrival_radius_m", 6.0)),
            "hold_last": bool(command_body.get("hold_last_waypoint", False)),
            "face_ship": bool(command_body.get("face_ship", False)),
        }
        self.mission_waypoints = []
        self.mode = "ship_relative"
        self.behavior = BEHAVIOR_SHIP_RELATIVE

    def update_ship_relative(self) -> bool:
        """Retarget the moving ship-relative waypoint; return False while ship data is stale."""
        plan = self.ship_plan
        ship = self.ship_states.get(plan["ship_id"]) if plan else None
        if not plan or not ship or time.time() - ship["stamp"] > SHIP_STATE_TIMEOUT_S:
            return False
        waypoint = plan["waypoints"][plan["index"]]
        local_x = float(waypoint.get("x", 0.0))
        local_y = float(waypoint.get("y", 0.0))
        bearing = (ship["heading"] + math.degrees(math.atan2(local_x, local_y))) % 360.0
        lat, lon = destination_point(ship["lat"], ship["lon"], bearing, math.hypot(local_x, local_y))
        self.target = {"latitude": lat, "longitude": lon, "altitude": ship["alt"] + float(waypoint.get("z", 0.0))}
        ship_speed = math.hypot(ship["vn"], ship["ve"])
        self.rtb_follow_speed_mps = ship_speed
        self.rtb_follow_heading = math.degrees(math.atan2(ship["ve"], ship["vn"])) % 360.0 if ship_speed > 0.05 else ship["heading"]

        if haversine_m(self.lat, self.lon, lat, lon) > plan["arrival_radius_m"]:
            return True
        is_last = plan["index"] >= len(plan["waypoints"]) - 1
        if not is_last:
            plan["index"] += 1
        elif not plan["hold_last"]:
            self.ship_plan = None
            self.mode = "loiter"
            self.behavior = BEHAVIOR_IDLE
            self.rtb_follow_heading = None
            self.rtb_follow_speed_mps = None
            self.target = self.random_target()
            return False
        return True

    def random_target(self) -> dict[str, float]:
        return {
            "latitude": HOME_LAT + random.uniform(-TARGET_JITTER_DEG, TARGET_JITTER_DEG),
            "longitude": HOME_LON + random.uniform(-TARGET_JITTER_DEG, TARGET_JITTER_DEG),
            "altitude": HOME_ALT,
        }

    def handle_command(self, command: dict[str, Any]) -> None:
        command_body = command.get("command", command)
        command_type = command_body.get("type")
        if command_type == "ship_relative_trajectory":
            self.start_ship_relative(command_body)
            return
        if command_type != "rtcm_data":
            self.ship_plan = None
        if command_type == "rtb":
            self.mode = "rtb"
            self.behavior = BEHAVIOR_RETURN_TO_BOAT
            self.mission_waypoints = []
            self.target = {"latitude": HOME_LAT, "longitude": HOME_LON, "altitude": HOME_ALT}
        elif command_type == "rtb_follow":
            self.mode = "rtb_follow"
            self.behavior = behavior_for_command(command_type) or self.behavior
            self.mission_waypoints = []
            target = command_body.get("target", {})
            self.target = {
                "latitude": float(target.get("latitude", self.lat)),
                "longitude": float(target.get("longitude", self.lon)),
                "altitude": float(target.get("altitude", self.alt)),
            }
            self.rtb_follow_heading = float(command_body.get("heading", self.heading)) % 360.0
            self.rtb_follow_speed_mps = max(0.0, float(command_body.get("speed_mps", SPEED_MPS)))
        elif command_type == "land_on_boat_step":
            self.mode = "land_on_boat"
            self.behavior = behavior_for_command(command_type) or self.behavior
            self.mission_waypoints = []
            target = command_body.get("target", {})
            self.target = {
                "latitude": float(target.get("latitude", self.lat)),
                "longitude": float(target.get("longitude", self.lon)),
                "altitude": float(target.get("altitude", self.alt)),
            }
            self.rtb_follow_heading = float(command_body.get("heading", self.heading)) % 360.0
            vn = float(command_body.get("velocity_north_ms", 0.0))
            ve = float(command_body.get("velocity_east_ms", 0.0))
            self.rtb_follow_speed_mps = math.hypot(vn, ve)
        elif command_type == "waypoint":
            self.mode = "waypoint"
            self.behavior = behavior_for_command(command_type, command_body.get("source")) or self.behavior
            self.mission_waypoints = []
            self.rtb_follow_heading = None
            self.rtb_follow_speed_mps = None
            target = command_body.get("target", {})
            self.target = {
                "latitude": float(target.get("latitude", self.lat)),
                "longitude": float(target.get("longitude", self.lon)),
                "altitude": float(target.get("altitude", self.alt)),
            }
        elif command_type == "trajectory":
            self.mode = "trajectory"
            self.behavior = BEHAVIOR_SHIP_RELATIVE
            self.rtb_follow_heading = None
            self.rtb_follow_speed_mps = None
        elif command_type == "cancel_sar":
            self.mission_waypoints = []
            self.mode = "loiter"
            self.behavior = BEHAVIOR_IDLE
            self.target = self.random_target()
        elif command_type in ("search_grid", "mob"):
            self.rtb_follow_heading = None
            self.rtb_follow_speed_mps = None
            # Server embeds pre-computed waypoints as [[lat, lon, alt], ...]
            sim_wps = command_body.get("sim_waypoints", [])
            if sim_wps:
                self.mission_waypoints = [
                    {"latitude": float(wp[0]), "longitude": float(wp[1]), "altitude": float(wp[2])}
                    for wp in sim_wps
                ]
                self.mode = "sar_mission"
                self.behavior = behavior_for_command(command_type) or self.behavior
                self.target = self.mission_waypoints.pop(0)
        elif command_type == "mission_plan":
            self.rtb_follow_heading = None
            self.rtb_follow_speed_mps = None
            mission_wps = command_body.get("waypoints", [])
            parsed = []
            for wp in mission_wps:
                if not isinstance(wp, dict):
                    continue
                lat = wp.get("latitude")
                lon = wp.get("longitude")
                if lat is None or lon is None:
                    continue
                parsed.append(
                    {
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "altitude": float(wp.get("altitude", HOME_ALT)),
                    }
                )
            if parsed:
                self.mission_complete_pending = False
                self.mission_waypoints = parsed
                self.mode = "mission_plan"
                self.behavior = behavior_for_command(command_type) or self.behavior
                self.target = self.mission_waypoints.pop(0)

    def step(self) -> None:
        now = time.time()
        dt = min(0.5, max(0.001, now - self.last_step))
        self.last_step = now

        if self.mode == "ship_relative" and not self.update_ship_relative():
            return

        distance = haversine_m(self.lat, self.lon, self.target["latitude"], self.target["longitude"])
        if self.mode == "ship_relative":
            pass
        elif distance < max(4.0, SPEED_MPS * dt * 2.0):
            if self.mode == "rtb":
                self.mode = "hold"
                self.behavior = BEHAVIOR_IDLE
            elif self.mode in ("rtb_follow", "land_on_boat"):
                pass
            elif self.mode in ("sar_mission", "mission_plan"):
                if self.mission_waypoints:
                    self.target = self.mission_waypoints.pop(0)
                else:
                    if self.mode == "mission_plan":
                        self.mission_complete_pending = True
                    self.mode = "loiter"
                    self.behavior = BEHAVIOR_IDLE
                    self.target = self.random_target()
            else:
                self.target = self.random_target()
                self.mode = "loiter"
                self.behavior = BEHAVIOR_IDLE
            return

        move_bearing: float | None = None
        face_ship = None
        if self.mode == "ship_relative" and self.ship_plan and self.ship_plan["face_ship"]:
            face_ship = self.ship_states.get(self.ship_plan["ship_id"])
        if self.mode in ("rtb_follow", "land_on_boat", "ship_relative") and self.rtb_follow_heading is not None:
            # Station keeping combines the YP velocity with a small position
            # correction. It never caps travel at the moving target, avoiding
            # the overshoot-and-correct oscillation caused by point chasing.
            target_heading = self.rtb_follow_heading
            target_speed = self.rtb_follow_speed_mps or 0.0
            target_bearing = bearing_deg(self.lat, self.lon, self.target["latitude"], self.target["longitude"])
            correction_speed = min(2.0, distance * 0.12)
            correction_bearing = target_bearing
            correction_north = math.cos(math.radians(correction_bearing)) * correction_speed
            correction_east = math.sin(math.radians(correction_bearing)) * correction_speed
            base_north = target_speed * math.cos(math.radians(target_heading))
            base_east = target_speed * math.sin(math.radians(target_heading))
            desired_north = base_north + correction_north
            desired_east = base_east + correction_east
            desired_speed = math.hypot(desired_north, desired_east)
            if desired_speed > 0.01:
                desired_heading = math.degrees(math.atan2(desired_east, desired_north)) % 360.0
                if face_ship is not None:
                    # Yaw is independent of the direction of travel, as with the real bridges.
                    move_bearing = desired_heading
                    self.heading = smooth_angle(
                        self.heading,
                        bearing_deg(self.lat, self.lon, face_ship["lat"], face_ship["lon"]),
                        min(1.0, dt * 2.5),
                    )
                else:
                    self.heading = smooth_angle(self.heading, desired_heading, min(1.0, dt * 2.5))
                    move_bearing = self.heading
            travel = min(desired_speed, SPEED_MPS * 1.5) * dt
        else:
            bearing = bearing_deg(self.lat, self.lon, self.target["latitude"], self.target["longitude"])
            self.heading = smooth_angle(self.heading, bearing, min(1.0, dt * 1.8))
            move_bearing = self.heading
            travel = min(distance, SPEED_MPS * dt)
        if move_bearing is None:
            move_bearing = self.heading
        self.lat, self.lon = destination_point(self.lat, self.lon, move_bearing, travel)

        desired_alt = self.target.get("altitude", HOME_ALT)
        self.alt += max(-1.0, min(1.0, desired_alt - self.alt)) * min(1.0, dt)
        if VEHICLE_TYPE == "usv":
            self.alt = 0.0
        elif VEHICLE_TYPE == "uuv":
            self.alt = min(-1.0, self.alt)

        self.local_x += math.sin(math.radians(move_bearing)) * travel
        self.local_y += math.cos(math.radians(move_bearing)) * travel
        self.battery = max(0.0, self.battery - dt * 0.000035)

    def messages(self) -> list[dict[str, Any]]:
        messages = [
            build_telemetry(
                VEHICLE_ID,
                VEHICLE_TYPE,
                latitude=self.lat,
                longitude=self.lon,
                altitude=self.alt,
                heading=self.heading,
                behavior=self.behavior,
                mode=self.mode,
                armed=True,
                simulated=True,
                battery={"percentage": self.battery, "voltage": 22.2 * self.battery, "current": -4.0},
            )
        ]
        if self.mission_complete_pending:
            self.mission_complete_pending = False
            messages.append(build_event(VEHICLE_ID, VEHICLE_TYPE, EVENT_MISSION_COMPLETE))
        return messages


async def main() -> None:
    sim = VehicleSim()
    uri = f"{SERVER_WS_URL.rstrip('/')}/{VEHICLE_ID}"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=30, ping_timeout=20) as ws:
                print(f"{VEHICLE_ID} connected to {uri}")
                sim.last_step = time.time()
                receiver = asyncio.create_task(receive_commands(ws, sim))
                ship_listener = asyncio.create_task(ship_state_listener(sim))
                try:
                    while True:
                        sim.step()
                        for msg in sim.messages():
                            await ws.send(json.dumps(msg))
                        await asyncio.sleep(1.0 / SEND_HZ)
                finally:
                    receiver.cancel()
                    ship_listener.cancel()
        except Exception as exc:
            print(f"{VEHICLE_ID} reconnecting after error: {exc}")
            await asyncio.sleep(2.0)


async def ship_state_listener(sim: VehicleSim) -> None:
    """Track every vehicle's position via the server's read-only state feed, for ship-relative missions."""
    base = SERVER_WS_URL.rstrip("/")
    marker = "/ws/vehicle"
    if marker not in base:
        return
    uri = f"{base.split(marker, 1)[0]}/ws/ship_state"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=10, ping_timeout=10, max_size=None) as ws:
                async for raw in ws:
                    message = json.loads(raw)
                    if message.get("op") == "snapshot":
                        for vehicle in message.get("vehicles", []):
                            sim.update_ship_state(vehicle)
                    elif message.get("op") == "vehicle_update":
                        sim.update_ship_state(message.get("vehicle") or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"{VEHICLE_ID} ship-state listener error: {exc}")
            await asyncio.sleep(1.0)


async def receive_commands(ws: websockets.WebSocketClientProtocol, sim: VehicleSim) -> None:
    async for raw in ws:
        try:
            sim.handle_command(json.loads(raw))
        except Exception as exc:
            print(f"command parse failed: {exc}")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination_point(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    radius = 6_371_000.0
    brng = math.radians(bearing)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    dr = distance_m / radius
    p2 = math.asin(math.sin(p1) * math.cos(dr) + math.cos(p1) * math.sin(dr) * math.cos(brng))
    l2 = l1 + math.atan2(math.sin(brng) * math.sin(dr) * math.cos(p1), math.cos(dr) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def smooth_angle(current: float, target: float, amount: float) -> float:
    delta = (target - current + 540) % 360 - 180
    return (current + delta * amount) % 360


if __name__ == "__main__":
    asyncio.run(main())
