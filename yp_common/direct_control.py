"""MAVLink senders for the direct-control commands: set_velocity, set_heading and rc_override."""
from __future__ import annotations

import math
from typing import Any

# SET_POSITION_TARGET_LOCAL_NED type masks (a set bit means "ignore that field").
VELOCITY_AND_YAW_MASK = 0b100111000111  # use vx/vy/vz + yaw
YAW_ONLY_MASK = 0b100111111111  # use yaw only
RC_IGNORE = 65535  # RC_CHANNELS_OVERRIDE: leave the channel untouched
RC_RELEASE = 0  # RC_CHANNELS_OVERRIDE: return the channel to the radio


def send_velocity(master: Any, mavutil: Any, command: dict[str, Any]) -> None:
    """Command a NED velocity and yaw. ArduPilot expires it after ~3 s, so callers must resend."""
    master.mav.set_position_target_local_ned_send(
        0, master.target_system, master.target_component,
        mavutil.mavlink.MAV_FRAME_LOCAL_NED, VELOCITY_AND_YAW_MASK,
        0, 0, 0,
        float(command.get("velocity_north_ms", 0.0)),
        float(command.get("velocity_east_ms", 0.0)),
        float(command.get("velocity_down_ms", 0.0)),
        0, 0, 0,
        math.radians(float(command.get("heading_deg", 0.0))), 0,
    )


def send_heading(master: Any, mavutil: Any, command: dict[str, Any]) -> None:
    """Command a yaw/heading target without changing the position or velocity target."""
    master.mav.set_position_target_local_ned_send(
        0, master.target_system, master.target_component,
        mavutil.mavlink.MAV_FRAME_LOCAL_NED, YAW_ONLY_MASK,
        0, 0, 0, 0, 0, 0, 0, 0, 0,
        math.radians(float(command.get("heading_deg", 0.0))), 0,
    )


def send_rc_override(master: Any, command: dict[str, Any]) -> None:
    """Override RC channels 1-18 (PWM in microseconds). Unlisted channels are left alone; 0 releases one."""
    pwm = [RC_IGNORE] * 18
    for channel, value in (command.get("channels") or {}).items():
        index = int(channel) - 1
        if 0 <= index < 18:
            pwm[index] = int(value)
    if any(value != RC_IGNORE for value in pwm[8:]):
        master.mav.rc_channels_override_send(master.target_system, master.target_component, *pwm)
    else:
        master.mav.rc_channels_override_send(master.target_system, master.target_component, *pwm[:8])
