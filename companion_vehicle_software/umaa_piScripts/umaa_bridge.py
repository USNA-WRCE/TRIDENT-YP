from __future__ import annotations

import asyncio
import json
import os
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
VEHICLE_ID = os.getenv("VEHICLE_ID", "sim-umaa")
VEHICLE_TYPE = os.getenv("VEHICLE_TYPE", "usv")
SEND_HZ = float(os.getenv("SEND_HZ", "5"))
UMAA_BACKEND = os.getenv("UMAA_BACKEND", "loopback").lower()

LOOPBACK_LAT = float(os.getenv("LOOPBACK_LAT", "38.989639"))
LOOPBACK_LON = float(os.getenv("LOOPBACK_LON", "-76.478643"))
LOOPBACK_ALT = float(os.getenv("LOOPBACK_ALT", "0.0"))
LOOPBACK_HEADING = float(os.getenv("LOOPBACK_HEADING", "0.0"))
LOOPBACK_BATTERY = float(os.getenv("LOOPBACK_BATTERY", "1.0"))
LOOPBACK_SPEED_MPS = float(os.getenv("LOOPBACK_SPEED_MPS", "1.5"))
LOOPBACK_TURN_RATE_DPS = float(os.getenv("LOOPBACK_TURN_RATE_DPS", "15.0"))
LOOPBACK_ARRIVAL_RADIUS_M = float(os.getenv("LOOPBACK_ARRIVAL_RADIUS_M", "2.0"))
LOOPBACK_BATTERY_DRAIN_PER_M = float(os.getenv("LOOPBACK_BATTERY_DRAIN_PER_M", "0.00015"))
LOOPBACK_BATTERY_DRAIN_PER_S = float(os.getenv("LOOPBACK_BATTERY_DRAIN_PER_S", "0.00001"))
LOOPBACK_IDLE_BATTERY_DRAIN_PER_S = float(os.getenv("LOOPBACK_IDLE_BATTERY_DRAIN_PER_S", "0.000002"))
EARTH_RADIUS_M = 6_378_137.0

RTI_DOMAIN_ID = int(os.getenv("RTI_DOMAIN_ID", "1"))
RTI_QOS_FILE = os.getenv("RTI_QOS_FILE", "")
RTI_SOURCE_GUID = os.getenv("RTI_SOURCE_GUID", "")
RTI_COMMAND_TOPIC = os.getenv("RTI_COMMAND_TOPIC", "")
RTI_ACK_TOPIC = os.getenv("RTI_ACK_TOPIC", "")
RTI_STATUS_TOPIC = os.getenv("RTI_STATUS_TOPIC", "")
RTI_EXEC_STATUS_TOPIC = os.getenv("RTI_EXEC_STATUS_TOPIC", "")
RTI_NAVSATFIX_TOPIC = os.getenv("RTI_NAVSATFIX_TOPIC", "")
RTI_BATTERY_TOPIC = os.getenv("RTI_BATTERY_TOPIC", "")
RTI_HEARTBEAT_TOPIC = os.getenv("RTI_HEARTBEAT_TOPIC", "")
RTI_PUBLISHER_NAME = os.getenv("RTI_PUBLISHER_NAME", "")
RTI_SUBSCRIBER_NAME = os.getenv("RTI_SUBSCRIBER_NAME", "")

CONFIG_CASTERS: dict[str, Any] = {
    "SERVER_WS_URL": str,
    "VEHICLE_ID": str,
    "VEHICLE_TYPE": str,
    "SEND_HZ": float,
    "UMAA_BACKEND": str,
    "RTI_DOMAIN_ID": int,
    "RTI_QOS_FILE": str,
    "RTI_SOURCE_GUID": str,
    "RTI_COMMAND_TOPIC": str,
    "RTI_ACK_TOPIC": str,
    "RTI_STATUS_TOPIC": str,
    "RTI_EXEC_STATUS_TOPIC": str,
    "RTI_NAVSATFIX_TOPIC": str,
    "RTI_BATTERY_TOPIC": str,
    "RTI_HEARTBEAT_TOPIC": str,
    "RTI_PUBLISHER_NAME": str,
    "RTI_SUBSCRIBER_NAME": str,
}

