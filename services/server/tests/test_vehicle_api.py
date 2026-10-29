import unittest
from unittest.mock import patch

from fastapi import FastAPI

from app import auth, main
from app.api_commands import CommandValidationError, normalize_api_command
from app.deconfliction import DeconflictionEngine
from support import DatabaseTestCase

TELEMETRY = {
    "op": "telemetry", "vehicle_type": "usv", "behavior": "idle", "mode": "GUIDED", "armed": True,
    "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 1.5}, "heading": 45,
}


class CommandNormalizationTests(unittest.TestCase):
    def test_waypoint_defaults_altitude_and_validates_ranges(self):
        command = {"type": "waypoint", "target": {"latitude": 38.9, "longitude": -76.4}}
        self.assertEqual(normalize_api_command(command, default_altitude=12.0)["target"]["altitude"], 12.0)
        with self.assertRaises(CommandValidationError):
            normalize_api_command({"type": "waypoint", "target": {"latitude": 91, "longitude": 0}})
        with self.assertRaises(CommandValidationError):
            normalize_api_command({"type": "waypoint", "target": {"latitude": "nan", "longitude": 0}})

    def test_velocity_is_resolved_into_north_east_components(self):
        command = normalize_api_command({"type": "set_velocity", "speed_ms": 2, "heading_deg": 450})
        self.assertAlmostEqual(command["heading_deg"], 90.0)
        self.assertAlmostEqual(command["velocity_north_ms"], 0.0, places=6)
        self.assertAlmostEqual(command["velocity_east_ms"], 2.0)

    def test_rc_override_requires_valid_channels_and_pwm(self):
        self.assertEqual(normalize_api_command({"type": "rc_override", "channels": {"1": 1500, 3: 0}})["channels"], {"1": 1500, "3": 0})
        for channels in ({}, {"0": 1500}, {"19": 1500}, {"1": 500}, {"1": 3000}, {"x": 1500}):
            with self.assertRaises(CommandValidationError):
                normalize_api_command({"type": "rc_override", "channels": channels})

    def test_mission_and_ship_relative_shapes(self):
        mission = normalize_api_command({"type": "mission_plan", "waypoints": [{"latitude": 1, "longitude": 2, "altitude": 3}]})
        self.assertEqual(mission["waypoints"][0]["command_id"], 16)
        ship = normalize_api_command({
            "type": "ship_relative_trajectory", "ship_vehicle_id": "yp",
            "local_waypoints": [{"x": 1, "y": 2, "z": 3}],
        })
        self.assertEqual(ship["loop_count"], 1)
        for bad in ({"type": "mission_plan", "waypoints": []}, {"type": "mob"}, {"type": "nope"}, {"type": "set_mode"}):
            with self.assertRaises(CommandValidationError):
                normalize_api_command(bad)


class DirectControlTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import MagicMock
        self.master = MagicMock(target_system=1, target_component=1)
        self.mavutil = MagicMock()
        self.mavutil.mavlink.MAV_FRAME_LOCAL_NED = 1

    def test_velocity_and_heading_use_local_ned_setpoints(self):
        from yp_common import direct_control
        command = normalize_api_command({"type": "set_velocity", "speed_ms": 3, "heading_deg": 90, "vertical_ms": 1})
        direct_control.send_velocity(self.master, self.mavutil, command)
        args = self.master.mav.set_position_target_local_ned_send.call_args.args
        self.assertEqual(args[4], direct_control.VELOCITY_AND_YAW_MASK)
        self.assertAlmostEqual(args[8], 0.0, places=6)
        self.assertAlmostEqual(args[9], 3.0)
        self.assertAlmostEqual(args[10], -1.0)
        self.assertAlmostEqual(args[14], 1.5707963, places=5)
        direct_control.send_heading(self.master, self.mavutil, {"heading_deg": 180})
        self.assertEqual(self.master.mav.set_position_target_local_ned_send.call_args.args[4], direct_control.YAW_ONLY_MASK)

    def test_rc_override_ignores_unlisted_channels_and_extends_only_when_needed(self):
        from yp_common import direct_control
        direct_control.send_rc_override(self.master, {"channels": {"1": 1500, "3": 0}})
        args = self.master.mav.rc_channels_override_send.call_args.args
        self.assertEqual(args[2:], (1500, 65535, 0, 65535, 65535, 65535, 65535, 65535))
        direct_control.send_rc_override(self.master, {"channels": {"10": 1600}})
        self.assertEqual(len(self.master.mav.rc_channels_override_send.call_args.args), 2 + 18)


