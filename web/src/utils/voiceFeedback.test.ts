import { describe, expect, it } from "vitest";

import { commandAckSpeech } from "./voiceFeedback";

describe("spoken command acknowledgement summaries", () => {
  it("distinguishes a routed command from an unavailable vehicle connection", () => {
    expect(commandAckSpeech({
      source: "ui",
      vehicle_id: "DroneJr",
      delivered: true,
      command: { type: "takeoff", altitude_m: 15 },
    })).toBe("Takeoff command sent to DroneJr to 15 meters.");

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
});