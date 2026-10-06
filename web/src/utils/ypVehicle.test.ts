import { describe, expect, it } from "vitest";
import type { Vehicle } from "../types";
import { selectYpVehicle } from "./ypVehicle";

function vehicle(vehicle_id: string, vehicle_type: Vehicle["vehicle_type"] = "yp"): Vehicle {
  return {
    vehicle_id,
    vehicle_type,
    connected: true,
    last_seen: 0,
    position: { latitude: 0, longitude: 0, altitude: 0 },
  };
}

describe("selectYpVehicle", () => {
  it("prefers the explicitly assigned role over another vehicle typed as YP", () => {
    const simulatedGps = vehicle("YP689");
    const emulator = vehicle("Hunter_YPEmulator");

    expect(selectYpVehicle([simulatedGps, emulator], emulator.vehicle_id)).toBe(emulator);
  });

  it("uses the typed YP as fallback when there is no assigned vehicle", () => {
    const simulatedGps = vehicle("YP689");

    expect(selectYpVehicle([simulatedGps], null)).toBe(simulatedGps);
  });

  it("keeps the assigned vehicle authoritative when it has not reported YP type", () => {
    const simulatedGps = vehicle("YP689");
    const emulator = vehicle("Hunter_YPEmulator", "usv");

    expect(selectYpVehicle([simulatedGps, emulator], emulator.vehicle_id)).toBe(emulator);
  });
});
