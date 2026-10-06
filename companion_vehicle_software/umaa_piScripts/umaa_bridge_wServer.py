from __future__ import annotations

import asyncio
import json
import math
import os
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any

from aiohttp import web
import websockets

# --- DEFAULT CONFIGURATION SETTINGS ---

CONFIG_PATH = Path(os.getenv("UMAA_CONFIG_PATH", "umaa_config.json"))

DEFAULT_CONFIG = {
    "SERVER_WS_URL": os.getenv("SERVER_WS_URL", "ws://192.168.0.174:8000/ws/vehicle"),
    "VEHICLE_ID": os.getenv("VEHICLE_ID", "garc-01"),
    "VEHICLE_TYPE": os.getenv("VEHICLE_TYPE", "usv"),
    "SEND_HZ": float(os.getenv("SEND_HZ", "5")),
    "UMAA_BACKEND": os.getenv("UMAA_BACKEND", "rti").lower(),
    "RTI_DOMAIN_ID": int(os.getenv("RTI_DOMAIN_ID", "3")),
    "RTI_QOS_FILE": os.getenv("RTI_QOS_FILE", "umaa_comms_setup.xml"),
    "RTI_SOURCE_GUID": os.getenv("RTI_SOURCE_GUID", "garc_source_guid"),
    "RTI_PUBLISHER_NAME": os.getenv("RTI_PUBLISHER_NAME", "umaaCommsProfile"),
    "RTI_SUBSCRIBER_NAME": os.getenv("RTI_SUBSCRIBER_NAME", "umaaCommsProfile"),
    "RTI_COMMAND_TOPIC": os.getenv("RTI_COMMAND_TOPIC", "PowerCommand"),
    "RTI_ACK_TOPIC": os.getenv("RTI_ACK_TOPIC", "PowerCommandAckReport"),
    "RTI_STATUS_TOPIC": os.getenv("RTI_STATUS_TOPIC", "PowerReport"),
    "RTI_EXEC_STATUS_TOPIC": os.getenv("RTI_EXEC_STATUS_TOPIC", "PowerCommandStatus"),
    "RTI_NAVSATFIX_TOPIC": os.getenv("RTI_NAVSATFIX_TOPIC", "NavSatFix"),
    "RTI_BATTERY_TOPIC": os.getenv("RTI_BATTERY_TOPIC", "BatteryState"),
    "RTI_HEARTBEAT_TOPIC": os.getenv("RTI_HEARTBEAT_TOPIC", "Heartbeat"),
}

CONFIG_FIELDS = [
    ("YP Connection", "SERVER_WS_URL", "YP server WebSocket URL", "ws://host:8000/ws/vehicle"),
    ("YP Connection", "VEHICLE_ID", "Vehicle ID", "garc-01"),
    ("YP Connection", "VEHICLE_TYPE", "Vehicle type", "usv"),
    ("YP Connection", "SEND_HZ", "Telemetry rate (Hz)", "5"),
    ("UMAA / RTI", "UMAA_BACKEND", "Backend", "rti"),
    ("UMAA / RTI", "RTI_DOMAIN_ID", "RTI domain ID", "3"),
    ("UMAA / RTI", "RTI_QOS_FILE", "RTI QoS file", "umaa_comms_setup.xml"),
    ("UMAA / RTI", "RTI_SOURCE_GUID", "RTI source GUID", "garc_source_guid"),
    ("UMAA / RTI", "RTI_PUBLISHER_NAME", "RTI publisher name", "umaaCommsProfile"),
    ("UMAA / RTI", "RTI_SUBSCRIBER_NAME", "RTI subscriber name", "umaaCommsProfile"),
    ("UMAA / RTI", "RTI_COMMAND_TOPIC", "Command topic", "PowerCommand"),
    ("UMAA / RTI", "RTI_ACK_TOPIC", "Acknowledgement topic", "PowerCommandAckReport"),
    ("UMAA / RTI", "RTI_STATUS_TOPIC", "Status topic", "PowerReport"),
    ("UMAA / RTI", "RTI_EXEC_STATUS_TOPIC", "Execution status topic", "PowerCommandStatus"),
    ("UMAA / RTI", "RTI_NAVSATFIX_TOPIC", "Navigation topic", "NavSatFix"),
    ("UMAA / RTI", "RTI_BATTERY_TOPIC", "Battery topic", "BatteryState"),
    ("UMAA / RTI", "RTI_HEARTBEAT_TOPIC", "Heartbeat topic", "Heartbeat"),
]

