# trident-client

Python client for commanding TRIDENT vehicles and reading their state through the ground-station server.
Commands appear on the operators' map exactly as if sent from the web UI and are logged as `source=api`
with your service account's name.

```bash
pip install ./clients/python            # requests only
pip install "./clients/python[stream]"  # plus websockets, for live streaming
```

An administrator creates a service token (see the root README, "Scripting and programmatic control"), then:

```bash
export TRIDENT_URL=https://yp-server
export TRIDENT_TOKEN=<token>
```

## Commands

Every call names the vehicle and returns a `CommandAck` (`command_id`, `issued_by`, `source`, `stamp`, `command`).
The ack means the server delivered the command to the vehicle's bridge, not that the vehicle finished it.

```python
from trident_client import TridentClient

yp = TridentClient()                      # or TridentClient(url, token)
yp.goto("blueboat-1", 38.98, -76.48)      # altitude defaults to the vehicle's current altitude
yp.rtb("apache-1")
yp.mission("apache-1", lats, lons, alts)  # absolute mission from parallel arrays
yp.ship_relative_mission("apache-1", "yp-1", x_m=[0, 20], y_m=[30, 30], z_m=15, loops=2)
yp.search_grid("apache-1", 38.98, -76.48, grid_size_m=200)
yp.set_mode("blueboat-1", "GUIDED")
yp.set_velocity("blueboat-1", speed_ms=2.0, heading_deg=270)
yp.set_heading("blueboat-1", 90)
yp.rc_override("blueboat-1", {1: 1500, 3: 1650})   # needs the rc_override permission
yp.release_rc("blueboat-1")

boat = yp.vehicle("blueboat-1")           # bind the name once
boat.goto(38.98, -76.48)
```

Ship-relative points use `x` meters to the ship's right, `y` meters ahead of its bow, `z` meters above it.
`set_velocity`, `set_heading` and `rc_override` are setpoints that the autopilot drops after a few seconds:
resend them from a loop. The vehicle must already be in a guided mode for velocity and heading.

Errors derive from `TridentError`: `InvalidCommand`, `AuthenticationError`, `PermissionDenied`,
`VehicleNotFound`, and `VehicleNotConnected` (carry `.status` and `.payload`).

## State and feedback loops

```python
from trident_client import geo

s = yp.state("blueboat-1")     # VehicleState: position, heading_deg, speed_ms, course_deg, mode, armed, ...
fleet = yp.vehicles()

goal = (38.98, -76.48)
for s in yp.stream_states(["blueboat-1"]):          # pushed on every telemetry update
    if s.position is None:
        continue
    dist = geo.distance_m(s.latitude, s.longitude, *goal)
    if dist < 3:
        break
    yp.set_velocity("blueboat-1", min(2.0, dist / 5), geo.bearing_deg(s.latitude, s.longitude, *goal))
```

Velocity is estimated by the server from successive position fixes; roll and pitch are not available.
Use `yp.stream()` for raw events, including `command_ack` from every operator, and `yp.state(id).overlays`
for the active waypoint, mission, search and ship-relative plans.

`examples/` has runnable scripts.
