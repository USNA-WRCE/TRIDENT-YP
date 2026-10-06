import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from yp_gps import KNOTS_TO_MPS, parse_nmea, send_fix


class NMEATelemetryTests(unittest.TestCase):
    def test_gga_does_not_invent_a_speed(self):
        parsed = parse_nmea("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["altitude"], 545.4)
        self.assertNotIn("speed_mps", parsed)

    def test_rmc_reports_speed_in_meters_per_second(self):
        parsed = parse_nmea("$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["speed_mps"], 22.4 * KNOTS_TO_MPS)

    def test_fix_without_speed_omits_behavior(self):
        websocket = AsyncMock()

        asyncio.run(send_fix(websocket, 38.9, -76.4, 2.0, 90.0))
        payload = json.loads(websocket.send.await_args.args[0])
        self.assertNotIn("behavior", payload)
