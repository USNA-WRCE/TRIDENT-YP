import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class FakeRecord:
    def __init__(self, value, timestamp=None):
        self.value = value
        self.timestamp = timestamp

    def get_value(self):
        return self.value

    def get_time(self):
        return self.timestamp


class FakeTable:
    def __init__(self, records):
        self.records = records


class FakeQueryApi:
    def __init__(self, tables):
        self.tables = tables
        self.query_text = ""

    def query(self, query, org):
        self.query_text = query
        return self.tables


class OpenMctHistoryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_vehicle_catalog_includes_retained_ids_and_retention(self):
        query_api = FakeQueryApi([
            FakeTable([FakeRecord("boat-02"), FakeRecord("boat-01"), FakeRecord("boat-02")]),
        ])
        with patch.object(main, "query_api", query_api):
            response = self.client.get("/api/openmct/vehicles")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"vehicles": ["boat-01", "boat-02"], "retention_seconds": main.settings["message_retention_seconds"]})
        self.assertIn('r._measurement == "yp_messages"', query_api.query_text)
        self.assertIn("|> last()", query_api.query_text)
        self.assertIn('distinct(column: "vehicle_id")', query_api.query_text)

    def test_history_query_filters_measurement_vehicle_and_field(self):
        timestamp = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        query_api = FakeQueryApi([FakeTable([FakeRecord(38.9, timestamp)])])
        with patch.object(main, "query_api", query_api):
            response = self.client.get(
                "/api/openmct/history/boat-01/yp_messages/latitude",
                params={"start": 1790940000000, "end": 1790943600000},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            "timestamp": int(timestamp.timestamp() * 1000),
            "value": 38.9,
            "id": "boat-01.latitude",
        }])
        self.assertIn('r._measurement == "yp_messages"', query_api.query_text)
        self.assertIn('r.vehicle_id == "boat-01"', query_api.query_text)
        self.assertIn('r._field == "latitude"', query_api.query_text)

    def test_history_rejects_unsupported_measurements_and_reversed_ranges(self):
        with patch.object(main, "query_api", FakeQueryApi([])):
            unsupported = self.client.get(
                "/api/openmct/history/boat-01/other/latitude",
                params={"start": 1000, "end": 2000},
            )
            reversed_range = self.client.get(
                "/api/openmct/history/boat-01/yp_messages/latitude",
                params={"start": 2000, "end": 1000},
            )

        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(reversed_range.status_code, 400)


if __name__ == "__main__":
    unittest.main()