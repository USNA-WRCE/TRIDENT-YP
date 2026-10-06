import { describe, expect, it } from "vitest";
import { createDemoVehicles, demoVehicleSnapshot, handleDemoCommand, stepDemoVehicle, updateDemoVehicleColor } from "./demo";
import { haversineMeters } from "../utils/geo";

describe("hardware-free telemetry and commands", () => {
  it("publishes one telemetry payload per vehicle step with a bounded trail", () => {
    const vehicles = createDemoVehicles();
    expect(new Set(vehicles.map((vehicle) => vehicle.vehicle_id)).size).toBe(vehicles.length);
    for (let tick = 0; tick < 510; tick++) {
      for (const vehicle of vehicles) {
        const messages = stepDemoVehicle(vehicle, 0.2, tick / 5, vehicles);
        expect(messages).toHaveLength(1);
        expect(messages[0]).toMatchObject({ op: "telemetry", vehicle_id: vehicle.vehicle_id });
        expect(messages[0].position.latitude).toBeCloseTo(vehicle.lat);
        expect(typeof messages[0].behavior).toBe("string");
      }
    }
    for (const vehicle of vehicles) {
      const snapshot = demoVehicleSnapshot(vehicle);
      expect(snapshot.connected).toBe(true);
      expect(snapshot.history).toHaveLength(500);
      expect(snapshot.position?.latitude).toBeCloseTo(vehicle.lat);
      expect(snapshot.behavior).toBeTruthy();
      expect(snapshot).not.toHaveProperty("messages");
    }
  });

  it("advances mission waypoints and lets a new waypoint interrupt the queue", () => {
    const vehicles = createDemoVehicles();
    const vehicle = vehicles[1];
    const first = { latitude: vehicle.lat, longitude: vehicle.lon, altitude: vehicle.alt };
    const second = { ...first, latitude: first.latitude + 0.001 };
    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "mission_plan", waypoints: [first, second] });
    stepDemoVehicle(vehicle, 0.2, 1, vehicles);
    expect(vehicle.target).toEqual(second);
    const override = { ...first, longitude: first.longitude - 0.001 };
    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "waypoint", target: override });
    expect(vehicle.target).toEqual(override);
    expect(vehicle.missionWaypoints).toEqual([]);
    expect(vehicle.mode).toBe("waypoint");
  });

  it("reports and clears demo SAR behavior for search commands", () => {
    const vehicles = createDemoVehicles();
    const vehicle = vehicles[1];
    handleDemoCommand(vehicles, vehicle.vehicle_id, {
      type: "search_grid",
      lat: vehicle.lat + 0.001,
      lon: vehicle.lon,
      altitude_m: vehicle.alt,
    });
    expect(demoVehicleSnapshot(vehicle).behavior).toBe("search_grid");

    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "cancel_sar" });
    expect(demoVehicleSnapshot(vehicle).behavior).toBe("idle");

    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "mob" });
    expect(demoVehicleSnapshot(vehicle).behavior).toBe("mob_search");

    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "cancel_sar" });
    expect(demoVehicleSnapshot(vehicle).behavior).toBe("idle");
  });

  it("keeps the RTB target following the moving mother ship", () => {
    const vehicles = createDemoVehicles();
    const [yp, vehicle] = vehicles;
    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "rtb" });
    const previousTarget = { ...vehicle.target };
    stepDemoVehicle(yp, 10, 10, vehicles);
    stepDemoVehicle(vehicle, 0.2, 10, vehicles);
    expect(haversineMeters(previousTarget.latitude, previousTarget.longitude, vehicle.target.latitude, vehicle.target.longitude)).toBeGreaterThan(20);
    expect(vehicle.mode).toBe("rtb");
  });

  it("converts ship-relative waypoints and retains chosen marker colors", () => {
    const vehicles = createDemoVehicles();
    const [yp, vehicle] = vehicles;
    handleDemoCommand(vehicles, vehicle.vehicle_id, {
      type: "ship_relative_trajectory", ship_vehicle_id: yp.vehicle_id,
      local_waypoints: [{ x: 0, y: 0, z: 20 }],
    });
    expect(vehicle.target.latitude).toBeCloseTo(yp.lat);
    expect(vehicle.target.longitude).toBeCloseTo(yp.lon);
    expect(vehicle.target.altitude).toBe(yp.alt + 20);
    updateDemoVehicleColor(vehicles, vehicle.vehicle_id, "#123456");
    expect(demoVehicleSnapshot(vehicle)).toMatchObject({ marker_color: "#123456" });
  });

  it("faces the direction of travel when inward-facing mode is disabled", () => {
    const vehicles = createDemoVehicles();
    const [yp, vehicle] = vehicles;
    vehicle.lat = yp.lat - 0.0001;
    vehicle.lon = yp.lon;
    const headingBeforeDispatch = vehicle.heading;
    handleDemoCommand(vehicles, vehicle.vehicle_id, {
      type: "ship_relative_trajectory", ship_vehicle_id: yp.vehicle_id,
      local_waypoints: [{ x: 0, y: 0, z: vehicle.alt, yaw_deg: 270 }],
    });
    stepDemoVehicle(vehicle, 0.2, 1, vehicles);
    expect(vehicle.mode).toBe("ship_relative");
    expect(vehicle.targetYawDeg).toBeCloseTo(0, 1);
    expect(Math.abs(vehicle.heading)).toBeLessThan(Math.abs(headingBeforeDispatch));
  });

  it("faces inward and repeats the full ship-relative waypoint list", () => {
    const vehicles = createDemoVehicles();
    const [yp, vehicle] = vehicles;
    vehicle.lat = yp.lat + 0.00001;
    vehicle.lon = yp.lon;
    vehicle.alt = yp.alt;
    const localWaypoint = { x: 0, y: 0, z: 0, yaw_deg: 0 };
    handleDemoCommand(vehicles, vehicle.vehicle_id, {
      type: "ship_relative_trajectory",
      ship_vehicle_id: yp.vehicle_id,
      local_waypoints: [localWaypoint],
      face_ship: true,
      loop_count: 2,
    });

    expect(vehicle.shipRelativeWaypoints).toHaveLength(2);
    stepDemoVehicle(vehicle, 0.2, 1, vehicles);
    expect(vehicle.mode).toBe("ship_relative");
    expect(vehicle.heading).toBeCloseTo(180);
    expect(vehicle.targetYawDeg).toBeCloseTo(180);
    stepDemoVehicle(vehicle, 0.2, 2, vehicles);
    expect(vehicle.mode).toBe("hold");
    expect(vehicle.heading).toBeCloseTo(180);
  });

  it("keeps tracking the final relative waypoint when hold is enabled", () => {
    const vehicles = createDemoVehicles();
    const [yp, vehicle] = vehicles;
    vehicle.lat = yp.lat;
    vehicle.lon = yp.lon;
    vehicle.alt = yp.alt;
    handleDemoCommand(vehicles, vehicle.vehicle_id, {
      type: "ship_relative_trajectory",
      ship_vehicle_id: yp.vehicle_id,
      local_waypoints: [{ x: 0, y: 0, z: 0 }],
      hold_last_waypoint: true,
    });
    stepDemoVehicle(vehicle, 0.2, 1, vehicles);
    expect(vehicle.mode).toBe("ship_relative");
    expect(vehicle.shipRelativeIndex).toBe(0);
    stepDemoVehicle(yp, 2, 2, vehicles);
    stepDemoVehicle(vehicle, 0.2, 2, vehicles);
    expect(vehicle.mode).toBe("ship_relative");
    expect(vehicle.target.latitude).toBeCloseTo(yp.lat);
    expect(vehicle.target.longitude).toBeCloseTo(yp.lon);
  });

  it("releases the final relative waypoint when a new waypoint command arrives", () => {
    const vehicles = createDemoVehicles();
    const [yp, vehicle] = vehicles;
    handleDemoCommand(vehicles, vehicle.vehicle_id, {
      type: "ship_relative_trajectory",
      ship_vehicle_id: yp.vehicle_id,
      local_waypoints: [{ x: 0, y: 0, z: 0 }],
      hold_last_waypoint: true,
    });
    const override = { latitude: vehicle.lat + 0.001, longitude: vehicle.lon, altitude: vehicle.alt };
    handleDemoCommand(vehicles, vehicle.vehicle_id, { type: "waypoint", target: override });
    expect(vehicle.mode).toBe("waypoint");
    expect(vehicle.target).toEqual(override);
    expect(vehicle.shipRelativeWaypoints).toEqual([]);
  });
});
