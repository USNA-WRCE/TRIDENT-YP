import gzip
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class FakeRecord:
    def __init__(self, values):
        self.values = values

    def get_time(self):
        return self.values["_time"]


class FakeQueryApi:
    def __init__(self, records=None, error=None):
        self.records = records or []
        self.error = error

    def query_stream(self, **kwargs):
        if self.error:
            raise self.error
        return iter(self.records)


class FlightLogExportTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def export(self):
        return self.client.get(
            "/api/logs/export", params={"last_hours": 1},
            headers={"Authorization": "Bearer token"},
        )

    def test_range_requires_start_and_end(self):
        with self.assertRaisesRegex(ValueError, "start and end"):
            main._log_range(None, None, None)

    def test_range_rejects_reversed_timestamps(self):
        with self.assertRaisesRegex(ValueError, "start must be before end"):
            main._log_range("2026-09-02T10:00:00Z", "2026-09-02T09:00:00Z", None)

    def test_export_requires_permission(self):
        response = self.client.get("/api/logs/export", params={"last_hours": 1})
        self.assertEqual(response.status_code, 401)

    def test_empty_export_returns_not_found(self):
        with patch.object(main, "require_permission", return_value=None), patch.object(main, "query_api", FakeQueryApi()):
            response = self.export()
        self.assertEqual(response.status_code, 404)

    def test_query_failure_returns_service_unavailable(self):
        with patch.object(main, "require_permission", return_value=None), patch.object(
            main, "query_api", FakeQueryApi(error=RuntimeError("offline"))
        ):
            response = self.export()
        self.assertEqual(response.status_code, 503)

    def test_successful_export_has_metadata_and_message_records(self):
        record = FakeRecord(
            {
                "_time": datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
                "vehicle_id": "boat-01",
                "vehicle_type": "usv",
                "kind": "telemetry",
                "latitude": 38.9,
                "longitude": -76.4,
            }
        )
        with patch.object(main, "require_permission", return_value=None), patch.object(
            main, "query_api", FakeQueryApi([record])
        ):
            response = self.export()
            body = response.content

        lines = [json.loads(line) for line in gzip.decompress(body).decode().splitlines()]
        self.assertEqual(lines[0]["format"], "yp-ground-station-log")
        self.assertEqual(lines[1]["fields"]["latitude"], 38.9)
        self.assertEqual(lines[1]["kind"], "telemetry")
        self.assertNotIn("kind", lines[1]["fields"])
        self.assertIn("attachment", response.headers["content-disposition"])

    def test_writer_builds_tagged_point_with_stable_fields(self):
        while not main._influx_write_queue.empty():
            main._influx_write_queue.get_nowait()
        with patch.object(main, "write_api", object()):
            main.write_influx("telemetry", "boat-01", "usv", 1756814400.0, {"latitude": 38.9, "behavior": "waypoint", "mode": None})
        point = main._influx_write_queue.get_nowait()
        line = point.to_line_protocol()
        self.assertIn("kind=telemetry", line)
        self.assertIn("vehicle_id=boat-01", line)
        self.assertIn("latitude=38.9", line)
        self.assertIn('behavior="waypoint"', line)
        self.assertNotIn("mode=", line)

    def test_telemetry_fields_flatten_to_openmct_names(self):
        fields = main._telemetry_influx_fields({
            "position": {"latitude": 1.0, "longitude": 2.0, "altitude": 3.0},
            "heading": 90.0,
            "behavior": "idle",
            "battery": {"percentage": 0.5},
            "gps": {"fix_type": 3, "satellites": 12},
        })
        self.assertEqual(fields["latitude"], 1.0)
        self.assertEqual(fields["battery_percentage"], 0.5)
        self.assertEqual(fields["gps_satellites"], 12)
        self.assertEqual(fields["behavior"], "idle")


if __name__ == "__main__":
    unittest.main()
