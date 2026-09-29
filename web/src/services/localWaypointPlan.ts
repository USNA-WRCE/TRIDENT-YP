export interface LocalPlanWaypoint {
  x: number;
  y: number;
  z: number;
  yaw_deg: number;
}

export interface ImportedLocalWaypointPlan {
  waypoints: LocalPlanWaypoint[];
  faceShip?: boolean;
  loopCount?: number;
  dispatchAltitudeOffset?: number;
  holdLastWaypoint?: boolean;
}

export function applyDispatchAltitudeOffset(
  waypoints: LocalPlanWaypoint[],
  altitudeOffset: number,
): LocalPlanWaypoint[] {
  if (!Number.isFinite(altitudeOffset) || altitudeOffset < 0) {
    throw new Error("Dispatch altitude offset must be a non-negative number.");
  }
  return waypoints.map((waypoint) => ({ ...waypoint, z: waypoint.z + altitudeOffset }));
}

export function serializeLocalWaypointPlan(
  waypoints: LocalPlanWaypoint[],
  faceShip: boolean,
  loopCount: number,
  dispatchAltitudeOffset = 15,
  holdLastWaypoint = false,
): string {
  return JSON.stringify({
    format: "yp-local-waypoint-plan",
    version: 3,
    saved_at: new Date().toISOString(),
    face_ship: faceShip,
    loop_count: loopCount,
    dispatch_altitude_offset_m: dispatchAltitudeOffset,
    hold_last_waypoint: holdLastWaypoint,
    waypoints,
  }, null, 2);
}

export function parseLocalWaypointPlan(text: string): ImportedLocalWaypointPlan {
  const payload = JSON.parse(text) as {
    format?: unknown;
    version?: unknown;
    waypoints?: unknown;
    face_ship?: unknown;
    loop_count?: unknown;
    dispatch_altitude_offset_m?: unknown;
    hold_last_waypoint?: unknown;
  };
  if (payload.format !== "yp-local-waypoint-plan" || ![1, 2, 3].includes(Number(payload.version)) || !Array.isArray(payload.waypoints)) {
    throw new Error("Unsupported local waypoint plan format.");
  }

  const fileVersion = Number(payload.version);
  const savedAltitudeBase = Number(payload.dispatch_altitude_offset_m);
  const altitudeBase = fileVersion === 1
    ? 15
    : Number.isFinite(savedAltitudeBase) ? Math.max(0, Math.min(100, Math.floor(savedAltitudeBase))) : 15;

  const waypoints = payload.waypoints.flatMap((entry): LocalPlanWaypoint[] => {
    if (entry == null || typeof entry !== "object") return [];
    const waypoint = entry as Record<string, unknown>;
    const x = Number(waypoint.x);
    const y = Number(waypoint.y);
    const z = Number(waypoint.z);
    const yaw = waypoint.yaw_deg == null ? 0 : Number(waypoint.yaw_deg);
    if (![x, y, z, yaw].every(Number.isFinite)) return [];
    const adjustment = fileVersion === 1
      ? z + (Number.isFinite(savedAltitudeBase) ? savedAltitudeBase : 0) - altitudeBase
      : z;
    return [{ x, y, z: adjustment, yaw_deg: yaw }];
  });
  if (!waypoints.length) throw new Error("Plan contains no valid waypoints.");

  const loopValue = Number(payload.loop_count);
  return {
    waypoints,
    faceShip: typeof payload.face_ship === "boolean" ? payload.face_ship : undefined,
    loopCount: Number.isFinite(loopValue)
      ? Math.max(1, Math.min(100, Math.floor(loopValue)))
      : undefined,
    dispatchAltitudeOffset: altitudeBase,
    holdLastWaypoint: typeof payload.hold_last_waypoint === "boolean" ? payload.hold_last_waypoint : undefined,
  };
}
