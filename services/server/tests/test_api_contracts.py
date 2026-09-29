import asyncio
import math
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, WebSocketDisconnect

from app import auth, main
from app.deconfliction import DeconflictionEngine
from support import DatabaseTestCase


class APIContractTests(DatabaseTestCase):
    def create_app(self):
        # Register the production endpoints and cookie middleware, with external
        # service startup excluded so these contracts run without any hardware.
        app = FastAPI()
        app.router.routes.extend(main.app.routes)
        app.middleware("http")(main.authenticate_cookie_requests)
        return app

    def setUp(self):
        super().setUp()
        for name in ("vehicles", "vehicle_queues", "ros_connections", "shared_waypoints",
                     "shared_sar_patterns", "shared_mission_plans", "shared_mission_completion_targets",
                     "shared_ship_relative_plans"):
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

    def test_cookie_login_user_management_and_custom_permissions(self):
        response = self.login()
        self.assertNotIn("token", response.json())
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/me").json()["username"], "operator")
        self.assertEqual(self.client.post("/api/auth/users", json={
            "username": "viewer", "password": "password", "permission_level": "view_only",
        }).status_code, 200)
        self.assertEqual(self.client.put("/api/auth/users/viewer/permissions", json={
            "permissions": ["read_telemetry", "upload_mission"],
        }).status_code, 200)
        self.login("viewer")
        self.assertEqual(self.client.get("/api/auth/me").json()["permissions"], ["read_telemetry", "upload_mission"])
        self.assertEqual(self.client.get("/api/auth/users").status_code, 403)
        self.assertEqual(self.client.put("/api/auth/users/viewer/password", json={"password": "new password"}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.login("viewer", "new password")

    def test_vehicle_telemetry_waypoint_command_and_late_ui_snapshot(self):
        with self.client.websocket_connect("/ws/ui") as ui:
            self.assertEqual(ui.receive_json()["op"], "snapshot")
            with self.client.websocket_connect("/ws/vehicle/boat-01") as vehicle:
                vehicle.send_json({
                    "vehicle_type": "usv", "topic": "/vehicles/boat-01/navsatfix",
                    "type": "sensor_msgs/msg/NavSatFix",
                    "msg": {"latitude": 38.9, "longitude": -76.4, "altitude": 1, "heading": 45},
                })
                update = ui.receive_json()
                self.assertEqual(update["op"], "vehicle_update")
                self.assertEqual(update["vehicle"]["position"]["latitude"], 38.9)
                self.assertNotIn("history", update["vehicle"])
                command = {"type": "waypoint", "target": {"latitude": 38.91, "longitude": -76.41, "altitude": 10}}
                ui.send_json({"op": "command", "vehicle_id": "boat-01", "command": command})
                self.assertEqual(ui.receive_json()["op"], "waypoint_overlay")
                self.assertTrue(ui.receive_json()["delivered"])
                self.assertEqual(vehicle.receive_json()["command"], command)
                with self.client.websocket_connect("/ws/ui") as late_ui:
                    snapshot = late_ui.receive_json()
                    self.assertEqual(snapshot["waypoints"][0]["latitude"], 38.91)
                    self.assertEqual(len(snapshot["vehicles"][0]["history"]), 1)
                vehicle.close()
                self.assertEqual(ui.receive_json(), {"op": "vehicle_removed", "vehicle_id": "boat-01"})
        self.assertNotIn("boat-01", main.vehicle_queues)
        self.assertEqual(self.client.get("/api/vehicles/boat-01").status_code, 404)

    def test_rosbridge_publish_updates_subscribers_and_http_vehicle_state(self):
        topic = "/vehicles/boat-01/navsatfix"
        with self.client.websocket_connect("/ws/rosbridge") as ros:
            self.assertEqual(ros.receive_json()["op"], "status")
            ros.send_json({"op": "subscribe", "topic": topic})
            self.assertEqual(ros.receive_json()["op"], "status")
            message = {"latitude": 38.9, "longitude": -76.4, "altitude": 0}
            ros.send_json({"op": "publish", "topic": topic, "type": "sensor_msgs/msg/NavSatFix", "msg": message})
            self.assertEqual(ros.receive_json(), {"op": "publish", "topic": topic, "type": "sensor_msgs/msg/NavSatFix", "msg": message})
            self.assertEqual(self.client.get("/api/vehicles/boat-01").json()["position"], message)

    def test_local_planner_requires_mission_permission(self):
        auth.create_user("viewer", "password", "view_only")
        self.login("viewer")
        command = {"type": "ship_relative_trajectory", "trajectory": []}
        with self.client.websocket_connect("/ws/ui") as ui:
            ui.receive_json()
            ui.send_json({"op": "command", "vehicle_id": "boat-01", "command": command})
            self.assertIn("Insufficient permissions", ui.receive_json()["error"])
        auth.set_user_permissions("viewer", {"upload_mission"})
        with self.client.websocket_connect("/ws/ui") as ui:
            ui.receive_json()
            with self.client.websocket_connect("/ws/vehicle/boat-01") as vehicle:
                ui.send_json({"op": "command", "vehicle_id": "boat-01", "command": command})
                self.assertTrue(ui.receive_json()["delivered"])
                self.assertEqual(vehicle.receive_json()["command"], command)


class VehicleConnectionCleanupTests(unittest.IsolatedAsyncioTestCase):
    def test_sitl_ship_relative_dispatch_sends_and_completes_waypoint(self):
        now = time.time()
        ship_lat, ship_lon = 38.9, -76.4
        plan = {
            "ship_vehicle_id": "yp-boat",
            "vehicle_id": "sitl-drone",
            "waypoints": [
                {"x": 0, "y": 20, "z": 15, "yaw_deg": 0},
                {"x": 0, "y": 20, "z": 15, "yaw_deg": 0},
            ],
            "index": 0,
            "arrival_radius_m": 6.0,
            "guided_forced": False,
            "face_ship": True,
        }
        states = {
            "yp-boat": {"lat": ship_lat, "lon": ship_lon, "alt": 2.0, "heading_deg": 0.0, "vn_ms": 1.5, "ve_ms": -0.5, "stamp": now},
            "sitl-drone": {"lat": 38.9005, "lon": ship_lon, "alt": 2.0, "heading_deg": 0.0, "vn_ms": 0.0, "ve_ms": 0.0, "stamp": now},
        }
        master = MagicMock()
        master.mode_mapping.return_value = {"GUIDED": 4}
        mavlink = SimpleNamespace(
            MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
            MAV_FRAME_GLOBAL_INT=5,
            MAV_MODE_FLAG_CUSTOM_MODE_ENABLED=1,
            MAV_CMD_COMPONENT_ARM_DISARM=400,
        )

        with patch.object(main, "_mavutil", SimpleNamespace(mavlink=mavlink)), patch.dict(
            main._sitl_nav_states, states, clear=True,
        ), patch.dict(main.sitl_bridge_info, {"sitl-drone": {"vehicle_type": "uav"}}, clear=True):
            self.assertIs(main._step_sitl_ship_relative(master, plan), plan)
            send_args = master.mav.set_position_target_global_int_send.call_args.args
            expected_lat = ship_lat + math.degrees(20 / 6_378_137.0)
            self.assertAlmostEqual(send_args[5] / 1e7, expected_lat, places=5)
            self.assertAlmostEqual(send_args[7], 17.0)
            self.assertAlmostEqual(send_args[14], math.pi)
            self.assertEqual(send_args[8:10], (1.5, -0.5))

            main._sitl_nav_states["sitl-drone"].update({"lat": expected_lat, "lon": ship_lon, "alt": 17.0, "stamp": time.time()})
            self.assertIs(main._step_sitl_ship_relative(master, plan), plan)
            self.assertEqual(plan["index"], 1)
            self.assertIsNone(main._step_sitl_ship_relative(master, plan))
            self.assertEqual(plan["index"], 2)

    def test_sitl_ship_relative_default_yaw_faces_waypoint_travel_bearing(self):
        now = time.time()
        plan = {
            "ship_vehicle_id": "yp-boat",
            "vehicle_id": "sitl-drone",
            "waypoints": [{"x": 0, "y": 20, "z": 15, "yaw_deg": 270}],
            "index": 0,
            "arrival_radius_m": 6.0,
            "guided_forced": False,
            "face_ship": False,
        }
        states = {
            "yp-boat": {"lat": 38.9, "lon": -76.4, "alt": 2.0, "heading_deg": 0.0, "vn_ms": 0.0, "ve_ms": 0.0, "stamp": now},
            "sitl-drone": {"lat": 38.899, "lon": -76.4, "alt": 17.0, "heading_deg": 180.0, "vn_ms": 0.0, "ve_ms": 0.0, "stamp": now},
        }
        master = MagicMock()
        mavlink = SimpleNamespace(
            MAV_FRAME_GLOBAL_RELATIVE_ALT_INT=6,
            MAV_FRAME_GLOBAL_INT=5,
            MAV_MODE_FLAG_CUSTOM_MODE_ENABLED=1,
            MAV_CMD_COMPONENT_ARM_DISARM=400,
        )
        with patch.object(main, "_mavutil", SimpleNamespace(mavlink=mavlink)), patch.dict(
            main._sitl_nav_states, states, clear=True,
        ), patch.dict(main.sitl_bridge_info, {"sitl-drone": {"vehicle_type": "uav"}}, clear=True):
            main._step_sitl_ship_relative(master, plan)
            send_args = master.mav.set_position_target_global_int_send.call_args.args
            self.assertAlmostEqual(send_args[14], 0.0, places=5)

    async def test_sar_path_is_computed_once_and_only_simulators_receive_navigation_points(self):
        for vehicle_id in ("sim-drone", "hardware-drone"):
            with self.subTest(vehicle_id=vehicle_id):
                command = {"type": "search_grid", "lat": 38.9, "lon": -76.4}
                queue = asyncio.Queue()
                with patch.dict(main.vehicle_queues, {vehicle_id: queue}, clear=True), patch.dict(
                    main.vehicles, {}, clear=True,
                ), patch.dict(main.shared_sar_patterns, {}, clear=True), patch.object(
                    main, "broadcast_ui", AsyncMock(),
                ) as broadcast, patch.object(main, "write_influx"), patch.object(
                    main, "broadcast_ros", AsyncMock(),
                ), patch.object(main._sar_missions, "calculate_search_grid_waypoints", return_value=[
                    (38.9, -76.4, 30), (38.91, -76.41, 30),
                ]) as calculate:
                    await main.route_command(vehicle_id, command, "ui")
                    calculate.assert_called_once()
                    pattern = broadcast.await_args_list[0].args[0]
                    self.assertEqual(pattern["waypoints"], [[38.9, -76.4], [38.91, -76.41]])
                    delivered_command = queue.get_nowait()["command"]
                    if vehicle_id.startswith("sim-"):
                        self.assertEqual(delivered_command["sim_waypoints"], [[38.9, -76.4, 30], [38.91, -76.41, 30]])
                    else:
                        self.assertNotIn("sim_waypoints", delivered_command)
                    self.assertNotIn("sim_waypoints", command)

    async def test_disconnect_cancels_waiting_sender(self):
        websocket = AsyncMock()
        websocket.receive_json.side_effect = WebSocketDisconnect()
        before = set(asyncio.all_tasks())
        with patch.dict(main.vehicle_queues, {}, clear=True), patch.object(main, "broadcast_ui", AsyncMock()):
            await main.vehicle_ws(websocket, "test-boat")
            self.assertNotIn("test-boat", main.vehicle_queues)
        self.assertEqual(set(asyncio.all_tasks()), before)

    async def test_old_connection_cannot_remove_replacement_queue(self):
        replacement = asyncio.Queue()
        websocket = AsyncMock()

        async def disconnect_after_replacement():
            main.vehicle_queues["test-boat"] = replacement
            raise WebSocketDisconnect()

        websocket.receive_json.side_effect = disconnect_after_replacement
        with patch.dict(main.vehicle_queues, {}, clear=True), patch.dict(
            main.vehicles, {"test-boat": {"connected": True}}, clear=True,
        ), patch.object(main, "broadcast_ui", AsyncMock()) as broadcast:
            await main.vehicle_ws(websocket, "test-boat")
            self.assertIs(main.vehicle_queues["test-boat"], replacement)
            self.assertTrue(main.vehicles["test-boat"]["connected"])
            broadcast.assert_not_awaited()

    async def test_shutdown_cancels_and_awaits_every_background_service(self):
        tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(5)]
        with patch.object(main, "rtcm_task", tasks[0]), patch.object(
            main, "cleanup_task", tasks[1],
        ), patch.object(main, "deconfliction_task", tasks[2]), patch.dict(
            main.sitl_bridges, {"bridge": tasks[3]}, clear=True,
        ), patch.dict(main._rtb_follow_tasks, {"boat": tasks[4]}, clear=True), patch.object(main, "influx_client", None):
            await main.shutdown()
        self.assertTrue(all(task.done() for task in tasks))


class RTCMFragmentTests(unittest.IsolatedAsyncioTestCase):
    def test_fragment_flags_and_data_reconstruct_original_frame(self):
        for length in (1, 180, 181, 359, 719, 720):
            with self.subTest(length=length):
                frame = bytes(index % 256 for index in range(length))
                fragments = main.fragment_rtcm_frame(frame, 35)
                self.assertEqual(b"".join(bytes(fragment["data"]) for fragment in fragments), frame)
                for index, fragment in enumerate(fragments):
                    self.assertLessEqual(fragment["len"], 180)
                    self.assertEqual(fragment["flags"] >> 3, 3)
                    self.assertEqual((fragment["flags"] >> 1) & 3, index)
                    self.assertEqual(fragment["flags"] & 1, int(len(fragments) > 1))

    async def test_rtcm_corrections_are_sent_to_each_connected_vehicle(self):
        queues = {"boat": asyncio.Queue(), "drone": asyncio.Queue()}
        with patch.dict(main.vehicle_queues, queues, clear=True):
            await main.distribute_rtcm_frame(b"x" * 181, 7)
        for vehicle_id, queue in queues.items():
            commands = [queue.get_nowait(), queue.get_nowait()]
            self.assertEqual([item["command"]["len"] for item in commands], [180, 1])
            self.assertTrue(all(item["vehicle_id"] == vehicle_id for item in commands))
            self.assertTrue(all(item["source"] == "rtcm_service" for item in commands))


if __name__ == "__main__":
    unittest.main()
