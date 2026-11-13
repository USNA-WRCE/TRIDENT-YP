function goto_with_feedback(vehicle_id, lat, lon)
%goto_with_feedback Drive a vehicle to a point with a proportional speed controller using live state.
%
% Usage:
%   setenv('TRIDENT_URL', 'https://yp-server');
%   setenv('TRIDENT_TOKEN', '<token>');
%   goto_with_feedback('blueboat-1', 38.98, -76.48)

    if nargin < 1 || isempty(vehicle_id)
        vehicle_id = 'blueboat-1';
    end
    if nargin < 2 || isempty(lat)
        lat = 38.98;
    end
    if nargin < 3 || isempty(lon)
        lon = -76.48;
    end
    if ischar(lat) || isstring(lat)
        lat = str2double(lat);
    end
    if ischar(lon) || isstring(lon)
        lon = str2double(lon);
    end

    script_dir = fileparts(mfilename('fullpath'));
    addpath(fullfile(script_dir, '..', 'trident_client'));

    MAX_SPEED_MS = 2.0;
    ARRIVAL_M = 3.0;
    POLL_INTERVAL_S = 0.2;

    yp = TridentClient();
    cleanup = onCleanup(@() yp.close());

    yp.set_mode(vehicle_id, 'GUIDED');
    while true
        state = yp.state(vehicle_id);
        if isempty(state.position)
            pause(POLL_INTERVAL_S);
            continue;
        end
        distance = geo.distance_m(state.latitude, state.longitude, lat, lon);
        if distance < ARRIVAL_M
            heading = state.heading_deg;
            if isempty(heading)
                heading = 0.0;
            end
            yp.set_velocity(vehicle_id, 0.0, heading);
            disp('arrived');
            break;
        end
        speed = min(MAX_SPEED_MS, distance / 5.0);
        bearing = geo.bearing_deg(state.latitude, state.longitude, lat, lon);
        yp.set_velocity(vehicle_id, speed, bearing);
        pause(POLL_INTERVAL_S);
    end
end
