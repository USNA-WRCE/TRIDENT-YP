import unittest
from unittest.mock import patch

from support import DatabaseTestCase

from app import auth, main


class FakeDeleteApi:
    def __init__(self):
        self.calls = []

    def delete(self, **kwargs):
        self.calls.append(kwargs)


class InfluxDbAdminTests(DatabaseTestCase):
    def test_admin_can_delete_all_points_from_configured_bucket(self):
        fake_delete_api = FakeDeleteApi()
        with patch.object(main, "delete_api", fake_delete_api):
            response = self.client.request("DELETE", "/api/influxdb/data", json={"confirmation": "DELETE"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"deleted": True})
        self.assertEqual(len(fake_delete_api.calls), 1)
        self.assertEqual(fake_delete_api.calls[0]["bucket"], main.INFLUX_BUCKET)
        self.assertEqual(fake_delete_api.calls[0]["org"], main.INFLUX_ORG)
        self.assertEqual(fake_delete_api.calls[0]["predicate"], '_measurement="yp_messages"')

    def test_non_admin_cannot_delete_influxdb_data(self):
        self.assertTrue(auth.create_user("viewer", "password", "view_only")[0])
        login_response = self.client.post(
            "/api/auth/login", json={"username": "viewer", "password": "password"},
        )
        self.assertEqual(login_response.status_code, 200, login_response.text)

        fake_delete_api = FakeDeleteApi()
        with patch.object(main, "delete_api", fake_delete_api):
            response = self.client.request("DELETE", "/api/influxdb/data", json={"confirmation": "DELETE"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(fake_delete_api.calls, [])

    def test_delete_reports_unavailable_influxdb(self):
        with patch.object(main, "delete_api", None):
            response = self.client.request("DELETE", "/api/influxdb/data", json={"confirmation": "DELETE"})

        self.assertEqual(response.status_code, 503)

    def test_admin_must_explicitly_confirm_before_deletion(self):
        fake_delete_api = FakeDeleteApi()
        with patch.object(main, "delete_api", fake_delete_api):
            response = self.client.request("DELETE", "/api/influxdb/data", json={"confirmation": "delete"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake_delete_api.calls, [])


if __name__ == "__main__":
    unittest.main()