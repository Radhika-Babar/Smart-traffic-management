import { useEffect, useState } from "react";

import { TimeChart } from "@/charts";
import { DataTable, EmptyState, ErrorState, Field, LevelBadge, LoadingRows, Metric, PageHeader, Panel, Pill } from "@/components/common";
import { Button, Dialog, Input, Switch } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { IntersectionConfig } from "@/types/api";
import { cn, dateTime, fixed, title } from "@/utils/format";

interface HistoryPoint {
  t: string;
  volume: number;
  wait: number;
  density: number;
}

function AddDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [x, setX] = useState("3");
  const [y, setY] = useState("0");
  const [demand, setDemand] = useState("240");
  const { run, busy } = useAction();
  return (
    <Dialog open onClose={onClose} title="Add intersection">
      <p className="mb-3 text-[13px] text-ink-2">
        The new junction joins the road grid at this position, with four approaches, 21 virtual sensors and a signal
        controller. The simulation resets so the network can be rebuilt. Stored history is kept.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Airport Link" />
          </Field>
        </div>
        <Field label="Grid column" hint="Columns run west to east. The demo city uses 0 to 2.">
          <Input type="number" value={x} onChange={(e) => setX(e.target.value)} />
        </Field>
        <Field label="Grid row" hint="Rows run north to south. The demo city uses 0 and 1.">
          <Input type="number" value={y} onChange={(e) => setY(e.target.value)} />
        </Field>
        <Field label="Entry demand (veh/h)">
          <Input type="number" value={demand} onChange={(e) => setDemand(e.target.value)} />
        </Field>
      </div>
      <div className="mt-4 flex justify-end">
        <Button
          variant="primary"
          disabled={busy}
          onClick={async () => {
            const created = await run(
              () =>
                api.post("/api/intersections", {
                  name,
                  grid_x: Number(x),
                  grid_y: Number(y),
                  base_demand_vph: Number(demand),
                }),
              "Intersection added to the network",
            );
            if (created) {
              onDone();
              onClose();
            }
          }}
        >
          Add intersection
        </Button>
      </div>
    </Dialog>
  );
}