runtime_status: dict[str, Any] = {
    "status": "starting",
    "adapter_status": "starting",
    "server_connected": False,
    "last_telemetry_at": None,
    "last_command_at": None,
    "last_command_error": None,
    "last_error": None,
}


def current_configuration() -> dict[str, Any]:
    return {name: globals()[name] for name in CONFIG_CASTERS}


def validate_configuration(values: dict[str, Any]) -> dict[str, Any]:
    configuration = current_configuration()
    for name, value in values.items():
        if name not in CONFIG_CASTERS:
            raise ValueError(f"Unsupported configuration field: {name}")
        try:
            configuration[name] = CONFIG_CASTERS[name](value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid value for {name}: {value}") from exc

    configuration["UMAA_BACKEND"] = configuration["UMAA_BACKEND"].lower()
    if configuration["UMAA_BACKEND"] not in {"loopback", "rti", "rticonnext", "dds"}:
        raise ValueError("UMAA_BACKEND must be loopback or rti")
    if not configuration["SERVER_WS_URL"].strip():
        raise ValueError("SERVER_WS_URL is required")
    if not configuration["VEHICLE_ID"].strip():
        raise ValueError("VEHICLE_ID is required")
    if not configuration["VEHICLE_TYPE"].strip():
        raise ValueError("VEHICLE_TYPE is required")
    if not math.isfinite(configuration["SEND_HZ"]) or configuration["SEND_HZ"] <= 0:
        raise ValueError("SEND_HZ must be a finite number greater than zero")
    if configuration["RTI_DOMAIN_ID"] < 0:
        raise ValueError("RTI_DOMAIN_ID cannot be negative")
    return configuration


def apply_configuration(values: dict[str, Any]) -> dict[str, Any]:
    configuration = validate_configuration(values)
    globals().update(configuration)
    return configuration


@dataclass(slots=True)
class TelemetryEvent:
    vehicle_id: str
    vehicle_type: str
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    heading: float | None = None
    behavior: str | None = None
    mode: str | None = None
    armed: bool | None = None
    battery: dict[str, Any] | None = None
    event: str | None = None  # set for one-shot events such as mission_complete
    stamp: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        if self.event:
            return build_event(self.vehicle_id, self.vehicle_type, self.event, stamp=self.stamp)
        return build_telemetry(
            self.vehicle_id,
            self.vehicle_type,
            latitude=self.latitude,
            longitude=self.longitude,
            altitude=self.altitude,
            heading=self.heading,
            behavior=self.behavior,
            mode=self.mode,
            armed=self.armed,
            battery=self.battery,
            stamp=self.stamp,
        )


class UmaaAdapter(ABC):
    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def drain_events(self) -> list[TelemetryEvent]:
        raise NotImplementedError

    @abstractmethod
    async def send_command(self, command: dict[str, Any], source: str | None = None) -> None:
        raise NotImplementedError


class LoopbackUmaaAdapter(UmaaAdapter):
    def __init__(self) -> None:
        self._running = False
        self._events: asyncio.Queue[TelemetryEvent] = asyncio.Queue()
        self._lat = LOOPBACK_LAT
        self._lon = LOOPBACK_LON
        self._alt = LOOPBACK_ALT
        self._heading = LOOPBACK_HEADING % 360.0
        self._battery = max(0.0, min(1.0, LOOPBACK_BATTERY))
        self._last_tick = time.time()
        self._target: dict[str, float] | None = None
        self._mission_queue: list[dict[str, float]] = []
        self._mode = "idle"
        self._behavior = BEHAVIOR_IDLE
        self._mission_active = False

    async def start(self) -> None:
        self._running = True
        self._last_tick = time.time()

    async def stop(self) -> None:
        self._running = False

    @staticmethod
    def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlat = lat2_rad - lat1_rad
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2.0) ** 2
        return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))

    @staticmethod
    def _destination_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        bearing_rad = math.radians(bearing_deg)
        angular = distance_m / EARTH_RADIUS_M
        lat2 = math.asin(
            math.sin(lat_rad) * math.cos(angular)
            + math.cos(lat_rad) * math.sin(angular) * math.cos(bearing_rad)
        )
        lon2 = lon_rad + math.atan2(
            math.sin(bearing_rad) * math.sin(angular) * math.cos(lat_rad),
            math.cos(angular) - math.sin(lat_rad) * math.sin(lat2),
        )
        return math.degrees(lat2), math.degrees(lon2)

    def _advance_state(self) -> None:
        now = time.time()
        dt = max(0.0, min(1.0, now - self._last_tick))
        self._last_tick = now

        if dt <= 0.0:
            return

        moved_m = 0.0
        if self._target is not None:
            target_lat = self._target["latitude"]
            target_lon = self._target["longitude"]
            target_alt = self._target["altitude"]
            distance_m = self._distance_m(self._lat, self._lon, target_lat, target_lon)
            if distance_m <= LOOPBACK_ARRIVAL_RADIUS_M:
                self._lat = target_lat
                self._lon = target_lon
                self._alt = target_alt
                if self._mission_queue:
                    self._target = self._mission_queue.pop(0)
                    self._mode = "mission"
                else:
                    self._mode = "holding"
                    self._behavior = BEHAVIOR_IDLE
                    self._target = None
                    if self._mission_active:
                        self._mission_active = False
                        self._events.put_nowait(TelemetryEvent(VEHICLE_ID, VEHICLE_TYPE, event=EVENT_MISSION_COMPLETE))
            else:
                step_m = min(distance_m, LOOPBACK_SPEED_MPS * dt)
                bearing_deg = math.degrees(math.atan2(
                    math.sin(math.radians(target_lon - self._lon)) * math.cos(math.radians(target_lat)),
                    math.cos(math.radians(self._lat)) * math.sin(math.radians(target_lat))
                    - math.sin(math.radians(self._lat)) * math.cos(math.radians(target_lat)) * math.cos(math.radians(target_lon - self._lon)),
                ))
                if math.isnan(bearing_deg):
                    bearing_deg = self._heading
                self._heading = self._turn_toward(self._heading, bearing_deg, LOOPBACK_TURN_RATE_DPS * dt)
                self._lat, self._lon = self._destination_point(self._lat, self._lon, self._heading, step_m)
                self._alt += (target_alt - self._alt) * min(1.0, dt * 0.8)
                self._mode = "moving"
                moved_m = step_m
        else:
            self._mode = "holding" if self._mode == "moving" else self._mode

        drain = LOOPBACK_IDLE_BATTERY_DRAIN_PER_S * dt + LOOPBACK_BATTERY_DRAIN_PER_M * moved_m + LOOPBACK_BATTERY_DRAIN_PER_S * dt * max(0.0, LOOPBACK_SPEED_MPS)
        self._battery = max(0.0, self._battery - drain)

    @staticmethod
    def _turn_toward(current_deg: float, target_deg: float, max_delta_deg: float) -> float:
        delta = ((target_deg - current_deg + 540.0) % 360.0) - 180.0
        if abs(delta) <= max_delta_deg:
            return target_deg % 360.0
        return (current_deg + math.copysign(max_delta_deg, delta)) % 360.0

    async def drain_events(self) -> list[TelemetryEvent]:
        self._advance_state()
        events: list[TelemetryEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                break

        if self._running and not events:
            await self._queue_telemetry()
            while True:
                try:
                    events.append(self._events.get_nowait())
                except asyncio.QueueEmpty:
                    break

        return events

    async def send_command(self, command: dict[str, Any], source: str | None = None) -> None:
        cmd_type = str(command.get("type") or "")
        if cmd_type in {"waypoint", "rtb_follow"}:
            target = command.get("target") or {}
            self._mission_queue = []
            self._target = {
                "latitude": float(target.get("latitude", LOOPBACK_LAT)),
                "longitude": float(target.get("longitude", LOOPBACK_LON)),
                "altitude": float(target.get("altitude", LOOPBACK_ALT)),
            }
            self._mode = "guiding"
            self._mission_active = False
            self._behavior = behavior_for_command(cmd_type, source) or BEHAVIOR_IDLE
        elif cmd_type == "rtb":
            self._mission_queue = []
            self._target = {
                "latitude": LOOPBACK_LAT,
                "longitude": LOOPBACK_LON,
                "altitude": LOOPBACK_ALT,
            }
            self._mode = "returning"
            self._mission_active = False
            self._behavior = BEHAVIOR_RETURN_TO_BOAT
        elif cmd_type == "cancel_sar":
            self._mission_queue = []
            self._target = None
            self._mode = "holding"
            self._mission_active = False
            self._behavior = BEHAVIOR_IDLE
        elif cmd_type == "mission_plan":
            waypoints = command.get("waypoints") or []
            parsed: list[dict[str, float]] = []
            for waypoint in waypoints:
                if not isinstance(waypoint, dict):
                    continue
                lat = waypoint.get("latitude")
                lon = waypoint.get("longitude")
                if lat is None or lon is None:
                    continue
                parsed.append(
                    {
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "altitude": float(waypoint.get("altitude", LOOPBACK_ALT)),
                    }
                )
            if parsed:
                self._target = parsed[0]
                self._mission_queue = parsed[1:]
                self._mode = "mission"
                self._mission_active = True
                self._behavior = behavior_for_command(cmd_type) or BEHAVIOR_IDLE
        elif cmd_type in {"search_grid", "mob", "trajectory", "ship_relative_trajectory"}:
            self._mission_queue = []
            self._target = None
            self._mode = cmd_type
            self._mission_active = False
            self._behavior = behavior_for_command(cmd_type) or BEHAVIOR_SHIP_RELATIVE
        elif cmd_type == "set_mode":
            mode = command.get("mode")
            if mode:
                # For UMAA bridge, map mode strings to internal mode states if needed
                # For now, just log the request
                print(f"[LOOPBACK] set_mode requested: {mode}")
        print(f"[LOOPBACK] command source={source} payload={command}")

    async def _queue_telemetry(self) -> None:
        await self._events.put(
            TelemetryEvent(
                vehicle_id=VEHICLE_ID,
                vehicle_type=VEHICLE_TYPE,
                latitude=self._lat,
                longitude=self._lon,
                altitude=self._alt,
                heading=self._heading,
                behavior=self._behavior,
                mode=self._mode,
                armed=self._target is not None,
                battery={"percentage": self._battery, "voltage": 24.0, "current": 0.0},
            )
        )


class RtiConnextUmaaAdapter(UmaaAdapter):
    def __init__(self) -> None:
        try:
            import rti.connextdds  # noqa: F401
        except ImportError as exc:
            raise SystemExit(
                "rti.connextdds is required for UMAA DDS bridging. Install the RTI Connext Python SDK and source its environment before starting this bridge."
            ) from exc

        missing = [
            name
            for name, value in {
                "RTI_COMMAND_TOPIC": RTI_COMMAND_TOPIC,
                "RTI_ACK_TOPIC": RTI_ACK_TOPIC,
                "RTI_STATUS_TOPIC": RTI_STATUS_TOPIC,
                "RTI_NAVSATFIX_TOPIC": RTI_NAVSATFIX_TOPIC,
                "RTI_BATTERY_TOPIC": RTI_BATTERY_TOPIC,
                "RTI_HEARTBEAT_TOPIC": RTI_HEARTBEAT_TOPIC,
            }.items()
            if not value.strip()
        ]
        if missing:
            raise SystemExit(
                "RTI backend is configured, but these environment variables are still unset: "
                + ", ".join(missing)
                + ". Set the UMAA topic names for your vehicle profile before using UMAA_BACKEND=rti."
            )

        self._events: asyncio.Queue[TelemetryEvent] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._running = False
        self._topic_map = {
            "command": RTI_COMMAND_TOPIC,
            "ack": RTI_ACK_TOPIC,
            "status": RTI_STATUS_TOPIC,
            "navsatfix": RTI_NAVSATFIX_TOPIC,
            "battery": RTI_BATTERY_TOPIC,
            "heartbeat": RTI_HEARTBEAT_TOPIC,
        }

    async def start(self) -> None:
        raise NotImplementedError(
            "RTI DDS participant/readers/writers are not implemented. Replace this placeholder with "
            "the vehicle profile's generated UMAA type support, QoS loading, and configured topic bindings."
        )

    async def stop(self) -> None:
        self._running = False
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def drain_events(self) -> list[TelemetryEvent]:
        events: list[TelemetryEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def send_command(self, command: dict[str, Any], source: str | None = None) -> None:
        raise NotImplementedError(
            "YP-to-UMAA command mapping is not implemented. Map command.type and its target/waypoints "
            "to the vehicle's generated command type, publish it on the command topic, and correlate its acknowledgement."
        )

    async def _reader_loop(self) -> None:
        raise NotImplementedError(
            "UMAA telemetry readers are not implemented. Deserialize the vehicle's status, navigation, "
            "battery, and heartbeat reports and enqueue mapped TelemetryEvent instances."
        )


def build_adapter() -> UmaaAdapter:
    if UMAA_BACKEND in {"rti", "rticonnext", "dds"}:
        return RtiConnextUmaaAdapter()
    return LoopbackUmaaAdapter()


async def send_vehicle_payload(ws: websockets.WebSocketClientProtocol, event: TelemetryEvent) -> None:
    await ws.send(json.dumps(event.to_payload()))


async def run_bridge() -> None:
    adapter: UmaaAdapter | None = None
    runtime_status.update(
        status="starting",
        adapter_status="starting",
        server_connected=False,
        last_command_error=None,
        last_error=None,
    )
    try:
        adapter = build_adapter()
        await adapter.start()
        runtime_status["adapter_status"] = "ready"
        runtime_status["status"] = "connecting"

        uri = f"{SERVER_WS_URL.rstrip('/')}/{VEHICLE_ID}"
        print(f"[INFO] UMAA bridge backend={UMAA_BACKEND} vehicle={VEHICLE_ID}")
        print(f"[INFO] WebSocket URL: {uri}")

        while True:
            try:
                async with websockets.connect(uri, ping_interval=10, ping_timeout=10) as ws:
                    print(f"[INFO] Connected to YP server as {VEHICLE_ID}")
                    runtime_status.update(status="connected", server_connected=True, last_error=None)
                    while True:
                        for event in await adapter.drain_events():
                            await send_vehicle_payload(ws, event)
                            runtime_status["last_telemetry_at"] = time.time()

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0 / max(SEND_HZ, 1.0))
                        except asyncio.TimeoutError:
                            continue

                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        command = payload.get("command", payload)
                        if not isinstance(command, dict):
                            continue

                        try:
                            await adapter.send_command(command, source=str(payload.get("source") or "ui"))
                        except NotImplementedError as exc:
                            runtime_status["last_command_error"] = str(exc)
                            print(f"[ERROR] UMAA command not handled: {exc}")
                            continue
                        runtime_status["last_command_at"] = time.time()
            except Exception as exc:
                runtime_status.update(status="reconnecting", server_connected=False, last_error=str(exc))
                print(f"[WARN] UMAA bridge reconnecting after error: {exc}")
                await asyncio.sleep(2.0)
    finally:
        runtime_status.update(status="stopped", adapter_status="stopped", server_connected=False)
        if adapter is not None:
            await adapter.stop()


if __name__ == "__main__":
    asyncio.run(run_bridge())