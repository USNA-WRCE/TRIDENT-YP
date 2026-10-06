import type { Command } from "../types";

interface CommandAcknowledgement {
  source?: unknown;
  vehicle_id?: unknown;
  delivered?: unknown;
  command?: {
    type: string;
    altitude_m?: number;
    target?: { altitude?: number };
    grid_size_m?: number;
    mode?: string;
  };
}

export function commandAckSpeech(payload: CommandAcknowledgement): string | null {
  if (payload.source !== "ui" && payload.source !== "sar_api") return null;
  const vehicleId = String(payload.vehicle_id || "vehicle");
  const command = payload.command;
  if (!command) return null;
  if (payload.delivered === false) {
    return `The ${command.type.replace(/_/g, " ")} command for ${vehicleId} was not delivered because its vehicle connection is unavailable.`;
  }
  switch (command.type as Command["type"] | "mob") {
    case "takeoff":
      return `Takeoff command sent to ${vehicleId} to ${Number(command.altitude_m ?? 15)} meters.`;
    case "waypoint": {
      const altitude = command.target?.altitude;
      return `Waypoint command sent to ${vehicleId}${altitude == null ? "." : ` with a target altitude of ${altitude} meters.`}`;
    }
    case "search_grid":
      return `Search grid command sent to ${vehicleId} for a ${Number(command.grid_size_m ?? 200)} meter grid.`;
    case "rtb":
      return `Return to the YP vessel started for ${vehicleId}.`;
    case "land_on_boat":
      return `Landing guidance to the YP vessel started for ${vehicleId}.`;
    case "mob":
      return `Man overboard search dispatched to ${vehicleId}.`;
    case "mission_plan":
      return `Mission sent to ${vehicleId}.`;
    case "set_mode":
      return `Flight mode change sent to ${vehicleId}${command.mode ? `: ${command.mode}` : ""}.`;
    case "arm":
      return `Arm command sent to ${vehicleId}.`;
    case "disarm":
      return `Disarm command sent to ${vehicleId}.`;
    default:
      return `${command.type.replace(/_/g, " ")} command sent to ${vehicleId}.`;
  }
}