import { describe, expect, it } from "vitest";
import { applyDispatchAltitudeOffset, parseLocalWaypointPlan, serializeLocalWaypointPlan } from "./localWaypointPlan";

describe("local waypoint plan files", () => {
  it("round-trips local coordinates, inward-facing option, and loop count", () => {
    const source = [{ x: 12.5, y: -4, z: 20, yaw_deg: 35 }];
    const serialized = serializeLocalWaypointPlan(source, true, 3, 25);
    expect(parseLocalWaypointPlan(serialized)).toEqual({
      waypoints: source,
      faceShip: true,
      loopCount: 3,
      dispatchAltitudeOffset: 25,
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
      waypoints: [{ x: 1, y: 2, z: -12, yaw_deg: 0 }],
      faceShip: undefined,
      loopCount: 100,
      dispatchAltitudeOffset: 15,
    });
  });

  it("adds the dispatch altitude offset uniformly to every waypoint", () => {
    expect(applyDispatchAltitudeOffset([
      { x: 0, y: 20, z: 15, yaw_deg: 0 },
      { x: 20, y: 0, z: 25, yaw_deg: 90 },
    ], 10)).toEqual([
      { x: 0, y: 20, z: 25, yaw_deg: 0 },
      { x: 20, y: 0, z: 35, yaw_deg: 90 },
    ]);
  });

  it("migrates version 1 effective waypoint altitudes to a 15 m base", () => {
    const legacyPlan = JSON.stringify({
      format: "yp-local-waypoint-plan",
      version: 1,
      dispatch_altitude_offset_m: 0,
      waypoints: [{ x: 0, y: 20, z: 15, yaw_deg: 0 }],
    });
    expect(parseLocalWaypointPlan(legacyPlan)).toMatchObject({
      waypoints: [{ x: 0, y: 20, z: 0, yaw_deg: 0 }],
      dispatchAltitudeOffset: 15,
    });
  });
});
