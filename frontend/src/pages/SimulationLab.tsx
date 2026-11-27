import { Dices, Pause, Play, RotateCcw, Square } from "lucide-react";
import { useEffect, useState } from "react";

import { ErrorState, Field, LoadingRows, Metric, PageHeader, Panel, Pill } from "@/components/common";
import { Button, Dialog, Select, Slider, Switch } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { Scenario } from "@/types/api";
import { clock, cn, DIRECTIONS, duration, fixed, title } from "@/utils/format";

const SPEEDS = [1, 2, 5, 10];

export function SimulationLab() {
  const live = useLive((s) => s.live);
  const simulation = live?.simulation;
  const intersections = live?.intersections ?? [];
  const scenarios = useFetch(() => api.get<Scenario[]>("/api/simulation/scenarios"), "scenarios");
  const { run, busy } = useAction();
  const [volume, setVolume] = useState(1);
  const [spawn, setSpawn] = useState(240);
  const [customSpawn, setCustomSpawn] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [emergencyType, setEmergencyType] = useState("AMBULANCE");
  const [incidentType, setIncidentType] = useState("MAJOR_ACCIDENT");
  const [site, setSite] = useState("INT-002");
  const [direction, setDirection] = useState("EAST");

  const volumeNow = simulation?.volume_factor;
  useEffect(() => {
    if (volumeNow !== undefined) setVolume(volumeNow);
  }, [volumeNow]);

  const sitesOptions = intersections.map((i) => ({ value: i.id, label: i.name }));

  if (!simulation) {
    return (
      <>
        <PageHeader name="Simulation Lab" />
        <LoadingRows rows={8} />
      </>
    );
  }
  const state = simulation.state;
  const rush = simulation.scenario === "MORNING_RUSH";
  const destination = site === "INT-006" ? "INT-001" : "INT-006";

  return (
    <>
      <PageHeader
        name="Simulation Lab"
        finding={`${simulation.scenario_name} · ${title(state)} at ${simulation.speed}x · run ${simulation.run_id ?? "–"}`}
        actions={
          <>
            <Button disabled={busy || state === "RUNNING"} variant="primary" onClick={() => run(() => api.post(state === "PAUSED" ? "/api/simulation/resume" : "/api/simulation/start"))}>
              <Play /> {state === "PAUSED" ? "Resume" : "Start"}
            </Button>
            <Button disabled={busy || state !== "RUNNING"} onClick={() => run(() => api.post("/api/simulation/pause"))}>
              <Pause /> Pause
            </Button>
            <Button disabled={busy} onClick={() => setConfirming(true)}>
              <RotateCcw /> Reset
            </Button>
          </>
        }
      />

      <section aria-label="Simulation status" className="mb-3 grid grid-cols-2 divide-line rounded-lg border border-line bg-card md:grid-cols-5 md:divide-x">
        <Metric label="City clock" value={clock(simulation.clock)} />
        <Metric label="Run time" value={duration(simulation.sim_seconds)} hint="Simulated time since the last reset" />
        <Metric label="Vehicles" value={fixed(live?.overview.total_vehicles)} />
        <Metric label="Weather" value={title(simulation.weather)} />
        <Metric label="Tick cost" value={fixed(simulation.tick_ms, 2)} unit="ms" hint="CPU time per simulation step. The worker runs on its own thread, away from the API event loop." />
      </section>

      <Panel name="Scenarios" className="mb-3" hint="Each scenario changes demand immediately and schedules its timed events">
        {scenarios.error ? (
          <ErrorState message={scenarios.error} onRetry={scenarios.reload} />
        ) : !scenarios.data ? (
          <LoadingRows />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {scenarios.data.map((scenario) => {
              const active = scenario.key === simulation.scenario;
              return (
                <button
                  key={scenario.key}
                  disabled={busy}
                  aria-pressed={active}
                  onClick={() => run(() => api.post("/api/simulation/scenario", { key: scenario.key }), `${scenario.name} applied`)}
                  className={cn(
                    "rounded-md border p-2.5 text-left transition-colors hover:bg-card-hover disabled:opacity-60",
                    active ? "border-accent" : "border-line",
                  )}
                >
                  <span className="flex items-center justify-between gap-2 text-[13px] font-medium">
                    {scenario.name}
                    {active ? <Pill tone="info">Active</Pill> : null}
                  </span>
                  <span className="mt-0.5 block text-xs leading-snug text-ink-3">{scenario.description}</span>
                </button>
              );
            })}
          </div>
        )}
        <div className="mt-2">
          <Button disabled={busy} onClick={() => run(() => api.post("/api/simulation/scenario", { key: "RANDOM" }), "Random scenario generated")}>
            <Dices /> Generate random scenario
          </Button>
        </div>
      </Panel>

      <div className="grid gap-3 xl:grid-cols-3">
        <Panel name="Traffic controls">
          <div className="space-y-3.5">
            <div>
              <div className="mb-1 text-xs text-ink-3">Simulation speed</div>
              <div className="inline-flex gap-1" role="group" aria-label="Simulation speed">
                {SPEEDS.map((speed) => (
                  <Button
                    key={speed}
                    size="sm"
                    variant={simulation.speed === speed ? "primary" : "outline"}
                    aria-pressed={simulation.speed === speed}
                    onClick={() => run(() => api.post("/api/simulation/speed", { speed }))}
                  >
                    {speed}x
                  </Button>
                ))}
              </div>
            </div>
            <Field label={`Traffic volume ×${volume.toFixed(1)}`} hint="Multiplies demand on every entry road">
              <Slider
                label="Traffic volume"
                value={volume}
                min={0}
                max={3}
                step={0.1}
                onChange={setVolume}
                onCommit={(value) => run(() => api.post("/api/simulation/parameters", { volume_factor: value }))}
              />
            </Field>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs text-ink-3">
                <span>Vehicle spawn rate {customSpawn ? `${spawn} veh/h per entry` : "from each approach's demand"}</span>
                <Switch
                  checked={customSpawn}
                  label="Override spawn rate"
                  onChange={(on) => {
                    setCustomSpawn(on);
                    run(() => api.post("/api/simulation/parameters", on ? { spawn_rate_vph: spawn } : { clear_spawn_rate: true }));
                  }}
                />
              </div>
              {customSpawn ? (
                <Slider
                  label="Spawn rate"
                  value={spawn}
                  min={0}
                  max={900}
                  step={20}
                  onChange={setSpawn}
                  onCommit={(value) => run(() => api.post("/api/simulation/parameters", { spawn_rate_vph: value }))}
                />
              ) : null}
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px]">Peak-hour demand</span>
              <Switch
                checked={rush}
                label="Peak-hour simulation"
                onChange={(on) => run(() => api.post("/api/simulation/scenario", { key: on ? "MORNING_RUSH" : "NORMAL" }))}
              />
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[13px]">Rainy weather</span>
              <Switch
                checked={simulation.weather === "RAIN"}
                label="Rainy weather"
                onChange={(on) => run(() => api.post("/api/simulation/weather", { condition: on ? "RAIN" : "CLEAR" }))}
              />
            </div>
          </div>
        </Panel>

        <Panel name="Inject an event">
          <div className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Intersection">
                <Select label="Target intersection" className="w-full" value={site} onChange={setSite} options={sitesOptions} />
              </Field>
              <Field label="Approach">
                <Select label="Target approach" className="w-full" value={direction} onChange={setDirection} options={DIRECTIONS.map((d) => ({ value: d, label: title(d) }))} />
              </Field>
            </div>
            <div className="flex items-end gap-2">
              <Field label="Accident simulation">
                <Select
                  label="Incident"
                  className="w-full"
                  value={incidentType}
                  onChange={setIncidentType}
                  options={["MINOR_ACCIDENT", "MAJOR_ACCIDENT", "ROAD_BLOCKAGE", "SUDDEN_SLOWDOWN", "LANE_CLOSURE"].map((t) => ({ value: t, label: title(t) }))}
                />
              </Field>
              <Button
                disabled={busy}
                onClick={() => run(() => api.post("/api/incidents/simulate", { incident_type: incidentType, intersection_id: site, direction }), "Simulated incident injected")}
              >
                Inject
              </Button>
            </div>
            <div className="flex items-end gap-2">
              <Field label="Emergency vehicle">
                <Select
                  label="Emergency vehicle"
                  className="w-full"
                  value={emergencyType}
                  onChange={setEmergencyType}
                  options={["AMBULANCE", "FIRE_ENGINE", "POLICE"].map((t) => ({ value: t, label: title(t) }))}
                />
              </Field>
              <Button
                disabled={busy}
                onClick={() => run(() => api.post("/api/emergency-vehicles", { vehicle_type: emergencyType, origin: site, destination }), `Dispatched to ${destination}`)}
              >
                Dispatch
              </Button>
            </div>
          </div>
        </Panel>

        <Panel name="Device faults">
          <div className="space-y-2">
            <p className="text-[13px] text-ink-2">Faults last a few minutes, then devices recover by themselves. Sensors can also be restarted from the IoT Sensors page.</p>
            <Button
              className="w-full"
              disabled={busy}
              onClick={() => run(() => api.post("/api/simulation/sensor-failure", { intersection_id: site, direction, count: 5, duration_s: 180 }), "All sensors on this approach go offline for 3 minutes")}
            >
              Fail all sensors on this approach
            </Button>
            <Button
              className="w-full"
              disabled={busy}
              onClick={() => run(() => api.post("/api/simulation/sensor-failure", { count: 8, duration_s: 120 }), "Eight random sensors go offline for 2 minutes")}
            >
              Fail 8 random sensors
            </Button>
            <Button
              className="w-full"
              disabled={busy}
              onClick={() => run(() => api.post("/api/simulation/controller-failure", { intersection_id: site, duration_s: 60 }), "Controller stops reporting for 60 s")}
            >
              <Square /> Fail signal controller
            </Button>
          </div>
        </Panel>
      </div>

      <Dialog open={confirming} onClose={() => setConfirming(false)} title="Reset the simulation?">
        <p className="text-[13px] text-ink-2">
          All vehicles, incidents and emergency runs on the road are removed and a new run begins. Stored readings and
          analytics are kept.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setConfirming(false)}>Keep running</Button>
          <Button
            variant="danger"
            onClick={async () => {
              setConfirming(false);
              await run(() => api.post("/api/simulation/reset"), "Simulation reset");
            }}
          >
            Reset simulation
          </Button>
        </div>
      </Dialog>
    </>
  );
}