config_state = DEFAULT_CONFIG.copy()
restart_requested = asyncio.Event()

runtime_status: dict[str, Any] = {
    "status": "starting",
    "adapter_status": "starting",
    "server_connected": False,
    "last_telemetry_at": None,
    "last_command_at": None,
    "last_command_error": None,
    "last_error": None,
}

# --- DATA MODELS ---

@dataclass(slots=True)
class TelemetryEvent:
    vehicle_id: str
    vehicle_type: str
    topic: str
    msg_type: str
    msg: dict[str, Any]
    stamp: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "vehicle_type": self.vehicle_type,
            "topic": self.topic,
            "type": self.msg_type,
            "stamp": self.stamp,
            "msg": self.msg,
        }

# --- UMAA ADAPTER DEFINITIONS ---

class UmaaAdapter(ABC):
    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def drain_events(self) -> list[TelemetryEvent]:
        raise NotImplementedError

    @abstractmethod
    async def send_command(self, command: dict[str, Any], source: str | None = None) -> None:
        raise NotImplementedError


class LoopbackUmaaAdapter(UmaaAdapter):
    """
    Simulation backend for Ground Station development without real vehicles connected.
    """
    def __init__(self) -> None:
        self._running = False
        self._events: asyncio.Queue[TelemetryEvent] = asyncio.Queue()
        self._lat = float(os.getenv("LOOPBACK_LAT", "38.989639"))
        self._lon = float(os.getenv("LOOPBACK_LON", "-76.478643"))
        self._alt = 0.0
        self._heading = 0.0
        self._battery = 1.0
        self._last_tick = time.time()
        self._mode = "idle"

    async def start(self) -> None:
        self._running = True
        self._last_tick = time.time()
        await self._queue_bridge_status()

    async def stop(self) -> None:
        self._running = False

    async def drain_events(self) -> list[TelemetryEvent]:
        # Generate generic simulated events
        now = time.time()
        if now - self._last_tick >= (1.0 / max(1.0, config_state["SEND_HZ"])):
            self._last_tick = now
            await self._queue_heartbeat()
            await self._queue_navsatfix()
            await self._queue_bridge_status()

        events: list[TelemetryEvent] = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        return events

    async def send_command(self, command: dict[str, Any], source: str | None = None) -> None:
        print(f"[LOOPBACK] Received command: {command}")

    async def _queue_heartbeat(self) -> None:
        await self._events.put(TelemetryEvent(
            vehicle_id=config_state["VEHICLE_ID"], vehicle_type=config_state["VEHICLE_TYPE"],
            topic=f"/vehicles/{config_state['VEHICLE_ID']}/heartbeat", msg_type="yp_ground_station/msg/Heartbeat",
            msg={"mode": self._mode, "armed": False}
        ))

    async def _queue_navsatfix(self) -> None:
        stamp = time.time()
        sec, nanosec = int(stamp), int((stamp - int(stamp)) * 1e9)
        await self._events.put(TelemetryEvent(
            vehicle_id=config_state["VEHICLE_ID"], vehicle_type=config_state["VEHICLE_TYPE"],
            topic=f"/vehicles/{config_state['VEHICLE_ID']}/navsatfix", msg_type="sensor_msgs/msg/NavSatFix",
            msg={
                "header": {"stamp": {"sec": sec, "nanosec": nanosec}, "frame_id": "map"},
                "status": {"status": 0, "service": 1},
                "latitude": self._lat, "longitude": self._lon, "altitude": self._alt, "heading": self._heading,
            }
        ))

    async def _queue_bridge_status(self) -> None:
        await self._events.put(TelemetryEvent(
            vehicle_id=config_state["VEHICLE_ID"], vehicle_type=config_state["VEHICLE_TYPE"],
            topic=f"/vehicles/{config_state['VEHICLE_ID']}/status", msg_type="yp_ground_station/msg/BridgeStatus",
            msg={"backend": "loopback", "status": "connected", "mode": self._mode, "battery": self._battery}
        ))


