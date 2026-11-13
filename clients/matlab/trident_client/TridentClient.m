classdef TridentClient < handle
    %TridentClient Connection to a TRIDENT server. Every command names the target vehicle explicitly.
    %   url and token default to the TRIDENT_URL and TRIDENT_TOKEN environment variables.

    properties (SetAccess = immutable)
        url char
        timeout double = 10.0
    end

    properties (Access = ?ClientTests)
        token char
        verify = true
        transport_fn
    end

    methods
        function obj = TridentClient(url, token, varargin)
            if nargin < 1 || isempty(url)
                url = getenv('TRIDENT_URL');
            end
            if nargin < 2 || isempty(token)
                token = getenv('TRIDENT_TOKEN');
            end
            if isempty(url)
                error('TridentClient:ValueError', 'server url is required (argument or TRIDENT_URL)');
            end
            if isempty(token)
                error('TridentClient:ValueError', 'service token is required (argument or TRIDENT_TOKEN)');
            end

            p = inputParser();
            p.addParameter('timeout', 10.0);
            p.addParameter('verify', true);
            p.parse(varargin{:});

            clean_url = char(url);
            while ~isempty(clean_url) && clean_url(end) == '/'
                clean_url(end) = [];
            end
            obj.url = clean_url;
            obj.token = char(token);
            obj.timeout = double(p.Results.timeout);
            obj.verify = p.Results.verify;
            obj.transport_fn = @obj.default_transport;
        end

        function close(~)
            %close Close client session (no-op for stateless MATLAB HTTP transport).
        end

        % ---- state -------------------------------------------------------------------------

        function list = vehicles(obj)
            %vehicles State of every connected vehicle.
            body = obj.request('GET', '/api/fleet/state');
            raw_list = body.vehicles;
            if isempty(raw_list)
                list = VehicleState.empty(1, 0);
                return;
            end
            if iscell(raw_list)
                n = numel(raw_list);
                list = repmat(VehicleState(), 1, n);
                for i = 1:n
                    list(i) = VehicleState.from_json(raw_list{i});
                end
            else
                n = numel(raw_list);
                list = repmat(VehicleState(), 1, n);
                for i = 1:n
                    list(i) = VehicleState.from_json(raw_list(i));
                end
            end
        end

        function s = state(obj, vehicle_id)
            %state Latest state of one vehicle, including its active map overlays in .overlays.
            body = obj.request('GET', sprintf('/api/vehicles/%s/state', char(vehicle_id)));
            s = VehicleState.from_json(body);
        end

        % ---- commands ----------------------------------------------------------------------

        function ack = send_command(obj, vehicle_id, command)
            %send_command Send a raw command struct (struct('type', ..., ...)) to a vehicle.
            body = obj.request('POST', sprintf('/api/vehicles/%s/command', char(vehicle_id)), command);
            delivered = true;
            if isfield(body, 'delivered') && ~isempty(body.delivered)
                delivered = logical(body.delivered);
            end
            ack = CommandAck(body.ack, delivered);
        end

        function ack = goto(obj, vehicle_id, latitude, longitude, altitude)
            %goto Go to one point. The map shows the waypoint. altitude defaults to the vehicle's current altitude.
            target = struct('latitude', double(latitude), 'longitude', double(longitude));
            if nargin >= 5 && ~isempty(altitude)
                target.altitude = double(altitude);
            end
            ack = obj.send_command(vehicle_id, struct('type', 'waypoint', 'target', target));
        end

        function ack = rtb(obj, vehicle_id)
            %rtb Return to the mother vessel and follow it.
            ack = obj.send_command(vehicle_id, struct('type', 'rtb'));
        end

        function ack = land_on_boat(obj, vehicle_id)
            ack = obj.send_command(vehicle_id, struct('type', 'land_on_boat'));
        end

        function ack = arm(obj, vehicle_id)
            ack = obj.send_command(vehicle_id, struct('type', 'arm'));
        end

        function ack = disarm(obj, vehicle_id)
            ack = obj.send_command(vehicle_id, struct('type', 'disarm'));
        end

        function ack = takeoff(obj, vehicle_id, altitude_m)
            if nargin < 3 || isempty(altitude_m)
                altitude_m = 15.0;
            end
            ack = obj.send_command(vehicle_id, struct('type', 'takeoff', 'altitude_m', double(altitude_m)));
        end

        function ack = set_mode(obj, vehicle_id, mode)
            %set_mode Set the autopilot mode by name, e.g. "GUIDED" or "HOLD".
            ack = obj.send_command(vehicle_id, struct('type', 'set_mode', 'mode', char(mode)));
        end

        function ack = cancel_search(obj, vehicle_id)
            %cancel_search Cancel a running search grid / man-overboard pattern.
            ack = obj.send_command(vehicle_id, struct('type', 'cancel_sar'));
        end

        function ack = search_grid(obj, vehicle_id, latitude, longitude, varargin)
            %search_grid Fly a lawnmower search grid centred on a point. The map shows the pattern.
            p = inputParser();
            p.addParameter('grid_size_m', 200.0);
            p.addParameter('swath_m', 20.0);
            p.addParameter('altitude_m', 30.0);
            p.parse(varargin{:});
            cmd = struct( ...
                'type', 'search_grid', ...
                'lat', double(latitude), ...
                'lon', double(longitude), ...
                'grid_size_m', double(p.Results.grid_size_m), ...
                'swath_m', double(p.Results.swath_m), ...
                'altitude_m', double(p.Results.altitude_m) ...
            );
            ack = obj.send_command(vehicle_id, cmd);
        end

        function ack = mission(obj, vehicle_id, latitudes, longitudes, varargin)
            %mission Upload and start an absolute mission from parallel lat/lon/alt arrays. The map shows the route.
            %   altitudes may be a vector, a single value for every point, or [] (current altitude).
            if numel(latitudes) ~= numel(longitudes)
                error('TridentClient:ValueError', 'latitudes and longitudes must have the same length');
            end
            altitudes = [];
            args = varargin;
            if ~isempty(args) && (isnumeric(args{1}) || iscell(args{1}))
                altitudes = args{1};
                args = args(2:end);
            end

            p = inputParser();
            p.addParameter('acceptance_radius_m', 8.0);
            p.addParameter('hold_time_s', 0.0);
            p.addParameter('auto_arm_start', true);
            p.addParameter('force_guided_on_complete', false);
            p.parse(args{:});

            count = numel(latitudes);
            alts = TridentClient.per_point(altitudes, count, 'altitudes', []);
            waypoints = cell(1, count);
            for i = 1:count
                pt = struct( ...
                    'latitude', double(latitudes(i)), ...
                    'longitude', double(longitudes(i)), ...
                    'acceptance_radius_m', double(p.Results.acceptance_radius_m), ...
                    'hold_time_s', double(p.Results.hold_time_s) ...
                );
                if ~isempty(alts{i})
                    pt.altitude = double(alts{i});
                end
                waypoints{i} = pt;
            end
            cmd = struct( ...
                'type', 'mission_plan', ...
                'waypoints', {waypoints}, ...
                'auto_arm_start', logical(p.Results.auto_arm_start), ...
                'force_guided_on_complete', logical(p.Results.force_guided_on_complete) ...
            );
            ack = obj.send_command(vehicle_id, cmd);
        end

        function ack = ship_relative_mission(obj, vehicle_id, ship_id, x_m, y_m, varargin)
            %ship_relative_mission Fly points defined relative to a ship, tracking it as it moves.
            %   x_m is meters to the ship's right (starboard), y_m meters ahead of the ship's bow
            %   and z_m meters above the ship. yaw_deg is an optional heading relative to the ship's heading.
            if numel(x_m) ~= numel(y_m)
                error('TridentClient:ValueError', 'x_m and y_m must have the same length');
            end
            z_m = 0.0;
            yaw_deg = [];
            args = varargin;
            if ~isempty(args) && (isnumeric(args{1}) || iscell(args{1}))
                z_m = args{1};
                args = args(2:end);
                if ~isempty(args) && (isnumeric(args{1}) || iscell(args{1}))
                    yaw_deg = args{1};
                    args = args(2:end);
                end
            end

            p = inputParser();
            p.addParameter('z_m', z_m);
            p.addParameter('yaw_deg', yaw_deg);
            p.addParameter('arrival_radius_m', 6.0);
            p.addParameter('update_hz', 10.0);
            p.addParameter('face_ship', false);
            p.addParameter('loops', 1);
            p.addParameter('hold_last_waypoint', false);
            p.parse(args{:});

            count = numel(x_m);
            zs = TridentClient.per_point(p.Results.z_m, count, 'z_m', 0.0);
            yaws = TridentClient.per_point(p.Results.yaw_deg, count, 'yaw_deg', []);
            points = cell(1, count);
            for i = 1:count
                pt = struct('x', double(x_m(i)), 'y', double(y_m(i)), 'z', double(zs{i}));
                if ~isempty(yaws{i})
                    pt.yaw_deg = double(yaws{i});
                end
                points{i} = pt;
            end
            cmd = struct( ...
                'type', 'ship_relative_trajectory', ...
                'ship_vehicle_id', char(ship_id), ...
                'local_waypoints', {points}, ...
                'arrival_radius_m', double(p.Results.arrival_radius_m), ...
                'update_hz', double(p.Results.update_hz), ...
                'face_ship', logical(p.Results.face_ship), ...
                'loop_count', round(double(p.Results.loops)), ...
                'hold_last_waypoint', logical(p.Results.hold_last_waypoint) ...
            );
            ack = obj.send_command(vehicle_id, cmd);
        end

        function ack = set_velocity(obj, vehicle_id, speed_ms, heading_deg, vertical_ms)
            %set_velocity Command a ground speed along a true-north heading (the vehicle also points that way).
            %   Vehicles must be in a guided mode and the autopilot drops the setpoint after a few seconds,
            %   so call this repeatedly (a few Hz) from a control loop. vertical_ms is positive up.
            if nargin < 5 || isempty(vertical_ms)
                vertical_ms = 0.0;
            end
            cmd = struct( ...
                'type', 'set_velocity', ...
                'speed_ms', double(speed_ms), ...
                'heading_deg', double(heading_deg), ...
                'vertical_ms', double(vertical_ms) ...
            );
            ack = obj.send_command(vehicle_id, cmd);
        end

        function ack = set_heading(obj, vehicle_id, heading_deg)
            %set_heading Point the vehicle at a true-north heading.
            ack = obj.send_command(vehicle_id, struct('type', 'set_heading', 'heading_deg', double(heading_deg)));
        end

        function ack = rc_override(obj, vehicle_id, channels)
            %rc_override Override RC channels with PWM values in microseconds.
            %   channels may be a containers.Map, dictionary, or struct (e.g. struct('x1', 1500) or
            %   containers.Map({1, 3}, {1500, 1650})). Unlisted channels are untouched and 0 releases
            %   a channel to the radio. Requires the rc_override permission.
            chan_map = containers.Map('KeyType', 'char', 'ValueType', 'any');
            if isa(channels, 'containers.Map')
                ks = keys(channels);
                for i = 1:numel(ks)
                    k = ks{i};
                    chan_map(num2str(k)) = round(double(channels(k)));
                end
            elseif isstruct(channels)
                fns = fieldnames(channels);
                for i = 1:numel(fns)
                    fn = fns{i};
                    key = regexprep(fn, '^x(?=\d+$)', '');
                    chan_map(key) = round(double(channels.(fn)));
                end
            else
                error('TridentClient:ValueError', 'channels must be a containers.Map or struct');
            end
            ack = obj.send_command(vehicle_id, struct('type', 'rc_override', 'channels', chan_map));
        end

        function ack = release_rc(obj, vehicle_id, channels)
            %release_rc Release RC channels (default 1-8) back to the pilot's radio.
            if nargin < 3 || isempty(channels)
                channels = 1:8;
            end
            chan_map = containers.Map('KeyType', 'char', 'ValueType', 'any');
            for i = 1:numel(channels)
                chan_map(num2str(channels(i))) = 0;
            end
            ack = obj.rc_override(vehicle_id, chan_map);
        end

        function v = vehicle(obj, vehicle_id)
            %vehicle Handle that binds vehicle_id so calls read boat.goto(lat, lon).
            v = Vehicle(obj, vehicle_id);
        end
    end

    methods (Access = private)
        function payload = request(obj, method, path, body)
            if nargin < 4
                body = [];
            end
            full_url = [obj.url, path];
            [status_code, payload] = obj.transport_fn(method, full_url, body);
            if status_code < 400
                return;
            end
            if isstruct(payload) && isfield(payload, 'error') && ~isempty(payload.error)
                msg = char(payload.error);
            else
                msg = sprintf('HTTP %d', status_code);
            end
            switch status_code
                case 400
                    err_id = 'TridentClient:InvalidCommand';
                case 401
                    err_id = 'TridentClient:AuthenticationError';
                case 403
                    err_id = 'TridentClient:PermissionDenied';
                case 404
                    err_id = 'TridentClient:VehicleNotFound';
                case 409
                    err_id = 'TridentClient:VehicleNotConnected';
                otherwise
                    err_id = 'TridentClient:TridentError';
            end
            error(err_id, '%s (HTTP %d)', msg, status_code);
        end

        function [status_code, payload] = default_transport(obj, method, full_url, body)
            try
                auth_header = matlab.net.http.HeaderField('Authorization', ['Bearer ', obj.token]);
                if strcmpi(method, 'GET')
                    req = matlab.net.http.RequestMessage(matlab.net.http.RequestMethod.GET, auth_header);
                else
                    content_header = matlab.net.http.HeaderField('Content-Type', 'application/json');
                    json_body = matlab.net.http.MessageBody();
                    json_body.Payload = uint8(unicode2native(jsonencode(body), 'UTF-8'));
                    req = matlab.net.http.RequestMessage(matlab.net.http.RequestMethod.POST, [auth_header, content_header], json_body);
                end
                opts = matlab.net.http.HTTPOptions('ConnectTimeout', obj.timeout, 'ResponseTimeout', obj.timeout);
                if islogical(obj.verify) && ~obj.verify
                    opts.VerifyServerName = false;
                    opts.CertificateFilename = '';
                elseif ischar(obj.verify) || isstring(obj.verify)
                    opts.CertificateFilename = char(obj.verify);
                end
                resp = req.send(matlab.net.URI(full_url), opts);
                status_code = double(resp.StatusCode);
                if isempty(resp.Body) || isempty(resp.Body.Data)
                    payload = struct();
                elseif ischar(resp.Body.Data) || isstring(resp.Body.Data)
                    try
                        payload = jsondecode(char(resp.Body.Data));
                    catch
                        payload = struct();
                    end
                elseif isstruct(resp.Body.Data)
                    payload = resp.Body.Data;
                else
                    payload = struct();
                end
            catch err
                error('TridentClient:TridentError', 'cannot reach server: %s', err.message);
            end
        end
    end

    methods (Static, Access = private)
        function out = per_point(values, count, name, default_val)
            if isempty(values)
                out = repmat({default_val}, 1, count);
                return;
            end
            if isnumeric(values) && isscalar(values)
                out = repmat({double(values)}, 1, count);
                return;
            end
            if numel(values) ~= count
                error('TridentClient:ValueError', '%s has %d entries but %d waypoints were given', name, numel(values), count);
            end
            out = cell(1, count);
            if iscell(values)
                for i = 1:count
                    out{i} = values{i};
                end
            else
                for i = 1:count
                    out{i} = double(values(i));
                end
            end
        end
    end
end
