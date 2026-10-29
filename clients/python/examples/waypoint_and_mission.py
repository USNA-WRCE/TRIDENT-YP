"""Send a waypoint, then a short absolute mission, and print the acks.

Usage: TRIDENT_URL=... TRIDENT_TOKEN=... python waypoint_and_mission.py apache-1
"""
import sys

from trident_client import TridentClient

vehicle_id = "sim-uav1"

with TridentClient() as yp:
    print(yp.goto(vehicle_id, 38.990203, -76.485314, 20))
    print(yp.mission(vehicle_id, [38.9800, 38.9810, 38.9810], [-76.4800, -76.4800, -76.4790], 30))
    print(yp.state(vehicle_id).overlays["mission_plan"])
