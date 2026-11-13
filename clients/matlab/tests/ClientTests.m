classdef ClientTests < matlab.unittest.TestCase
    %ClientTests Unit tests for the MATLAB TRIDENT client.

    properties
        client
        last_method
        last_url
        last_body
    end

    methods (TestMethodSetup)
        function createClient(testCase)
            addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'trident_client'));
            testCase.client = TridentClient('http://server/', 'token');
            testCase.client.transport_fn = @(method, url, body) testCase.mock_ack_transport(method, url, body);
        end
    end

    methods
        function [status, payload] = mock_ack_transport(testCase, method, url, body)
            testCase.last_method = method;
            testCase.last_url = url;
            testCase.last_body = body;
            status = 200;
            payload = struct( ...
                'ok', true, ...
                'delivered', true, ...
                'ack', struct( ...
                    'command_id', 'abc', ...
                    'vehicle_id', 'boat-01', ...
                    'source', 'api', ...
                    'issued_by', 'svc-x', ...
                    'stamp', 1.0, ...
                    'command', body ...
                ) ...
            );
        end
    end

    methods (Test)
        function test_goto_and_vehicle_handle_name_the_vehicle(testCase)
            ack = testCase.client.goto('boat-01', 38.9, -76.4);
            testCase.verifyEqual(testCase.last_method, 'POST');
            testCase.verifyEqual(testCase.last_url, 'http://server/api/vehicles/boat-01/command');
            testCase.verifyEqual(testCase.last_body, struct('type', 'waypoint', 'target', struct('latitude', 38.9, 'longitude', -76.4)));
            testCase.verifyEqual(ack.command_id, 'abc');
            testCase.verifyEqual(ack.source, 'api');
            testCase.verifyEqual(ack.issued_by, 'svc-x');

            testCase.client.vehicle('boat-02').goto(1, 2, 3);
            testCase.verifyEqual(testCase.last_url, 'http://server/api/vehicles/boat-02/command');
            testCase.verifyEqual(testCase.last_body.target.altitude, 3);
        end

        function test_mission_arrays_expand_altitude_and_validate_lengths(testCase)
            testCase.client.mission('uav-01', [1, 2], [3, 4], 25);
            wps = testCase.last_body.waypoints;
            testCase.verifyEqual([wps{1}.altitude, wps{2}.altitude], [25, 25]);

            testCase.client.mission('uav-01', [1, 2], [3, 4]);
            wps = testCase.last_body.waypoints;
            testCase.verifyFalse(isfield(wps{1}, 'altitude'));

            testCase.verifyError(@() testCase.client.mission('uav-01', [1, 2], [3]), 'TridentClient:ValueError');
            testCase.verifyError(@() testCase.client.mission('uav-01', [1, 2], [3, 4], [10]), 'TridentClient:ValueError');
        end

        function test_ship_relative_mission_and_direct_control_payloads(testCase)
            testCase.client.ship_relative_mission('uav-01', 'yp', [0, 10], [20, 20], 15, 'loops', 2);
            sent = testCase.last_body;
            testCase.verifyEqual(sent.ship_vehicle_id, 'yp');
            testCase.verifyEqual(sent.local_waypoints{2}, struct('x', 10, 'y', 20, 'z', 15));
            testCase.verifyEqual(sent.loop_count, 2);

            testCase.client.set_velocity('boat-01', 2, 90);
            testCase.verifyEqual(testCase.last_body, struct('type', 'set_velocity', 'speed_ms', 2, 'heading_deg', 90, 'vertical_ms', 0.0));

            ch = containers.Map({1}, {1500});
            testCase.client.rc_override('boat-01', ch);
            testCase.verifyEqual(testCase.last_body.type, 'rc_override');
            testCase.verifyEqual(testCase.last_body.channels('1'), 1500);

            testCase.client.release_rc('boat-01', [1, 3]);
            testCase.verifyEqual(testCase.last_body.channels('1'), 0);
            testCase.verifyEqual(testCase.last_body.channels('3'), 0);
        end

        function test_http_errors_map_to_exceptions(testCase)
            cases = {
                400, 'TridentClient:InvalidCommand';
                401, 'TridentClient:AuthenticationError';
                403, 'TridentClient:PermissionDenied';
                404, 'TridentClient:VehicleNotFound';
                409, 'TridentClient:VehicleNotConnected'
            };
            for i = 1:size(cases, 1)
                status = cases{i, 1};
                err_id = cases{i, 2};
                testCase.client.transport_fn = @(~, ~, ~) deal(status, struct('error', 'nope'));
                testCase.verifyError(@() testCase.client.rtb('boat-01'), err_id);
            end
        end

        function test_state_parsing(testCase)
            testCase.client.transport_fn = @(~, ~, ~) deal(200, struct( ...
                'vehicle_id', 'boat-01', ...
                'connected', true, ...
                'position', struct('latitude', 1, 'longitude', 2, 'altitude', 3), ...
                'heading_deg', 90, ...
                'velocity', struct('north_ms', 1, 'east_ms', 0, 'speed_ms', 1, 'course_deg', 0), ...
                'mode', 'GUIDED', ...
                'armed', true, ...
                'overlays', struct('waypoint', []) ...
            ));
            state = testCase.client.state('boat-01');
            testCase.verifyClass(state, 'VehicleState');
            testCase.verifyEqual(state.position, [1, 2, 3]);
            testCase.verifyEqual(state.speed_ms, 1.0);
            testCase.verifyEqual(state.mode, 'GUIDED');
        end

        function test_requires_url_and_token(testCase)
            orig_token = getenv('TRIDENT_TOKEN');
            setenv('TRIDENT_TOKEN', '');
            cleanup = onCleanup(@() setenv('TRIDENT_TOKEN', orig_token));
            testCase.verifyError(@() TridentClient('http://x', ''), 'TridentClient:ValueError');
        end

        function test_geo_helpers(testCase)
            testCase.verifyEqual(geo.distance_m(0, 0, 0, 1), 111319.5, 'AbsTol', 1.0);
            testCase.verifyEqual(geo.bearing_deg(0, 0, 1, 0), 0.0, 'AbsTol', 1e-6);
            testCase.verifyEqual(geo.bearing_deg(0, 0, 0, 1), 90.0, 'AbsTol', 1e-6);
            testCase.verifyEqual(geo.angle_diff_deg(10, 350), 20);
            [lat, lon] = geo.offset_latlon(38.0, -76.0, 100, 0);
            testCase.verifyEqual(geo.distance_m(38.0, -76.0, lat, lon), 100, 'AbsTol', 0.5);
        end
    end
end
