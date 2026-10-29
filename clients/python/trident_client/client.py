"""Python client for commanding vehicles and reading their state through the TRIDENT server."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Optional, Sequence, Union
from urllib.parse import urlparse, urlunparse

import requests

from .errors import (
    AuthenticationError, InvalidCommand, PermissionDenied, TridentError, VehicleNotConnected, VehicleNotFound,
)

Number = Union[int, float]
AltitudeArg = Union[None, Number, Sequence[Number]]


@dataclass(frozen=True)
class CommandAck:
    """Server acknowledgement that a command was accepted and queued to the vehicle.

    This confirms delivery to the vehicle's bridge, not that the vehicle has finished executing it;
    watch ``state()`` or ``stream()`` for that.
    """

    command_id: str
    vehicle_id: str
    source: str
    issued_by: Optional[str]
    stamp: float
    command: dict[str, Any]
    delivered: bool = True


@dataclass(frozen=True)
class VehicleState:
    """Latest known state of one vehicle.

    Heading and position come from the vehicle's autopilot. Velocity is estimated by the server from
    successive position fixes, so it is noisy at low speed. Roll and pitch are not reported by bridges.
    """

    vehicle_id: str
    vehicle_type: Optional[str]
    connected: bool
    age_s: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]
    altitude: Optional[float]
    heading_deg: Optional[float]
    north_ms: float
    east_ms: float
    speed_ms: float
    course_deg: Optional[float]
    mode: Optional[str]
    armed: Optional[bool]
    behavior: Optional[str]
    battery: Optional[dict[str, Any]]
    overlays: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def position(self) -> Optional[tuple[float, float, float]]:
        """(latitude, longitude, altitude) or None before the first fix."""
        if self.latitude is None or self.longitude is None:
            return None
        return self.latitude, self.longitude, self.altitude or 0.0

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "VehicleState":
        position = data.get("position") or {}
        velocity = data.get("velocity") or {}
        return cls(
            vehicle_id=data["vehicle_id"],
            vehicle_type=data.get("vehicle_type"),
            connected=bool(data.get("connected", True)),
            age_s=data.get("age_s"),
            latitude=position.get("latitude"),
            longitude=position.get("longitude"),
            altitude=position.get("altitude"),
            heading_deg=data.get("heading_deg"),
            north_ms=float(velocity.get("north_ms") or 0.0),
            east_ms=float(velocity.get("east_ms") or 0.0),
            speed_ms=float(velocity.get("speed_ms") or 0.0),
            course_deg=velocity.get("course_deg"),
            mode=data.get("mode"),
            armed=data.get("armed"),
            behavior=data.get("behavior"),
            battery=data.get("battery"),
            overlays=dict(data.get("overlays") or {}),
            raw=dict(data),
        )


def _per_point(values: AltitudeArg, count: int, name: str, default: Optional[Number] = None) -> list[Optional[Number]]:
    """Expand a scalar/None/sequence argument to one value per waypoint."""
    if values is None:
        return [default] * count
    if isinstance(values, (int, float)):
        return [values] * count
    values = list(values)
    if len(values) != count:
        raise ValueError(f"{name} has {len(values)} entries but {count} waypoints were given")
    return values


class TridentClient:
    """Connection to a TRIDENT server. Every command names the target vehicle explicitly.

    ``url`` and ``token`` default to the ``TRIDENT_URL`` and ``TRIDENT_TOKEN`` environment variables.
    """

    def __init__(self, url: Optional[str] = None, token: Optional[str] = None, *, timeout: float = 10.0,
                 verify: Union[bool, str] = True):
        url = url or os.environ.get("TRIDENT_URL")
        token = token or os.environ.get("TRIDENT_TOKEN")
        if not url:
            raise ValueError("server url is required (argument or TRIDENT_URL)")
        if not token:
            raise ValueError("service token is required (argument or TRIDENT_TOKEN)")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._verify = verify
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {token}"
        self._token = token

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "TridentClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- transport -------------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._session.request(
                method, f"{self.url}{path}", timeout=self.timeout, verify=self._verify, **kwargs,
            )
        except requests.RequestException as error:
            raise TridentError(f"cannot reach server: {error}") from error
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code < 400:
            return payload
        message = payload.get("error") or f"HTTP {response.status_code}"
        error_type = {
            400: InvalidCommand, 401: AuthenticationError, 403: PermissionDenied,
            404: VehicleNotFound, 409: VehicleNotConnected,
        }.get(response.status_code, TridentError)
        raise error_type(message, response.status_code, payload)

    # ---- state -----------------------------------------------------------------------------

    def vehicles(self) -> list[VehicleState]:
        """State of every connected vehicle."""
        return [VehicleState.from_json(v) for v in self._request("GET", "/api/fleet/state")["vehicles"]]

    def state(self, vehicle_id: str) -> VehicleState:
        """Latest state of one vehicle, including its active map overlays in ``.overlays``."""
        return VehicleState.from_json(self._request("GET", f"/api/vehicles/{vehicle_id}/state"))

    def stream(self, vehicle_ids: Optional[Sequence[str]] = None) -> Iterator[dict[str, Any]]:
        """Yield server events as they happen: ``snapshot``, ``state``, ``command_ack`` and ``vehicle_removed``.

        Requires the ``websockets`` package (``pip install trident-client[stream]``). ``vehicle_ids`` filters
        the stream; the initial snapshot always lists every vehicle.
        """
        try:
            from websockets.sync.client import connect
        except ImportError as error:
            raise TridentError("streaming requires the 'websockets' package (version 11 or newer)") from error
        import json

        parsed = urlparse(self.url)
        ws_url = urlunparse(parsed._replace(scheme="wss" if parsed.scheme == "https" else "ws", path="/ws/api"))
        with connect(ws_url, additional_headers={"Authorization": f"Bearer {self._token}"}) as socket:
            if vehicle_ids:
                socket.send(json.dumps({"op": "subscribe", "vehicle_ids": list(vehicle_ids)}))
            for message in socket:
                event = json.loads(message)
                if "error" in event:
                    raise AuthenticationError(event["error"])
                yield event

    def stream_states(self, vehicle_ids: Optional[Sequence[str]] = None) -> Iterator[VehicleState]:
        """Yield a ``VehicleState`` every time a (subscribed) vehicle reports."""
        for event in self.stream(vehicle_ids):
            if event.get("op") == "state":
                yield VehicleState.from_json(event["vehicle"])

    # ---- commands --------------------------------------------------------------------------

    def send_command(self, vehicle_id: str, command: Mapping[str, Any]) -> CommandAck:
        """Send a raw command dict (``{"type": ..., ...}``) to a vehicle. Prefer the typed helpers below."""
        body = self._request("POST", f"/api/vehicles/{vehicle_id}/command", json=dict(command))
        ack = body["ack"]
        return CommandAck(
            command_id=ack["command_id"], vehicle_id=ack["vehicle_id"], source=ack["source"],
            issued_by=ack.get("issued_by"), stamp=ack["stamp"], command=ack["command"],
            delivered=bool(body.get("delivered", True)),
        )

    def goto(self, vehicle_id: str, latitude: float, longitude: float, altitude: Optional[float] = None) -> CommandAck:
        """Go to one point. The map shows the waypoint. ``altitude`` defaults to the vehicle's current altitude."""
        target: dict[str, Any] = {"latitude": latitude, "longitude": longitude}
        if altitude is not None:
            target["altitude"] = altitude
        return self.send_command(vehicle_id, {"type": "waypoint", "target": target})

    def rtb(self, vehicle_id: str) -> CommandAck:
        """Return to the mother vessel and follow it."""
        return self.send_command(vehicle_id, {"type": "rtb"})

    def land_on_boat(self, vehicle_id: str) -> CommandAck:
        return self.send_command(vehicle_id, {"type": "land_on_boat"})

    def arm(self, vehicle_id: str) -> CommandAck:
        return self.send_command(vehicle_id, {"type": "arm"})

    def disarm(self, vehicle_id: str) -> CommandAck:
        return self.send_command(vehicle_id, {"type": "disarm"})

    def takeoff(self, vehicle_id: str, altitude_m: float = 15.0) -> CommandAck:
        return self.send_command(vehicle_id, {"type": "takeoff", "altitude_m": altitude_m})

    def set_mode(self, vehicle_id: str, mode: str) -> CommandAck:
        """Set the autopilot mode by name, e.g. ``"GUIDED"`` or ``"HOLD"``."""
        return self.send_command(vehicle_id, {"type": "set_mode", "mode": mode})

    def cancel_search(self, vehicle_id: str) -> CommandAck:
        """Cancel a running search grid / man-overboard pattern."""
        return self.send_command(vehicle_id, {"type": "cancel_sar"})

    def search_grid(self, vehicle_id: str, latitude: float, longitude: float, *, grid_size_m: float = 200.0,
                    swath_m: float = 20.0, altitude_m: float = 30.0) -> CommandAck:
        """Fly a lawnmower search grid centred on a point. The map shows the pattern."""
        return self.send_command(vehicle_id, {
            "type": "search_grid", "lat": latitude, "lon": longitude,
            "grid_size_m": grid_size_m, "swath_m": swath_m, "altitude_m": altitude_m,
        })

    def mission(self, vehicle_id: str, latitudes: Sequence[Number], longitudes: Sequence[Number],
                altitudes: AltitudeArg = None, *, acceptance_radius_m: float = 8.0, hold_time_s: float = 0.0,
                auto_arm_start: bool = True, force_guided_on_complete: bool = False) -> CommandAck:
        """Upload and start an absolute mission from parallel lat/lon/alt arrays. The map shows the route.

        ``altitudes`` may be a sequence, a single value for every point, or None (current altitude).
        """
        if len(latitudes) != len(longitudes):
            raise ValueError("latitudes and longitudes must have the same length")
        count = len(latitudes)
        alts = _per_point(altitudes, count, "altitudes")
        waypoints = []
        for lat, lon, alt in zip(latitudes, longitudes, alts):
            point: dict[str, Any] = {
                "latitude": lat, "longitude": lon,
                "acceptance_radius_m": acceptance_radius_m, "hold_time_s": hold_time_s,
            }
            if alt is not None:
                point["altitude"] = alt
            waypoints.append(point)
        return self.send_command(vehicle_id, {
            "type": "mission_plan", "waypoints": waypoints,
            "auto_arm_start": auto_arm_start, "force_guided_on_complete": force_guided_on_complete,
        })

    def ship_relative_mission(self, vehicle_id: str, ship_id: str, x_m: Sequence[Number], y_m: Sequence[Number],
                              z_m: AltitudeArg = 0.0, yaw_deg: AltitudeArg = None, *, arrival_radius_m: float = 6.0,
                              update_hz: float = 10.0, face_ship: bool = False, loops: int = 1,
                              hold_last_waypoint: bool = False) -> CommandAck:
        """Fly points defined relative to a ship, tracking it as it moves.

        ``x_m`` is meters to the ship's right (starboard), ``y_m`` meters ahead of the ship's bow and
        ``z_m`` meters above the ship. ``yaw_deg`` is an optional heading relative to the ship's heading.
        """
        if len(x_m) != len(y_m):
            raise ValueError("x_m and y_m must have the same length")
        count = len(x_m)
        zs = _per_point(z_m, count, "z_m", 0.0)
        yaws = _per_point(yaw_deg, count, "yaw_deg")
        points = []
        for x, y, z, yaw in zip(x_m, y_m, zs, yaws):
            point: dict[str, Any] = {"x": x, "y": y, "z": z}
            if yaw is not None:
                point["yaw_deg"] = yaw
            points.append(point)
        return self.send_command(vehicle_id, {
            "type": "ship_relative_trajectory", "ship_vehicle_id": ship_id, "local_waypoints": points,
            "arrival_radius_m": arrival_radius_m, "update_hz": update_hz, "face_ship": face_ship,
            "loop_count": loops, "hold_last_waypoint": hold_last_waypoint,
        })

    def set_velocity(self, vehicle_id: str, speed_ms: float, heading_deg: float, vertical_ms: float = 0.0) -> CommandAck:
        """Command a ground speed along a true-north heading (the vehicle also points that way).

        Vehicles must be in a guided mode and the autopilot drops the setpoint after a few seconds,
        so call this repeatedly (a few Hz) from a control loop. ``vertical_ms`` is positive up.
        """
        return self.send_command(vehicle_id, {
            "type": "set_velocity", "speed_ms": speed_ms, "heading_deg": heading_deg, "vertical_ms": vertical_ms,
        })

    def set_heading(self, vehicle_id: str, heading_deg: float) -> CommandAck:
        """Point the vehicle at a true-north heading."""
        return self.send_command(vehicle_id, {"type": "set_heading", "heading_deg": heading_deg})

    def rc_override(self, vehicle_id: str, channels: Mapping[int, int]) -> CommandAck:
        """Override RC channels with PWM values in microseconds, e.g. ``{1: 1500, 3: 1650}``.

        Unlisted channels are untouched and 0 releases a channel to the radio. The autopilot reverts when
        updates stop, so resend continuously. Requires the ``rc_override`` permission.
        """
        return self.send_command(vehicle_id, {"type": "rc_override", "channels": {str(k): int(v) for k, v in channels.items()}})

    def release_rc(self, vehicle_id: str, channels: Sequence[int] = tuple(range(1, 9))) -> CommandAck:
        """Release RC channels (default 1-8) back to the pilot's radio."""
        return self.rc_override(vehicle_id, {channel: 0 for channel in channels})

    def vehicle(self, vehicle_id: str) -> "Vehicle":
        """Handle that binds ``vehicle_id`` so calls read ``boat.goto(lat, lon)``."""
        return Vehicle(self, vehicle_id)


class Vehicle:
    """Convenience wrapper binding one vehicle id to a client; every method forwards to ``TridentClient``."""

    def __init__(self, client: TridentClient, vehicle_id: str):
        self.client = client
        self.vehicle_id = vehicle_id

    def __repr__(self) -> str:
        return f"Vehicle({self.vehicle_id!r})"

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.client, name)
        if name.startswith("_") or not callable(method) or name in ("vehicles", "stream", "stream_states", "vehicle", "close"):
            raise AttributeError(name)

        def bound(*args: Any, **kwargs: Any) -> Any:
            return method(self.vehicle_id, *args, **kwargs)

        bound.__doc__ = method.__doc__
        return bound