class RtiConnextUmaaAdapter(UmaaAdapter):
    """
    RTI Connext DDS backend matching the UMAA standardized Interface Control Document (ICD).
    """
    def __init__(self) -> None:
        try:
            import rti.connextdds as dds
            self.dds = dds
        except ImportError as exc:
            raise SystemExit(
                "rti.connextdds is required for UMAA DDS bridging. Install the RTI Connext 6.x Python SDK "
                "and source its environment before starting this bridge."
            ) from exc

        self._events: asyncio.Queue[TelemetryEvent] = asyncio.Queue()
        self._running = False
        
        # DDS Entities
        self.participant = None
        self.publisher = None
        self.subscriber = None
        
        # DDS Writers/Readers
        self.power_command_writer = None
        self.power_report_reader = None
        self.nav_reader = None

    async def start(self) -> None:
        self._running = True
        try:
            # 1. Load QoS Profile
            print(f"[RTI] Loading QoS Profile: {config_state['RTI_QOS_FILE']}")
            qos_provider = self.dds.QosProvider(config_state["RTI_QOS_FILE"])

            # 2. Create Participant on Configured Domain (e.g. Domain 3)
            self.participant = self.dds.DomainParticipant(config_state["RTI_DOMAIN_ID"])
            
            # 3. Create Publisher and Subscriber
            self.publisher = self.dds.Publisher(self.participant)
            self.subscriber = self.dds.Subscriber(self.participant)

            # NOTE: Generated python types (like PowerCommandType, PowerReportType) typically need to be
            # imported from rtiddsgen output. In their absence, use generic DynamicData or fail gracefully.
            try:
                import umaa_types  # User's generated UMAA package
                self.PowerCommandType = umaa_types.PowerCommandType
                self.PowerReportType = umaa_types.PowerReportType
                self.NavSatFixType = umaa_types.NavSatFixType
                
                # 4. Create Writers and Readers
                cmd_topic = self.dds.Topic(self.participant, config_state["RTI_COMMAND_TOPIC"], self.PowerCommandType)
                self.power_command_writer = self.dds.DataWriter(self.publisher, cmd_topic)

                report_topic = self.dds.Topic(self.participant, config_state["RTI_STATUS_TOPIC"], self.PowerReportType)
                self.power_report_reader = self.dds.DataReader(self.subscriber, report_topic)

                nav_topic = self.dds.Topic(self.participant, config_state["RTI_NAVSATFIX_TOPIC"], self.NavSatFixType)
                self.nav_reader = self.dds.DataReader(self.subscriber, nav_topic)
                
            except ImportError:
                print("[WARN] Generated Python UMAA types not found. Operating DataWriters/Readers in scaffold mode.")
                
            # Start Async polling task for DDS readers
            self._reader_task = asyncio.create_task(self._dds_reader_loop())

        except Exception as e:
            print(f"[RTI] Error initializing DDS: {e}")
            traceback.print_exc()
            raise

    async def stop(self) -> None:
        self._running = False
        if hasattr(self, '_reader_task'):
            self._reader_task.cancel()
        if self.participant:
            self.participant.close()

    async def drain_events(self) -> list[TelemetryEvent]:
        events: list[TelemetryEvent] = []
        while not self._events.empty():
            events.append(self._events.get_nowait())
        return events

    async def send_command(self, command: dict[str, Any], source: str | None = None) -> None:
        """
        Translate JSON commands from YP Ground Station to C++/Python UMAA DDS Types.
        """
        cmd_type = command.get("type", "")
        
        # Example mapping of YP interface commands to UMAA power module control commands
        if cmd_type in ["power_control", "pulse", "set_mode"]:
            channel = command.get("channel", 0)
            is_on = command.get("isOn", False)
            
            print(f"[RTI] Sending PowerCommand -> Channel: {channel}, ON: {is_on}")
            
            if self.power_command_writer and hasattr(self, 'PowerCommandType'):
                # Instantiate generated type
                cmd_sample = self.PowerCommandType()
                cmd_sample.channel = channel
                cmd_sample.isOn = is_on
                # Write to DDS Bus
                self.power_command_writer.write(cmd_sample)
            else:
                print(f"[RTI-SCAFFOLD] Would dispatch UMAA command: {command}")
                
        elif cmd_type == "waypoint":
            print(f"[RTI-SCAFFOLD] Dispatching waypoint navigation: {command.get('target')}")
        else:
            print(f"[RTI] Unrecognized or unhandled command type: {cmd_type}")

    async def _dds_reader_loop(self) -> None:
        """
        Poll DataReaders asynchronously and push to the bridge event queue.
        """
        while self._running:
            try:
                # Poll Nav Reader
                if self.nav_reader:
                    nav_samples = self.nav_reader.take()
                    for sample in nav_samples:
                        if sample.info.valid:
                            data = sample.data
                            stamp = time.time()
                            await self._events.put(TelemetryEvent(
                                vehicle_id=config_state["VEHICLE_ID"], vehicle_type=config_state["VEHICLE_TYPE"],
                                topic=f"/vehicles/{config_state['VEHICLE_ID']}/navsatfix", msg_type="sensor_msgs/msg/NavSatFix",
                                msg={
                                    "header": {"stamp": {"sec": int(stamp), "nanosec": int((stamp - int(stamp))*1e9)}, "frame_id": "map"},
                                    "status": {"status": 0, "service": 1},
                                    "latitude": data.latitude, "longitude": data.longitude, "altitude": data.altitude,
                                }
                            ))
                
                # Poll Status/Power Reader
                if self.power_report_reader:
                    report_samples = self.power_report_reader.take()
                    for sample in report_samples:
                        if sample.info.valid:
                            # Forward power system updates as bridge status updates or generic JSON payloads
                            pass
                            
            except Exception as e:
                pass # Ignore polling timeouts
            
            await asyncio.sleep(1.0 / max(1.0, config_state["SEND_HZ"]))

