import unittest

from app.voice_commands import VoiceCommandError, parse_voice_command


class VoiceCommandTests(unittest.TestCase):
    def setUp(self):
        self.vehicles = [
            {
                "vehicle_id": "DroneThe3rd",
                "vehicle_type": "uav",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 15},
            },
            {
                "vehicle_id": "Ledger McQueen",
                "vehicle_type": "uav",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 15},
            },
            {
                "vehicle_id": "DroneJr",
                "vehicle_type": "uav",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 15},
            },
            {
                "vehicle_id": "uav-1",
                "vehicle_type": "uav",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 15},
            },
            {
                "vehicle_id": "BoatOne",
                "vehicle_type": "usv",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 0},
            },
            {"vehicle_id": "YP689", "vehicle_type": "yp", "connected": True},
        ]

    def test_takeoff_resolves_spoken_ordinal_vehicle_id(self):
        result = parse_voice_command("Drone the third takeoff at 15.26 meters", self.vehicles, "YP689")

        self.assertEqual(result["vehicle_id"], "DroneThe3rd")
        self.assertEqual(result["command"], {"type": "takeoff", "altitude_m": 15.26})
        self.assertIn("to 15.3 meters", result["summary"])
        self.assertTrue(result["requires_confirmation"])

    def test_standalone_altitude_uses_current_position_waypoint(self):
        result = parse_voice_command("DroneJr set altitude to 20 meters", self.vehicles, "YP689")

        self.assertEqual(
            result["command"],
            {"type": "waypoint", "target": {"latitude": 38.9, "longitude": -76.4, "altitude": 20}},
        )
        self.assertTrue(result["requires_confirmation"])

    def test_standalone_altitude_is_rejected_for_surface_vehicles(self):
        with self.assertRaisesRegex(VoiceCommandError, "only supported for aerial vehicles"):
            parse_voice_command("BoatOne set altitude to 20 meters", self.vehicles, "YP689")

    def test_confirmation_mode_can_be_disabled_or_require_every_command(self):
        phrase = "DroneJr return to boat"

        unconfirmed = parse_voice_command(phrase, self.vehicles, "YP689", confirmation_mode="none")
        confirmed = parse_voice_command(phrase, self.vehicles, "YP689", confirmation_mode="all")

        self.assertFalse(unconfirmed["requires_confirmation"])
        self.assertTrue(confirmed["requires_confirmation"])

    def test_spoken_compound_measurements_are_supported(self):
        result = parse_voice_command("Ledger McQueen search a twenty five meter grid at location", self.vehicles, "YP689", {"latitude": 38.95, "longitude": -76.48})

        self.assertEqual(result["command"]["grid_size_m"], 25)
        self.assertIn("38.950, -76.480", result["summary"])
        self.assertIn("30.0-meter altitude", result["summary"])

    def test_grid_search_uses_selected_map_point_when_named(self):
        result = parse_voice_command(
            "Ledger McQueen search a 50 meter grid at the selected point",
            self.vehicles,
            "YP689",
            {"latitude": 38.95, "longitude": -76.48},
        )

        self.assertEqual(result["command"]["type"], "search_grid")
        self.assertEqual(result["command"]["lat"], 38.95)
        self.assertEqual(result["command"]["lon"], -76.48)
        self.assertEqual(result["command"]["grid_size_m"], 50)

    def test_waypoint_uses_explicit_coordinates(self):
        result = parse_voice_command(
            "DroneJr fly to 38.989, -76.478",
            self.vehicles,
            "YP689",
        )

        self.assertEqual(result["command"]["target"]["latitude"], 38.989)
        self.assertEqual(result["command"]["target"]["longitude"], -76.478)
        self.assertEqual(result["command"]["target"]["altitude"], 15)

    def test_boat_commands_resolve_yp_role(self):
        return_result = parse_voice_command("DroneJr return to boat", self.vehicles, "YP689")
        land_result = parse_voice_command("DroneJr land on boat", self.vehicles, "YP689")
        spoken_alias = parse_voice_command("Drone Junior return to boat", self.vehicles, "YP689")

        self.assertEqual(return_result["command"], {"type": "rtb"})
        self.assertIn("YP689", return_result["summary"])
        self.assertEqual(land_result["command"], {"type": "land_on_boat"})
        self.assertEqual(spoken_alias["vehicle_id"], "DroneJr")

    def test_location_reference_without_map_point_is_rejected(self):
        with self.assertRaisesRegex(VoiceCommandError, "Say latitude and longitude"):
            parse_voice_command("DroneJr fly to that point", self.vehicles, "YP689")

    def test_unknown_vehicle_is_rejected(self):
        with self.assertRaisesRegex(VoiceCommandError, "Name a connected vehicle"):
            parse_voice_command("UnknownDrone takeoff at 15 meters", self.vehicles, "YP689")

    def test_spoken_number_matches_numeric_vehicle_id(self):
        result = parse_voice_command("UAV one return to boat", self.vehicles, "YP689")

        self.assertEqual(result["vehicle_id"], "uav-1")

    def test_disconnected_vehicle_is_rejected(self):
        self.vehicles[0]["connected"] = False
        with self.assertRaisesRegex(VoiceCommandError, "is not connected"):
            parse_voice_command("Drone the third takeoff at 15 meters", self.vehicles, "YP689")

    def test_invalid_coordinate_range_is_rejected(self):
        with self.assertRaisesRegex(VoiceCommandError, "out of range"):
            parse_voice_command("DroneJr fly to 91, -76.478", self.vehicles, "YP689")


if __name__ == "__main__":
    unittest.main()