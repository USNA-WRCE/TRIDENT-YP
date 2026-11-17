"""Publish simulated boat telemetry from an IDE such as VS Code or Thonny.

Edit the configuration values below, then run this file. Stop with Ctrl+C.
"""

import asyncio
import json
import math
import time

import websockets

# Change these values for your server and map.
VEHICLE_NAME = "student-boat-01"
VEHICLE_TYPE = "usv"
# vehicle type options: "uav" (quadrotor), "uavf" (fixed wing), "usv" (surface vessel), "ugv" (ground vehicle), "uuv" (underwater vehicle), "yp" (yard patrol boat)
BASE_LATITUDE = 38.9822
BASE_LONGITUDE = -76.4819
SERVER_URL = "ws://localhost:8000/ws/vehicle"

CIRCLE_RADIUS_M = 10.0
SPEED_MPS = 1.0
SEND_INTERVAL_S = 0.2
METERS_PER_DEGREE = 111_320.0


async def main() -> None:
    """Send simulated position and heading telemetry until interrupted."""
    uri = f"{SERVER_URL.rstrip('/')}/{VEHICLE_NAME}"
    longitude_scale = METERS_PER_DEGREE * math.cos(math.radians(BASE_LATITUDE))
    started = time.monotonic()

    async with websockets.connect(uri) as websocket:
        print(f"Publishing {VEHICLE_NAME} to {uri}; stop with Ctrl+C.")
        while True:
            elapsed = time.monotonic() - started
            angle = -(SPEED_MPS / CIRCLE_RADIUS_M) * elapsed
            angle_degrees = math.degrees(angle)
            latitude = BASE_LATITUDE + CIRCLE_RADIUS_M * math.sin(angle) / METERS_PER_DEGREE
            longitude = BASE_LONGITUDE + CIRCLE_RADIUS_M * math.cos(angle) / longitude_scale

            telemetry = {
                "op": "telemetry",
                "vehicle_id": VEHICLE_NAME,
                "vehicle_type": VEHICLE_TYPE,
                "stamp": time.time(),
                "position": {
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": 0.0,
                },
                "heading": (180.0 - angle_degrees) % 360.0,
                "behavior": "circle",
                "mode": "SIMULATED",
                "armed": False,
                "simulated": True,
            }
            await websocket.send(json.dumps(telemetry))
            await asyncio.sleep(SEND_INTERVAL_S)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTelemetry stopped.")
