from unittest.mock import patch

from support import DatabaseTestCase

from app import auth, main


class VoiceApiTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.vehicles = {
            "DroneJr": {
                "vehicle_id": "DroneJr",
                "vehicle_type": "uav",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 15},
            },
            "YP689": {
                "vehicle_id": "YP689",
                "vehicle_type": "yp",
                "connected": True,
                "position": {"latitude": 38.9, "longitude": -76.4, "altitude": 0},
            },
        }

    def test_interpret_returns_preview_without_returning_transcript(self):
        with (
            patch.object(main, "vehicles", self.vehicles),
            patch.object(main, "_select_yp_vehicle_locked", return_value=self.vehicles["YP689"]),
            patch.object(main, "transcribe_audio", return_value="drone junior return to boat") as transcribe,
        ):
            response = self.client.post("/api/voice/interpret", content=b"ephemeral audio")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["vehicle_id"], "DroneJr")
        self.assertEqual(response.json()["command"], {"type": "rtb"})
        self.assertNotIn("transcript", response.json())
        transcribe.assert_called_once_with(b"ephemeral audio")

    def test_interpret_rejects_missing_auth_before_transcribing(self):
        self.client.cookies.clear()
        with patch.object(main, "transcribe_audio") as transcribe:
            response = self.client.post("/api/voice/interpret", content=b"ephemeral audio")

        self.assertEqual(response.status_code, 401)
        transcribe.assert_not_called()

    def test_interpret_rejects_command_without_user_permission(self):
        auth.create_user("viewer", "password", "view_only")
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"username": "viewer", "password": "password"})
        with patch.object(main, "transcribe_audio", return_value="drone junior return to boat"):
            response = self.client.post("/api/voice/interpret", content=b"ephemeral audio")

        self.assertEqual(response.status_code, 403)

    def test_login_cookie_is_secure_behind_https_proxy(self):
        response = self.client.post(
            "/api/auth/login",
            json={"username": "operator", "password": "password"},
            headers={"x-forwarded-proto": "https"},
        )

        self.assertIn("secure", response.headers["set-cookie"].lower())

    def test_speak_returns_uncached_wave_audio(self):
        audio = b"RIFF-test-wave"
        with patch.object(main, "synthesize_speech", return_value=audio) as synthesize:
            response = self.client.post("/api/voice/speak", json={"text": "Waypoint command sent."})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["cache-control"], "no-store, private")
        self.assertEqual(response.content, audio)
        synthesize.assert_called_once_with("Waypoint command sent.")

    def test_speak_rejects_missing_auth_and_oversized_text(self):
        self.client.cookies.clear()
        with patch.object(main, "synthesize_speech") as synthesize:
            unauthorized = self.client.post("/api/voice/speak", json={"text": "Hello"})
        self.assertEqual(unauthorized.status_code, 401)
        synthesize.assert_not_called()

        self.login()
        oversized = self.client.post("/api/voice/speak", json={"text": "x" * 401})
        self.assertEqual(oversized.status_code, 413)