# trident-client (MATLAB)

MATLAB client for commanding TRIDENT vehicles and reading their state through the ground-station server.
Commands appear on the operators' map exactly as if sent from the web UI and are logged as `source=api`
with your service account's name.

Add `clients/matlab/trident_client` to your MATLAB path:

```matlab
addpath('clients/matlab/trident_client');
```

An administrator creates a service token (see the root README, "Scripting and programmatic control"), then configure the environment in your shell or directly in MATLAB:

```matlab
setenv('TRIDENT_URL', 'https://yp-server');
setenv('TRIDENT_TOKEN', '<token>');
```

## Commands

Every call names the vehicle and returns a `CommandAck` (`command_id`, `issued_by`, `source`, `stamp`, `command`).
The ack means the server delivered the command to the vehicle's bridge, not that the vehicle finished it.

```matlab
yp = TridentClient();                      % or TridentClient(url, token)
yp.goto('blueboat-1', 38.98, -76.48);      % altitude defaults to the vehicle's current altitude
yp.rtb('apache-1');
yp.mission('apache-1', lats, lons, alts);  % absolute mission from parallel arrays
yp.ship_relative_mission('apache-1', 'yp-1', [0, 20], [30, 30], 15, 'loops', 2);
yp.search_grid('apache-1', 38.98, -76.48, 'grid_size_m', 200);
yp.set_mode('blueboat-1', 'GUIDED');
yp.set_velocity('blueboat-1', 2.0, 270);
yp.set_heading('blueboat-1', 90);
yp.rc_override('blueboat-1', containers.Map({1, 3}, {1500, 1650})); % needs the rc_override permission
yp.release_rc('blueboat-1');

boat = yp.vehicle('blueboat-1');           % bind the name once
boat.goto(38.98, -76.48);
```

Ship-relative points use `x` meters to the ship's right, `y` meters ahead of its bow, `z` meters above it.
`set_velocity`, `set_heading` and `rc_override` are setpoints that the autopilot drops after a few seconds:
resend them from a loop. The vehicle must already be in a guided mode for velocity and heading.

HTTP errors throw MATLAB exceptions with identifiers under `TridentClient:*`:
`TridentClient:InvalidCommand` (400), `TridentClient:AuthenticationError` (401),
`TridentClient:PermissionDenied` (403), `TridentClient:VehicleNotFound` (404),
and `TridentClient:VehicleNotConnected` (409).

## State and feedback loops

```matlab
s = yp.state('blueboat-1');     % VehicleState: position, heading_deg, speed_ms, course_deg, mode, armed, ...
fleet = yp.vehicles();

goal = [38.98, -76.48];
while true
    s = yp.state('blueboat-1');
    if isempty(s.position)
        pause(0.2);
        continue;
    end
    dist = geo.distance_m(s.latitude, s.longitude, goal(1), goal(2));
    if dist < 3
        break;
    end
    yp.set_velocity('blueboat-1', min(2.0, dist / 5), geo.bearing_deg(s.latitude, s.longitude, goal(1), goal(2)));
    pause(0.2);
end
```

Velocity is estimated by the server from successive position fixes; roll and pitch are not available.
Use `yp.state(id).overlays` for the active waypoint, mission, search and ship-relative plans.

`examples/` has runnable scripts (`waypoint_and_mission.m` and `goto_with_feedback.m`).
