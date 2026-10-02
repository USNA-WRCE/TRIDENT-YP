import { describe, expect, it } from "vitest";
import { summarizeFlightLog } from "./flightLog";

describe("summarizeFlightLog", () => {
  it("extracts vehicle IDs, available numeric fields, and time bounds", () => {
    const text = [
      JSON.stringify({ format: "yp-ground-station-log", schema_version: 1 }),
      JSON.stringify({ timestamp: "2026-10-01T12:00:00.000Z", vehicle_id: "boat-02", fields: { longitude: -76.4, mode: "loiter" } }),
      JSON.stringify({ timestamp: "2026-10-01T12:01:00.000Z", vehicle_id: "boat-01", fields: { latitude: 38.9 } }),
      JSON.stringify({ timestamp: "2026-10-01T12:02:00.000Z", vehicle_id: "boat-02", fields: { latitude: 38.8 } }),
    ].join("\n");

    expect(summarizeFlightLog(text)).toEqual({
      vehicles: ["boat-01", "boat-02"],
      firstTimestamp: Date.parse("2026-10-01T12:00:00.000Z"),
      lastTimestamp: Date.parse("2026-10-01T12:02:00.000Z"),
      fieldsByVehicle: { "boat-01": ["latitude"], "boat-02": ["latitude", "longitude"] },
    });
  });

  it("rejects files that are not flight log exports", () => {
    expect(() => summarizeFlightLog("{}\n{}"))
      .toThrow("This file is not a TRIDENT flight log backup.");
  });

  it("reports malformed telemetry rows with their line number", () => {
    const text = `${JSON.stringify({ format: "yp-ground-station-log" })}\nnot-json`;
    expect(() => summarizeFlightLog(text)).toThrow("Invalid JSON on flight log line 2.");
  });
});