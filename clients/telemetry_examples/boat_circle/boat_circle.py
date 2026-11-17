"""Publish simulated boat telemetry that traces a 10-meter-radius circle.

This module connects to a telemetry WebSocket server and continuously
streams simulated Unmanned Surface Vehicle (USV) GPS coordinates and
heading data along a clockwise circular path.

Usage:
    Run with default parameters:
        $ python boat_circle.py

    Run with custom vehicle ID, server URL, and center coordinates:
        $ python boat_circle.py --vehicle-id boat-02 \\
            --url ws://localhost:8000/ws/vehicle \\
            --latitude 38.988610 --longitude -76.485153
"""

import argparse
import asyncio
import json
import math
import time

import websockets

# Simulation physical and geographic constants.
RADIUS_M = 10.0  # Radius of the circular trajectory in meters.
SPEED_MPS = 1.0  # Tangential vessel speed in meters per second.
SEND_INTERVAL_S = 0.2  # Telemetry broadcast interval (5 Hz).
METERS_PER_DEGREE = 111_320.0  # Approximate meters per degree of latitude.


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the telemetry publisher.

    Returns:
        argparse.Namespace: Parsed CLI options containing vehicle ID,
            WebSocket endpoint URL, and circle center coordinates.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vehicle-id",
        default="boat-student-01",
        help="unique identifier for the simulated vehicle",
    )
    parser.add_argument(
        "--url",
        default="ws://localhost:8000/ws/vehicle",
        help="base WebSocket URL for the telemetry server",
    )
    parser.add_argument(
        "--latitude",
        type=float,
        default=38.9822,
        help="circle center latitude in decimal degrees",
    )
    parser.add_argument(
        "--longitude",
        type=float,
        default=-76.4819,
        help="circle center longitude in decimal degrees",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    """Connect to the WebSocket server and stream circular telemetry.

    Computes real-time position offsets using polar-to-Cartesian
    conversion and applies local tangent-plane scaling to convert
    meter offsets into WGS84 latitude/longitude coordinates.

    Args:
        args: Parsed command-line arguments specifying connection
            details and the geographic center of the orbit.
    """
    # Normalize trailing slashes before appending the vehicle identifier.
    uri = f"{args.url.rstrip('/')}/{args.vehicle_id}"

    # Adjust meters-per-degree longitude based on the cosine of latitude
    # to account for meridian convergence toward the poles.
    longitude_scale = METERS_PER_DEGREE * math.cos(math.radians(args.latitude))

    # Use monotonic clock to prevent system clock jumps from distorting
    # angular velocity calculations.
    started = time.monotonic()

    async with websockets.connect(uri) as websocket:
        print(f"Publishing {args.vehicle_id} to {uri}; press Ctrl+C to stop.")

        while True:
            elapsed = time.monotonic() - started

            # Negative angular velocity produces clockwise motion around
            # the origin when 0 rad points East (standard unit circle).
            angle = -(SPEED_MPS / RADIUS_M) * elapsed
            angle_deg = math.degrees(angle)

            # Convert polar coordinates (radius, angle) to local ENU
            # (East-North-Up) meter offsets from the center point.
            east_m = RADIUS_M * math.cos(angle)
            north_m = RADIUS_M * math.sin(angle)

            # Apply local flat-earth approximation to update coordinates.
            latitude = args.latitude + north_m / METERS_PER_DEGREE
            longitude = args.longitude + east_m / longitude_scale

            # Convert mathematical unit-circle angle (0° East, CCW) to
            # compass bearing (0° North, CW) tangent to clockwise motion:
            # tangent_math = angle - 90°; compass = (90° - tangent_math).
            heading = (180.0 - angle_deg) % 360.0

            telemetry = {
                "op": "telemetry",
                "vehicle_id": args.vehicle_id,
                "vehicle_type": "usv",
                "stamp": time.time(),
                "position": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": 0.0,
                },
                "heading": heading,
                "behavior": "circle",
                "mode": "SIMULATED",
                "armed": False,
                "simulated": True,
            }

            await websocket.send(json.dumps(telemetry))
            await asyncio.sleep(SEND_INTERVAL_S)


if __name__ == "__main__":
    # Gracefully handle manual termination without printing a traceback.
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        print("\nTelemetry stopped.")