export interface CircularWaypoint {
  x: number;
  y: number;
  z: number;
  yaw_deg: number;
}

export function generateCircularWaypoints(
  radiusMeters: number,
  waypointCount: number,
  altitudeOffsetMeters = 0,
): CircularWaypoint[] {
  if (!Number.isFinite(radiusMeters) || radiusMeters <= 0) {
    throw new Error("Circle radius must be greater than zero.");
  }
  if (!Number.isInteger(waypointCount) || waypointCount < 3 || waypointCount > 100) {
    throw new Error("Circle waypoint count must be between 3 and 100.");
  }
  if (!Number.isFinite(altitudeOffsetMeters)) {
    throw new Error("Waypoint altitude offset must be a finite number.");
  }

  return Array.from({ length: waypointCount }, (_, index) => {
    const angle = (2 * Math.PI * index) / waypointCount;
    return {
      x: radiusMeters * Math.sin(angle),
      y: radiusMeters * Math.cos(angle),
      z: altitudeOffsetMeters,
      yaw_deg: 0,
    };
  });
}
