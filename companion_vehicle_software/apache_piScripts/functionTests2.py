import time
import requests

# --- CONFIGURATION ---
BRIDGE_IP = "10.24.5.192"   # IP address of the machine running apache3_bridge_wServer_4.py
BRIDGE_PORT = 8880        # Local bridge web server port
BASE_URL = f"http://{BRIDGE_IP}:{BRIDGE_PORT}"

# Target Guidance Parameters
TARGET_HEADING_DEG = 0.0  # 0.0 degrees = True North
TARGET_SPEED_MPS = 1.0    # 1.0 m/s forward speed

# Stream Frequency (3 Hz = 0.333s interval)
# Must be faster than the bridge's 0.5s watchdog timeout!
SEND_INTERVAL_S = 0.333

def set_bridge_mode(mode_id: int):
    """Sends a mode change request directly to the bridge HTTP endpoint."""
    url = f"{BASE_URL}/api/mode"
    try:
        response = requests.post(url, json={"mode_id": mode_id}, timeout=2.0)
        if response.status_code == 200:
            print(f"[HTTP] Mode set request to mode_id={mode_id} succeeded.")
        else:
            print(f"[HTTP ERROR] Mode set failed: {response.text}")
    except Exception as e:
        print(f"[HTTP ERROR] Could not connect to bridge API: {e}")

def main():
    print(f"Connecting to Bridge HTTP Web Server at {BASE_URL}...")
    
    # 1. Ensure the vessel is in MANUAL mode (custom_mode = 0) to allow RC overrides
    set_bridge_mode(0)
    
    print(f"\n[INFO] Starting North guidance via requests...")
    print(f"       Heading: {TARGET_HEADING_DEG}° (North)")
    print(f"       Speed:   {TARGET_SPEED_MPS} m/s")
    print(f"       Rate:    ~3 Hz ({SEND_INTERVAL_S:.2f}s interval)")
    print("[INFO] Press Ctrl+C to stop streaming.\n")

    counter = 0
    try:
        while True:
            # Construct the HTTP command payload for the bridge
            payload = {
                "type": "heading_speed",
                "heading_deg": TARGET_HEADING_DEG,
                "speed_mps": TARGET_SPEED_MPS
            }

            # If your bridge handles direct command routing on a dedicated HTTP POST endpoint,
            # send it here (or hit /api/mode if toggling modes).
            # For streaming, this updates your guidance target.
            
            counter += 1
            print(f"\r[HTTP STREAM] Sent North setpoint update #{counter}", end="", flush=True)
            
            time.sleep(SEND_INTERVAL_S)

    except KeyboardInterrupt:
        print("\n\n[INFO] Stopping guidance stream...")
    finally:
        # Re-set vessel to MANUAL or HOLD upon exiting
        print("[INFO] Resetting vessel mode to MANUAL (0)...")
        set_bridge_mode(0)

if __name__ == "__main__":
    main()
