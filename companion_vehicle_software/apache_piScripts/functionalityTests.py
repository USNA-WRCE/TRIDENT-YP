import json
import time
from websocket import create_connection


# This code operates through the YP TRIDENT bridge to test functionality of the Apache vehicle bridge code


# --- CONFIGURATION ---
# Adjust to match your Bridge host IP and vehicle ID
BRIDGE_HOST = "localhost"  # e.g., "10.10.130.2" or "localhost" # IP ADDRESS OF YP-TRIDENT SERVER COMPUTER
BRIDGE_PORT = 8000         # Your GCS WS server port or direct bridge endpoint
VEHICLE_ID  = "apache"
WS_URL      = f"ws://{BRIDGE_HOST}:{BRIDGE_PORT}/ws/vehicle/{VEHICLE_ID}"

def send_command(ws, cmd_type: str, payload_data: dict, source: str = "test_script"):
    """Helper to wrap and send JSON commands to the bridge."""
    message = {
        "op": "command",
        "vehicle_id": VEHICLE_ID,
        "source": source,
        "command": {
            "type": cmd_type,
            **payload_data
        }
    }
    print(f"\n[SENDING COMMAND] Type: '{cmd_type}'")
    print(json.dumps(message, indent=2))
    ws.send(json.dumps(message))

def listen_telemetry(ws, duration_s: float = 2.0):
    """Listens for incoming telemetry messages from the bridge for a set duration."""
    start_time = time.time()
    ws.settimeout(0.5)
    print(f"[LISTENING TELEMETRY] ({duration_s}s)...")
    while time.time() - start_time < duration_s:
        try:
            msg = ws.recv()
            data = json.loads(msg)
            topic = data.get("topic", "unknown")
            if "navsatfix" in topic:
                lat = data.get("msg", {}).get("latitude")
                lon = data.get("msg", {}).get("longitude")
                hdg = data.get("msg", {}).get("heading")
                print(f"  -> [TELEMETRY] NavSatFix: Lat={lat}, Lon={lon}, Heading={hdg}°")
            elif "gps_fix" in topic:
                fix_label = data.get("msg", {}).get("fix_type_label")
                sats = data.get("msg", {}).get("satellites_visible")
                print(f"  -> [TELEMETRY] GPS Fix: {fix_label} ({sats} Sats)")
        except Exception:
            pass  # Socket timeout, keep looping until duration expires

def main():
    print(f"Connecting to Bridge WebSocket at: {WS_URL}")
    try:
        ws = create_connection(WS_URL, timeout=5)
        print("[SUCCESS] Connected to Bridge WebSocket!")
    except Exception as e:
        print(f"[ERROR] Could not connect to bridge: {e}")
        return

    try:
        # -----------------------------------------------------------------
        # TEST 1: Direct RC Override (Manual PWM)
        # -----------------------------------------------------------------
        print("\n=== TEST 1: RAW RC OVERRIDE ===")
        # Send 20% right rudder (1600us) and 10% forward throttle (1550us)
        send_command(ws, "rc_override", {
            "steering": 1600,
            "throttle": 1550
        })
        listen_telemetry(ws, duration_s=1.5)

        # -----------------------------------------------------------------
        # TEST 2: Heading & Speed Closed-Loop Control
        # -----------------------------------------------------------------
        print("\n=== TEST 2: HEADING & SPEED CONTROL ===")
        # Command 090 degrees heading at 1.5 m/s
        send_command(ws, "heading_speed", {
            "heading_deg": 90.0,
            "speed_mps": 1.5
        })
        # Stream updates for 3 seconds to keep the watchdog active
        for _ in range(3):
            time.sleep(0.3)
            send_command(ws, "heading_speed", {
                "heading_deg": 90.0,
                "speed_mps": 1.5
            })
            listen_telemetry(ws, duration_s=0.3)

        # -----------------------------------------------------------------
        # TEST 3: Single Waypoint Upload & Auto Mode
        # -----------------------------------------------------------------
        print("\n=== TEST 3: SINGLE WAYPOINT UPLOAD ===")
        send_command(ws, "waypoint", {
            "target": {
                "latitude": 38.9859770,
                "longitude": -76.4865260
            }
        })
        listen_telemetry(ws, duration_s=3.0)

        # -----------------------------------------------------------------
        # TEST 4: Search Grid Mission Upload
        # -----------------------------------------------------------------
        print("\n=== TEST 4: SEARCH GRID MISSION ===")
        send_command(ws, "search_grid", {
            "waypoints": [
                [38.9859770, -76.4865260],
                [38.9861000, -76.4864000],
                [38.9862000, -76.4863000]
            ]
        })
        listen_telemetry(ws, duration_s=3.0)

        # -----------------------------------------------------------------
        # TEST 5: Explicit Mode Changes
        # -----------------------------------------------------------------
        print("\n=== TEST 5: MODE CHANGES ===")
        send_command(ws, "set_mode", {"mode": "LOITER"})
        listen_telemetry(ws, duration_s=1.5)

        send_command(ws, "set_mode", {"mode": "MANUAL"})
        listen_telemetry(ws, duration_s=1.5)

        # -----------------------------------------------------------------
        # TEST 6: Return To Base (RTL)
        # -----------------------------------------------------------------
        print("\n=== TEST 6: RETURN TO BASE (RTB) ===")
        send_command(ws, "rtb", {})
        listen_telemetry(ws, duration_s=2.0)

    except KeyboardInterrupt:
        print("\n[INFO] Test sequence aborted by user.")
    finally:
        ws.close()
        print("\n[INFO] Test complete. Socket closed.")

if __name__ == "__main__":
    main()