class VehicleAPITests(DatabaseTestCase):
    def create_app(self):
        app = FastAPI()
        app.router.routes.extend(main.app.routes)
        app.middleware("http")(main.authenticate_cookie_requests)
        return app

    def setUp(self):
        super().setUp()
        for name in ("vehicles", "vehicle_queues", "ros_connections", "shared_waypoints",
                     "shared_sar_patterns", "shared_mission_plans", "shared_mission_completion_targets",
                     "shared_ship_relative_plans", "api_connections", "_sitl_nav_states"):
            patcher = patch.dict(getattr(main, name), {}, clear=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("ui_connections", set()), ("write_api", None),
                            ("_yp_role_vehicle_id", None),
                            ("deconfliction_engine", DeconflictionEngine())):
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def make_token(self, **payload):
        response = self.client.post("/api/auth/service-tokens", json={"name": "script", **payload})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"], {"Authorization": f"Bearer {response.json()['token']}"}

    def test_service_token_lifecycle(self):
        body = self.client.post("/api/auth/service-tokens", json={"name": "script"}).json()
        self.assertEqual(body["username"], "svc-script")
        self.assertIn("send_waypoint", body["permissions"])
        self.assertNotIn("rc_override", body["permissions"])
        headers = {"Authorization": f"Bearer {body['token']}"}
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).json()["username"], "svc-script")
        listing = self.client.get("/api/auth/service-tokens").json()["tokens"]
        self.assertEqual([t["name"] for t in listing], ["script"])
        self.assertNotIn("token", listing[0])
        self.assertEqual(self.client.post("/api/auth/service-tokens", json={"name": "script"}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/service-tokens", json={"name": "bad name!"}).status_code, 400)
        self.assertEqual(self.client.delete("/api/auth/service-tokens/script").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=headers).status_code, 401)
        self.assertEqual(self.client.delete("/api/auth/service-tokens/script").status_code, 404)
        self.assertEqual(self.client.post("/api/auth/service-tokens", json={"name": "script"}).status_code, 200)

    def test_service_token_requires_admin_and_cannot_log_in(self):
        _, headers = self.make_token()
        self.assertEqual(self.client.post("/api/auth/service-tokens", json={"name": "x"}, headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "svc-script", "password": "x"}).status_code, 401)

    def test_command_draws_overlay_acks_and_attributes_the_caller(self):
        _, headers = self.make_token()
        with self.client.websocket_connect("/ws/ui") as ui:
            ui.receive_json()
            with self.client.websocket_connect("/ws/vehicle/boat-01") as vehicle:
                vehicle.send_json(TELEMETRY)
                self.assertEqual(ui.receive_json()["op"], "vehicle_update")
                response = self.client.post("/api/vehicles/boat-01/command", headers=headers, json={
                    "type": "waypoint", "target": {"latitude": 38.91, "longitude": -76.41},
                })
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertTrue(body["ok"] and body["delivered"])
                self.assertEqual(body["ack"]["source"], "api")
                self.assertEqual(body["ack"]["issued_by"], "svc-script")
                self.assertEqual(body["ack"]["command"]["target"]["altitude"], 1.5)
                overlay = ui.receive_json()
                self.assertEqual(overlay["op"], "waypoint_overlay")
                self.assertEqual(overlay["waypoint"]["latitude"], 38.91)
                ui_ack = ui.receive_json()
                self.assertEqual(ui_ack["command_id"], body["ack"]["command_id"])
                self.assertEqual(ui_ack["issued_by"], "svc-script")
                forwarded = vehicle.receive_json()
                self.assertEqual(forwarded["source"], "api")
                self.assertEqual(forwarded["issued_by"], "svc-script")
                self.assertEqual(forwarded["command"]["type"], "waypoint")

    def test_search_grid_and_mission_plan_update_the_map(self):
        _, headers = self.make_token(permission_level="mission_planning")
        with self.client.websocket_connect("/ws/ui") as ui:
            ui.receive_json()
            with self.client.websocket_connect("/ws/vehicle/uav-01") as vehicle:
                vehicle.send_json({**TELEMETRY, "vehicle_type": "uav"})
                ui.receive_json()
                response = self.client.post("/api/vehicles/uav-01/command", headers=headers, json={
                    "type": "mission_plan",
                    "waypoints": [{"latitude": 38.9, "longitude": -76.4, "altitude": 20}, {"latitude": 38.91, "longitude": -76.4, "altitude": 20}],
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(ui.receive_json()["op"], "mission_plan_overlay")
        self.assertIn("uav-01", main.shared_mission_plans)

    def test_error_responses(self):
        _, headers = self.make_token(permission_level="view_only")
        url = "/api/vehicles/boat-01/command"
        waypoint = {"type": "waypoint", "target": {"latitude": 1, "longitude": 2, "altitude": 3}}
        self.assertEqual(self.client.post(url, json=waypoint, headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual(self.client.post(url, json=waypoint, headers=headers).status_code, 403)
        headers = self.service_headers("full", "waypoint_command")
        self.assertEqual(self.client.post(url, json={"type": "bogus"}, headers=headers).status_code, 400)
        self.assertEqual(self.client.post(url, json=waypoint, headers=headers).status_code, 404)
        with self.client.websocket_connect("/ws/vehicle/boat-01") as vehicle:
            vehicle.send_json(TELEMETRY)
            self.assertEqual(self.client.post(url, json={"type": "waypoint", "target": {"latitude": 99, "longitude": 0}}, headers=headers).status_code, 400)
            rc = {"type": "rc_override", "channels": {"1": 1500}}
            forbidden = self.client.post(url, json=rc, headers=headers)
            self.assertEqual(forbidden.status_code, 403)
            self.assertEqual(forbidden.json()["required_permission"], "rc_override")
            self.assertEqual(self.client.post(url, json=rc).status_code, 200)

    def test_client_library_end_to_end(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "clients" / "python"))
        self.addCleanup(sys.path.remove, sys.path[0])
        from trident_client import TridentClient, VehicleNotFound

        token = self.client.post("/api/auth/service-tokens", json={"name": "e2e", "permission_level": "mission_planning"}).json()["token"]
        api = TridentClient("http://testserver", token)
        server = self.client

        def through_test_client(method, url, **kwargs):
            kwargs.pop("timeout", None), kwargs.pop("verify", None)
            return server.request(method, url.replace("http://testserver", ""), headers=dict(api._session.headers), **kwargs)

        api._session.request = through_test_client
        with self.client.websocket_connect("/ws/vehicle/boat-01") as vehicle:
            vehicle.send_json(TELEMETRY)
            boat = api.vehicle("boat-01")
            ack = boat.goto(38.91, -76.41)
            self.assertEqual((ack.issued_by, ack.source, ack.delivered), ("svc-e2e", "api", True))
            self.assertEqual(vehicle.receive_json()["command"]["target"]["altitude"], 1.5)
            self.assertEqual(boat.state().overlays["waypoint"]["latitude"], 38.91)
            boat.mission([38.9, 38.91], [-76.4, -76.4], 10)
            self.assertEqual(vehicle.receive_json()["command"]["type"], "mission_plan")
            self.assertEqual([s.vehicle_id for s in api.vehicles()], ["boat-01"])
        with self.assertRaises(VehicleNotFound):
            api.goto("nope", 1, 2, 3)

    def service_headers(self, name, level):
        response = self.client.post("/api/auth/service-tokens", json={"name": name, "permission_level": level})
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_disconnected_vehicle_is_rejected_without_overlay(self):
        headers = self.service_headers("s", "waypoint_command")
        main.vehicles["ghost"] = {"vehicle_id": "ghost"}
        response = self.client.post("/api/vehicles/ghost/command", headers=headers, json={
            "type": "waypoint", "target": {"latitude": 1, "longitude": 2, "altitude": 3},
        })
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("ghost", main.shared_waypoints)

    def test_state_endpoints_expose_pose_velocity_and_overlays(self):
        headers = self.service_headers("s", "view_only")
        self.assertEqual(self.client.get("/api/fleet/state").status_code, 200)
        self.assertEqual(self.client.get("/api/fleet/state", headers={"Authorization": "Bearer bad"}).status_code, 401)
        with self.client.websocket_connect("/ws/vehicle/boat-01") as vehicle:
            vehicle.send_json({**TELEMETRY, "stamp": 1000.0})
            vehicle.send_json({**TELEMETRY, "stamp": 1010.0, "position": {"latitude": 38.9001, "longitude": -76.4, "altitude": 1.5}})
            state = None
            for _ in range(50):
                state = self.client.get("/api/vehicles/boat-01/state", headers=headers).json()
                if state.get("velocity", {}).get("speed_ms", 0) > 0:
                    break
            self.assertEqual(state["heading_deg"], 45)
            self.assertEqual(state["mode"], "GUIDED")
            self.assertTrue(state["armed"] and state["connected"])
            self.assertGreater(state["velocity"]["north_ms"], 0)
            self.assertIsNone(state["overlays"]["waypoint"])
            fleet = self.client.get("/api/fleet/state", headers=headers).json()
            self.assertEqual([v["vehicle_id"] for v in fleet["vehicles"]], ["boat-01"])
        self.assertEqual(self.client.get("/api/vehicles/nope/state", headers=headers).status_code, 404)

    def test_state_stream_requires_auth_and_filters_by_subscription(self):
        headers = self.service_headers("s", "view_only")
        self.client.cookies.clear()
        with self.client.websocket_connect("/ws/api") as rejected:
            self.assertIn("error", rejected.receive_json())
        with self.client.websocket_connect("/ws/vehicle/boat-01") as boat, self.client.websocket_connect("/ws/vehicle/boat-02") as other:
            boat.send_json(TELEMETRY)
            other.send_json(TELEMETRY)
            with self.client.websocket_connect("/ws/api", headers=headers) as stream:
                self.assertEqual(sorted(v["vehicle_id"] for v in stream.receive_json()["vehicles"]), ["boat-01", "boat-02"])
                stream.send_json({"op": "subscribe", "vehicle_ids": ["boat-02"]})
                self.assertEqual(stream.receive_json()["vehicle_ids"], ["boat-02"])
                boat.send_json(TELEMETRY)
                other.send_json({**TELEMETRY, "heading": 90})
                update = stream.receive_json()
                self.assertEqual(update["op"], "state")
                self.assertEqual(update["vehicle"]["vehicle_id"], "boat-02")
                self.assertEqual(update["vehicle"]["heading_deg"], 90)


if __name__ == "__main__":
    unittest.main()
