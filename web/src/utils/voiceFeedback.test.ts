import { describe, expect, it } from "vitest";

import { commandAckSpeech } from "./voiceFeedback";

describe("spoken command acknowledgement summaries", () => {
  it("distinguishes a routed command from an unavailable vehicle connection", () => {
    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: true,
      command: { type: "takeoff", altitude_m: 15.26 },
    })).toBe("Takeoff command sent to DroneJr to 15.3 meters.");

    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: true,
      command: { type: "waypoint", target: { altitude: 12.34 } },
    })).toBe("Waypoint command sent to DroneJr with a target altitude of 12.3 meters.");

    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: false,
      command: { type: "rtb" },
    })).toContain("not delivered because its vehicle connection is unavailable");
  });


  it("does not announce internal commands to operators", () => {
    expect(commandAckSpeech({
      source: "deconfliction",
      vehicle_id: "DroneJr",
      delivered: true,
      command: { type: "waypoint" },
    })).toBeNull();
  });

  it("announces SAR cancellation only when an active SAR pattern was cleared", () => {
    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: true,
      command: { type: "cancel_sar" },
    })).toBeNull();

    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: true,
      sar_cancelled: true,
      command: { type: "cancel_sar" },
    })).toBe("SAR search canceled for DroneJr.");

    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: false,
      sar_cancelled: true,
      command: { type: "cancel_sar" },
    })).toBeNull();
  });
});