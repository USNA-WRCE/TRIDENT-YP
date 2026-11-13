classdef geo
    %geo Small geodesy helpers for writing feedback loops against vehicle state.

    properties (Constant)
        EARTH_RADIUS_M = 6378137.0
    end

    methods (Static)
        function d = distance_m(lat1, lon1, lat2, lon2)
            %distance_m Great-circle distance in meters.
            p1 = deg2rad(double(lat1));
            p2 = deg2rad(double(lat2));
            dlat = deg2rad(double(lat2) - double(lat1));
            dlon = deg2rad(double(lon2) - double(lon1));
            a = sin(dlat ./ 2).^2 + cos(p1) .* cos(p2) .* sin(dlon ./ 2).^2;
            d = geo.EARTH_RADIUS_M .* 2 .* atan2(sqrt(a), sqrt(max(0.0, 1.0 - a)));
        end

        function b = bearing_deg(lat1, lon1, lat2, lon2)
            %bearing_deg Initial bearing from point 1 to point 2 in degrees clockwise from true north, 0-360.
            p1 = deg2rad(double(lat1));
            p2 = deg2rad(double(lat2));
            dlon = deg2rad(double(lon2) - double(lon1));
            y = sin(dlon) .* cos(p2);
            x = cos(p1) .* sin(p2) - sin(p1) .* cos(p2) .* cos(dlon);
            b = mod(rad2deg(atan2(y, x)), 360.0);
        end

        function [lat_out, lon_out] = offset_latlon(lat, lon, north_m, east_m)
            %offset_latlon Point reached by moving north_m / east_m meters from (lat, lon) (flat-earth, fine below ~10 km).
            lat = double(lat);
            lon = double(lon);
            lat_out = lat + rad2deg(double(north_m) ./ geo.EARTH_RADIUS_M);
            lon_out = lon + rad2deg(double(east_m) ./ (geo.EARTH_RADIUS_M .* cos(deg2rad(lat))));
            if nargout <= 1
                lat_out = [lat_out, lon_out];
            end
        end

        function diff = angle_diff_deg(target, current)
            %angle_diff_deg Signed shortest rotation (-180, 180] from current to target heading.
            diff = mod(double(target) - double(current) + 180.0, 360.0) - 180.0;
            diff(diff == -180.0) = 180.0;
        end
    end
end
