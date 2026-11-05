"""Shared ArduCopter land-on-boat step logic for the vehicle bridges.

ArduCopter's SET_POSITION_TARGET_GLOBAL_INT handler treats ``type_mask & 0b111``
as "position ignored", so X/Y position cannot be used while ignoring only Z.
The only way to command a constant descent rate with no altitude target is a
velocity-only command, so horizontal pad tracking is done here with a P
controller on top of the pad's feed-forward velocity.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Any, Callable, Optional

try:
    from pymavlink import mavutil
except ImportError:  # the server only needs this module when pymavlink is present
    mavutil = None  # type: ignore[assignment]

EARTH_RADIUS_M = 6_378_137.0

# Ignore position (bits 0-2), acceleration (6-8) and yaw rate (11); use velocity + yaw.
VELOCITY_ONLY_TYPE_MASK = 0b100111000111

DEFAULT_HORIZONTAL_GAIN = 1.0           # (m/s) per m of horizontal pad error
DEFAULT_MAX_CORRECTION_MS = 2.0
DEFAULT_TOUCHDOWN_DWELL_S = 1.5
DEFAULT_STALL_TIMEOUT_S = 3.0
DEFAULT_BACKUP_MAX_ALT_M = 2.0
DEFAULT_STALL_FRACTION = 0.3            # measured/commanded descent rate below this = stalled
STALL_WINDOW_S = 2.0


class LandStepController:
    """Streams velocity-only landing commands and decides when to disarm."""

    def __init__(self, log: Callable[[str], None] = print) -> None:
        self._log = log
        self._last_step = 0.0
        self._guided_forced = False
        self._touchdown_since: Optional[float] = None
        self._disarm_sent = False
        self._stall_since: Optional[float] = None
        self._alt_samples: deque[tuple[float, float]] = deque()

    def _reset(self) -> None:
        self._guided_forced = False
        self._touchdown_since = None
        self._disarm_sent = False
        self._stall_since = None
        self._alt_samples.clear()

    def _disarm(self, master) -> None:
        master.mav.command_long_send(
            master.target_system, master.target_component,
            mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 21196, 0, 0, 0, 0, 0,
        )
        self._disarm_sent = True

    def _backup_touchdown(self, now: float, alt_m: Optional[float], sink_ms: float, command: dict[str, Any]) -> bool:
        """True when commanded descent has stalled close to the ground.

        Backup for when landed_state never reports ON_GROUND: the vehicle is
        commanded down but its measured descent rate has collapsed, below the
        altitude ceiling, for stall_timeout_s.
        """
        max_alt_m = float(command.get("touchdown_backup_max_alt_m", DEFAULT_BACKUP_MAX_ALT_M))
        if alt_m is None or sink_ms <= 0.0 or alt_m > max_alt_m:
            self._stall_since = None
            self._alt_samples.clear()
            return False

        self._alt_samples.append((now, alt_m))
        while self._alt_samples and now - self._alt_samples[0][0] > STALL_WINDOW_S:
            self._alt_samples.popleft()
        t0, alt0 = self._alt_samples[0]
        span_s = now - t0
        if span_s < STALL_WINDOW_S * 0.75:
            return False

        descent_rate_ms = (alt0 - alt_m) / span_s
        if descent_rate_ms >= float(command.get("touchdown_stall_fraction", DEFAULT_STALL_FRACTION)) * sink_ms:
            self._stall_since = None
            return False

        if self._stall_since is None:
            self._stall_since = now
        return now - self._stall_since >= float(command.get("touchdown_stall_timeout_s", DEFAULT_STALL_TIMEOUT_S))

    def step(
        self,
        master,
        command: dict[str, Any],
        landed_state: int,
        position: Optional[tuple[float, float, float]],
    ) -> bool:
        """Process one land_on_boat_step. Returns True when this call disarmed.

        ``position`` is the vehicle's fresh (lat, lon, home-relative alt) or None.
        """
        target = command.get("target", {})
        lat, lon = target.get("latitude"), target.get("longitude")
        if lat is None or lon is None:
            return False

        now = time.monotonic()
        # A gap in steps means the sequence just (re)started.
        if now - self._last_step > 1.0:
            self._reset()
        self._last_step = now

        auto_disarm = bool(command.get("auto_disarm", True))
        sink_ms = max(0.0, float(command.get("sink_rate_ms") or 0.0))

        if self._disarm_sent:
            return False

        if landed_state == mavutil.mavlink.MAV_LANDED_STATE_ON_GROUND:
            if self._touchdown_since is None:
                self._touchdown_since = now
            if auto_disarm and now - self._touchdown_since >= float(command.get("touchdown_dwell_s", DEFAULT_TOUCHDOWN_DWELL_S)):
                self._disarm(master)
                self._log("[LAND] Onboard landing detector confirms touchdown; disarmed.")
                return True
            return False
        self._touchdown_since = None

        alt_m = position[2] if position is not None else None
        if auto_disarm and self._backup_touchdown(now, alt_m, sink_ms, command):
            self._disarm(master)
            self._log(f"[LAND] Descent stalled at {alt_m:.2f} m with no landed_state; backup touchdown, disarmed.")
            return True

        try:
            if not self._guided_forced:
                master.set_mode("GUIDED")
                self._guided_forced = True
            elif master.flightmode != "GUIDED":
                self._log("[LAND] Safety pilot has taken control; halting land-on-boat guidance")
                return False
        except Exception as exc:
            self._log(f"[WARN] Could not set GUIDED mode for land-on-boat: {exc}")

        if position is None:
            return False

        # Pad feed-forward velocity plus a P term on the horizontal error.
        lat_f, lon_f = float(lat), float(lon)
        err_n = math.radians(lat_f - position[0]) * EARTH_RADIUS_M
        err_e = math.radians(lon_f - position[1]) * EARTH_RADIUS_M * math.cos(math.radians((lat_f + position[0]) / 2.0))
        gain = float(command.get("horizontal_gain", DEFAULT_HORIZONTAL_GAIN))
        corr_n, corr_e = gain * err_n, gain * err_e
        max_corr = float(command.get("max_correction_ms", DEFAULT_MAX_CORRECTION_MS))
        corr_mag = math.hypot(corr_n, corr_e)
        if corr_mag > max_corr > 0.0:
            corr_n, corr_e = corr_n * max_corr / corr_mag, corr_e * max_corr / corr_mag

        vn = float(command.get("velocity_north_ms") or 0.0) + corr_n
        ve = float(command.get("velocity_east_ms") or 0.0) + corr_e
        yaw_rad = math.radians(float(command.get("heading") or 0.0))

        master.mav.set_position_target_global_int_send(
            0, master.target_system, master.target_component,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            VELOCITY_ONLY_TYPE_MASK,
            0, 0, 0.0,              # position ignored
            vn, ve, sink_ms,        # NED velocity; +down
            0, 0, 0,
            yaw_rad, 0,
        )
        return False
