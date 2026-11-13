classdef VehicleState
    %VehicleState Latest known state of one vehicle.
    %   Heading and position come from the vehicle's autopilot. Velocity is estimated
    %   by the server from successive position fixes, so it is noisy at low speed.
    %   Roll and pitch are not reported by bridges.

    properties (SetAccess = immutable)
        vehicle_id char
        vehicle_type
        connected logical = true
        age_s
        latitude
        longitude
        altitude
        heading_deg
        north_ms double = 0.0
        east_ms double = 0.0
        speed_ms double = 0.0
        course_deg
        mode
        armed
        behavior
        battery
        overlays struct = struct()
        raw struct = struct()
    end

    properties (Dependent)
        position
    end

    methods
        function obj = VehicleState(data)
            if nargin == 0 || isempty(data)
                return;
            end
            obj.vehicle_id = char(data.vehicle_id);
            obj.vehicle_type = VehicleState.opt_char(data, 'vehicle_type');
            if isfield(data, 'connected') && ~isempty(data.connected)
                obj.connected = logical(data.connected);
            else
                obj.connected = true;
            end
            obj.age_s = VehicleState.opt_double(data, 'age_s');

            pos = VehicleState.opt_struct(data, 'position');
            obj.latitude = VehicleState.opt_double(pos, 'latitude');
            obj.longitude = VehicleState.opt_double(pos, 'longitude');
            obj.altitude = VehicleState.opt_double(pos, 'altitude');

            obj.heading_deg = VehicleState.opt_double(data, 'heading_deg');

            vel = VehicleState.opt_struct(data, 'velocity');
            obj.north_ms = VehicleState.opt_double_default(vel, 'north_ms', 0.0);
            obj.east_ms = VehicleState.opt_double_default(vel, 'east_ms', 0.0);
            obj.speed_ms = VehicleState.opt_double_default(vel, 'speed_ms', 0.0);
            obj.course_deg = VehicleState.opt_double(vel, 'course_deg');

            obj.mode = VehicleState.opt_char(data, 'mode');
            if isfield(data, 'armed') && ~isempty(data.armed)
                obj.armed = logical(data.armed);
            else
                obj.armed = [];
            end
            obj.behavior = VehicleState.opt_char(data, 'behavior');
            if isfield(data, 'battery') && ~isempty(data.battery)
                obj.battery = data.battery;
            else
                obj.battery = [];
            end
            obj.overlays = VehicleState.opt_struct(data, 'overlays');
            obj.raw = data;
        end

        function pos = get.position(obj)
            %[latitude, longitude, altitude] or [] before the first fix.
            if isempty(obj.latitude) || isempty(obj.longitude)
                pos = [];
                return;
            end
            if isempty(obj.altitude)
                alt = 0.0;
            else
                alt = obj.altitude;
            end
            pos = [obj.latitude, obj.longitude, alt];
        end
    end

    methods (Static)
        function obj = from_json(data)
            obj = VehicleState(data);
        end
    end

    methods (Static, Access = private)
        function val = opt_char(s, field)
            if isstruct(s) && isfield(s, field) && ~isempty(s.(field))
                val = char(s.(field));
            else
                val = [];
            end
        end

        function val = opt_double(s, field)
            if isstruct(s) && isfield(s, field) && ~isempty(s.(field))
                val = double(s.(field));
            else
                val = [];
            end
        end

        function val = opt_double_default(s, field, default_val)
            val = VehicleState.opt_double(s, field);
            if isempty(val)
                val = default_val;
            end
        end

        function val = opt_struct(s, field)
            if isstruct(s) && isfield(s, field) && isstruct(s.(field)) && ~isempty(s.(field))
                val = s.(field);
            else
                val = struct();
            end
        end
    end
end
