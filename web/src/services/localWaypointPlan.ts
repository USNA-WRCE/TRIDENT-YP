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
}

export function serializeLocalWaypointPlan(
  waypoints: LocalPlanWaypoint[],
  faceShip: boolean,
  loopCount: number,
): string {
  return JSON.stringify({
    format: "yp-local-waypoint-plan",
    version: 1,
    saved_at: new Date().toISOString(),
    face_ship: faceShip,
    loop_count: loopCount,
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
  };
  if (payload.format !== "yp-local-waypoint-plan" || payload.version !== 1 || !Array.isArray(payload.waypoints)) {
    throw new Error("Unsupported local waypoint plan format.");
  }

  const waypoints = payload.waypoints.flatMap((entry): LocalPlanWaypoint[] => {
    if (entry == null || typeof entry !== "object") return [];
    const waypoint = entry as Record<string, unknown>;
    const x = Number(waypoint.x);
    const y = Number(waypoint.y);
    const z = Number(waypoint.z);
    const yaw = waypoint.yaw_deg == null ? 0 : Number(waypoint.yaw_deg);
    if (![x, y, z, yaw].every(Number.isFinite)) return [];
    return [{ x, y, z, yaw_deg: yaw }];
  });
  if (!waypoints.length) throw new Error("Plan contains no valid waypoints.");

  const loopValue = Number(payload.loop_count);
  return {
    waypoints,
    faceShip: typeof payload.face_ship === "boolean" ? payload.face_ship : undefined,
    loopCount: Number.isFinite(loopValue)
      ? Math.max(1, Math.min(100, Math.floor(loopValue)))
      : undefined,
  };
}
