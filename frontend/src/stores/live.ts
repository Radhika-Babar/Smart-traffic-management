import { create } from "zustand";

import type {
  Alert,
  Frame,
  LivePayload,
  SensorLive,
  SeriesPoint,
  SystemEvent,
  Topology,
} from "@/types/api";

export interface FrameBuffer {
  previous: Frame | null;
  current: Frame | null;
  receivedAt: number;
}

/**
 * Vehicle frames arrive five times a second and only the map reads them, so
 * they live outside React state and never trigger a render.
 */
export const frames: FrameBuffer = { previous: null, current: null, receivedAt: 0 };

const MAX_SERIES = 240;
const MAX_EVENTS = 60;

export interface Toast {
  id: number;
  kind: "error" | "success";
  message: string;
}

interface LiveState {
  connection: "connecting" | "open" | "closed";
  topology: Topology | null;
  live: LivePayload | null;
  series: SeriesPoint[];
  sensors: SensorLive[];
  events: SystemEvent[];
  alertsVersion: number;
  incidentsVersion: number;
  emergencyVersion: number;
  toasts: Toast[];
  setConnection: (connection: LiveState["connection"]) => void;
  receive: (kind: string, data: unknown) => void;
  toast: (kind: Toast["kind"], message: string) => void;
  dismissToast: (id: number) => void;
}

let toastSerial = 0;

export const useLive = create<LiveState>((set, get) => ({
  connection: "connecting",
  topology: null,
  live: null,
  series: [],
  sensors: [],
  events: [],
  alertsVersion: 0,
  incidentsVersion: 0,
  emergencyVersion: 0,
  toasts: [],
  setConnection: (connection) => set({ connection }),
  toast: (kind, message) => {
    const id = ++toastSerial;
    set({ toasts: [...get().toasts, { id, kind, message }].slice(-3) });
    setTimeout(() => get().dismissToast(id), 5000);
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  receive: (kind, data) => {
    switch (kind) {
      case "frame":
        frames.previous = frames.current;
        frames.current = data as Frame;
        frames.receivedAt = performance.now();
        break;
      case "live": {
        const live = data as LivePayload;
        const series = get().series;
        const point = live.point;
        const fresh = point && series[series.length - 1]?.t !== point.t;
        set({ live, series: fresh ? [...series, point].slice(-MAX_SERIES) : series });
        break;
      }
      case "series":
        set({ series: data as SeriesPoint[] });
        break;
      case "sensors":
        set({ sensors: data as SensorLive[] });
        break;
      case "topology":
        set({ topology: data as Topology });
        break;
      case "event":
        set({ events: [data as SystemEvent, ...get().events].slice(0, MAX_EVENTS) });
        break;
      case "persisted": {
        // Sent after the database commit, so a refetch is sure to see the new rows.
        const kinds = data as string[];
        const state = get();
        set({
          alertsVersion: state.alertsVersion + (kinds.includes("alert") ? 1 : 0),
          incidentsVersion: state.incidentsVersion + (kinds.some((k) => k === "incident" || k === "congestion") ? 1 : 0),
          emergencyVersion: state.emergencyVersion + (kinds.includes("emergency") ? 1 : 0),
        });
        break;
      }
      case "simulation": {
        const live = get().live;
        if (live) set({ live: { ...live, simulation: data as LivePayload["simulation"] } });
        break;
      }
      case "transport": {
        const live = get().live;
        if (live) set({ live: { ...live, transport: data as LivePayload["transport"] } });
        break;
      }
    }
  },
}));

export type { Alert };