# --- BRIDGE LIFECYCLE MANAGEMENT ---

def build_adapter() -> UmaaAdapter:
    if config_state["UMAA_BACKEND"] in {"rti", "rticonnext", "dds"}:
        return RtiConnextUmaaAdapter()
    return LoopbackUmaaAdapter()

async def send_vehicle_payload(ws: websockets.WebSocketClientProtocol, event: TelemetryEvent) -> None:
    await ws.send(json.dumps(event.to_payload()))

async def run_bridge() -> None:
    adapter: UmaaAdapter | None = None
    runtime_status.update(status="starting", adapter_status="starting", server_connected=False)
    
    try:
        adapter = build_adapter()
        await adapter.start()
        runtime_status["adapter_status"] = "ready"
        runtime_status["status"] = "connecting"

        uri = f"{config_state['SERVER_WS_URL'].rstrip('/')}/{config_state['VEHICLE_ID']}"
        print(f"[INFO] UMAA bridge backend={config_state['UMAA_BACKEND']} vehicle={config_state['VEHICLE_ID']}")
        print(f"[INFO] WebSocket URL: {uri}")

        while True:
            try:
                async with websockets.connect(uri, ping_interval=10, ping_timeout=10) as ws:
                    print(f"[INFO] Connected to YP server as {config_state['VEHICLE_ID']}")
                    runtime_status.update(status="connected", server_connected=True, last_error=None)
                    
                    while True:
                        # 1. Telemetry -> Server
                        for event in await adapter.drain_events():
                            await send_vehicle_payload(ws, event)
                            runtime_status["last_telemetry_at"] = time.time()

                        # 2. Server -> Commands
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0 / max(config_state["SEND_HZ"], 1.0))
                            payload = json.loads(raw)
                            command = payload.get("command", payload)
                            if isinstance(command, dict):
                                await adapter.send_command(command, source=str(payload.get("source", "ui")))
                                runtime_status["last_command_at"] = time.time()
                                
                        except asyncio.TimeoutError:
                            continue
                        except json.JSONDecodeError:
                            continue
                        except Exception as exc:
                            runtime_status["last_command_error"] = str(exc)
                            
            except Exception as exc:
                runtime_status.update(status="reconnecting", server_connected=False, last_error=str(exc))
                await asyncio.sleep(2.0)
    finally:
        runtime_status.update(status="stopped", adapter_status="stopped", server_connected=False)
        if adapter:
            await adapter.stop()


