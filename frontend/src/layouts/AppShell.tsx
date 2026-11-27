import { AnimatePresence, motion } from "framer-motion";
import {
  Ambulance,
  BellRing,
  BrainCircuit,
  ChartColumn,
  FlaskConical,
  Gauge,
  Map as MapIcon,
  Menu,
  Pause,
  Play,
  Radio,
  Settings,
  TrafficCone,
  TriangleAlert,
  Waypoints,
  X,
} from "lucide-react";
import { useState, type ComponentType } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { Pill } from "@/components/common";
import { Button, Hint } from "@/components/ui/primitives";
import { useAction } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import { clock, cn } from "@/utils/format";

interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  count?: "alerts" | "emergencies" | "incidents";
}

const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: Gauge },
  { to: "/map", label: "Live Traffic Map", icon: MapIcon },
  { to: "/intersections", label: "Intersections", icon: Waypoints },
  { to: "/sensors", label: "IoT Sensors", icon: Radio },
  { to: "/signals", label: "Signal Control", icon: TrafficCone },
  { to: "/emergency", label: "Emergency Management", icon: Ambulance, count: "emergencies" },
  { to: "/incidents", label: "Incidents", icon: TriangleAlert, count: "incidents" },
  { to: "/prediction", label: "Traffic Prediction", icon: BrainCircuit },
  { to: "/analytics", label: "Analytics", icon: ChartColumn },
  { to: "/alerts", label: "Alerts", icon: BellRing, count: "alerts" },
  { to: "/simulation", label: "Simulation Lab", icon: FlaskConical },
  { to: "/settings", label: "System Settings", icon: Settings },
];

function Mark() {
  return (
    <svg viewBox="0 0 32 32" className="size-7 shrink-0" aria-hidden="true">
      <path d="M10 3v26M22 3v26M3 10h26M3 22h26" stroke="#3B82F6" strokeWidth="2.5" />
      <circle cx="22" cy="10" r="4.5" fill="#22C55E" />
    </svg>
  );
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const overview = useLive((s) => s.live?.overview);
  const counts = {
    alerts: overview?.active_alerts ?? 0,
    emergencies: overview?.active_emergencies ?? 0,
    incidents: overview?.active_incidents ?? 0,
  };
  return (
    <nav aria-label="Main" className="flex h-full flex-col gap-0.5 p-2">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13.5px] transition-colors",
              isActive ? "bg-card font-medium text-ink" : "text-ink-2 hover:bg-card/60 hover:text-ink",
            )
          }
        >
          <item.icon className="size-4 shrink-0" />
          <span className="truncate">{item.label}</span>
          {item.count && counts[item.count] > 0 ? (
            <span className="tnum ml-auto text-xs text-ink-2">{counts[item.count]}</span>
          ) : null}
        </NavLink>
      ))}
    </nav>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const simulation = useLive((s) => s.live?.simulation);
  const transport = useLive((s) => s.live?.transport);
  const connection = useLive((s) => s.connection);
  const { run, busy } = useAction();
  const running = simulation?.state === "RUNNING";
  const fallback = transport?.mode === "LOCAL_FALLBACK";

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-raised px-3">
      <Button size="icon" variant="ghost" className="lg:hidden" aria-label="Open navigation" onClick={onMenu}>
        <Menu />
      </Button>
      <div className="flex items-center gap-2">
        <Mark />
        <div className="leading-tight">
          <div className="text-[15px] font-semibold">Gridline</div>
          <div className="hidden text-[11px] text-ink-3 sm:block">Traffic control · demo city</div>
        </div>
      </div>
      <div className="ml-auto flex min-w-0 items-center gap-3">
        <Hint text="All sensors, vehicles and incidents on this platform are software-simulated. No physical hardware is connected.">
          <Pill tone="info">Simulated data</Pill>
        </Hint>
        {transport ? (
          <Hint
            text={
              fallback
                ? "The MQTT broker is unavailable. Messages are moving through an in-process fallback, not MQTT."
                : `Sensor data is flowing through the Mosquitto broker at ${transport.broker}.`
            }
          >
            <Pill tone={fallback ? "warn" : "good"}>{fallback ? "Local fallback" : "MQTT"}</Pill>
          </Hint>
        ) : null}
        {connection !== "open" ? <Pill tone="bad">Reconnecting</Pill> : null}
        <div className="tnum hidden text-right leading-tight md:block">
          <div className="text-[13px]">{clock(simulation?.clock)}</div>
          <div className="text-[11px] text-ink-3">
            City clock · {simulation ? `${simulation.speed}x` : "–"}
          </div>
        </div>
        <Button
          size="sm"
          disabled={busy || !simulation}
          aria-label={running ? "Pause simulation" : "Resume simulation"}
          onClick={() => run(() => api.post(running ? "/api/simulation/pause" : "/api/simulation/resume"))}
        >
          {running ? <Pause /> : <Play />}
          <span className="hidden sm:inline">{running ? "Pause" : "Resume"}</span>
        </Button>
      </div>
    </header>
  );
}

function Toasts() {
  const toasts = useLive((s) => s.toasts);
  const dismiss = useLive((s) => s.dismissToast);
  return (
    <div className="fixed right-3 bottom-3 z-50 flex w-80 max-w-[calc(100%-24px)] flex-col gap-2" aria-live="polite">
      <AnimatePresence>
        {toasts.map((toast) => (
          <motion.div
            key={toast.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className={cn(
              "flex items-start gap-2 rounded-md border bg-raised p-2.5 text-[13px] shadow-xl",
              toast.kind === "error" ? "border-bad/60" : "border-line-strong",
            )}
          >
            <span className="flex-1">{toast.message}</span>
            <button aria-label="Dismiss" onClick={() => dismiss(toast.id)} className="text-ink-3 hover:text-ink">
              <X className="size-3.5" />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function AppShell() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const fullBleed = location.pathname === "/map";
  return (
    <div className="flex h-full flex-col">
      <TopBar onMenu={() => setOpen(true)} />
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-[228px] shrink-0 overflow-y-auto border-r border-line bg-raised lg:block">
          <Sidebar />
        </aside>
        <AnimatePresence>
          {open ? (
            <>
              <motion.button
                aria-label="Close navigation"
                className="fixed inset-0 z-30 bg-black/60 lg:hidden"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setOpen(false)}
              />
              <motion.aside
                className="fixed inset-y-0 left-0 z-40 w-64 border-r border-line bg-raised lg:hidden"
                initial={{ x: -256 }}
                animate={{ x: 0 }}
                exit={{ x: -256 }}
                transition={{ duration: 0.18 }}
              >
                <Sidebar onNavigate={() => setOpen(false)} />
              </motion.aside>
            </>
          ) : null}
        </AnimatePresence>
        <main className={cn("min-w-0 flex-1 overflow-y-auto", !fullBleed && "p-4 lg:p-5")}>
          <Outlet />
        </main>
      </div>
      <Toasts />
    </div>
  );
}