function Editor({ config, onChange }: { config: IntersectionConfig; onChange: () => void }) {
  const [name, setName] = useState(config.name);
  const [description, setDescription] = useState(config.description ?? "");
  const [limit, setLimit] = useState(String(config.speed_limit_ms));
  const { run, busy } = useAction();
  const history = useFetch(() => api.get<HistoryPoint[]>(`/api/intersections/${config.id}/history?hours=24`), config.id);
  const live = useLive((s) => s.live?.intersections.find((i) => i.id === config.id));

  useEffect(() => {
    setName(config.name);
    setDescription(config.description ?? "");
    setLimit(String(config.speed_limit_ms));
  }, [config]);

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Panel name="Details">
        <div className="space-y-2.5">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Description">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="No description" />
          </Field>
          <Field label="Speed limit (m/s)" hint="Changing the limit resets the simulation so the road network can be rebuilt.">
            <Input type="number" step="0.1" value={limit} onChange={(e) => setLimit(e.target.value)} />
          </Field>
          <div className="flex justify-end">
            <Button
              disabled={busy}
              onClick={async () => {
                await run(
                  () =>
                    api.patch(`/api/intersections/${config.id}`, {
                      name,
                      description: description || null,
                      speed_limit_ms: Number(limit),
                    }),
                  "Intersection saved",
                );
                onChange();
              }}
            >
              Save details
            </Button>
          </div>
        </div>
      </Panel>
      <Panel name="Traffic directions" hint="Disabling an approach removes that road, so the simulation resets. Stored history is kept.">
        <DataTable>
          <thead>
            <tr>
              <th>Approach</th>
              <th className="!text-right">Entry demand (veh/h)</th>
              <th className="!text-right">Live level</th>
              <th className="!text-right">Enabled</th>
            </tr>
          </thead>
          <tbody>
            {config.approaches.map((approach) => (
              <tr key={approach.direction}>
                <td>{title(approach.direction)}</td>
                <td className="text-right">
                  <Input
                    aria-label={`${approach.direction} demand`}
                    type="number"
                    className="ml-auto w-24 text-right"
                    defaultValue={approach.base_demand_vph}
                    onBlur={async (event) => {
                      const value = Number(event.target.value);
                      if (value !== approach.base_demand_vph) {
                        await run(
                          () =>
                            api.patch(`/api/intersections/${config.id}/approaches/${approach.direction}`, {
                              base_demand_vph: value,
                            }),
                          "Demand updated",
                        );
                        onChange();
                      }
                    }}
                  />
                </td>
                <td className="text-right">
                  {live?.approaches.find((a) => a.direction === approach.direction) ? (
                    <LevelBadge level={live.approaches.find((a) => a.direction === approach.direction)!.level} />
                  ) : (
                    "–"
                  )}
                </td>
                <td>
                  <div className="flex justify-end">
                    <Switch
                      checked={approach.enabled}
                      label={`Enable ${approach.direction} approach`}
                      disabled={busy}
                      onChange={async (enabled) => {
                        await run(
                          () => api.patch(`/api/intersections/${config.id}/approaches/${approach.direction}`, { enabled }),
                          "Network rebuilt",
                        );
                        onChange();
                      }}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Panel>
      <Panel name="Last 24 hours" className="xl:col-span-2" hint="Stored per-minute aggregates. Seeded history is replaced by live data as the simulation runs.">
        {history.error ? (
          <ErrorState message={history.error} onRetry={history.reload} />
        ) : history.loading && !history.data ? (
          <LoadingRows />
        ) : (
          <TimeChart
            data={history.data ?? []}
            x="t"
            series={[{ key: "volume", name: "Volume", color: "#3987e5" }]}
            format={dateTime}
            unit="vehicles"
            height={160}
          />
        )}
      </Panel>
    </div>
  );
}

export function Intersections() {
  const configs = useFetch(() => api.get<IntersectionConfig[]>("/api/intersections"), "intersections");
  const live = useLive((s) => s.live?.intersections) ?? [];
  const [selected, setSelected] = useState("INT-001");
  const [adding, setAdding] = useState(false);
  const config = configs.data?.find((c) => c.id === selected) ?? configs.data?.[0];

  return (
    <>
      <PageHeader
        name="Intersections"
        finding={configs.data ? `${configs.data.length} intersections on the network` : undefined}
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            Add intersection
          </Button>
        }
      />
      {configs.error ? (
        <Panel>
          <ErrorState message={configs.error} onRetry={configs.reload} />
        </Panel>
      ) : !configs.data ? (
        <LoadingRows rows={8} />
      ) : !configs.data.length ? (
        <Panel>
          <EmptyState message="No intersections yet" />
        </Panel>
      ) : (
        <>
          <Panel flush className="mb-3 overflow-x-auto">
            <table className="w-full min-w-[640px] text-[13px]">
              <thead>
                <tr className="text-xs text-ink-3">
                  <th className="h-8 px-4 text-left font-normal">Intersection</th>
                  <th className="text-left font-normal">Congestion</th>
                  <th className="text-left font-normal">Health</th>
                  <th className="text-left font-normal">Mode</th>
                  <th className="text-right font-normal">Sensors</th>
                  <th className="pr-4 text-right font-normal">Vehicles</th>
                </tr>
              </thead>
              <tbody>
                {configs.data.map((row) => {
                  const state = live.find((i) => i.id === row.id);
                  const healthy = state?.controller === "ONLINE" && state.active_sensors === state.total_sensors;
                  return (
                    <tr
                      key={row.id}
                      onClick={() => setSelected(row.id)}
                      className={cn(
                        "h-10 cursor-pointer border-t border-line hover:bg-card-hover",
                        row.id === config?.id && "bg-card-hover",
                      )}
                    >
                      <td className="px-4">
                        <button className="text-left font-medium">{row.name}</button>
                        <span className="ml-2 text-xs text-ink-3">{row.id}</span>
                      </td>
                      <td>{state ? <LevelBadge level={state.congestion} /> : "–"}</td>
                      <td>{state ? <Pill tone={healthy ? "good" : "warn"}>{healthy ? "Healthy" : "Degraded"}</Pill> : "–"}</td>
                      <td className="text-ink-2">{title(state?.mode ?? row.signal.mode)}</td>
                      <td className="tnum text-right text-ink-2">
                        {state ? `${state.active_sensors} of ${state.total_sensors}` : row.sensor_count}
                      </td>
                      <td className="tnum pr-4 text-right">{fixed(state?.vehicles)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
          {config ? (
            <>
              <section className="mb-3 grid grid-cols-3 divide-x divide-line rounded-lg border border-line bg-card">
                <Metric label="Grid position" value={`${config.grid_x}, ${config.grid_y}`} />
                <Metric label="Minimum green" value={fixed(config.signal.min_green_s)} unit="s" />
                <Metric label="Maximum green" value={fixed(config.signal.max_green_s)} unit="s" />
              </section>
              <Editor config={config} onChange={configs.reload} />
            </>
          ) : null}
        </>
      )}
      {adding ? <AddDialog onClose={() => setAdding(false)} onDone={configs.reload} /> : null}
    </>
  );
}
