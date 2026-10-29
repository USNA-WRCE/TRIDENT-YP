"""Small geodesy helpers for writing feedback loops against vehicle state."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_378_137.0


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2 in degrees clockwise from true north, 0-360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0


def offset_latlon(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Point reached by moving north_m / east_m meters from (lat, lon) (flat-earth, fine below ~10 km)."""
    return (
        lat + math.degrees(north_m / EARTH_RADIUS_M),
        lon + math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat)))),
    )


def angle_diff_deg(target: float, current: float) -> float:
    """Signed shortest rotation (-180, 180] from current to target heading."""
    diff = (target - current + 180.0) % 360.0 - 180.0
    return 180.0 if diff == -180.0 else diff
