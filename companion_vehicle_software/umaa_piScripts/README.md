# UMAA Bridge Starter

This companion package is a starter harness for vehicles that speak UMAA over RTI Connext DDS.

What it does:
- Connects to the YP websocket contract at `/ws/vehicle/{vehicle_id}`.
- Forwards UMAA-style telemetry as unified YP `telemetry` messages (position, heading, behavior, mode, armed, battery) and `mission_complete` events.
- Accepts YP commands and passes them into a pluggable UMAA adapter.

Current adapters:
- `loopback` - local test mode that now moves toward received waypoints, turns gradually, and drains battery over time.
- `rti_connext` - DDS adapter shell that is ready to be wired to the generated UMAA types and command/report services from the RTI starter kit.

How to use it:
1. Set `VEHICLE_ID`, `VEHICLE_TYPE`, and `SERVER_WS_URL`.
2. Leave `UMAA_BACKEND=loopback` to smoke-test the bridge.
3. Select `UMAA_BACKEND=rti` only after implementing the vehicle-specific DDS participant, type support, and command/report mappings. Entering topic names alone does not enable the hardware path.

Run `python umaa_bridge_wServer.py` to start the bridge with its local
configuration and diagnostics page at `http://localhost:8082` (`WEB_PORT`
overrides the port). Configuration is saved to `umaa_config.json` by default;
`UMAA_CONFIG_PATH` changes that location. Saving settings restarts the bridge
connection. The page reports YP connectivity, adapter state, recent
telemetry/commands, and errors. Run `python umaa_bridge.py` without the local
HTTP server.

Recommended workflow for UMAA:
1. Run the sim bridge first with `docker compose up sim-umaa`.
2. Use `sim-umaa` in the UI and verify commands, map updates, and SAR routing against loopback telemetry.
3. When the real vehicle arrives, switch to `docker compose --profile umaa-real up umaa-bridge` and fill in the RTI topic names.

Smoke test client:
- `python companion_vehicle_software/umaa_piScripts/sim_umaa_smoke_test.py`
- It connects to `sim-umaa`, sends a waypoint 25 m east, prints telemetry for a few seconds, then sends RTB.
- Override `--waypoint-distance-m`, `--waypoint-bearing-deg`, or `--rtb-wait-s` if you want a longer or shorter run.

Loopback tuning knobs:
- `LOOPBACK_SPEED_MPS`
- `LOOPBACK_TURN_RATE_DPS`
- `LOOPBACK_ARRIVAL_RADIUS_M`
- `LOOPBACK_BATTERY_DRAIN_PER_M`
- `LOOPBACK_BATTERY_DRAIN_PER_S`

RTI wiring knobs:
- `RTI_DOMAIN_ID`
- `RTI_QOS_FILE`
- `RTI_COMMAND_TOPIC`
- `RTI_ACK_TOPIC`
- `RTI_STATUS_TOPIC`
- `RTI_NAVSATFIX_TOPIC`
- `RTI_BATTERY_TOPIC`
- `RTI_HEARTBEAT_TOPIC`
- `RTI_SOURCE_GUID`
- `RTI_PUBLISHER_NAME`
- `RTI_SUBSCRIBER_NAME`

The DDS side is intentionally isolated in `RtiConnextUmaaAdapter` so the topic map for a specific UMAA vehicle can be added without touching the websocket contract or the UI. The RTI adapter is not yet a working hardware connection; see `IMPLEMENTATION_BASELINE.md` for the current implementation gaps.