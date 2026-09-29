import { describe, expect, it } from "vitest";
import { parseLocalWaypointPlan, serializeLocalWaypointPlan } from "./localWaypointPlan";

describe("local waypoint plan files", () => {
  it("round-trips local coordinates, inward-facing option, and loop count", () => {
    const source = [{ x: 12.5, y: -4, z: 20, yaw_deg: 35 }];
    const serialized = serializeLocalWaypointPlan(source, true, 3);
    expect(parseLocalWaypointPlan(serialized)).toEqual({
      waypoints: source,
      faceShip: true,
      loopCount: 3,
    });
  });

  it("rejects unsupported formats and drops invalid waypoint records", () => {
    expect(() => parseLocalWaypointPlan(JSON.stringify({ waypoints: [] }))).toThrow("Unsupported");
    const serialized = JSON.stringify({
      format: "yp-local-waypoint-plan",
      version: 1,
      waypoints: [{ x: 1, y: 2, z: 3 }, { x: "bad", y: 0, z: 0 }],
      loop_count: 500,
    });
    expect(parseLocalWaypointPlan(serialized)).toEqual({
      waypoints: [{ x: 1, y: 2, z: 3, yaw_deg: 0 }],
      faceShip: undefined,
      loopCount: 100,
    });
  });
});
