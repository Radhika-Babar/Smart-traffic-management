import { Pause, Play, X } from "lucide-react";
import { useState } from "react";

import { EmptyState, Field, LevelBadge, Metric, Pill, SignalLamp, Skeleton } from "@/components/common";
import { DecisionExplainer } from "@/components/DecisionExplainer";
import { CityMap } from "@/components/map/CityMap";
import { VEHICLE_LEGEND } from "@/components/map/renderer";
import { phaseText, SignalControls } from "@/components/SignalControls";
import { Button, Select, Slider, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { IntersectionLive, Scenario } from "@/types/api";
import { cn, fixed, kmh, title } from "@/utils/format";

function TrafficTab({ item }: { item: IntersectionLive }) {
  return (
    <table className="tnum w-full text-[13px]">
      <thead>
        <tr className="text-xs text-ink-3">
          <th className="py-1 text-left font-normal">Approach</th>
          <th className="text-right font-normal">Queue</th>
          <th className="text-right font-normal">Density</th>
          <th className="text-right font-normal">Wait</th>
          <th className="text-right font-normal">km/h</th>
        </tr>
      </thead>
      <tbody>
        {item.approaches.map((approach) => (
          <tr key={approach.direction} className="border-t border-line">
            <td className="py-1.5">
              <span className="flex items-center gap-1.5">
                <SignalLamp aspect={item.heads[approach.direction]} />
                {title(approach.direction)}
              </span>
              <LevelBadge level={approach.level} />
            </td>
            <td className="text-right">{fixed(approach.queue)}</td>
            <td className="text-right">{fixed(approach.density)}%</td>
            <td className="text-right">{fixed(approach.avg_wait_s)} s</td>
            <td className="text-right">{kmh(approach.speed)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SensorsTab({ item }: { item: IntersectionLive }) {
  const sensors = useLive((s) => s.sensors).filter((s) => s.intersection_id === item.id);
  const { run } = useAction();
  return (
    <ul className="max-h-80 space-y-0 overflow-y-auto pr-1">
      {sensors.map((sensor) => {
        const values = Object.entries(sensor.latest).filter(([key]) => key !== "timestamp");
        return (
          <li key={sensor.id} className="flex items-center gap-2 border-t border-line py-1.5 first:border-t-0">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 text-[13px]">
                <span className="tnum">{sensor.id}</span>
                <span className="truncate text-xs text-ink-3">
                  {sensor.direction ? title(sensor.direction) : "Site"} · {title(sensor.sensor_type)}
                </span>
              </div>
              <div className="tnum truncate text-xs text-ink-3">
                {values.length ? values.map(([key, value]) => `${title(key)} ${value}`).join(" · ") : "No reading yet"}
              </div>
            </div>
            {sensor.status === "OFFLINE" ? (
              <Button size="sm" onClick={() => run(() => api.post(`/api/sensors/${sensor.id}/restart`))}>
                Restart
              </Button>
            ) : (
              <Pill tone={sensor.status === "ONLINE" ? "good" : "neutral"}>{title(sensor.status)}</Pill>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ParametersTab({ item }: { item: IntersectionLive }) {
  const factors = useLive((s) => s.live?.simulation.intersection_factors);
  const [factor, setFactor] = useState(factors?.[item.id] ?? 1);
  const { run } = useAction();
  return (
    <div className="space-y-3">
      <Field
        label={`Local demand ×${factor.toFixed(1)}`}
        hint="Scales arrivals on this intersection's entry roads in the running simulation."
      >
        <Slider
          label="Local demand"
          value={factor}
          min={0}
          max={3}
          step={0.1}
          onChange={setFactor}
          onCommit={(value) =>
            run(() => api.post("/api/simulation/parameters", { intersection_id: item.id, intersection_factor: value }))
          }
        />
      </Field>
      <div className="grid grid-cols-2 gap-1.5">
        <Button
          size="sm"
          onClick={() =>
            run(
              () =>
                api.post("/api/simulation/sensor-failure", {
                  intersection_id: item.id,
                  count: 2,
                  duration_s: 120,
                }),
              "Two sensors will drop out for 2 minutes",
            )
          }
        >
          Fail 2 sensors
        </Button>
        <Button
          size="sm"
          onClick={() =>
            run(
              () =>
                api.post("/api/incidents/simulate", {
                  incident_type: "MAJOR_ACCIDENT",
                  intersection_id: item.id,
                  direction: item.approaches[0].direction,
                }),
              "Simulated accident injected",
            )
          }
        >
          Inject accident
        </Button>
      </div>
    </div>
  );
}

function IntersectionPanel({ item, onClose }: { item: IntersectionLive; onClose: () => void }) {
  return (
    <aside className="flex min-h-0 flex-col overflow-y-auto border-t border-line bg-card lg:w-[372px] lg:shrink-0 lg:border-t-0 lg:border-l">
      <div className="flex items-start justify-between gap-2 px-4 pt-3">
        <div className="min-w-0">
          <h2 className="truncate text-base font-semibold">{item.name}</h2>
          <div className="text-xs text-ink-3">
            {item.id} · {phaseText(item)} · {title(item.mode)}
          </div>
        </div>
        <Button size="icon" variant="ghost" aria-label="Close panel" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="grid grid-cols-3 divide-x divide-line">
        <Metric label="Vehicles" value={fixed(item.vehicles)} />
        <Metric label="Sensors" value={`${item.active_sensors}/${item.total_sensors}`} />
        <Metric label="Next" value={fixed(item.remaining)} unit="s" hint="Seconds of planned green left" />
      </div>
      <Tabs defaultValue="why" className="px-4 pb-4">
        <TabList
          tabs={[
            { value: "why", label: "Decision" },
            { value: "traffic", label: "Traffic" },
            { value: "sensors", label: "Sensors" },
            { value: "control", label: "Control" },
            { value: "params", label: "Parameters" },
          ]}
        />
        <TabPanel value="why" className="pt-3">
          <DecisionExplainer scores={item.scores} decision={item.last_decision} active={item.active_direction} />
        </TabPanel>
        <TabPanel value="traffic" className="pt-2">
          <TrafficTab item={item} />
        </TabPanel>
        <TabPanel value="sensors" className="pt-2">
          <SensorsTab item={item} />
        </TabPanel>
        <TabPanel value="control" className="pt-3">
          <SignalControls item={item} />
        </TabPanel>
        <TabPanel value="params" className="pt-3">
          <ParametersTab item={item} />
        </TabPanel>
      </Tabs>
    </aside>
  );
}

export function LiveMap() {
  const live = useLive((s) => s.live);
  const topology = useLive((s) => s.topology);
  const [selected, setSelected] = useState<string | null>("INT-002");
  const item = live?.intersections.find((i) => i.id === selected) ?? null;
  const simulation = live?.simulation;
  const { run, busy } = useAction();
  const scenarios = useFetch(() => api.get<Scenario[]>("/api/simulation/scenarios"), "scenarios");
  const running = simulation?.state === "RUNNING";

  return (
    <div className="flex h-full min-h-[640px] flex-col lg:flex-row">
      <div className="flex min-h-[420px] min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-2">
          <h1 className="text-lg font-semibold">Live Traffic Map</h1>
          <div className="hidden flex-wrap items-center gap-x-3 text-xs text-ink-3 xl:flex">
            {VEHICLE_LEGEND.map((entry) => (
              <span key={entry.label} className="flex items-center gap-1">
                <span className="h-1.5 w-3 rounded-[1px]" style={{ background: entry.color }} />
                {entry.label}
              </span>
            ))}
            <span className="flex items-center gap-1">
              <span className="size-1.5 bg-accent" />
              Sensor site
            </span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Select
              label="Trigger scenario"
              className="w-44"
              value={simulation?.scenario ?? ""}
              placeholder="Scenario"
              options={(scenarios.data ?? []).map((s) => ({ value: s.key, label: s.name }))}
              onChange={(key) => run(() => api.post("/api/simulation/scenario", { key }))}
            />
            <Button
              size="sm"
              disabled={busy || !simulation}
              onClick={() => run(() => api.post(running ? "/api/simulation/pause" : "/api/simulation/resume"))}
            >
              {running ? <Pause /> : <Play />}
              {running ? "Pause" : "Resume"}
            </Button>
          </div>
        </div>
        {topology ? (
          <CityMap selected={selected} onSelect={setSelected} className="min-h-0 flex-1" />
        ) : (
          <div className="flex-1 p-6">
            <Skeleton className="h-full w-full" />
          </div>
        )}
        <div className="flex gap-1.5 overflow-x-auto border-t border-line px-3 py-2">
          {live?.intersections.map((entry) => (
            <button
              key={entry.id}
              onClick={() => setSelected(entry.id)}
              className={cn(
                "flex h-7 shrink-0 items-center gap-1.5 rounded-md border px-2 text-xs transition-colors",
                entry.id === selected ? "border-accent text-ink" : "border-line text-ink-2 hover:bg-card-hover",
              )}
            >
              {entry.name}
              <LevelBadge level={entry.congestion} />
            </button>
          ))}
        </div>
      </div>
      {item ? (
        <IntersectionPanel item={item} onClose={() => setSelected(null)} />
      ) : (
        <aside className="hidden w-[372px] shrink-0 border-l border-line bg-card lg:block">
          <EmptyState message="Select an intersection" detail="Click a junction on the map" />
        </aside>
      )}
    </div>
  );
}
