classdef CommandAck
    %CommandAck Server acknowledgement that a command was accepted and queued to the vehicle.
    %   This confirms delivery to the vehicle's bridge, not that the vehicle has finished
    %   executing it; watch state() or poll_states() for that.

    properties (SetAccess = immutable)
        command_id char
        vehicle_id char
        source char
        issued_by
        stamp double
        command struct
        delivered logical = true
    end

    methods
        function obj = CommandAck(ack, delivered)
            if nargin < 2 || isempty(delivered)
                delivered = true;
            end
            obj.command_id = char(ack.command_id);
            obj.vehicle_id = char(ack.vehicle_id);
            obj.source = char(ack.source);
            if isfield(ack, 'issued_by') && ~isempty(ack.issued_by)
                obj.issued_by = char(ack.issued_by);
            else
                obj.issued_by = [];
            end
            obj.stamp = double(ack.stamp);
            if isfield(ack, 'command') && isstruct(ack.command)
                obj.command = ack.command;
            else
                obj.command = struct();
            end
            obj.delivered = logical(delivered);
        end
    end
end
