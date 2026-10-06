import json
import threading
import time
from websocket import create_connection

SERVER_HOST = "localhost"   # IP of C2 WebServer (e.g., 10.10.130.2 or localhost)
SERVER_PORT = 8000
VEHICLE_ID  = "apache3"
WS_URL      = f"ws://{SERVER_HOST}:{SERVER_PORT}/ws/vehicle/{VEHICLE_ID}"

_streaming_active = False
_stream_lock = threading.Lock()
_target_heading = 0.0
_target_speed = 1.0
_ws_connection = None

def stream_worker():
    """Background thread sending 3Hz guidance commands while streaming is active."""
    global _streaming_active, _ws_connection
    
    while True:
        with _stream_lock:
            active = _streaming_active
            hdg = _target_heading
            spd = _target_speed
            ws = _ws_connection

        if not active or ws is None:
            time.sleep(0.1)
            continue

        payload = {
            "op": "command",
            "vehicle_id": VEHICLE_ID,
            "source": "interactive_gui",
            "command": {
                "type": "heading_speed",
                "heading_deg": hdg,
                "speed_mps": spd
            }
        }

        try:
            ws.send(json.dumps(payload))
        except Exception as e:
            print(f"\n[STREAM ERROR] Send failed: {e}")
            break

        time.sleep(0.33)  # 3 Hz streaming rate

def listen_telemetry_worker():
    """Background thread printing telemetry returned from the server/bridge."""
    global _ws_connection
    while True:
        with _stream_lock:
            ws = _ws_connection
        if ws is None:
            time.sleep(0.5)
            continue

        try:
            ws.settimeout(0.5)
            msg = ws.recv()
            data = json.loads(msg)
            if data.get("op") == "telemetry" and data.get("position"):
                p = data["position"]
                print(f"\r[TELEMETRY] Lat: {p['latitude']:.7f} | Lon: {p['longitude']:.7f} | Hdg: {data.get('heading')}° | Behavior: {data.get('behavior')}   ", end="", flush=True)
        except Exception:
            pass

def main():
    global _ws_connection, _streaming_active, _target_heading, _target_speed

    print("==================================================")
    print(" APACHE 3 INTERACTIVE COMMAND & STREAM TESTER ")
    print("==================================================")
    print(f"Connecting to C2 Server: {WS_URL}...")

    try:
        _ws_connection = create_connection(WS_URL, timeout=5)
        print("[SUCCESS] Connected to C2 Server WebSocket!")
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return

    # Start background workers
    threading.Thread(target=stream_worker, daemon=True).start()
    threading.Thread(target=listen_telemetry_worker, daemon=True).start()

    try:
        while True:
            print("\n\n--------------------------------------------------")
            print(f" STREAM STATUS: {'[ACTIVE]' if _streaming_active else '[PAUSED/OFF]'}")
            print(f" SETPOINTS    : Heading = {_target_heading}°, Speed = {_target_speed} m/s")
            print("--------------------------------------------------")
            print(" [1] Toggle Heading/Speed Stream ON/OFF")
            print(" [2] Change Setpoints (Heading & Speed)")
            print(" [3] Send One-Shot Raw RC Override (Steering/Throttle PWM)")
            print(" [4] Send Waypoint Command")
            print(" [5] Change Flight Mode (MANUAL, AUTO, LOITER, HOLD)")
            print(" [6] Trigger RTB (Return to Base)")
            print(" [Q] Quit")
            print("--------------------------------------------------")

            choice = input("Select an option [1-6, Q]: ").strip().upper()

            if choice == "1":
                with _stream_lock:
                    _streaming_active = not _streaming_active
                print(f"\n--> Stream toggled: {'ENABLED' if _streaming_active else 'PAUSED'}")

            elif choice == "2":
                try:
                    hdg_in = float(input("Enter target heading (0 - 360 degrees): "))
                    spd_in = float(input("Enter target speed (m/s): "))
                    with _stream_lock:
                        _target_heading = hdg_in % 360.0
                        _target_speed = spd_in
                    print(f"\n--> Updated setpoints to: Heading={_target_heading}°, Speed={_target_speed} m/s")
                except ValueError:
                    print("\n[ERROR] Invalid number entered.")

            elif choice == "3":
                with _stream_lock:
                    _streaming_active = False  # Pause stream for raw override
                try:
                    str_pwm = int(input("Enter Steering PWM (1100=Left, 1500=Center, 1900=Right): "))
                    thr_pwm = int(input("Enter Throttle PWM (1100=Rev, 1500=Neutral, 1900=Fwd): "))
                    payload = {
                        "op": "command",
                        "vehicle_id": VEHICLE_ID,
                        "command": {
                            "type": "rc_override",
                            "steering": str_pwm,
                            "throttle": thr_pwm
                        }
                    }
                    _ws_connection.send(json.dumps(payload))
                    print("\n--> Sent Raw RC Override command!")
                except ValueError:
                    print("\n[ERROR] Invalid PWM value entered.")

            elif choice == "4":
                with _stream_lock:
                    _streaming_active = False
                try:
                    lat_in = float(input("Enter target latitude (e.g. 38.985977): "))
                    lon_in = float(input("Enter target longitude (e.g. -76.486526): "))
                    payload = {
                        "op": "command",
                        "vehicle_id": VEHICLE_ID,
                        "command": {
                            "type": "waypoint",
                            "target": {"latitude": lat_in, "longitude": lon_in}
                        }
                    }
                    _ws_connection.send(json.dumps(payload))
                    print("\n--> Sent Waypoint command! Apache will switch to AUTO mode.")
                except ValueError:
                    print("\n[ERROR] Invalid coordinate entered.")

            elif choice == "5":
                with _stream_lock:
                    _streaming_active = False
                mode_in = input("Enter Mode (MANUAL, AUTO, LOITER, HOLD): ").strip().upper()
                payload = {
                    "op": "command",
                    "vehicle_id": VEHICLE_ID,
                    "command": {
                        "type": "set_mode",
                        "mode": mode_in
                    }
                }
                _ws_connection.send(json.dumps(payload))
                print(f"\n--> Sent mode change request: {mode_in}")

            elif choice == "6":
                with _stream_lock:
                    _streaming_active = False
                payload = {
                    "op": "command",
                    "vehicle_id": VEHICLE_ID,
                    "command": {"type": "rtb"}
                }
                _ws_connection.send(json.dumps(payload))
                print("\n--> Sent Return To Base (RTB) command!")

            elif choice == "Q":
                print("\nExiting script...")
                break

    except KeyboardInterrupt:
        print("\nAborted by user.")
    finally:
        with _stream_lock:
            _streaming_active = False
            if _ws_connection:
                _ws_connection.close()
        print("Disconnected cleanly.")

if __name__ == "__main__":
    main()
