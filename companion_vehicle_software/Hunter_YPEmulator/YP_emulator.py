import asyncio
import json
import math
import os
import time
from typing import Any

import websockets
from pymavlink import mavutil

from yp_common.telemetry import BEHAVIOR_IDLE, build_telemetry, gps_block_from_mavlink

# Configuration
SERVER_WS_URL = os.getenv("SERVER_WS_URL", "ws://192.168.0.25:8000/ws/vehicle")
VEHICLE_ID = os.getenv("VEHICLE_ID", "yp")
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))
SEND_HZ = float(os.getenv("SEND_HZ", "5"))

MOVING_SPEED_MPS = 0.5

async def send_telemetry(ws: websockets.WebSocketClientProtocol, lat: float, lon: float, alt: float, heading: float, speed: float):
    behavior = "underway" if speed > MOVING_SPEED_MPS else BEHAVIOR_IDLE
    payload = build_telemetry(
        VEHICLE_ID, "yp",
        latitude=lat, longitude=lon, altitude=alt, heading=heading,
        behavior=behavior, mode="ship-gps", armed=True,
    )
    await ws.send(json.dumps(payload))

async def command_loop(ws: websockets.WebSocketClientProtocol, master) -> None:
    # Listen for server commands (e.g. RTCM corrections) and forward them to the Cube
    async for raw in ws:
        try:
            server_msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if server_msg.get("op") != "command" or server_msg.get("vehicle_id") != VEHICLE_ID:
            continue
        command_data = server_msg.get("command", {})
        if command_data.get("type") == "rtcm_data":
            flags = command_data.get("flags", 0)
            data_len = command_data.get("len", 0)
            raw_data = command_data.get("data", [])
            if data_len > 0:
                padded_payload = bytearray(raw_data + [0] * (180 - len(raw_data)))
                try:
                    master.mav.gps_rtcm_data_send(flags, data_len, padded_payload)
                except Exception as e:
                    print(f"[RTCM] MAVLink send error: {e}")

async def mavlink_loop(ws: websockets.WebSocketClientProtocol):
    print(f"Connecting to Cube on {SERIAL_PORT} at {BAUD_RATE} baud...")
    master = mavutil.mavlink_connection(SERIAL_PORT, baud=BAUD_RATE)
    
    master.wait_heartbeat()
    print("Heartbeat received! Requesting GLOBAL_POSITION_INT...")
    
    master.mav.request_data_stream_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_POSITION,
        int(SEND_HZ),
        1
    )
    master.mav.command_long_send(master.target_system, master.target_component, mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT, int(1e6 / 2), 0, 0, 0, 0, 0)
    master.mav.command_long_send(master.target_system, master.target_component, mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, mavutil.mavlink.MAVLINK_MSG_ID_GPS2_RAW, int(1e6 / 2), 0, 0, 0, 0, 0)

    asyncio.create_task(command_loop(ws, master))

    last_send = 0.0
    last_mav_rx = time.time()
    while True:
        msg = master.recv_match(type=["GLOBAL_POSITION_INT", "GPS_RAW_INT", "GPS2_RAW"], blocking=False)
        if msg:
            last_mav_rx = time.time()
            now = time.time()
            if msg.get_type() in ("GPS_RAW_INT", "GPS2_RAW"):
                await ws.send(json.dumps(build_telemetry(VEHICLE_ID, "yp", gps=gps_block_from_mavlink(msg), stamp=now)))
            elif (now - last_send) >= (1.0 / SEND_HZ):
                lat = msg.lat / 1e7
                lon = msg.lon / 1e7
                alt = msg.relative_alt / 1000.0 
                heading = msg.hdg / 100.0 if msg.hdg != 65535 else 0.0
                speed = math.sqrt(msg.vx**2 + msg.vy**2) / 100.0
                
                await send_telemetry(ws, lat, lon, alt, heading, speed)
                last_send = now
        elif time.time() - last_mav_rx > 5.0:
            raise ConnectionError("MAVLink connection lost (no telemetry for 5s)")
        await asyncio.sleep(0.01)

async def main():
    uri = f"{SERVER_WS_URL.rstrip('/')}/{VEHICLE_ID}"
    while True:
        try:
            async with websockets.connect(uri, ping_interval=30, ping_timeout=20) as ws:
                print(f"YP Cube telemetry connected to {uri}")
                await mavlink_loop(ws)
        except Exception as exc:
            print(f"YP Cube reconnecting after error: {exc}")
            await asyncio.sleep(2.0)

if __name__ == "__main__":
    asyncio.run(main())