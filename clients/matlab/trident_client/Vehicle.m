classdef Vehicle < handle
    %Vehicle Convenience wrapper binding one vehicle id to a client; every method forwards to TridentClient.

    properties (SetAccess = immutable)
        client
        vehicle_id char
    end

    methods
        function obj = Vehicle(client, vehicle_id)
            obj.client = client;
            obj.vehicle_id = char(vehicle_id);
        end

        function s = state(obj)
            s = obj.client.state(obj.vehicle_id);
        end

        function ack = send_command(obj, command)
            ack = obj.client.send_command(obj.vehicle_id, command);
        end

        function ack = goto(obj, latitude, longitude, varargin)
            ack = obj.client.goto(obj.vehicle_id, latitude, longitude, varargin{:});
        end

        function ack = rtb(obj)
            ack = obj.client.rtb(obj.vehicle_id);
        end

        function ack = land_on_boat(obj)
            ack = obj.client.land_on_boat(obj.vehicle_id);
        end

        function ack = arm(obj)
            ack = obj.client.arm(obj.vehicle_id);
        end

        function ack = disarm(obj)
            ack = obj.client.disarm(obj.vehicle_id);
        end

        function ack = takeoff(obj, varargin)
            ack = obj.client.takeoff(obj.vehicle_id, varargin{:});
        end

        function ack = set_mode(obj, mode)
            ack = obj.client.set_mode(obj.vehicle_id, mode);
        end

        function ack = cancel_search(obj)
            ack = obj.client.cancel_search(obj.vehicle_id);
        end

        function ack = search_grid(obj, latitude, longitude, varargin)
            ack = obj.client.search_grid(obj.vehicle_id, latitude, longitude, varargin{:});
        end

        function ack = mission(obj, latitudes, longitudes, varargin)
            ack = obj.client.mission(obj.vehicle_id, latitudes, longitudes, varargin{:});
        end

        function ack = ship_relative_mission(obj, ship_id, x_m, y_m, varargin)
            ack = obj.client.ship_relative_mission(obj.vehicle_id, ship_id, x_m, y_m, varargin{:});
        end

        function ack = set_velocity(obj, speed_ms, heading_deg, varargin)
            ack = obj.client.set_velocity(obj.vehicle_id, speed_ms, heading_deg, varargin{:});
        end

        function ack = set_heading(obj, heading_deg)
            ack = obj.client.set_heading(obj.vehicle_id, heading_deg);
        end

        function ack = rc_override(obj, channels)
            ack = obj.client.rc_override(obj.vehicle_id, channels);
        end

        function ack = release_rc(obj, varargin)
            ack = obj.client.release_rc(obj.vehicle_id, varargin{:});
        end
    end
end
