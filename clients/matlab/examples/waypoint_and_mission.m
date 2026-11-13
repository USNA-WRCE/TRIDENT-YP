% Send a waypoint, then a short absolute mission, and print the acks.
%
% Usage:
%   setenv('TRIDENT_URL', 'https://yp-server');
%   setenv('TRIDENT_TOKEN', '<token>');
%   waypoint_and_mission

script_dir = fileparts(mfilename('fullpath'));
addpath(fullfile(script_dir, '..', 'trident_client'));

vehicle_id = 'sim-uav1';

yp = TridentClient();
cleanup = onCleanup(@() yp.close());

disp(yp.goto(vehicle_id, 38.990203, -76.485314, 20));
disp(yp.mission(vehicle_id, [38.9800, 38.9810, 38.9810], [-76.4800, -76.4800, -76.4790], 30));
disp(yp.state(vehicle_id).overlays.mission_plan);
