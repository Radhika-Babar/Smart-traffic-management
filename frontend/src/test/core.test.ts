import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, query } from "@/services/api";
import { frames, useLive } from "@/stores/live";
import { duration, fixed, kmh, secondsBetween, title } from "@/utils/format";

import { live } from "./fixtures";

describe("format", () => {
  it("prints a dash for missing values and keeps real zeros", () => {
    expect(fixed(null)).toBe("–");
    expect(fixed(0, 1)).toBe("0.0");
    expect(kmh(null)).toBe("–");
    expect(kmh(10)).toBe("36");
  });

  it("formats durations", () => {
    expect(duration(42)).toBe("42 s");
    expect(duration(125)).toBe("2 m 5 s");
    expect(duration(3700)).toBe("1 h 1 m");
    expect(duration(null)).toBe("–");
  });

  it("makes enum values readable", () => {
    expect(title("MAJOR_ACCIDENT")).toBe("Major accident");
  });

  it("measures the gap between timestamps", () => {
    expect(secondsBetween("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")).toBe(60);
    expect(secondsBetween(null, "2026-01-01T00:01:00Z")).toBeNull();
  });
});

describe("api client", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("builds query strings and drops empty filters", () => {
    expect(query({ a: 1, b: null, c: "", d: false })).toBe("?a=1&d=false");
    expect(query({ a: undefined })).toBe("");
  });

  it("surfaces the backend's error message and code", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "not_manual", message: "Switch to MANUAL first" } }), { status: 409 }),
    );
    await expect(api.post("/api/x")).rejects.toMatchObject({
      message: "Switch to MANUAL first",
      status: 409,
      code: "not_manual",
    });
  });

  it("reports an unreachable backend plainly", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("failed"));
    const failure = await api.get("/api/x").catch((error: ApiError) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).code).toBe("network");
  });
});

describe("live store", () => {
  beforeEach(() =>
    useLive.setState({ live: null, series: [], events: [], alertsVersion: 0, incidentsVersion: 0, emergencyVersion: 0 }),
  );

  it("appends each new series point once", () => {
    const point = { t: "2026-01-01T08:00:00Z", density: 1, queue: 2, wait: 3, speed: 4, volume: 5 };
    useLive.getState().receive("live", { ...live, point });
    useLive.getState().receive("live", { ...live, point });
    expect(useLive.getState().series).toHaveLength(1);
  });

  it("keeps vehicle frames out of React state", () => {
    const frame = { time: 1, clock: "", vehicles: [], counts: {}, signals: {}, incidents: [] };
    useLive.getState().receive("frame", frame);
    expect(frames.current).toBe(frame);
    expect(useLive.getState().live).toBeNull();
  });

  it("refreshes lists only once rows are committed, never on the raw event", () => {
    useLive.getState().receive("alert", {});
    useLive.getState().receive("incident", {});
    expect(useLive.getState().alertsVersion).toBe(0);
    useLive.getState().receive("persisted", ["alert", "congestion"]);
    expect(useLive.getState().alertsVersion).toBe(1);
    expect(useLive.getState().incidentsVersion).toBe(1);
    expect(useLive.getState().emergencyVersion).toBe(0);
    useLive.getState().receive("persisted", ["emergency"]);
    expect(useLive.getState().emergencyVersion).toBe(1);
  });

  it("merges simulation updates into the live payload", () => {
    useLive.getState().receive("live", live);
    useLive.getState().receive("simulation", { ...live.simulation, state: "PAUSED" });
    expect(useLive.getState().live?.simulation.state).toBe("PAUSED");
  });
});
