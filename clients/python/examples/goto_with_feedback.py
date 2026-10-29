"""Drive a vehicle to a point with a proportional speed controller using live state.

Usage: TRIDENT_URL=... TRIDENT_TOKEN=... python goto_with_feedback.py blueboat-1 38.98 -76.48
"""
import sys

from trident_client import TridentClient, geo

vehicle_id, lat, lon = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
MAX_SPEED_MS, ARRIVAL_M = 2.0, 3.0

with TridentClient() as yp:
    yp.set_mode(vehicle_id, "GUIDED")
    for state in yp.stream_states([vehicle_id]):
        if state.position is None:
            continue
        distance = geo.distance_m(state.latitude, state.longitude, lat, lon)
        if distance < ARRIVAL_M:
            yp.set_velocity(vehicle_id, 0.0, state.heading_deg or 0.0)
            print("arrived")
            break
        yp.set_velocity(vehicle_id, min(MAX_SPEED_MS, distance / 5.0), geo.bearing_deg(state.latitude, state.longitude, lat, lon))
