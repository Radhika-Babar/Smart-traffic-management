import { useEffect, useState } from "react";

import { DataTable, ErrorState, Field, LoadingRows, Metric, PageHeader, Panel, Pill } from "@/components/common";
import { Button, Dialog, Input, Select, Slider } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { SystemHealth, SystemSettings } from "@/types/api";
import { duration, fixed, title } from "@/utils/format";

type Weight = keyof SystemSettings["algorithm"]["weights"];
const WEIGHTS: { key: Weight; label: string }[] = [
  { key: "queue", label: "Queue length" },
  { key: "waiting", label: "Waiting time" },
  { key: "density", label: "Traffic density" },
  { key: "demand", label: "Vehicle demand" },
  { key: "speed", label: "Low speed" },
];

type Threshold = "queue" | "occupancy" | "wait" | "speed";
const THRESHOLDS: { key: Threshold; label: string }[] = [
  { key: "queue", label: "Queue (vehicles)" },
  { key: "occupancy", label: "Occupancy (%)" },
  { key: "wait", label: "Wait (s)" },
  { key: "speed", label: "Speed (m/s, at or below)" },
];

function Transport({ uptime, clients, database }: { uptime?: number; clients?: number; database?: SystemHealth["database"] }) {
  const transport = useLive((s) => s.live?.transport);
  if (!transport) return <LoadingRows />;
  const fallback = transport.mode === "LOCAL_FALLBACK";
  const topics = Object.entries(transport.received);
  return (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Pill tone={fallback ? "warn" : "good"}>{fallback ? "Local fallback, not MQTT" : "MQTT connected"}</Pill>
        <span className="text-[13px] text-ink-2">Broker {transport.broker}</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3">
        <Metric label="Reconnects" value={transport.reconnects} />
        <Metric label="Rejected payloads" value={transport.invalid_payloads + transport.malformed} hint="Messages that failed schema validation or JSON parsing" />
        <Metric label="Processing backlog" value={transport.backlog} />
        <Metric label="Backend uptime" value={duration(uptime)} />
        <Metric label="Dashboard clients" value={clients ?? "–"} />
        <Metric label="Database" value={title(database?.backend ?? "–")} sub={`${fixed(database?.rows_written)} rows this run`} />
      </div>
      <DataTable className="mt-2">
        <thead>
          <tr>
            <th>Topic group</th>
            <th className="!text-right">Messages received</th>
          </tr>
        </thead>
        <tbody>
          {topics.map(([topic, count]) => (
            <tr key={topic}>
              <td className="text-ink-2">traffic/{topic}</td>
              <td className="tnum text-right">{fixed(count)}</td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </>
  );
}

export function Settings() {
  const config = useFetch(() => api.get<SystemSettings>("/api/system/config"), "config");
  const health = useFetch(() => api.get<SystemHealth>("/api/system/health"), "health");
  const [form, setForm] = useState<SystemSettings | null>(null);
  const [cleanup, setCleanup] = useState(false);
  const [days, setDays] = useState("30");
  const { run, busy } = useAction();

  useEffect(() => {
    if (config.data) setForm(config.data);
  }, [config.data]);

  if (config.error) {
    return (
      <>
        <PageHeader name="System Settings" />
        <Panel>
          <ErrorState message={config.error} onRetry={config.reload} />
        </Panel>
      </>
    );
  }
  if (!form) {
    return (
      <>
        <PageHeader name="System Settings" />
        <LoadingRows rows={8} />
      </>
    );
  }
  const weights = form.algorithm.weights;
  const total = WEIGHTS.reduce((sum, w) => sum + weights[w.key], 0);
  const setAlgorithm = (patch: Partial<SystemSettings["algorithm"]>) => setForm({ ...form, algorithm: { ...form.algorithm, ...patch } });

  return (
    <>
      <PageHeader
        name="System Settings"
        finding="Algorithm weights and thresholds apply to the running system as soon as they are saved"
        actions={
          <Button variant="primary" disabled={busy} onClick={() => run(() => api.put("/api/system/config", form), "Settings applied")}>
            Save settings
          </Button>
        }
      />
      <div className="grid gap-3 xl:grid-cols-2">
        <Panel name="Adaptive signal algorithm" hint="Priority = 100 × weighted mean of the normalised inputs. Weights are relative, so they need not sum to 1.">
          <div className="space-y-2.5">
            <Field label="Control strategy" hint="Fixed time is the comparison baseline: every approach gets the default green in rotation.">
              <Select
                label="Control strategy"
                className="w-full"
                value={form.algorithm.control_strategy}
                onChange={(value) => setAlgorithm({ control_strategy: value as "ADAPTIVE" | "FIXED_TIME" })}
                options={[
                  { value: "ADAPTIVE", label: "Adaptive" },
                  { value: "FIXED_TIME", label: "Fixed time (baseline)" },
                ]}
              />
            </Field>
            {WEIGHTS.map((weight) => (
              <Field key={weight.key} label={`${weight.label}: ${weights[weight.key].toFixed(2)} (${total ? fixed((100 * weights[weight.key]) / total) : 0}% of score)`}>
                <Slider
                  label={`${weight.label} weight`}
                  value={weights[weight.key]}
                  min={0}
                  max={1}
                  step={0.05}
                  onChange={(value) => setAlgorithm({ weights: { ...weights, [weight.key]: value } })}
                />
              </Field>
            ))}
            <div className="grid grid-cols-2 gap-2">
              <Field label="Starvation limit (s)" hint="After this long an approach with demand is served next, whatever its score.">
                <Input type="number" value={form.algorithm.max_wait_limit_s} onChange={(e) => setAlgorithm({ max_wait_limit_s: Number(e.target.value) })} />
              </Field>
              <Field label="Hysteresis" hint="The current green keeps running only if its score beats its best rival by this factor.">
                <Input type="number" step="0.05" value={form.algorithm.hysteresis} onChange={(e) => setAlgorithm({ hysteresis: Number(e.target.value) })} />
              </Field>
            </div>
          </div>
        </Panel>

        <Panel name="Congestion thresholds" hint="A level needs at least two indicators to agree, so one noisy sensor cannot raise it.">
          <DataTable>
            <thead>
              <tr>
                <th>Indicator</th>
                <th className="!text-right">Moderate</th>
                <th className="!text-right">High</th>
                <th className="!text-right">Severe</th>
              </tr>
            </thead>
            <tbody>
              {THRESHOLDS.map((row) => (
                <tr key={row.key}>
                  <td className="whitespace-nowrap">{row.label}</td>
                  {[0, 1, 2].map((index) => (
                    <td key={index} className="text-right">
                      <Input
                        aria-label={`${row.label} level ${index + 1}`}
                        type="number"
                        step="0.5"
                        className="ml-auto w-20 text-right"
                        value={form.congestion[row.key][index]}
                        onChange={(event) => {
                          const next = [...form.congestion[row.key]] as [number, number, number];
                          next[index] = Number(event.target.value);
                          setForm({ ...form, congestion: { ...form.congestion, [row.key]: next } });
                        }}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </DataTable>
          <div className="mt-3 grid grid-cols-3 gap-2">
            <Field label="Alert from">
              <Select
                label="Alert level"
                className="w-full"
                value={form.congestion.alert_level}
                onChange={(value) => setForm({ ...form, congestion: { ...form.congestion, alert_level: value } })}
                options={["MODERATE", "HIGH", "SEVERE"].map((l) => ({ value: l, label: title(l) }))}
              />
            </Field>
            <Field label="Raise after (s)">
              <Input type="number" value={form.congestion.raise_after_s} onChange={(e) => setForm({ ...form, congestion: { ...form.congestion, raise_after_s: Number(e.target.value) } })} />
            </Field>
            <Field label="Clear after (s)">
              <Input type="number" value={form.congestion.clear_after_s} onChange={(e) => setForm({ ...form, congestion: { ...form.congestion, clear_after_s: Number(e.target.value) } })} />
            </Field>
          </div>
          <div className="mt-3">
            <Field label="Random sensor faults: mean hours between failures per sensor (0 turns them off)">
              <Input type="number" value={form.sensor_mtbf_hours} onChange={(e) => setForm({ ...form, sensor_mtbf_hours: Number(e.target.value) })} />
            </Field>
          </div>
        </Panel>

        <Panel name="Messaging and runtime">
          <Transport uptime={health.data?.uptime_s} clients={health.data?.websocket_clients} database={health.data?.database} />
        </Panel>

        <Panel name="Data retention">
          <p className="mb-3 text-[13px] text-ink-2">
            History survives resets and restarts. This is the only action that deletes it, and it cannot be undone.
          </p>
          <Button variant="danger" onClick={() => setCleanup(true)}>
            Delete old history
          </Button>
          <p className="mt-3 text-xs text-ink-3">
            No third-party services or API keys are used. Everything runs on this machine. Sensors and vehicles are
            software-simulated and no physical hardware is connected.
          </p>
        </Panel>
      </div>

      <Dialog open={cleanup} onClose={() => setCleanup(false)} title="Delete old history?">
        <Field label="Delete records older than (days)">
          <Input type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} />
        </Field>
        <div className="mt-4 flex justify-end gap-2">
          <Button onClick={() => setCleanup(false)}>Keep history</Button>
          <Button
            variant="danger"
            onClick={async () => {
              setCleanup(false);
              await run(() => api.post("/api/system/cleanup", { older_than_days: Number(days), confirm: true }), "Old history deleted");
            }}
          >
            Delete permanently
          </Button>
        </div>
      </Dialog>
    </>
  );
}
