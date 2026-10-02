export interface FlightLogSummary {
  vehicles: string[];
  firstTimestamp: number;
  lastTimestamp: number;
  fieldsByVehicle: Record<string, string[]>;
}

interface FlightLogRecord {
  timestamp: string;
  vehicle_id: string;
  fields: Record<string, unknown>;
}

export function summarizeFlightLog(text: string): FlightLogSummary {
  const lines = text.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length < 2) throw new Error("The selected file contains no flight log records.");

  let metadata: { format?: string };
  try {
    const parsed: unknown = JSON.parse(lines[0]);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    metadata = parsed as { format?: string };
  } catch {
    throw new Error("The selected file is not a valid flight log.");
  }
  if (metadata.format !== "yp-ground-station-log") {
    throw new Error("This file is not a TRIDENT flight log backup.");
  }

  const vehicles = new Set<string>();
  const fieldsByVehicle: Record<string, Set<string>> = {};
  let firstTimestamp = Number.POSITIVE_INFINITY;
  let lastTimestamp = Number.NEGATIVE_INFINITY;

  for (const [index, line] of lines.slice(1).entries()) {
    let record: FlightLogRecord;
    try {
      const parsed: unknown = JSON.parse(line);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
      record = parsed as FlightLogRecord;
    } catch {
      throw new Error(`Invalid JSON on flight log line ${index + 2}.`);
    }

    const timestamp = Date.parse(record.timestamp);
    if (!record.vehicle_id || !Number.isFinite(timestamp) || !record.fields || typeof record.fields !== "object" || Array.isArray(record.fields)) {
      throw new Error(`Invalid telemetry record on flight log line ${index + 2}.`);
    }

    vehicles.add(record.vehicle_id);
    fieldsByVehicle[record.vehicle_id] ??= new Set<string>();
    for (const [field, value] of Object.entries(record.fields)) {
      if (typeof value === "number" && Number.isFinite(value)) fieldsByVehicle[record.vehicle_id].add(field);
    }
    firstTimestamp = Math.min(firstTimestamp, timestamp);
    lastTimestamp = Math.max(lastTimestamp, timestamp);
  }

  return {
    vehicles: [...vehicles].sort(),
    firstTimestamp,
    lastTimestamp,
    fieldsByVehicle: Object.fromEntries(
      Object.entries(fieldsByVehicle).map(([vehicleId, fields]) => [vehicleId, [...fields].sort()]),
    ),
  };
}