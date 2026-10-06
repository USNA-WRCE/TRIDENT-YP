import type { Vehicle } from "../types";

export function selectYpVehicle(
  vehicles: readonly Vehicle[],
  roleVehicleId?: string | null,
): Vehicle | undefined {
  return (
    vehicles.find((vehicle) => vehicle.vehicle_id === roleVehicleId) ??
    vehicles.find((vehicle) => vehicle.vehicle_type === "yp")
  );
}
