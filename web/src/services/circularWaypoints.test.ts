import { describe, expect, it } from "vitest";
import { generateCircularWaypoints } from "./circularWaypoints";

describe("circular local waypoint generation", () => {
  it("creates evenly spaced points at the requested radius starting at the bow", () => {
    const points = generateCircularWaypoints(20, 4, 12);
    expect(points).toHaveLength(4);
    expect(points[0]).toMatchObject({ x: 0, y: 20, z: 12 });
    expect(points[1].x).toBeCloseTo(20);
    expect(points[1].y).toBeCloseTo(0);
    for (const point of points) {
      expect(Math.hypot(point.x, point.y)).toBeCloseTo(20);
      expect(point.z).toBe(12);
    }
  });

  it("uses zero waypoint adjustment by default", () => {
    expect(generateCircularWaypoints(10, 3).every((point) => point.z === 0)).toBe(true);
  });

  it("rejects a radius or count that cannot form a track", () => {
    expect(() => generateCircularWaypoints(0, 8)).toThrow("radius");
    expect(() => generateCircularWaypoints(10, 2)).toThrow("count");
    expect(() => generateCircularWaypoints(10, 101)).toThrow("count");
  });
});
