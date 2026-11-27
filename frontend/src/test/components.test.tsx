import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EmptyState, ErrorState, LevelBadge, Metric } from "@/components/common";
import { DecisionExplainer } from "@/components/DecisionExplainer";
import { phaseText, SignalControls } from "@/components/SignalControls";
import { HintProvider } from "@/components/ui/primitives";
import { Alerts } from "@/pages/Alerts";
import { Overview } from "@/pages/Overview";
import { useLive } from "@/stores/live";

import { intersection, live, score } from "./fixtures";

function mount(element: ReactElement) {
  return render(
    <HintProvider>
      <MemoryRouter>{element}</MemoryRouter>
    </HintProvider>,
  );
}

function mockFetch(routes: Record<string, unknown>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    const key = Object.keys(routes).find((route) => path.startsWith(route));
    return new Response(JSON.stringify(key ? routes[key] : {}), { status: key ? 200 : 404 });
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  useLive.setState({ live: null, series: [], events: [], sensors: [], toasts: [], topology: null });
});

describe("common states", () => {
  it("renders a metric with unit", () => {
    mount(<Metric label="Average wait" value="18.4" unit="s" />);
    expect(screen.getByText("Average wait")).toBeInTheDocument();
    expect(screen.getByText("18.4")).toBeInTheDocument();
  });

  it("offers retry on error", async () => {
    const retry = vi.fn();
    mount(<ErrorState message="Backend down" onRetry={retry} />);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("shows empty state copy and congestion level text, not colour alone", () => {
    mount(
      <>
        <EmptyState message="No alerts" />
        <LevelBadge level="SEVERE" />
      </>,
    );
    expect(screen.getByText("No alerts")).toBeInTheDocument();
    expect(screen.getByText("Severe")).toBeInTheDocument();
  });
});

describe("decision explainer", () => {
  it("shows every approach, the reason and the live green", () => {
    mount(
      <DecisionExplainer
        scores={intersection.scores}
        active="NORTH"
        decision={{
          action: "SWITCH",
          direction: "NORTH",
          green_s: 24,
          reason: "NORTH has highest priority 60 over WEST 45",
          scores: intersection.scores,
          decided_at: "2026-01-01T08:00:00Z",
        }}
      />,
    );
    expect(screen.getByText(/NORTH has highest priority 60/)).toBeInTheDocument();
    for (const name of ["North", "East", "South", "West"]) expect(screen.getByText(name)).toBeInTheDocument();
    expect(screen.getByText("Green now")).toBeInTheDocument();
  });

  it("flags starving and estimated approaches", () => {
    mount(
      <DecisionExplainer
        active={null}
        decision={null}
        scores={[{ ...score("EAST", 20), starving: true, degraded: true }]}
      />,
    );
    expect(screen.getByText("Starving")).toBeInTheDocument();
    expect(screen.getByText("Estimated")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for the first decision/)).toBeInTheDocument();
  });
});

describe("signal controls", () => {
  it("describes phases in words", () => {
    expect(phaseText(intersection)).toBe("Green North");
    expect(phaseText({ ...intersection, phase: "ALL_RED", active_direction: null })).toBe("All red clearance");
  });

  it("disables direction buttons in automatic mode", () => {
    mount(<SignalControls item={intersection} />);
    expect(screen.getByRole("button", { name: /East/ })).toBeDisabled();
  });

  it("sends a manual request through the API, never directly to a signal", async () => {
    const fetchMock = mockFetch({ "/api/signals": { message: "ok" } });
    mount(<SignalControls item={{ ...intersection, mode: "MANUAL" }} />);
    await userEvent.click(screen.getByRole("button", { name: /East/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/signals/SIG-001/manual-control");
    expect(JSON.parse(String(init?.body))).toMatchObject({ direction: "EAST" });
  });

  it("does not offer the direction that is already green", () => {
    mount(<SignalControls item={{ ...intersection, mode: "MANUAL" }} />);
    expect(screen.getByRole("button", { name: /North/ })).toBeDisabled();
  });

  it("shows an API rejection to the operator", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "x", message: "Direction disabled" } }), { status: 409 }),
    );
    mount(<SignalControls item={{ ...intersection, mode: "MANUAL" }} />);
    await userEvent.click(screen.getByRole("button", { name: /East/ }));
    await waitFor(() => expect(useLive.getState().toasts[0]?.message).toBe("Direction disabled"));
  });
});

describe("overview page", () => {
  it("computes the finding and metrics from live data", () => {
    mockFetch({ "/api/dashboard/events": [] });
    useLive.setState({ live });
    mount(<Overview />);
    expect(screen.getByRole("heading", { name: "Overview", level: 1 })).toBeInTheDocument();
    expect(screen.getByText(/Longest queue: 22 vehicles on West at Northgate Circle/)).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();
    expect(screen.getByText("No active alerts")).toBeInTheDocument();
  });

  it("shows a loading state before the first live message", () => {
    mockFetch({ "/api/dashboard/events": [] });
    mount(<Overview />);
    expect(screen.getByText(/Connecting to the traffic network/)).toBeInTheDocument();
  });
});

describe("alerts page", () => {
  const alert = {
    id: "ALT-1",
    category: "SENSOR_FAILURE",
    severity: "WARNING",
    description: "SNS-014 stopped reporting",
    intersection_id: "INT-001",
    raised_at: "2026-01-01T08:00:00Z",
    acknowledged: false,
    acknowledged_at: null,
    resolved: false,
    resolved_at: null,
    source: "LIVE",
  };

  it("loads from the API and acknowledges", async () => {
    const fetchMock = mockFetch({
      "/api/alerts/ALT-1/acknowledge": { ...alert, acknowledged: true },
      "/api/alerts": { items: [alert], total: 1 },
    });
    useLive.setState({ live });
    mount(<Alerts />);
    expect(await screen.findByText("SNS-014 stopped reporting")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([path]) => String(path) === "/api/alerts/ALT-1/acknowledge")).toBe(true),
    );
  });

  it("shows an error state with retry when the API fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("down"));
    mount(<Alerts />);
    expect(await screen.findByRole("alert")).toHaveTextContent("The backend cannot be reached");
  });

  it("shows an empty state", async () => {
    mockFetch({ "/api/alerts": { items: [], total: 0 } });
    mount(<Alerts />);
    expect(await screen.findByText("No alerts in this view")).toBeInTheDocument();
  });
});
