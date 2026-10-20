"""ArduPlane (fixed-wing / float plane) bridge with a local config + diagnostics web page.

Fixed-wing adaptations versus the ArduCopter bridge:
  * No hover: single waypoints, RTB-follow, and ship-relative targets are flown with
    MAV_CMD_DO_REPOSITION in GUIDED, so the aircraft loiters (circles) around the target.
  * Search grid, MOB search, and mission_plan are uploaded as AUTO missions (lossy-link
    safe, native fixed-wing waypoint tracking) with a NAV_TAKEOFF prepended when on the water.
  * Takeoff is a NAV_TAKEOFF + loiter mission (the plane climbs out, then circles overhead).
  * All altitudes are clamped to a minimum, lane spacing to a minimum turn-radius-safe swath,
    and arrival radii to at least the loiter radius.
  * land_on_boat_step is unsupported (a fixed-wing cannot hover/descend onto a moving deck).
"""
import asyncio
import json
import os
import socket
import threading
import time
import traceback
from pathlib import Path

from aiohttp import web
from pymavlink import mavutil
import websockets

import sar_missions
from yp_common.telemetry import (
    BEHAVIOR_ABSOLUTE_MISSION,
    BEHAVIOR_IDLE,
    BEHAVIOR_MOB_SEARCH,
    BEHAVIOR_SEARCH_GRID,
    BEHAVIOR_SHIP_RELATIVE,
    BEHAVIOR_TAKEOFF,
    BehaviorTracker,
    behavior_for_command,
    build_telemetry,
    gps_block_from_mavlink,
)
from yp_common.geometry import (
    distance_m as _distance_m,
    north_east_delta_m as _north_east_delta_m,
    relative_waypoint_to_global as _relative_waypoint_to_global,
)

CONFIG_PATH = Path("config.json")


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _resolve_webrtc_ip() -> str:
    configured_ip = os.getenv("WEBRTC_IP")
    if configured_ip:
        return configured_ip
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            detected_ip = sock.getsockname()[0]
            if detected_ip and not detected_ip.startswith("127."):
                return detected_ip
    except OSError:
        pass
    try:
        detected_ip = socket.gethostbyname(socket.gethostname())
        if detected_ip and not detected_ip.startswith("127."):
            return detected_ip
    except OSError:
        pass
    return "127.0.0.1"


DEFAULT_CONFIG = {
    "server_ws_url": os.getenv("SERVER_WS_URL", "ws://192.168.0.174:8000/ws/vehicle"),
    "vehicle_id": os.getenv("VEHICLE_ID", "floatplaneYP"),
    "mavlink_url": os.getenv("MAVLINK_URL", "/dev/serial0"),
    "mavlink_baud": int(os.getenv("MAVLINK_BAUD", "921600")),
    "send_hz": _env_float("SEND_HZ", 5.0),
    "video_url": os.getenv("VIDEO_URL", ""),
    "web_port": int(os.getenv("WEB_PORT", "8083")),
}

VEHICLE_TYPE = os.getenv("VEHICLE_TYPE", "uavf")  # "uavf" selects the fixed-wing icon in the web UI
WEBRTC_IP = _resolve_webrtc_ip()

# --- Fixed-wing tuning (override via env vars) ---
PLANE_MIN_ALT_M = _env_float("PLANE_MIN_ALT_M", 40.0)              # floor for every commanded altitude (AGL/relative)
PLANE_TAKEOFF_ALT_M = _env_float("PLANE_TAKEOFF_ALT_M", 50.0)      # default climb-out altitude
PLANE_TAKEOFF_PITCH_DEG = _env_float("PLANE_TAKEOFF_PITCH_DEG", 15.0)
PLANE_LOITER_RADIUS_M = _env_float("PLANE_LOITER_RADIUS_M", 0.0)   # 0 = use the autopilot's WP_LOITER_RAD
PLANE_CRUISE_AIRSPEED_MS = _env_float("PLANE_CRUISE_AIRSPEED_MS", 0.0)  # 0 = leave autopilot default
PLANE_MIN_ARRIVAL_M = _env_float("PLANE_MIN_ARRIVAL_M", 60.0)      # a plane cannot "arrive" inside its turn circle
PLANE_MIN_SWATH_M = _env_float("PLANE_MIN_SWATH_M", 80.0)          # lane spacing floor (turn radius)
PLANE_AIRBORNE_ALT_M = _env_float("PLANE_AIRBORNE_ALT_M", 5.0)
PLANE_AIRBORNE_GS_MS = _env_float("PLANE_AIRBORNE_GS_MS", 10.0)
PLANE_FORCE_ARM_MOB = os.getenv("PLANE_FORCE_ARM_MOB", "false").lower() == "true"
REPOSITION_MIN_INTERVAL_S = _env_float("REPOSITION_MIN_INTERVAL_S", 1.0)
PILOT_TAKEOVER_GRACE_S = 3.0

SHIP_STATE_TIMEOUT_S = _env_float("SHIP_STATE_TIMEOUT_S", 2.0)
SHIP_RELATIVE_DEFAULT_UPDATE_HZ = _env_float("SHIP_RELATIVE_UPDATE_HZ", 1.0)
SHIP_RELATIVE_DEFAULT_ARRIVAL_RADIUS_M = _env_float("SHIP_RELATIVE_ARRIVAL_RADIUS_M", 80.0)

PLANE_MODES = ["MANUAL", "FBWA", "FBWB", "CRUISE", "LOITER", "CIRCLE", "RTL", "GUIDED", "AUTO", "TAKEOFF"]
# Copter-style names the UI may send, mapped to their closest fixed-wing equivalent.
MODE_ALIASES = {
    "STABILIZE": "FBWA", "ALT_HOLD": "FBWB", "POSHOLD": "LOITER", "BRAKE": "LOITER", "HOVER": "LOITER",
    # The web UI's "uavf" mode list uses PX4-style names.
    "ALTITUDE_CONTROL": "FBWB", "POSITION_CONTROL": "LOITER", "OFFBOARD": "GUIDED", "EMERGENCY": "RTL",
}

config: dict = {}
reconnect_event: asyncio.Event | None = None

system_status = {
    "cube_connected": False,
    "cube_status": "Connecting...",
    "ws_connected": False,
    "ws_status": "Connecting...",
    "flight_mode": "UNKNOWN",
    "armed": False,
    "gps_status": "No Fix",
    "satellites": 0,
    "airspeed_ms": 0.0,
    "groundspeed_ms": 0.0,
    "last_hb_time": 0.0,
    "behavior": BEHAVIOR_IDLE,
}
behavior_tracker = BehaviorTracker(on_change=lambda behavior: system_status.__setitem__("behavior", behavior))

_mav_lock = threading.Lock()
_mav_master = None

# Held by mission threads so the telemetry loop skips MAVLink reads during blocking upload/arm sequences.
_sar_mission_lock = threading.Lock()
_sar_stop_event = threading.Event()

