import unittest
from unittest.mock import MagicMock

from trident_client import (
    AuthenticationError, InvalidCommand, PermissionDenied, TridentClient, VehicleNotConnected, VehicleNotFound,
    VehicleState, geo,
)


def response(status, payload):
    result = MagicMock(status_code=status)
    result.json.return_value = payload
    return result


def ack_payload(command):
    return {"ok": True, "delivered": True, "ack": {
        "command_id": "abc", "vehicle_id": "boat-01", "source": "api", "issued_by": "svc-x", "stamp": 1.0, "command": command,
    }}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = TridentClient("http://server/", "token")
        self.request = MagicMock(side_effect=lambda method, url, **kw: response(200, ack_payload(kw.get("json"))))
        self.client._session.request = self.request

    def sent(self):
        return self.request.call_args.kwargs["json"]

    def test_goto_and_vehicle_handle_name_the_vehicle(self):
        ack = self.client.goto("boat-01", 38.9, -76.4)
        self.assertEqual(self.request.call_args.args, ("POST", "http://server/api/vehicles/boat-01/command"))
        self.assertEqual(self.sent(), {"type": "waypoint", "target": {"latitude": 38.9, "longitude": -76.4}})
        self.assertEqual((ack.command_id, ack.source, ack.issued_by), ("abc", "api", "svc-x"))
        self.client.vehicle("boat-02").goto(1, 2, 3)
        self.assertEqual(self.request.call_args.args[1], "http://server/api/vehicles/boat-02/command")
        self.assertEqual(self.sent()["target"]["altitude"], 3)
        with self.assertRaises(AttributeError):
            self.client.vehicle("boat-02").vehicles

    def test_mission_arrays_expand_altitude_and_validate_lengths(self):
        self.client.mission("uav-01", [1, 2], [3, 4], 25)
        self.assertEqual([w["altitude"] for w in self.sent()["waypoints"]], [25, 25])
        self.client.mission("uav-01", [1, 2], [3, 4])
        self.assertNotIn("altitude", self.sent()["waypoints"][0])
        with self.assertRaises(ValueError):
            self.client.mission("uav-01", [1, 2], [3])
        with self.assertRaises(ValueError):
            self.client.mission("uav-01", [1, 2], [3, 4], [10])

    def test_ship_relative_mission_and_direct_control_payloads(self):
        self.client.ship_relative_mission("uav-01", "yp", [0, 10], [20, 20], 15, loops=2)
        sent = self.sent()
        self.assertEqual(sent["ship_vehicle_id"], "yp")
        self.assertEqual(sent["local_waypoints"][1], {"x": 10, "y": 20, "z": 15})
        self.assertEqual(sent["loop_count"], 2)
        self.client.set_velocity("boat-01", 2, 90)
        self.assertEqual(self.sent(), {"type": "set_velocity", "speed_ms": 2, "heading_deg": 90, "vertical_ms": 0.0})
        self.client.rc_override("boat-01", {1: 1500})
        self.assertEqual(self.sent(), {"type": "rc_override", "channels": {"1": 1500}})
        self.client.release_rc("boat-01", [1, 3])
        self.assertEqual(self.sent()["channels"], {"1": 0, "3": 0})

    def test_http_errors_map_to_exceptions(self):
        for status, error in ((400, InvalidCommand), (401, AuthenticationError), (403, PermissionDenied),
                              (404, VehicleNotFound), (409, VehicleNotConnected)):
            self.request.side_effect = lambda *a, status=status, **kw: response(status, {"error": "nope"})
            with self.assertRaises(error) as caught:
                self.client.rtb("boat-01")
            self.assertEqual(caught.exception.status, status)

    def test_state_parsing(self):
        self.request.side_effect = lambda *a, **kw: response(200, {
            "vehicle_id": "boat-01", "connected": True, "position": {"latitude": 1, "longitude": 2, "altitude": 3},
            "heading_deg": 90, "velocity": {"north_ms": 1, "east_ms": 0, "speed_ms": 1, "course_deg": 0},
            "mode": "GUIDED", "armed": True, "overlays": {"waypoint": None},
        })
        state = self.client.state("boat-01")
        self.assertIsInstance(state, VehicleState)
        self.assertEqual(state.position, (1, 2, 3))
        self.assertEqual((state.speed_ms, state.mode), (1.0, "GUIDED"))

    def test_requires_url_and_token(self):
        with self.assertRaises(ValueError):
            TridentClient("http://x", None)

    def test_geo_helpers(self):
        self.assertAlmostEqual(geo.distance_m(0, 0, 0, 1), 111319.5, delta=1)
        self.assertAlmostEqual(geo.bearing_deg(0, 0, 1, 0), 0.0)
        self.assertAlmostEqual(geo.bearing_deg(0, 0, 0, 1), 90.0)
        self.assertEqual(geo.angle_diff_deg(10, 350), 20)
        lat, lon = geo.offset_latlon(38.0, -76.0, 100, 0)
        self.assertAlmostEqual(geo.distance_m(38.0, -76.0, lat, lon), 100, delta=0.5)


if __name__ == "__main__":
    unittest.main()