# --- WEB SERVER (UI & CONFIGURATION) ---

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GARC UMAA Bridge Control</title>
  <style>
    :root { color-scheme: light; font-family: "Segoe UI", sans-serif; background: #eef3f2; color: #1c2927; }
    body { max-width: 900px; margin: 0 auto; padding: 28px 20px; }
    h1 { margin: 0 0 22px; font-size: 1.6rem; }
    .eyebrow { color: #16786a; font-size: .8rem; font-weight: 700; text-transform: uppercase; }
    h2 { margin: 28px 0 12px; font-size: 1.1rem; }
    .status { padding: 16px; background: #fff; border-left: 4px solid #16786a; }
    dl { display: grid; grid-template-columns: minmax(140px, 1fr) 2fr; gap: 10px 20px; margin: 0; }
    dt { color: #53635f; }
    dd { margin: 0; overflow-wrap: anywhere; font-weight: 600; }
    .error { color: #a12c25; white-space: pre-wrap; }
    form { padding: 18px; background: #fff; }
    fieldset { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 18px; padding: 0; border: 0; }
    legend { grid-column: 1 / -1; margin: 4px 0; font-weight: 700; }
    label { display: grid; gap: 5px; color: #53635f; font-size: .9rem; }
    input, select { width: 100%; min-width: 0; box-sizing: border-box; padding: 9px 10px; border: 1px solid #aebbb7; border-radius: 3px; background: #fff; color: #1c2927; font: inherit; }
    input:focus, select:focus { outline: 2px solid #16786a; outline-offset: 1px; }
    button { margin-top: 18px; padding: 10px 16px; border: 0; border-radius: 3px; background: #176c60; color: #fff; font: inherit; font-weight: 700; cursor: pointer; }
    .hint { margin: 12px 0 0; color: #53635f; font-size: .88rem; }
    @media (max-width: 560px) { body { padding: 22px 14px; } fieldset { grid-template-columns: 1fr; } dl { grid-template-columns: 1fr; gap: 4px; } dd { margin-bottom: 8px; } }
  </style>
</head>
<body>
  <div class="eyebrow">Companion Vehicle Software</div>
  <h1>GARC UMAA Bridge (Domain 3)</h1>
  <h2>Runtime</h2>
  <section class="status" aria-live="polite">
    <dl>
      <dt>Bridge status</dt><dd id="status">Loading</dd>
      <dt>Adapter status</dt><dd id="adapter">Loading</dd>
      <dt>YP server</dt><dd id="server">Loading</dd>
      <dt>Vehicle</dt><dd id="vehicle">Loading</dd>
      <dt>Backend</dt><dd id="backend">Loading</dd>
      <dt>Last telemetry</dt><dd id="telemetry">Never</dd>
      <dt>Last command</dt><dd id="command">Never</dd>
      <dt>Last command error</dt><dd id="command-error" class="error">None</dd>
      <dt>Last error</dt><dd id="error" class="error">None</dd>
    </dl>
  </section>
  <h2>Bridge Configuration</h2>
  <form method="post" action="/save">
    __CONFIG_FIELDS__
    <button type="submit">Save and restart bridge</button>
  </form>
  <script>
    const showTime = value => value ? new Date(value * 1000).toLocaleString() : 'Never';
    async function refresh() {
      try {
        const response = await fetch('/api/status');
        const data = await response.json();
        document.getElementById('status').textContent = data.status;
        document.getElementById('adapter').textContent = data.adapter_status;
        document.getElementById('server').textContent = data.server_connected ? 'Connected' : 'Disconnected';
        document.getElementById('vehicle').textContent = `${data.vehicle_id} (${data.vehicle_type})`;
        document.getElementById('backend').textContent = data.backend;
        document.getElementById('telemetry').textContent = showTime(data.last_telemetry_at);
        document.getElementById('command').textContent = showTime(data.last_command_at);
        document.getElementById('command-error').textContent = data.last_command_error || 'None';
        document.getElementById('error').textContent = data.last_error || 'None';
      } catch (error) {
        document.getElementById('status').textContent = 'Diagnostics unavailable';
      }
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>"""

def render_fields(configuration: dict[str, object]) -> str:
    sections: dict[str, list[str]] = {}
    for group, name, label, placeholder in CONFIG_FIELDS:
        val = configuration.get(name, placeholder)
        value = escape(str(val), quote=True)
        if name == "UMAA_BACKEND":
            control = (
                '<select id="UMAA_BACKEND" name="UMAA_BACKEND">'
                f'<option value="loopback"{" selected" if value == "loopback" else ""}>loopback simulation</option>'
                f'<option value="rti"{" selected" if value in {"rti", "rticonnext", "dds"} else ""}>RTI Connext DDS</option>'
                "</select>"
            )
        else:
            input_type = "number" if name in {"SEND_HZ", "RTI_DOMAIN_ID"} else "text"
            step = ' step="any"' if name == "SEND_HZ" else ""
            control = f'<input id="{name}" name="{name}" type="{input_type}" value="{value}" placeholder="{escape(str(placeholder), quote=True)}"{step}>'
            
        sections.setdefault(group, []).append(f'<label for="{name}">{escape(label)}{control}</label>')

    return "".join(
        f'<fieldset><legend>{escape(group)}</legend>{"".join(fields)}</fieldset>'
        for group, fields in sections.items()
    )

async def handle_index(request: web.Request) -> web.Response:
    page = PAGE.replace("__CONFIG_FIELDS__", render_fields(config_state))
    return web.Response(text=page, content_type="text/html")

async def handle_status(request: web.Request) -> web.Response:
    return web.json_response({
        "vehicle_id": config_state.get('VEHICLE_ID', 'garc-01'),
        "vehicle_type": config_state.get('VEHICLE_TYPE', 'usv'),
        "backend": config_state.get('UMAA_BACKEND', 'rti'),
        **runtime_status,
    })

async def handle_save(request: web.Request) -> web.Response:
    global config_state
    post_data = dict(await request.post())
    
    # Simple validation & casting
    for key, value in post_data.items():
        if key in ["SEND_HZ"]:
            config_state[key] = float(value)
        elif key in ["RTI_DOMAIN_ID"]:
            config_state[key] = int(value)
        else:
            config_state[key] = str(value).strip()
            
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config_state, f, indent=2)
        
    restart_requested.set()
    return web.HTTPSeeOther(location="/")

def load_configuration() -> None:
    global config_state
    if CONFIG_PATH.is_file():
        try:
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
                config_state.update(saved)
        except Exception as e:
            print(f"Error loading {CONFIG_PATH}: {e}")

async def main() -> None:
    load_configuration()
    
    # Initialize UI Application
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/save", handle_save)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("WEB_PORT", "8082"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[INFO] UMAA bridge web interface available at http://0.0.0.0:{port}")

    # Launch background bridge logic loop
    while True:
        restart_requested.clear()
        bridge_task = asyncio.create_task(run_bridge(), name="umaa-bridge-loop")
        
        await restart_requested.wait()
        
        print("[INFO] Settings updated via UI! Restarting bridge loop...")
        bridge_task.cancel()
        try:
            await bridge_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] UMAA bridge shutting down cleanly")