_vehicle_state_lock = threading.Lock()
_vehicle_state = {"lat": None, "lon": None, "alt": None, "heading_deg": None, "groundspeed": 0.0, "stamp": 0.0}
_ship_state_lock = threading.Lock()
_ship_state = {"vehicle_id": None, "lat": None, "lon": None, "alt": None, "heading_deg": None, "vn_ms": 0.0, "ve_ms": 0.0, "stamp": 0.0}
_ship_relative_thread: threading.Thread | None = None
_ship_relative_stop_event: threading.Event | None = None

_last_reposition_time = 0.0
_last_rtb_step_time = 0.0
_rtb_guided_forced = False
_current_mode: str | None = None
_armed_state: bool | None = None
_landed_state = 0
_last_land_warn_time = 0.0


# --------------------------------------------------------------------------
# Config + web server
# --------------------------------------------------------------------------

def load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            with open(CONFIG_PATH, "r") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception as exc:
            print(f"[WARN] Error loading config.json, using defaults: {exc}")
    return DEFAULT_CONFIG.copy()


def save_config(new_config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(new_config, f, indent=2)


def _default_video_url() -> str:
    return f"http://{WEBRTC_IP}:8889/cam/whep"


HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ArduPlane Bridge - Telemetry Diagnostics & Config</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; padding: 20px; max-width: 550px; margin: 0 auto; }}
        h2 {{ color: #38bdf8; border-bottom: 2px solid #334155; padding-bottom: 10px; margin-bottom: 15px; }}
        .diag-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 15px; margin-bottom: 25px; }}
        .diag-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }}
        .diag-item {{ background: #0f172a; padding: 10px; border-radius: 6px; border: 1px solid #1e293b; }}
        .diag-label {{ font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; font-weight: 700; letter-spacing: 0.5px; }}
        .diag-value {{ font-size: 1.05rem; font-weight: 600; margin-top: 4px; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; font-weight: 600; }}
        .badge-online {{ background: #065f46; color: #34d399; }}
        .badge-offline {{ background: #881337; color: #fda4af; }}
        label {{ display: block; margin-top: 15px; font-weight: 600; font-size: 0.9rem; color: #94a3b8; }}
        input, select {{ width: 100%; padding: 10px; margin-top: 5px; border-radius: 6px; border: 1px solid #475569; background: #1e293b; color: white; box-sizing: border-box; font-size: 1rem; }}
        .hint {{ font-size: 0.8rem; color: #64748b; margin-top: 4px; }}
        button {{ width: 100%; margin-top: 25px; padding: 12px; background: #2563eb; color: white; border: none; border-radius: 6px; font-size: 1rem; font-weight: bold; cursor: pointer; }}
        button:hover {{ background: #1d4ed8; }}
        .mode-btn {{ background: #0284c7; margin-top: 10px; }}
        .mode-btn:hover {{ background: #0369a1; }}
        .status-msg {{ font-size: 0.85rem; margin-top: 8px; font-weight: bold; text-align: center; }}
    </style>
</head>
<body>
    <h2>System Diagnostics</h2>
    <div class="diag-card">
        <div class="diag-grid">
            <div class="diag-item">
                <div class="diag-label">ArduPlane Controller</div>
                <div class="diag-value"><span id="cube_status" class="badge badge-offline">Offline</span></div>
            </div>
            <div class="diag-item">
                <div class="diag-label">GCS WebServer</div>
                <div class="diag-value"><span id="ws_status" class="badge badge-offline">Offline</span></div>
            </div>
            <div class="diag-item">
                <div class="diag-label">Active Flight Mode</div>
                <div class="diag-value" id="flight_mode" style="color: #38bdf8;">UNKNOWN</div>
            </div>
            <div class="diag-item">
                <div class="diag-label">Armed</div>
                <div class="diag-value"><span id="armed" class="badge badge-offline">Disarmed</span></div>
            </div>
            <div class="diag-item">
                <div class="diag-label">GPS Status</div>
                <div class="diag-value" id="gps_status" style="color: #f59e0b;">No Fix</div>
            </div>
            <div class="diag-item">
                <div class="diag-label">Behavior</div>
                <div class="diag-value" id="behavior" style="color: #a78bfa;">idle</div>
            </div>
            <div class="diag-item">
                <div class="diag-label">Airspeed</div>
                <div class="diag-value" id="airspeed_ms" style="color: #38bdf8;">0.0 m/s</div>
            </div>
            <div class="diag-item">
                <div class="diag-label">Groundspeed</div>
                <div class="diag-value" id="groundspeed_ms" style="color: #38bdf8;">0.0 m/s</div>
            </div>
        </div>
    </div>

    <h2>Flight Mode Control</h2>
    <div class="diag-card">
        <label style="margin-top:0;">Request Mode Change</label>
        <select id="mode_select">{mode_options}</select>
        <button type="button" class="mode-btn" onclick="sendModeChange()">Change Mode Now</button>
        <div id="mode_msg" class="status-msg"></div>
    </div>

    <h2>Configuration</h2>
    <form method="POST" action="/save">
        <label>GCS Server WebSocket URL</label>
        <input type="text" name="server_ws_url" value="{server_ws_url}" required>

        <label>Vehicle ID</label>
        <input type="text" name="vehicle_id" value="{vehicle_id}" required>

        <label>MAVLink Connection URL</label>
        <select id="mavlink_url_select" name="mavlink_url_select" onchange="toggleCustomUrl()">{mavlink_options}</select>
        <input type="text" id="mavlink_url_custom" name="mavlink_url_custom" value="{mavlink_url_custom}" style="display: {custom_display}; margin-top: 8px;" placeholder="e.g. tcp:192.168.1.168:5760, udpin:0.0.0.0:14550, /dev/ttyUSB0">

        <div id="baud_row">
            <label>MAVLink Baud Rate (serial only)</label>
            <input type="number" name="mavlink_baud" value="{mavlink_baud}">
        </div>

        <label>Update Rate (Hz)</label>
        <input type="number" step="0.1" name="send_hz" value="{send_hz}" required>

        <label>Video Endpoint URL</label>
        <input type="text" name="video_url" value="{video_url}" placeholder="{video_url_placeholder}">
        <div class="hint">Leave blank to auto-use MediaMTX WebRTC stream ({webrtc_ip}:8889).</div>

        <button type="submit">Save & Restart Telemetry Stream</button>
    </form>

    <script>
        function toggleCustomUrl() {{
            const select = document.getElementById('mavlink_url_select');
            const customInput = document.getElementById('mavlink_url_custom');
            const isCustom = select.value === 'custom';
            customInput.style.display = isCustom ? 'block' : 'none';
            customInput.required = isCustom;
            const url = isCustom ? customInput.value : select.value;
            document.getElementById('baud_row').style.display = /^(tcp|udp|udpin|udpout|udpbcast):/.test(url) ? 'none' : 'block';
        }}
        document.getElementById('mavlink_url_custom').addEventListener('input', toggleCustomUrl);
        window.addEventListener('DOMContentLoaded', toggleCustomUrl);

        async function sendModeChange() {{
            const select = document.getElementById('mode_select');
            const msgElem = document.getElementById('mode_msg');

            msgElem.style.color = '#38bdf8';
            msgElem.innerText = 'Sending mode change request...';

            try {{
                const res = await fetch('/api/mode', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ mode: select.value }})
                }});
                const data = await res.json();

                if (res.ok && data.ok) {{
                    msgElem.style.color = '#34d399';
                    msgElem.innerText = 'SUCCESS: Mode updated';
                }} else {{
                    msgElem.style.color = '#fda4af';
                    msgElem.innerText = 'FAILED: ' + (data.error || 'Request rejected');
                }}
            }} catch (e) {{
                msgElem.style.color = '#fda4af';
                msgElem.innerText = 'ERROR: Unable to communicate with bridge server';
            }}
        }}

        async function fetchStatus() {{
            try {{
                const res = await fetch('/api/status');
                const data = await res.json();

                const cubeElem = document.getElementById('cube_status');
                cubeElem.innerText = data.cube_status;
                cubeElem.className = 'badge ' + (data.cube_connected ? 'badge-online' : 'badge-offline');

                const wsElem = document.getElementById('ws_status');
                wsElem.innerText = data.ws_status;
                wsElem.className = 'badge ' + (data.ws_connected ? 'badge-online' : 'badge-offline');

                const armedElem = document.getElementById('armed');
                armedElem.innerText = data.armed ? 'Armed' : 'Disarmed';
                armedElem.className = 'badge ' + (data.armed ? 'badge-online' : 'badge-offline');

                document.getElementById('flight_mode').innerText = data.flight_mode;
                document.getElementById('behavior').innerText = data.behavior.replace(/_/g, ' ');
                document.getElementById('airspeed_ms').innerText = Number(data.airspeed_ms || 0).toFixed(1) + ' m/s';
                document.getElementById('groundspeed_ms').innerText = Number(data.groundspeed_ms || 0).toFixed(1) + ' m/s';

                const gpsText = data.gps_status + (data.satellites > 0 ? ` (${{data.satellites}} Sats)` : '');
                document.getElementById('gps_status').innerText = gpsText;
            }} catch (e) {{ console.error("Failed fetching status", e); }}
        }}
        setInterval(fetchStatus, 1000);
        fetchStatus();
    </script>
</body>
</html>
"""


MAVLINK_PRESETS = [
    ("/dev/serial0", "Serial: /dev/serial0 (Pi UART)"),
    ("/dev/ttyACM0", "Serial: /dev/ttyACM0 (USB)"),
    ("udpin:0.0.0.0:14550", "UDP listen: 0.0.0.0:14550 (SITL / MAVProxy out)"),
    ("udpin:0.0.0.0:14551", "UDP listen: 0.0.0.0:14551"),
    ("tcp:127.0.0.1:5760", "TCP: 127.0.0.1:5760 (local SITL)"),
]


def _mavlink_options_html(url: str) -> str:
    known = {value for value, _ in MAVLINK_PRESETS}
    opts = [
        f'<option value="{v}"{" selected" if v == url else ""}>{label}</option>'
        for v, label in MAVLINK_PRESETS
    ]
    opts.append(f'<option value="custom"{" selected" if url not in known else ""}>Custom (IP / serial)...</option>')
    return "".join(opts)


MODE_LABELS = {
    "MANUAL": "MANUAL (direct RC passthrough)",
    "FBWA": "FBWA (Fly-By-Wire A, stabilized)",
    "FBWB": "FBWB (Fly-By-Wire B, altitude hold)",
    "CRUISE": "CRUISE (heading + altitude hold)",
    "LOITER": "LOITER (circle in place)",
    "CIRCLE": "CIRCLE (fixed circle)",
    "RTL": "RTL (Return to Launch)",
    "GUIDED": "GUIDED (go to point)",
    "AUTO": "AUTO (run mission)",
    "TAKEOFF": "TAKEOFF (auto takeoff)",
}


async def handle_index(request):
    url = config["mavlink_url"]
    is_custom = url not in {v for v, _ in MAVLINK_PRESETS}
    html = HTML_TEMPLATE.format(
        mode_options="".join(f'<option value="{m}">{MODE_LABELS.get(m, m)}</option>' for m in PLANE_MODES),
        server_ws_url=config["server_ws_url"],
        vehicle_id=config["vehicle_id"],
        mavlink_options=_mavlink_options_html(url),
        mavlink_url_custom=url if is_custom else "",
        custom_display="block" if is_custom else "none",
        mavlink_baud=config["mavlink_baud"],
        send_hz=config["send_hz"],
        video_url=config.get("video_url", ""),
        video_url_placeholder=_default_video_url(),
        webrtc_ip=WEBRTC_IP,
    )
    return web.Response(text=html, content_type="text/html")


async def handle_status_api(request):
    if time.time() - system_status["last_hb_time"] > 4.0 and system_status["cube_connected"]:
        system_status["cube_connected"] = False
        system_status["cube_status"] = "Heartbeat Lost"
    return web.json_response(system_status)


async def handle_mode_api(request):
    try:
        body = await request.json()
        mode = _normalize_mode(str(body.get("mode", "")))
        if mode is None:
            return web.json_response({"ok": False, "error": "Unsupported mode for fixed-wing"}, status=400)
        with _mav_lock:
            master = _mav_master
        if not master or not system_status["cube_connected"]:
            return web.json_response({"ok": False, "error": "Autopilot disconnected"}, status=503)
        _stop_ship_relative_mission()
        _sar_stop_event.set()
        ok = await asyncio.to_thread(sar_missions.set_mode, master, mode, wait_for_ack=False)
        return web.json_response({"ok": ok, "mode": mode})
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=500)


async def handle_save(request):
    data = await request.post()
    config["server_ws_url"] = data.get("server_ws_url", config["server_ws_url"]).strip()
    config["vehicle_id"] = data.get("vehicle_id", config["vehicle_id"]).strip()
    selected = data.get("mavlink_url_select", "")
    if selected == "custom":
        selected = data.get("mavlink_url_custom", "").strip()
    if selected:
        config["mavlink_url"] = selected
    config["mavlink_baud"] = int(data.get("mavlink_baud", config["mavlink_baud"]))
    config["send_hz"] = float(data.get("send_hz", config["send_hz"]))
    config["video_url"] = data.get("video_url", config.get("video_url", "")).strip()
    save_config(config)
    reconnect_event.set()
    return web.HTTPFound(location="/")


async def start_web_server() -> None:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status_api)
    app.router.add_post("/api/mode", handle_mode_api)
    app.router.add_post("/save", handle_save)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(config.get("web_port", 8083))
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[INFO] Web interface running at http://0.0.0.0:{port}")


# --------------------------------------------------------------------------
# State helpers
# --------------------------------------------------------------------------

def _normalize_mode(mode: str) -> str | None:
    mode = MODE_ALIASES.get(mode.strip().upper(), mode.strip().upper())
    return mode if mode in PLANE_MODES else None


def _clamp_alt(alt_m: float | None) -> float:
    return max(PLANE_MIN_ALT_M, float(alt_m) if alt_m is not None else PLANE_MIN_ALT_M)


def _update_vehicle_state(lat, lon, alt, heading) -> None:
    with _vehicle_state_lock:
        _vehicle_state.update({"lat": lat, "lon": lon, "alt": alt, "heading_deg": heading, "stamp": time.time()})


def _snapshot_vehicle_state() -> dict:
    with _vehicle_state_lock:
        return dict(_vehicle_state)


def _capture_sar_telemetry(msg) -> None:
    """Callback for helpers that consume MAVLink themselves while the telemetry loop is paused."""
    if msg.get_type() != "GLOBAL_POSITION_INT":
        return
    heading_raw = getattr(msg, "hdg", None)
    heading = (heading_raw / 100.0) if heading_raw not in (None, 65535) else None
    _update_vehicle_state(msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000.0, heading)


def _is_airborne() -> bool:
    state = _snapshot_vehicle_state()
    if not _armed_state:
        return False
    if _landed_state == mavutil.mavlink.MAV_LANDED_STATE_IN_AIR:
        return True
    return (state.get("alt") or 0.0) > PLANE_AIRBORNE_ALT_M or (state.get("groundspeed") or 0.0) > PLANE_AIRBORNE_GS_MS


def _pilot_took_over(started_at: float) -> bool:
    """True if the safety pilot left GUIDED after the grace period following our mode request."""
    return time.monotonic() - started_at > PILOT_TAKEOVER_GRACE_S and _current_mode not in (None, "GUIDED")


def _ui_ws_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    marker = "/ws/vehicle"
    return f"{base.split(marker, 1)[0]}/ws/ship_state" if marker in base else base


def _update_ship_state(vehicle: dict) -> None:
    position = vehicle.get("position") or {}
    lat, lon = position.get("latitude"), position.get("longitude")
    if lat is None or lon is None:
        return
    heading = vehicle.get("heading")
    stamp = float(vehicle.get("last_seen") or time.time())
    vehicle_id = vehicle.get("vehicle_id")
    with _ship_state_lock:
        same = _ship_state.get("vehicle_id") == vehicle_id
        vn_ms = float(_ship_state.get("vn_ms") or 0.0) if same else 0.0
        ve_ms = float(_ship_state.get("ve_ms") or 0.0) if same else 0.0
        prev_stamp = float(_ship_state.get("stamp") or 0.0)
        if same and _ship_state.get("lat") is not None and stamp > prev_stamp:
            north_m, east_m = _north_east_delta_m(float(_ship_state["lat"]), float(_ship_state["lon"]), float(lat), float(lon))
            vn_ms, ve_ms = north_m / (stamp - prev_stamp), east_m / (stamp - prev_stamp)
        _ship_state.update({
            "vehicle_id": vehicle_id, "lat": float(lat), "lon": float(lon),
            "alt": float(position.get("altitude", 0.0)),
            "heading_deg": float(heading) % 360.0 if heading is not None else _ship_state.get("heading_deg"),
            "vn_ms": vn_ms, "ve_ms": ve_ms, "stamp": stamp,
        })


def _snapshot_ship_state(expected_vehicle_id: str | None = None) -> dict | None:
    with _ship_state_lock:
        if expected_vehicle_id and _ship_state.get("vehicle_id") != expected_vehicle_id:
            return None
        if _ship_state.get("lat") is None:
            return None
        return dict(_ship_state)


def _ship_state_is_fresh(ship_state: dict | None) -> bool:
    return bool(ship_state) and (time.time() - float(ship_state.get("stamp") or 0.0)) <= SHIP_STATE_TIMEOUT_S


async def ship_state_listener_loop(server_ws_url: str) -> None:
    url = _ui_ws_url(server_ws_url)
    while True:
        try:
            async with websockets.connect(url, ping_interval=10, ping_timeout=10) as ws:
                print(f"[INFO] Ship-state listener connected to {url}", flush=True)
                async for raw in ws:
                    try:
                        message = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    op = message.get("op")
                    vehicles = message.get("vehicles", []) if op == "snapshot" else [message.get("vehicle") or {}] if op == "vehicle_update" else []
                    for vehicle in vehicles:
                        if vehicle.get("vehicle_type") == "yp":
                            _update_ship_state(vehicle)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[WARN] Ship-state listener disconnected: {exc}", flush=True)
            await asyncio.sleep(1.0)


# --------------------------------------------------------------------------
# Fixed-wing flight primitives
# --------------------------------------------------------------------------

def set_cruise_airspeed(master) -> None:
    if PLANE_CRUISE_AIRSPEED_MS > 0:
        master.mav.command_long_send(
            master.target_system, master.target_component, mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
            0, 0, PLANE_CRUISE_AIRSPEED_MS, -1, 0, 0, 0, 0,
        )


def reposition(master, lat: float, lon: float, alt_m: float, change_mode: bool = True, loiter_radius_m: float | None = None) -> None:
    """Fly to and loiter around a point using DO_REPOSITION (the fixed-wing 'go to')."""
    global _last_reposition_time
    radius = PLANE_LOITER_RADIUS_M if loiter_radius_m is None else loiter_radius_m
    master.mav.command_int_send(
        master.target_system, master.target_component,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
        mavutil.mavlink.MAV_CMD_DO_REPOSITION,
        0, 0,
        -1.0,                       # speed: autopilot default
        1.0 if change_mode else 0.0,  # bit0: switch to GUIDED
        float(radius),              # loiter radius (0 = WP_LOITER_RAD)
        float("nan"),
        int(lat * 1e7), int(lon * 1e7), float(_clamp_alt(alt_m)),
    )
    _last_reposition_time = time.monotonic()


def goto_waypoint(master, lat: float, lon: float, alt_m: float, force_guided: bool = True) -> None:
    print(f"[NAV] Fixed-wing reposition to {lat:.6f}, {lon:.6f}, alt={_clamp_alt(alt_m):.0f}m (loiter on arrival)")
    reposition(master, lat, lon, alt_m, change_mode=force_guided)


def _rtb_gate() -> bool:
    """Force GUIDED once per continuous RTB stream; afterwards respect a pilot mode change."""
    global _last_rtb_step_time, _rtb_guided_forced
    now = time.monotonic()
    if now - _last_rtb_step_time > 2.0:
        _rtb_guided_forced = False
    _last_rtb_step_time = now
    if _rtb_guided_forced:
        return False
    _rtb_guided_forced = True
    return True


def follow_yp_target(master, command_data: dict) -> None:
    """RTB-follow: loiter over the (moving) YP position; velocity/yaw matching is not possible for a plane."""
    target = command_data.get("target", {})
    lat, lon = target.get("latitude"), target.get("longitude")
    if lat is None or lon is None:
        return
    first = _rtb_gate()
    if not first and _current_mode != "GUIDED":
        print("[RTB] Safety pilot has taken control; halting RTB-follow guidance")
        return
    if not first and time.monotonic() - _last_reposition_time < REPOSITION_MIN_INTERVAL_S:
        return
    reposition(master, float(lat), float(lon), target.get("altitude"), change_mode=first)


# --------------------------------------------------------------------------
# Ship-relative missions
# --------------------------------------------------------------------------

def _stop_ship_relative_mission() -> None:
    global _ship_relative_thread, _ship_relative_stop_event
    thread, stop_event = _ship_relative_thread, _ship_relative_stop_event
    if thread and thread.is_alive():
        if stop_event:
            stop_event.set()
        thread.join(timeout=2.0)
    _ship_relative_thread = None
    _ship_relative_stop_event = None


def _launch_ship_relative_mission(master, command_data: dict) -> None:
    global _ship_relative_thread, _ship_relative_stop_event
    local_waypoints = command_data.get("local_waypoints", [])
    ship_vehicle_id = command_data.get("ship_vehicle_id")
    if not ship_vehicle_id or not local_waypoints:
        print("[ERROR] ship_relative_trajectory requires ship_vehicle_id and at least one waypoint.")
        behavior_tracker.finish(BEHAVIOR_SHIP_RELATIVE)
        return
    if not _is_airborne():
        print("[ERROR] ship_relative_trajectory requires an airborne aircraft; send takeoff first.")
        behavior_tracker.finish(BEHAVIOR_SHIP_RELATIVE)
        return
    _stop_ship_relative_mission()
    stop_event = threading.Event()
    _ship_relative_stop_event = stop_event
    _ship_relative_thread = threading.Thread(
        target=_run_ship_relative_mission,
        args=(
            master, ship_vehicle_id, local_waypoints,
            max(PLANE_MIN_ARRIVAL_M, float(command_data.get("arrival_radius_m", SHIP_RELATIVE_DEFAULT_ARRIVAL_RADIUS_M))),
            max(REPOSITION_MIN_INTERVAL_S, 1.0 / max(float(command_data.get("update_hz", SHIP_RELATIVE_DEFAULT_UPDATE_HZ)), 0.1)),
            stop_event,
            max(1, min(100, int(command_data.get("loop_count", 1)))),
            bool(command_data.get("hold_last_waypoint", False)),
        ),
        daemon=True,
    )
    _ship_relative_thread.start()


def _run_ship_relative_mission(master, ship_vehicle_id, local_waypoints, arrival_radius_m, period_s, stop_event, loop_count, hold_last_waypoint) -> None:
    print(f"[SHIP-REL] Starting with {len(local_waypoints)} waypoints relative to {ship_vehicle_id}")
    waypoints = local_waypoints * loop_count
    started_at = time.monotonic()
    first_send = True
    try:
        for index, waypoint in enumerate(waypoints, start=1):
            while not stop_event.is_set():
                if _pilot_took_over(started_at):
                    print("[SHIP-REL] Safety pilot took control; aborting.")
                    return
                ship = _snapshot_ship_state(ship_vehicle_id)
                vehicle = _snapshot_vehicle_state()
                if not _ship_state_is_fresh(ship) or vehicle.get("lat") is None:
                    time.sleep(period_s)
                    continue

                target_lat, target_lon, target_alt = _relative_waypoint_to_global(
                    float(ship["lat"]), float(ship["lon"]), float(ship.get("heading_deg") or 0.0), float(ship.get("alt") or 0.0), waypoint,
                )
                reposition(master, target_lat, target_lon, target_alt, change_mode=first_send)
                first_send = False

                distance_m = _distance_m(float(vehicle["lat"]), float(vehicle["lon"]), target_lat, target_lon)
                alt_ok = abs(float(vehicle.get("alt") or 0.0) - _clamp_alt(target_alt)) <= max(10.0, arrival_radius_m * 0.25)
                if distance_m <= arrival_radius_m and alt_ok:
                    print(f"[SHIP-REL] Reached waypoint {index}/{len(waypoints)}")
                    if index < len(waypoints) or not hold_last_waypoint:
                        break
                time.sleep(period_s)
            if stop_event.is_set():
                print("[SHIP-REL] Mission interrupted.")
                return
        if not hold_last_waypoint:
            print("[SHIP-REL] Mission complete.")
    finally:
        behavior_tracker.finish(BEHAVIOR_SHIP_RELATIVE)


# --------------------------------------------------------------------------
# AUTO-mission based commands (takeoff, search grid, MOB, mission_plan)
# --------------------------------------------------------------------------

def _takeoff_item(lat: float, lon: float, alt_m: float) -> tuple:
    return (lat, lon, alt_m, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, PLANE_TAKEOFF_PITCH_DEG, 0.0, 0.0, 0.0)


def _loiter_unlim_item(lat: float, lon: float, alt_m: float) -> tuple:
    return (lat, lon, alt_m, mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM, 0.0, 0.0, PLANE_LOITER_RADIUS_M, 0.0)


def _waypoint_item(lat: float, lon: float, alt_m: float) -> tuple:
    return (lat, lon, _clamp_alt(alt_m), mavutil.mavlink.MAV_CMD_NAV_WAYPOINT, 0.0, 0.0, 0.0, 0.0)


def _speed_item(lat: float, lon: float) -> tuple:
    return (lat, lon, 0.0, mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED, 0.0, PLANE_CRUISE_AIRSPEED_MS, -1.0, 0.0)


def _launch_auto_mission(master, items: list, force_arm: bool = False, label: str = "mission") -> bool:
    """Upload items, arm if needed, and start AUTO. Caller must hold _sar_mission_lock."""
    if not items:
        print(f"[MISSION] {label}: nothing to upload.")
        return False
    print(f"[MISSION] {label}: uploading {len(items)} items")
    if not sar_missions.upload_mission(master, items):
        print(f"[MISSION] {label}: upload failed")
        return False
    if _sar_stop_event.is_set():
        print(f"[MISSION] {label}: cancelled before start")
        return False
    if not _armed_state:
        sar_missions.set_mode(master, "GUIDED")
        time.sleep(1.0)
        if not sar_missions.arm_vehicle(master, force=force_arm):
            print(f"[MISSION] {label}: arming failed")
            return False
        time.sleep(0.5)
    if _sar_stop_event.is_set():
        print(f"[MISSION] {label}: cancelled before start")
        return False
    sar_missions.set_mode(master, "AUTO")
    time.sleep(1.0)
    if not sar_missions.start_mission(master):
        print(f"[MISSION] {label}: MISSION_START not acked (AUTO mode may already be running)")
    print(f"[MISSION] {label}: started")
    return True


def _current_position_or_none() -> tuple[float, float] | None:
    state = _snapshot_vehicle_state()
    if state.get("lat") is None or time.time() - float(state.get("stamp") or 0.0) > 3.0:
        return None
    return float(state["lat"]), float(state["lon"])


def _run_takeoff(master, altitude_m: float) -> None:
    altitude_m = _clamp_alt(altitude_m)
    try:
        with _sar_mission_lock:
            _sar_stop_event.clear()
            position = _current_position_or_none()
            if position is None:
                print("[MISSION] Takeoff aborted: no fresh GPS position.")
                return
            lat, lon = position
            items = [_takeoff_item(lat, lon, altitude_m), _loiter_unlim_item(lat, lon, altitude_m)]
            if not _launch_auto_mission(master, items, label="takeoff"):
                return
        deadline = time.time() + 120.0
        while time.time() < deadline and behavior_tracker.get() == BEHAVIOR_TAKEOFF and not _sar_stop_event.is_set():
            alt = _snapshot_vehicle_state().get("alt")
            if alt is not None and alt >= altitude_m - 3.0:
                print(f"[MISSION] Takeoff complete at {alt:.0f}m; loitering overhead.")
                break
            time.sleep(0.5)
    except Exception as exc:
        print(f"[MISSION] takeoff error: {exc}")
        traceback.print_exc()
    finally:
        behavior_tracker.finish(BEHAVIOR_TAKEOFF)


def _search_items(waypoints: list, takeoff_alt_m: float, force_arm: bool = False) -> list:
    """Wrap pattern waypoints with an optional climb-out and a cruise-speed item."""
    start_lat, start_lon = float(waypoints[0][0]), float(waypoints[0][1])
    items: list = []
    if PLANE_CRUISE_AIRSPEED_MS > 0:
        items.append(_speed_item(start_lat, start_lon))
    if not _is_airborne():
        items.append(_takeoff_item(start_lat, start_lon, _clamp_alt(takeoff_alt_m)))
    items += [_waypoint_item(lat, lon, alt) for lat, lon, alt in waypoints]
    last_lat, last_lon, last_alt = waypoints[-1]
    items.append(_loiter_unlim_item(float(last_lat), float(last_lon), _clamp_alt(last_alt)))
    return items


def _run_search_grid(master, lat, lon, grid_size_m, swath_m, altitude_m) -> None:
    ok = False
    with _sar_mission_lock:
        _sar_stop_event.clear()
        try:
            swath_m = max(swath_m, PLANE_MIN_SWATH_M)
            altitude_m = _clamp_alt(altitude_m)
            waypoints = sar_missions.calculate_search_grid_waypoints(lat, lon, grid_size_m, swath_m, altitude_m)
            if not waypoints:
                print("[SAR] Failed to generate search grid waypoints")
                return
            ok = _launch_auto_mission(master, _search_items(waypoints, PLANE_TAKEOFF_ALT_M), label="search grid")
            print(f"[SAR] Search grid mission {'STARTED' if ok else 'FAILED'} ({len(waypoints)} waypoints, {swath_m:.0f}m lanes)")
        except Exception as exc:
            print(f"[SAR] Search grid error: {exc}")
            traceback.print_exc()
        finally:
            if not ok:
                behavior_tracker.finish(BEHAVIOR_SEARCH_GRID)


def _run_mob_search(master, track_points, corridor_half_width_m, swath_m, altitude_m, takeoff_altitude_m) -> None:
    ok = False
    with _sar_mission_lock:
        _sar_stop_event.clear()
        try:
            swath_m = max(swath_m, PLANE_MIN_SWATH_M)
            altitude_m = _clamp_alt(altitude_m)
            vehicle = _snapshot_vehicle_state()
            start_from_newest = False
            if vehicle.get("lat") is not None and len(track_points) >= 2:
                d_old = _distance_m(vehicle["lat"], vehicle["lon"], float(track_points[0][0]), float(track_points[0][1]))
                d_new = _distance_m(vehicle["lat"], vehicle["lon"], float(track_points[-1][0]), float(track_points[-1][1]))
                start_from_newest = d_new < d_old
            waypoints = sar_missions.calculate_mob_waypoints(
                track_points, corridor_half_width_m, swath_m, altitude_m, start_from_newest=start_from_newest,
            )
            if not waypoints:
                print("[SAR] Failed to generate MOB waypoints")
                return
            ok = _launch_auto_mission(
                master, _search_items(waypoints, takeoff_altitude_m), force_arm=PLANE_FORCE_ARM_MOB, label="MOB search",
            )
            print(f"[SAR] MOB search mission {'STARTED' if ok else 'FAILED'} ({len(waypoints)} waypoints)")
        except Exception as exc:
            print(f"[SAR] MOB search error: {exc}")
            traceback.print_exc()
        finally:
            if not ok:
                behavior_tracker.finish(BEHAVIOR_MOB_SEARCH)


_ITEM_COMMANDS = {
    "waypoint": mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
    "takeoff": mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
    "loiter_time": mavutil.mavlink.MAV_CMD_NAV_LOITER_TIME,
    "loiter_unlim": mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM,
    "loiter_turns": mavutil.mavlink.MAV_CMD_NAV_LOITER_TURNS,
    "land": mavutil.mavlink.MAV_CMD_NAV_LAND,
    "rtl": mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
    "do_jump": mavutil.mavlink.MAV_CMD_DO_JUMP,
}


def build_plane_mission_items(waypoints: list, hold_on_complete: bool) -> list:
    """Convert UI mission entries to fixed-wing mission items with plane-correct parameters."""
    items: list = []
    for wp in waypoints:
        if not isinstance(wp, dict) or wp.get("latitude") is None or wp.get("longitude") is None:
            continue
        item_type = str(wp.get("item_type") or "waypoint").lower()
        command_id = int(wp.get("command_id") or _ITEM_COMMANDS.get(item_type, mavutil.mavlink.MAV_CMD_NAV_WAYPOINT))
        lat, lon = float(wp["latitude"]), float(wp["longitude"])
        alt = float(wp.get("altitude", PLANE_TAKEOFF_ALT_M))

        if command_id == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
            defaults = (PLANE_TAKEOFF_PITCH_DEG, 0.0, 0.0, 0.0)
            alt = _clamp_alt(alt)
        elif command_id == mavutil.mavlink.MAV_CMD_NAV_LAND:
            defaults = (0.0, 0.0, 0.0, 0.0)
            alt = 0.0 if wp.get("altitude") is None else alt
        elif command_id in (mavutil.mavlink.MAV_CMD_NAV_LOITER_TIME, mavutil.mavlink.MAV_CMD_NAV_LOITER_TURNS, mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM):
            hold = float(wp.get("hold_time_s", 0.0))
            defaults = (hold if command_id != mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM else 0.0, 0.0, PLANE_LOITER_RADIUS_M, 0.0)
            alt = _clamp_alt(alt)
        elif command_id in (mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH, mavutil.mavlink.MAV_CMD_DO_JUMP):
            defaults = (0.0, 0.0, 0.0, 0.0)
        else:
            defaults = (0.0, 0.0, 0.0, 0.0)
            alt = _clamp_alt(alt)
        params = tuple(float(wp.get(f"param{i}", d)) for i, d in enumerate(defaults, 1))
        items.append((lat, lon, alt, command_id, *params))

    if not items:
        return items
    if hold_on_complete:
        lat, lon, alt = items[-1][:3]
        items.append(_loiter_unlim_item(lat, lon, _clamp_alt(alt)))
    if not _is_airborne() and int(items[0][3]) != int(mavutil.mavlink.MAV_CMD_NAV_TAKEOFF):
        items.insert(0, _takeoff_item(items[0][0], items[0][1], _clamp_alt(items[0][2])))
    return items


def _run_mission_plan(master, waypoints: list, auto_arm_start: bool, hold_on_complete: bool) -> None:
    with _sar_mission_lock:
        _sar_stop_event.clear()
        try:
            items = build_plane_mission_items(waypoints, hold_on_complete)
            if not items:
                print("[MISSION] mission_plan has no valid waypoints.")
                behavior_tracker.finish(BEHAVIOR_ABSOLUTE_MISSION)
                return
            if auto_arm_start:
                if not _launch_auto_mission(master, items, label="mission_plan"):
                    behavior_tracker.finish(BEHAVIOR_ABSOLUTE_MISSION)
            else:
                print(f"[MISSION] Uploading {len(items)} items (no auto start)")
                if not sar_missions.upload_mission(master, items):
                    print("[MISSION] Mission upload failed")
                behavior_tracker.finish(BEHAVIOR_ABSOLUTE_MISSION)
        except Exception as exc:
            print(f"[MISSION] mission_plan error: {exc}")
            traceback.print_exc()
            behavior_tracker.finish(BEHAVIOR_ABSOLUTE_MISSION)


def _cancel_active_missions(master) -> None:
    """Stop streaming/AUTO activity and make the plane loiter at its current position."""
    _sar_stop_event.set()
    _stop_ship_relative_mission()
    if _armed_state:
        sar_missions.set_mode(master, "LOITER", wait_for_ack=False)
    behavior_tracker.set(BEHAVIOR_IDLE)


def _spawn(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


# --------------------------------------------------------------------------
# Command handling
# --------------------------------------------------------------------------

async def handle_command(master, ws, vehicle_id: str, server_msg: dict) -> None:
    global _last_land_warn_time
    command_data = server_msg.get("command", {})
    cmd_type = command_data.get("type")

    if cmd_type not in ("ship_relative_trajectory", "rtcm_data"):
        await asyncio.to_thread(_stop_ship_relative_mission)
    if cmd_type != "rtcm_data":
        behavior_tracker.set(behavior_for_command(cmd_type, server_msg.get("source")))

    if cmd_type == "rtb_follow":
        follow_yp_target(master, command_data)

    elif cmd_type == "land_on_boat_step":
        # A fixed-wing cannot descend onto or hover over a moving deck.
        if time.time() - _last_land_warn_time > 5.0:
            print("[WARN] land_on_boat is not supported for fixed-wing; ignoring (use RTB-follow to loiter overhead).")
            _last_land_warn_time = time.time()
        behavior_tracker.set(BEHAVIOR_IDLE)

    elif cmd_type == "waypoint":
        target = command_data.get("target", {})
        lat, lon, alt = target.get("latitude"), target.get("longitude"), target.get("altitude")
        if lat is None or lon is None:
            print("[ERROR] Missing coordinates in waypoint command payload.")
        elif server_msg.get("source") == "rtb_follow":
            follow_yp_target(master, command_data)
        else:
            goto_waypoint(master, float(lat), float(lon), alt)

    elif cmd_type == "search_grid":
        lat, lon = command_data.get("lat"), command_data.get("lon")
        if None in (lat, lon):
            print("[ERROR] search_grid command missing lat/lon.")
        else:
            _spawn(
                _run_search_grid, master, float(lat), float(lon),
                float(command_data.get("grid_size_m", 400)),
                float(command_data.get("swath_m", PLANE_MIN_SWATH_M)),
                float(command_data.get("altitude_m", PLANE_MIN_ALT_M)),
            )

    elif cmd_type == "mob":
        track_points = command_data.get("track_points", [])
        if len(track_points) < 2:
            print(f"[ERROR] MOB command needs at least 2 track points, got {len(track_points)}")
        else:
            _spawn(
                _run_mob_search, master, track_points,
                float(command_data.get("corridor_half_width_m", 100.0)),
                float(command_data.get("swath_m", PLANE_MIN_SWATH_M)),
                float(command_data.get("altitude_m", PLANE_MIN_ALT_M)),
                float(command_data.get("takeoff_altitude_m", PLANE_TAKEOFF_ALT_M)),
            )

    elif cmd_type == "cancel_sar":
        print("[SAR] Cancel requested by operator; loitering in place.")
        await asyncio.to_thread(_cancel_active_missions, master)

    elif cmd_type == "arm":
        await asyncio.to_thread(sar_missions.arm_vehicle, master)

    elif cmd_type == "disarm":
        master.mav.command_long_send(master.target_system, master.target_component, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0, 0, 0, 0, 0, 0, 0, 0)

    elif cmd_type == "takeoff":
        _spawn(_run_takeoff, master, float(command_data.get("altitude_m", PLANE_TAKEOFF_ALT_M)))

    elif cmd_type == "rtcm_data":
        raw_data = command_data.get("data", [])
        data_len = command_data.get("len", 0)
        if data_len > 0:
            try:
                master.mav.gps_rtcm_data_send(command_data.get("flags", 0), data_len, bytearray(raw_data + [0] * (180 - len(raw_data))))
            except Exception as exc:
                print(f"[RTCM] MAVLink send error: {exc}")

    elif cmd_type == "ship_relative_trajectory":
        _launch_ship_relative_mission(master, command_data)

    elif cmd_type == "mission_plan":
        waypoints = command_data.get("waypoints", [])
        if isinstance(waypoints, list) and waypoints:
            _spawn(
                _run_mission_plan, master, waypoints,
                bool(command_data.get("auto_arm_start", True)),
                bool(command_data.get("force_guided_on_complete", False)),
            )
        else:
            print("[ERROR] mission_plan command missing waypoints array.")

    elif cmd_type == "set_mode":
        mode = _normalize_mode(str(command_data.get("mode", "")))
        if mode is None:
            print(f"[ERROR] Unsupported fixed-wing mode: {command_data.get('mode')!r}")
        else:
            if mode != "GUIDED":
                _sar_stop_event.set()
            sar_missions.set_mode(master, mode, wait_for_ack=False)
            print(f"[INFO] Set vehicle mode to {mode}")


# --------------------------------------------------------------------------
# Telemetry loop
# --------------------------------------------------------------------------

class MavlinkLost(Exception):
    pass


def _create_video_message(vehicle_id: str, video_url: str) -> dict:
    return {
        "op": "video_stream_update",
        "video": {"vehicle_id": vehicle_id, "enabled": True, "streams": [{"label": "Primary WebRTC", "url": video_url}]},
    }


def _connect_mavlink(url: str, baud: int):
    master = mavutil.mavlink_connection(url, baud=baud)
    while not master.wait_heartbeat(timeout=2):
        pass
    return master


async def telemetry_loop(cfg: dict) -> None:
    global _mav_master, _current_mode, _armed_state, _landed_state

    vehicle_id, send_hz = cfg["vehicle_id"], float(cfg["send_hz"])
    video_url = cfg.get("video_url") or _default_video_url()

    system_status.update({"cube_connected": False, "cube_status": "Connecting...", "ws_connected": False, "ws_status": "Connecting..."})
    print(f"[INFO] Connecting to MAVLink {cfg['mavlink_url']} @ {cfg['mavlink_baud']}")
    master = await asyncio.to_thread(_connect_mavlink, cfg["mavlink_url"], int(cfg["mavlink_baud"]))
    with _mav_lock:
        _mav_master = master
    system_status.update({"cube_connected": True, "cube_status": "Connected", "last_hb_time": time.time()})
    print(f"[SUCCESS] Heartbeat from system {master.target_system}, MAV_TYPE={master.messages['HEARTBEAT'].type}")

    master.mav.request_data_stream_send(master.target_system, master.target_component, mavutil.mavlink.MAV_DATA_STREAM_POSITION, max(1, int(send_hz)), 1)
    for msg_id, hz in (
        (mavutil.mavlink.MAVLINK_MSG_ID_EXTENDED_SYS_STATE, 2),
        (mavutil.mavlink.MAVLINK_MSG_ID_GPS_RAW_INT, 2),
        (mavutil.mavlink.MAVLINK_MSG_ID_GPS2_RAW, 2),
        (mavutil.mavlink.MAVLINK_MSG_ID_VFR_HUD, 4),
    ):
        master.mav.command_long_send(master.target_system, master.target_component, mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, msg_id, int(1e6 / hz), 0, 0, 0, 0, 0)
    set_cruise_airspeed(master)

    ship_task = asyncio.create_task(ship_state_listener_loop(cfg["server_ws_url"]))
    last_mav_rx = time.time()
    last_send_time = 0.0
    last_video_send_time = 0.0
    ws_url = f"{cfg['server_ws_url'].rstrip('/')}/{vehicle_id}"
    telemetry_sample = None

    try:
        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=10, ping_timeout=10) as ws:
                    system_status.update({"ws_connected": True, "ws_status": "Connected"})
                    print("[SUCCESS] WebSocket connected!")
                    last_video_send_time = 0.0
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=0.01)
                            server_msg = json.loads(raw)
                            if server_msg.get("op") == "command" and server_msg.get("vehicle_id") == vehicle_id:
                                await handle_command(master, ws, vehicle_id, server_msg)
                        except asyncio.TimeoutError:
                            pass
                        except json.JSONDecodeError:
                            print("[WARN] Server message was not valid JSON.")

                        mission_busy = _sar_mission_lock.locked()
                        received = 0
                        while not mission_busy and received < 20:
                            msg = master.recv_match(type=["GLOBAL_POSITION_INT", "EXTENDED_SYS_STATE", "GPS_RAW_INT", "GPS2_RAW", "HEARTBEAT", "VFR_HUD"], blocking=False)
                            if msg is None:
                                break
                            received += 1
                            last_mav_rx = time.time()
                            kind = msg.get_type()
                            if kind == "EXTENDED_SYS_STATE":
                                _landed_state = msg.landed_state
                            elif kind == "VFR_HUD":
                                system_status["airspeed_ms"] = round(msg.airspeed, 1)
                                system_status["groundspeed_ms"] = round(msg.groundspeed, 1)
                                with _vehicle_state_lock:
                                    _vehicle_state["groundspeed"] = float(msg.groundspeed)
                            elif kind == "HEARTBEAT":
                                if msg.get_srcSystem() != master.target_system:
                                    continue
                                system_status["last_hb_time"] = time.time()
                                system_status["cube_connected"] = True
                                system_status["cube_status"] = "Connected"
                                _armed_state = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                                system_status["armed"] = _armed_state
                                try:
                                    _current_mode = master.flightmode
                                    system_status["flight_mode"] = _current_mode
                                except Exception:
                                    pass
                                if not _armed_state:
                                    behavior_tracker.set(BEHAVIOR_IDLE)
                                else:
                                    behavior_tracker.observe_mode(_current_mode, guided_modes=("GUIDED", "AUTO", "TAKEOFF", "LOITER"))
                            elif kind in ("GPS_RAW_INT", "GPS2_RAW"):
                                gps = gps_block_from_mavlink(msg)
                                system_status["gps_status"] = gps["fix_type_label"]
                                system_status["satellites"] = gps["satellites"] or 0
                                await ws.send(json.dumps(build_telemetry(vehicle_id, VEHICLE_TYPE, behavior=behavior_tracker.get(), autopilot="ardupilot", gps=gps)))
                            elif kind == "GLOBAL_POSITION_INT":
                                heading_raw = getattr(msg, "hdg", None)
                                heading = (heading_raw / 100.0) if heading_raw not in (None, 65535) else None
                                telemetry_sample = (msg.lat / 1e7, msg.lon / 1e7, msg.relative_alt / 1000.0, heading)
                                _update_vehicle_state(*telemetry_sample)

                        now = time.time()
                        if mission_busy:
                            last_mav_rx = now
                        elif now - last_mav_rx > 5.0:
                            raise MavlinkLost("no MAVLink traffic for 5s")

                        if telemetry_sample is not None and now - last_send_time >= 1.0 / send_hz:
                            lat, lon, alt, heading = telemetry_sample
                            await ws.send(json.dumps(build_telemetry(
                                vehicle_id, VEHICLE_TYPE, latitude=lat, longitude=lon, altitude=alt, heading=heading,
                                behavior=behavior_tracker.get(), mode=_current_mode, armed=_armed_state, autopilot="ardupilot",
                            )))
                            last_send_time = now
                            telemetry_sample = None

                        if now - last_video_send_time >= 60.0:
                            await ws.send(json.dumps(_create_video_message(vehicle_id, video_url)))
                            last_video_send_time = now

                        await asyncio.sleep(0.01)
            except MavlinkLost:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                system_status.update({"ws_connected": False, "ws_status": "Server Offline (Retrying)"})
                print(f"[WARN] WebSocket session ended: {exc}")
                await asyncio.sleep(3.0)
    finally:
        ship_task.cancel()
        system_status.update({"ws_connected": False, "ws_status": "Disconnected"})
        with _mav_lock:
            _mav_master = None
        try:
            master.close()
        except Exception:
            pass


async def main() -> None:
    global config, reconnect_event
    config = load_config()
    reconnect_event = asyncio.Event()

    print("\n==============================")
    print(" ARDUPLANE (FLOAT PLANE) BRIDGE")
    print("==============================\n")
    await start_web_server()

    while True:
        reconnect_event.clear()
        task = asyncio.create_task(telemetry_loop(dict(config)))
        waiter = asyncio.create_task(reconnect_event.wait())
        done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if waiter in done:
            print("[INFO] Configuration changed; reconnecting...")
        waiter.cancel()
        if task not in done:
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            system_status.update({"cube_connected": False, "cube_status": f"Error: {exc}"})
            print(f"[ERROR] Bridge session failed: {exc}")
            traceback.print_exc()
            await asyncio.sleep(3.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] Shutting down cleanly")
